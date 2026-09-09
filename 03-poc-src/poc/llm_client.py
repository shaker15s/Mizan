"""Thin LLM client boundary for the POC Agent Runtime.

Defines a narrow protocol so the Agent Runtime is not coupled to any
specific provider SDK. The only concrete adapter provided is Anthropic.
Credentials are read from the environment; never embedded in source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

DEFAULT_MODEL = os.environ.get("POC_LLM_MODEL", "claude-sonnet-4-5-20250929")
DEFAULT_MAX_TOKENS = 1024


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
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        try:
            import anthropic
        except ImportError as error:
            raise LLMProviderError("anthropic SDK not installed") from error
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._model = model
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