"""Tests for the FORCE_TOOL_CHOICE opt-in (deployment-level, default OFF)."""

from __future__ import annotations

import httpx
import pytest

from poc import llm_client as llm_client_module
from poc.llm_client import LLMMessage, LLMToolDefinition


def _tools(count: int = 1) -> list[LLMToolDefinition]:
    return [
        LLMToolDefinition(name=f"tool_{index}", description="test tool", input_schema={"type": "object"})
        for index in range(count)
    ]


def _openai_response(message: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": message, "finish_reason": "tool_calls"}]},
        request=httpx.Request("POST", "https://test/v1/chat/completions"),
    )


def _capture_openai_post(captured: dict, monkeypatch) -> None:
    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _openai_response({"content": "ok", "tool_calls": []})

    monkeypatch.setattr(llm_client_module.httpx, "post", fake_post)


class _StubBlock:
    def __init__(self, block_type: str, text: str | None = None) -> None:
        self.type = block_type
        self.text = text


class _StubMessage:
    """Minimal stand-in for the anthropic Message shape chat() reads."""

    def __init__(self) -> None:
        self.content = [_StubBlock("text", "ok")]
        self.stop_reason = "end_turn"


def test_openai_tool_choice_absent_when_unset(monkeypatch):
    monkeypatch.delenv("FORCE_TOOL_CHOICE", raising=False)
    captured: dict = {}
    _capture_openai_post(captured, monkeypatch)
    client = llm_client_module.OpenAICompatibleLLMClient(api_key="k", model="m", base_url="https://x/v1")
    client.chat([LLMMessage(role="user", content="q")], tools=_tools(1))
    assert "tool_choice" not in captured["json"]


def test_openai_tool_choice_required_targets_single_tool(monkeypatch):
    monkeypatch.setenv("FORCE_TOOL_CHOICE", "required")
    captured: dict = {}
    _capture_openai_post(captured, monkeypatch)
    client = llm_client_module.OpenAICompatibleLLMClient(api_key="k", model="m", base_url="https://x/v1")
    client.chat([LLMMessage(role="user", content="q")], tools=_tools(1))
    assert captured["json"]["tool_choice"] == {"type": "function", "function": {"name": "tool_0"}}


def test_openai_tool_choice_required_multiple_tools_falls_back(monkeypatch):
    monkeypatch.setenv("FORCE_TOOL_CHOICE", "required")
    captured: dict = {}
    _capture_openai_post(captured, monkeypatch)
    client = llm_client_module.OpenAICompatibleLLMClient(api_key="k", model="m", base_url="https://x/v1")
    client.chat([LLMMessage(role="user", content="q")], tools=_tools(2))
    assert captured["json"]["tool_choice"] == "required"


def test_openai_tool_choice_any_maps_to_required(monkeypatch):
    monkeypatch.setenv("FORCE_TOOL_CHOICE", "any")
    captured: dict = {}
    _capture_openai_post(captured, monkeypatch)
    client = llm_client_module.OpenAICompatibleLLMClient(api_key="k", model="m", base_url="https://x/v1")
    client.chat([LLMMessage(role="user", content="q")], tools=_tools(1))
    assert captured["json"]["tool_choice"] == "required"


def test_anthropic_tool_choice_absent_when_unset(monkeypatch):
    monkeypatch.delenv("FORCE_TOOL_CHOICE", raising=False)
    captured: list[dict] = []
    client = llm_client_module.AnthropicLLMClient(api_key="test-key")
    monkeypatch.setattr(client._client.messages, "create", lambda **kwargs: captured.append(kwargs) or _StubMessage())
    client.chat([LLMMessage(role="user", content="q")], tools=_tools(1))
    assert "tool_choice" not in captured[0]


def test_anthropic_tool_choice_required_targets_single_tool(monkeypatch):
    monkeypatch.setenv("FORCE_TOOL_CHOICE", "required")
    captured: list[dict] = []
    client = llm_client_module.AnthropicLLMClient(api_key="test-key")
    monkeypatch.setattr(client._client.messages, "create", lambda **kwargs: captured.append(kwargs) or _StubMessage())
    client.chat([LLMMessage(role="user", content="q")], tools=_tools(1))
    assert captured[0]["tool_choice"] == {"type": "tool", "name": "tool_0"}


def test_anthropic_tool_choice_required_multiple_tools_falls_back(monkeypatch):
    monkeypatch.setenv("FORCE_TOOL_CHOICE", "required")
    captured: list[dict] = []
    client = llm_client_module.AnthropicLLMClient(api_key="test-key")
    monkeypatch.setattr(client._client.messages, "create", lambda **kwargs: captured.append(kwargs) or _StubMessage())
    client.chat([LLMMessage(role="user", content="q")], tools=_tools(2))
    assert captured[0]["tool_choice"] == {"type": "any"}


def test_anthropic_tool_choice_any_forces_any(monkeypatch):
    monkeypatch.setenv("FORCE_TOOL_CHOICE", "any")
    captured: list[dict] = []
    client = llm_client_module.AnthropicLLMClient(api_key="test-key")
    monkeypatch.setattr(client._client.messages, "create", lambda **kwargs: captured.append(kwargs) or _StubMessage())
    client.chat([LLMMessage(role="user", content="q")], tools=_tools(1))
    assert captured[0]["tool_choice"] == {"type": "any"}


def test_force_tool_choice_ignored_when_no_tools_passed(monkeypatch):
    monkeypatch.setenv("FORCE_TOOL_CHOICE", "required")
    captured: list[dict] = []
    client = llm_client_module.AnthropicLLMClient(api_key="test-key")
    monkeypatch.setattr(client._client.messages, "create", lambda **kwargs: captured.append(kwargs) or _StubMessage())
    client.chat([LLMMessage(role="user", content="q")])
    assert "tool_choice" not in captured[0]
