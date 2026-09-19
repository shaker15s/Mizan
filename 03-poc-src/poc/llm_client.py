"""Thin LLM client boundary for the POC Agent Runtime.

Defines a narrow protocol so the Agent Runtime is not coupled to any specific
provider SDK. Credentials are read from the environment or passed explicitly
by the composition root; they are never embedded in source and never reach
agent/LLM-visible payloads.

Harness-facing additions (all backwards compatible):

- **Generation options per client** (``temperature``, ``max_tokens``,
  ``timeout``, ``retries``) so the cockpit's settings panel can retune the
  model at runtime without touching env or restarting the process.
- **Transient-error policy**: 429/5xx/network failures are retried with
  exponential backoff + jitter honouring ``Retry-After``; auth/4xx failures
  fail immediately (retrying them only hides the real problem).
- **Streaming** (``stream_chat``) for the narrative pass: Anthropic SDK
  streaming, and hand-rolled SSE parsing for OpenAI-compatible endpoints.
  Providers without support raise :class:`LLMStreamUnsupported` so the caller
  degrades to a single blocking call instead of failing.
- ``usage`` (tokens) is surfaced for latency/cost telemetry.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

import httpx

DEFAULT_MODEL = os.environ.get("POC_LLM_MODEL", "claude-sonnet-4-5-20250929")
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_RETRIES = int(os.environ.get("LLM_RETRIES", "1"))
BACKOFF_BASE_SECONDS = 0.2
BACKOFF_CAP_SECONDS = 4.0
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class LLMProviderError(RuntimeError):
    """Raised when the LLM provider fails or is unreachable."""

    def __init__(self, message: str, *, retryable: bool = False, attempts: int = 1) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.attempts = attempts


class LLMStreamUnsupported(LLMProviderError):
    """The selected provider/endpoint cannot stream tokens."""


@dataclass(frozen=True)
class LLMToolCall:
    """Structured tool invocation returned by the model."""

    name: str
    arguments: Mapping[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class LLMUsage:
    """Token accounting for one provider call."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {"input": self.input_tokens, "output": self.output_tokens, "total": self.total_tokens}


@dataclass(frozen=True)
class LLMResponse:
    """Normalized model output: either text, tool calls, or both."""

    text: str | None = None
    tool_calls: tuple[LLMToolCall, ...] = ()
    raw: Mapping[str, Any] | None = None
    usage: LLMUsage | None = None


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LLMToolDefinition:
    """Provider-agnostic tool definition passed to the model."""

    name: str
    description: str
    input_schema: Mapping[str, Any]


class LLMClientProtocol(Protocol):
    """Minimal chat contract consumed by AgentRuntime."""

    def chat(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse: ...


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return min(BACKOFF_CAP_SECONDS, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
    base = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2**attempt))
    return base + random.uniform(0, base * 0.25)  # jitter: avoid thundering herd


def _is_transient(error: Exception) -> bool:
    status = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in _RETRY_STATUS
    text = str(error).lower()
    if any(token in text for token in ("401", "403", "invalid api key", "unauthorized", "authentication")):
        return False
    return any(
        token in text
        for token in ("timeout", "timed out", "connection", "temporarily", "rate limit", "too many requests", "503", "502", "overloaded")
    )


def _resolve_tool_choice(tools: Sequence["LLMToolDefinition"], mode: str) -> tuple[Any, str]:
    """Provider-neutral tool_choice policy shared by both adapters."""
    mode = (mode or "").strip().lower()
    if not mode or mode == "off" or not tools:
        return None, ""
    if mode == "required" and len(tools) == 1:
        return ("tool", tools[0].name), mode
    return ("any", None), mode


class _GenerationOptions:
    """Generation parameters resolved from explicit args → env → defaults."""

    def __init__(
        self,
        *,
        model: str | None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        force_tool_choice: str | None = None,
    ) -> None:
        self.model = model or os.environ.get("POC_LLM_MODEL", DEFAULT_MODEL)
        self.max_tokens = int(os.environ.get("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS) if max_tokens is None else max_tokens)
        self.temperature = float(os.environ.get("LLM_TEMPERATURE", DEFAULT_TEMPERATURE) if temperature is None else temperature)
        self.timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS) if timeout is None else timeout)
        self.retries = int(DEFAULT_RETRIES if retries is None else retries)
        self.force_tool_choice = (
            force_tool_choice if force_tool_choice is not None else os.environ.get("FORCE_TOOL_CHOICE", "")
        ).strip()


class _RetryableCall:
    """Executes one provider call with a bounded transient retry policy."""

    def __init__(self, options: _GenerationOptions) -> None:
        self.options = options

    def run(self, call: Callable[[], Any], *, what: str = "call") -> Any:
        attempts = max(1, self.options.retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return call()
            except Exception as error:  # noqa: BLE001 - normalized below
                last_error = error
                if isinstance(error, LLMStreamUnsupported) or not _is_transient(error) or attempt == attempts - 1:
                    break
                headers = getattr(getattr(error, "response", None), "headers", None) or {}
                time.sleep(_retry_delay(attempt, headers.get("retry-after") if hasattr(headers, "get") else None))
        raise LLMProviderError(
            f"LLM provider {what} failed: {type(last_error).__name__}: {last_error}",
            retryable=_is_transient(last_error or Exception()),
            attempts=min(attempts, (self.options.retries or 0) + 1),
        ) from last_error


class AnthropicLLMClient:
    """Official Anthropic SDK adapter implementing LLMClientProtocol."""

    provider = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        base_url: str | None = None,
        auth_token: str | None = None,
        *,
        temperature: float | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        force_tool_choice: str | None = None,
    ) -> None:
        try:
            import anthropic
        except ImportError as error:
            raise LLMProviderError("anthropic SDK not installed") from error
        kwargs: dict[str, Any] = {}
        resolved_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if resolved_api_key:
            kwargs["api_key"] = resolved_api_key
        resolved_base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        if resolved_base_url:
            kwargs["base_url"] = resolved_base_url
        resolved_auth_token = auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if resolved_auth_token:
            kwargs["auth_token"] = resolved_auth_token
        options = _GenerationOptions(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            retries=retries,
            force_tool_choice=force_tool_choice,
        )
        resolved_timeout = options.timeout
        if resolved_timeout:
            kwargs["timeout"] = resolved_timeout
        self._client = anthropic.Anthropic(**kwargs)
        self._options = options
        self._retry = _RetryableCall(options)

    # --- request building ---------------------------------------------------
    def _payload(self, messages: list[LLMMessage], system: str | None, tools: list[LLMToolDefinition] | None, *, stream: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._options.model,
            "max_tokens": self._options.max_tokens,
            "temperature": self._options.temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)} for t in tools
            ]
            choice, mode = _resolve_tool_choice(tools, self._options.force_tool_choice)
            if choice is not None:
                kind, name = choice
                kwargs["tool_choice"] = {"type": "tool", "name": name} if kind == "tool" else {"type": "any"}
        return kwargs

    # --- protocol -----------------------------------------------------------
    def chat(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse:
        kwargs = self._payload(messages, system, tools, stream=False)
        response = self._retry.run(lambda: self._client.messages.create(**kwargs), what="chat")
        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(LLMToolCall(name=block.name, arguments=block.input, call_id=block.id))
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tuple(tool_calls),
            raw={"stop_reason": response.stop_reason, "provider": self.provider},
            usage=LLMUsage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            )
            if usage is not None
            else None,
        )

    def stream_chat(
        self,
        messages: list[LLMMessage],
        on_delta: Callable[[str], None],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse:
        """Stream text deltas through ``on_delta`` while collecting the answer."""

        def _call() -> LLMResponse:
            collector = _TextCollector(on_delta)
            kwargs = self._payload(messages, system, tools, stream=True)
            try:
                with self._client.messages.stream(**kwargs) as stream:
                    for event in stream:
                        delta = getattr(getattr(event, "delta", None), "text", None)
                        if delta:
                            collector.push(delta)
                    final = stream.get_final_message()
            except AttributeError as error:  # older SDK without the stream helper
                raise LLMStreamUnsupported(f"anthropic streaming unavailable: {error}") from error
            text_parts = [block.text for block in final.content if getattr(block, "type", "") == "text"]
            collected = collector.text or ("\n".join(text_parts) if text_parts else None)
            return LLMResponse(text=collected, tool_calls=(), raw={"stop_reason": getattr(final, "stop_reason", None), "provider": self.provider, "streamed": True})

        return self._retry.run(_call, what="stream")


class _TextCollector:
    """Collects streamed deltas while forwarding them to a sink."""

    def __init__(self, sink: Callable[[str], None] | None = None) -> None:
        self._sink = sink
        self._parts: list[str] = []

    def push(self, delta: str) -> None:
        if not delta:
            return
        self._parts.append(delta)
        if self._sink is not None:
            try:
                self._sink(delta)
            except Exception:  # a dead client must not kill the provider call
                self._sink = None

    @property
    def text(self) -> str:
        return "".join(self._parts)


class OpenAICompatibleLLMClient:
    """Chat-completions adapter for ANY OpenAI-compatible endpoint.

    Covers OpenAI, NVIDIA NIM, OpenRouter, Together, DeepInfra, vLLM, Ollama,
    and every other provider exposing POST {base_url}/chat/completions — so a
    deployment can point the POC at the buyer's own model. Uses httpx directly
    (no SDK dependency); credentials stay in the environment and never reach
    the agent/LLM-visible payloads.
    """

    provider = "openai_compatible"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        *,
        temperature: float | None = None,
        retries: int | None = None,
        force_tool_choice: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self._base_url = (base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self._options = _GenerationOptions(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            retries=retries,
            force_tool_choice=force_tool_choice,
        )
        if not self._options.model:
            raise LLMProviderError("POC_LLM_MODEL must be set for an OpenAI-compatible provider.")
        self._retry = _RetryableCall(self._options)

    def _payload(
        self,
        messages: list[LLMMessage],
        system: str | None,
        tools: list[LLMToolDefinition] | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._options.model,
            "max_tokens": self._options.max_tokens,
            "temperature": self._options.temperature,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": m.role, "content": m.content} for m in messages],
        }
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": dict(t.input_schema)}}
                for t in tools
            ]
            choice, _mode = _resolve_tool_choice(tools, self._options.force_tool_choice)
            if choice is not None:
                kind, name = choice
                payload["tool_choice"] = {"type": "function", "function": {"name": name}} if kind == "tool" else "required"
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def chat(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse:
        payload = self._payload(messages, system, tools, stream=False)

        def _call() -> LLMResponse:
            try:
                response = httpx.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self._options.timeout,
                )
                response.raise_for_status()
                body = response.json()
            except (LLMProviderError,):
                raise
            except Exception as error:
                raise error if _is_transient(error) else LLMProviderError(f"LLM provider call failed: {error}", retryable=False) from error
            return self._parse(body)

        return self._retry.run(_call, what="call")

    def _parse(self, body: Mapping[str, Any]) -> LLMResponse:
        try:
            choice = body["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMProviderError("LLM provider returned an unrecognized response shape.") from error
        tool_calls: list[LLMToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (ValueError, TypeError) as error:
                raise LLMProviderError("LLM tool-call arguments are not valid JSON.") from error
            tool_calls.append(LLMToolCall(name=function.get("name", ""), arguments=arguments, call_id=call.get("id")))
        usage = body.get("usage") or {}
        return LLMResponse(
            text=message.get("content"),
            tool_calls=tuple(tool_calls),
            raw={"stop_reason": choice.get("finish_reason"), "provider": self.provider},
            usage=LLMUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            )
            if usage
            else None,
        )

    def stream_chat(
        self,
        messages: list[LLMMessage],
        on_delta: Callable[[str], None],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse:
        """Parse an SSE ``chat/completions`` stream without extra dependencies."""
        payload = self._payload(messages, system, tools, stream=True)
        collector = _TextCollector(on_delta)

        def _call() -> LLMResponse:
            try:
                with httpx.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self._options.timeout,
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        for choice in chunk.get("choices") or []:
                            delta = (choice.get("delta") or {}).get("content")
                            if delta:
                                collector.push(delta)
            except Exception as error:
                if _is_transient(error):
                    raise error
                raise LLMStreamUnsupported(f"openai-compatible streaming failed: {error}") from error
            return LLMResponse(
                text=collector.text or None, tool_calls=(), raw={"provider": self.provider, "streamed": True}
            )

        return self._retry.run(_call, what="stream")


def build_llm_client(
    model: str | None = None,
    *,
    provider: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    retries: int | None = None,
    force_tool_choice: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> Any:
    """Provider factory selected by ``LLM_PROVIDER`` (composition-root helper).

    ``anthropic`` (default) — official SDK; supports ANTHROPIC_BASE_URL for
    Anthropic-compatible providers (e.g. z.ai GLM). ``openai_compatible`` — any
    OpenAI-schema endpoint via LLM_BASE_URL/LLM_API_KEY/POC_LLM_MODEL.
    ``simulated`` — the offline Arabic rule engine (demo mode; same protocol).
    """
    resolved = (provider or os.environ.get("LLM_PROVIDER", "anthropic")).strip().lower()
    if resolved == "anthropic":
        return AnthropicLLMClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            retries=retries,
            force_tool_choice=force_tool_choice,
        )
    if resolved == "openai_compatible":
        return OpenAICompatibleLLMClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            retries=retries,
            force_tool_choice=force_tool_choice,
        )
    if resolved == "simulated":
        from poc.simulated_llm import SimulatedLLMClient

        return SimulatedLLMClient()
    raise ValueError(
        f"Unsupported LLM_PROVIDER {resolved!r}; use 'anthropic', 'openai_compatible', or 'simulated'."
    )


class FakeLLMClient:
    """Deterministic test double implementing LLMClientProtocol."""

    provider = "fake"

    def __init__(self, responses: list[LLMResponse] | LLMResponse | None = None, error: Exception | None = None) -> None:
        if isinstance(responses, LLMResponse):
            responses = [responses]
        self._responses = list(responses or [])
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "system": system, "tools": tools})
        if self._error:
            raise self._error
        if not self._responses:
            return LLMResponse(text="(no response configured)")
        return self._responses.pop(0)


__all__ = [
    "AnthropicLLMClient",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "FakeLLMClient",
    "LLMClientProtocol",
    "LLMMessage",
    "LLMProviderError",
    "LLMResponse",
    "LLMStreamUnsupported",
    "LLMToolCall",
    "LLMToolDefinition",
    "LLMUsage",
    "OpenAICompatibleLLMClient",
    "build_llm_client",
]
