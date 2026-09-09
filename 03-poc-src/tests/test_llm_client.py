"""Tests for the thin LLM client boundary."""

from __future__ import annotations

import pytest

from poc.llm_client import (
    FakeLLMClient,
    LLMClientProtocol,
    LLMMessage,
    LLMProviderError,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)


def test_fake_client_implements_protocol():
    client = FakeLLMClient()
    assert hasattr(client, "chat")


def test_fake_client_returns_configured_response():
    resp = LLMResponse(text="hello")
    client = FakeLLMClient(responses=[resp])
    result = client.chat([LLMMessage(role="user", content="test")])
    assert result.text == "hello"


def test_fake_client_raises_provider_error():
    client = FakeLLMClient(error=LLMProviderError("connection failed"))
    with pytest.raises(LLMProviderError):
        client.chat([LLMMessage(role="user", content="test")])


def test_fake_client_records_calls():
    client = FakeLLMClient()
    client.chat([LLMMessage(role="user", content="hi")], system="sys", tools=[])
    assert len(client.calls) == 1
    assert client.calls[0]["system"] == "sys"


def test_tool_definition_shape():
    td = LLMToolDefinition(name="customer.search", description="search", input_schema={"type": "object"})
    assert td.name == "customer.search"


def test_tool_call_shape():
    tc = LLMToolCall(name="customer.search", arguments={"query": "x"})
    assert tc.name == "customer.search"
    assert tc.arguments == {"query": "x"}