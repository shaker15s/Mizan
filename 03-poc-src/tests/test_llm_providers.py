"""Tests for the OpenAI-compatible LLM adapter and the provider factory."""

from __future__ import annotations

import json

import httpx
import pytest

from poc import llm_client as llm_client_module
from poc.llm_client import (
    LLMMessage,
    LLMProviderError,
    LLMToolDefinition,
    build_llm_client,
)


def _tools() -> list[LLMToolDefinition]:
    return [
        LLMToolDefinition(
            name="customer.search",
            description="Search customers",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        )
    ]


def _openai_response(message: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": message, "finish_reason": "tool_calls"}]},
        request=httpx.Request("POST", "https://test/v1/chat/completions"),
    )


def test_openai_client_sends_function_schema_and_system(monkeypatch):
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return _openai_response({"content": "ok", "tool_calls": []})

    monkeypatch.setattr(llm_client_module.httpx, "post", fake_post)
    client = llm_client_module.OpenAICompatibleLLMClient(
        api_key="test-key-123", model="glm-5.3-flash", base_url="https://api.z.ai/api/paas/v4/"
    )
    response = client.chat([LLMMessage(role="user", content="ابحث عن سكر")], system="sys", tools=_tools())

    assert captured["url"] == "https://api.z.ai/api/paas/v4/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key-123"
    body = captured["json"]
    assert body["model"] == "glm-5.3-flash"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "customer.search"
    assert body["tools"][0]["function"]["parameters"]["required"] == ["query"]
    assert response.text == "ok"
    assert response.tool_calls == ()


def test_openai_client_parses_tool_call_arguments_json(monkeypatch):
    monkeypatch.setattr(
        llm_client_module.httpx,
        "post",
        lambda url, **kwargs: _openai_response(
            {
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "customer.search", "arguments": json.dumps({"query": "سكر"})}}
                ],
            }
        ),
    )
    client = llm_client_module.OpenAICompatibleLLMClient(api_key="k", model="m", base_url="https://x/v1")
    response = client.chat([LLMMessage(role="user", content="q")], tools=_tools())
    assert response.tool_calls[0].name == "customer.search"
    assert response.tool_calls[0].arguments == {"query": "سكر"}
    assert response.tool_calls[0].call_id == "call_1"


def test_openai_client_wraps_transport_errors(monkeypatch):
    def boom(url, **kwargs):
        raise httpx.ConnectTimeout("down")

    monkeypatch.setattr(llm_client_module.httpx, "post", boom)
    client = llm_client_module.OpenAICompatibleLLMClient(api_key="k", model="m", base_url="https://x/v1")
    with pytest.raises(LLMProviderError):
        client.chat([LLMMessage(role="user", content="q")])


def test_openai_client_rejects_malformed_tool_arguments(monkeypatch):
    monkeypatch.setattr(
        llm_client_module.httpx,
        "post",
        lambda url, **kwargs: _openai_response(
            {"content": None, "tool_calls": [{"id": "c", "function": {"name": "t", "arguments": "{not json"}}]}
        ),
    )
    client = llm_client_module.OpenAICompatibleLLMClient(api_key="k", model="m", base_url="https://x/v1")
    with pytest.raises(LLMProviderError):
        client.chat([LLMMessage(role="user", content="q")])


def test_factory_selects_provider_by_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    client = build_llm_client()
    assert isinstance(client, llm_client_module.OpenAICompatibleLLMClient)


def test_factory_defaults_to_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    client = build_llm_client()
    assert isinstance(client, llm_client_module.AnthropicLLMClient)


def test_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "telepathy")
    with pytest.raises(ValueError):
        build_llm_client()
