"""Thin LLM client boundary for the POC Agent Runtime.

Defines a narrow protocol so the Agent Runtime is not coupled to any
specific provider SDK. The only concrete adapter provided is Anthropic.
Credentials are read from the environment; never embedded in source.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import httpx

DEFAULT_MODEL = os.environ.get("POC_LLM_MODEL", "claude-sonnet-4-5-20250929")
DEFAULT_MAX_TOKENS = 4096


class LLMProviderError(RuntimeError):
    """Raised when the LLM provider fails or is unreachable."""


@dataclass(frozen=True)
class LLMToolCall:
    """Structured tool invocation returned by the model."""
    name: str
    arguments: Mapping[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class LLMResponse:
    """Normalized model output: either text, tool calls, or both."""
    text: str | None = None
    tool_calls: tuple[LLMToolCall, ...] = ()
    raw: Mapping[str, Any] | None = None


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


class AnthropicLLMClient:
    """Official Anthropic SDK adapter implementing LLMClientProtocol."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
        auth_token: str | None = None,
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
        self._client = anthropic.Anthropic(**kwargs)
        self._model = model or os.environ.get("POC_LLM_MODEL", DEFAULT_MODEL)
        self._max_tokens = max_tokens

    def chat(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse:
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = [
                    {"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)}
                    for t in tools
                ]
            response = self._client.messages.create(**kwargs)
        except Exception as error:
            raise LLMProviderError(f"LLM provider call failed: {error}") from error

        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(LLMToolCall(name=block.name, arguments=block.input, call_id=block.id))
        return LLMResponse(text="\n".join(text_parts) if text_parts else None, tool_calls=tuple(tool_calls), raw={"stop_reason": response.stop_reason})


class OpenAICompatibleLLMClient:
    """Chat-completions adapter for ANY OpenAI-compatible endpoint.

    Covers OpenAI, NVIDIA NIM, OpenRouter, Together, DeepInfra, vLLM, Ollama,
    and every other provider exposing POST {base_url}/chat/completions — so a
    deployment can point the POC at the buyer's own model. Uses httpx directly
    (no SDK dependency); credentials stay in the environment and never reach
    the agent/LLM-visible payloads.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self._model = model or os.environ.get("POC_LLM_MODEL", "")
        self._base_url = (base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self._max_tokens = max_tokens
        self._timeout = timeout

    def chat(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": (
                ([{"role": "system", "content": system}] if system else [])
                + [{"role": m.role, "content": m.content} for m in messages]
            ),
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": dict(t.input_schema),
                    },
                }
                for t in tools
            ]
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as error:
            raise LLMProviderError(f"LLM provider call failed: {error}") from error

        try:
            message = body["choices"][0]["message"]
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
        text = message.get("content")
        return LLMResponse(text=text, tool_calls=tuple(tool_calls), raw={"stop_reason": body.get("choices", [{}])[0].get("finish_reason")})


def build_llm_client(model: str | None = None) -> LLMClientProtocol:
    """Provider factory selected by LLM_PROVIDER (composition-root helper).

    'anthropic' (default) — official SDK; supports ANTHROPIC_BASE_URL for
    Anthropic-compatible providers (e.g. z.ai GLM). 'openai_compatible' — any
    OpenAI-schema endpoint via LLM_BASE_URL/LLM_API_KEY/POC_LLM_MODEL.
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    if provider == "anthropic":
        return AnthropicLLMClient(model=model)
    if provider == "openai_compatible":
        return OpenAICompatibleLLMClient(model=model)
    raise ValueError(
        f"Unsupported LLM_PROVIDER {provider!r}; use 'anthropic' or 'openai_compatible'."
    )


class FakeLLMClient:
    """Deterministic test double implementing LLMClientProtocol."""

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