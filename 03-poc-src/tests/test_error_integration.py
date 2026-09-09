"""STEP 11 integration tests: structured errors flow through real boundaries."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.errors import (
    CONFIRMATION_EXPIRED,
    ERP_CONNECTION_ERROR,
    LLM_ERROR,
    PERMISSION_DENIED,
    SCHEMA_INVALID,
    TOOL_NOT_FOUND,
    StructuredError,
)
from poc.gateway import ToolGateway, ToolGatewayRequest
from poc.llm_client import FakeLLMClient, LLMProviderError, LLMResponse, LLMToolCall
from poc.odoo_client import OdooAuthenticationError, OdooTimeoutError
from poc.tool_contracts import get_registry

TENANT = "poc_tenant_001"
USER = "sales_user@test"
NO_ACCESS = "no_access_user@test"
VERSION = "1.0.0"


def assert_wire_shape(error: StructuredError | None) -> None:
    assert error is not None
    assert error.to_dict() == {
        "success": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
            "requires_user_action": error.requires_user_action,
        },
    }


def make_gateway(tmp_path: Path) -> ToolGateway:
    initialize(tmp_path / "poc.db")
    return ToolGateway(db_path=tmp_path / "poc.db")


def make_runtime(tmp_path, calls, user_id=USER):
    gw = make_gateway(tmp_path)
    llm = FakeLLMClient(responses=[LLMResponse(tool_calls=calls)])
    return AgentRuntime(llm_client=llm, gateway=gw, user_id=user_id, tenant_id=TENANT)


# --- Gateway boundary -------------------------------------------------------


def test_gateway_invalid_schema_returns_structured_error(tmp_path):
    gw = make_gateway(tmp_path)
    result = gw.handle_request(
        ToolGatewayRequest(
            request_id=str(uuid.uuid4()),
            user_id=USER,
            tenant_id=TENANT,
            tool_name="customer.search",
            tool_version=VERSION,
            arguments={"bad": "args"},
        )
    )
    assert result.structured_error is not None
    assert result.structured_error.code == SCHEMA_INVALID
    assert result.structured_error.retryable is False
    assert result.structured_error.requires_user_action is False
    assert_wire_shape(result.structured_error)


def test_gateway_unknown_tool_returns_structured_error(tmp_path):
    gw = make_gateway(tmp_path)
    result = gw.handle_request(
        ToolGatewayRequest(
            request_id=str(uuid.uuid4()),
            user_id=USER,
            tenant_id=TENANT,
            tool_name="nonexistent.tool",
            tool_version=VERSION,
            arguments={},
        )
    )
    assert result.structured_error is not None
    assert result.structured_error.code == TOOL_NOT_FOUND
    assert_wire_shape(result.structured_error)


def test_gateway_policy_denied_returns_structured_error(tmp_path):
    gw = make_gateway(tmp_path)
    result = gw.handle_request(
        ToolGatewayRequest(
            request_id=str(uuid.uuid4()),
            user_id=NO_ACCESS,
            tenant_id=TENANT,
            tool_name="customer.search",
            tool_version=VERSION,
            arguments={"query": "Acme"},
        )
    )
    assert result.structured_error is not None
    assert result.structured_error.code == PERMISSION_DENIED
    assert result.structured_error.requires_user_action is True
    assert_wire_shape(result.structured_error)


# --- Confirmation boundary ---------------------------------------------------


def test_confirmation_expired_returns_structured_error(tmp_path):
    from poc.confirmation import ConfirmationStore

    initialize(tmp_path / "poc.db")
    st = ConfirmationStore(db_path=tmp_path / "poc.db")
    created = st.create_proposal(
        "sales.order.create",
        VERSION,
        {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]},
        USER,
        TENANT,
    )
    assert created.proposal is not None
    proposal_id = created.proposal.proposal_id
    conn = sqlite3.connect(tmp_path / "poc.db")
    conn.execute(
        "UPDATE proposals SET expires_at = ? WHERE proposal_id = ?",
        (
            (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            proposal_id,
        ),
    )
    conn.commit()
    conn.close()
    approved = st.approve(proposal_id, USER, TENANT)
    assert approved.structured_error is not None
    assert approved.structured_error.code == CONFIRMATION_EXPIRED
    assert_wire_shape(approved.structured_error)


# --- Agent Runtime boundary ---------------------------------------------------


def test_agent_unknown_tool_returns_structured_error(tmp_path):
    rt = make_runtime(tmp_path, [LLMToolCall(name="nonexistent.tool", arguments={}, call_id="c1")])
    result = rt.process("do something")
    assert result.outcome == "unknown_tool_rejected"
    assert result.structured_error is not None
    assert result.structured_error.code == TOOL_NOT_FOUND
    assert_wire_shape(result.structured_error)


def test_agent_invalid_schema_returns_structured_error(tmp_path):
    rt = make_runtime(tmp_path, [LLMToolCall(name="customer.search", arguments={"bad": "args"}, call_id="c2")])
    result = rt.process("do something")
    assert result.outcome == "invalid_arguments"
    assert result.structured_error is not None
    assert result.structured_error.code == SCHEMA_INVALID
    assert_wire_shape(result.structured_error)


def test_agent_llm_error_returns_structured_error(tmp_path):
    gw = make_gateway(tmp_path)
    llm = FakeLLMClient(error=LLMProviderError("provider down"))
    rt = AgentRuntime(llm_client=llm, gateway=gw, user_id=USER, tenant_id=TENANT)
    result = rt.process("do something")
    assert result.outcome == "llm_error"
    assert result.structured_error is not None
    assert result.structured_error.code == LLM_ERROR
    assert_wire_shape(result.structured_error)


def test_agent_policy_denied_returns_structured_error(tmp_path):
    rt = make_runtime(
        tmp_path,
        [LLMToolCall(name="customer.search", arguments={"query": "Acme"}, call_id="c3")],
        user_id=NO_ACCESS,
    )
    result = rt.process("do something")
    assert result.gateway_result is not None
    assert result.structured_error is not None
    assert result.structured_error.code == PERMISSION_DENIED
    assert_wire_shape(result.structured_error)


def test_agent_odoo_timeout_returns_structured_error(tmp_path):
    rt = make_runtime(tmp_path, [LLMToolCall(name="customer.search", arguments={"query": "Acme"}, call_id="c4")])

    class ExplodingGateway:
        def handle_request(self, request):
            raise OdooTimeoutError("timeout", "connection timed out")

    rt.gateway = ExplodingGateway()
    result = rt.process("do something")
    assert result.outcome == "erp_error"
    assert result.structured_error is not None
    assert result.structured_error.code == ERP_CONNECTION_ERROR
    assert result.structured_error.retryable is True
    assert result.structured_error.requires_user_action is False
    assert_wire_shape(result.structured_error)


def test_agent_odoo_authentication_returns_structured_error(tmp_path):
    rt = make_runtime(tmp_path, [LLMToolCall(name="customer.search", arguments={"query": "Acme"}, call_id="c5")])

    class AuthFailGateway:
        def handle_request(self, request):
            raise OdooAuthenticationError("auth_fail", "bad credentials")

    rt.gateway = AuthFailGateway()
    result = rt.process("do something")
    assert result.outcome == "erp_error"
    assert result.structured_error is not None
    assert result.structured_error.code == ERP_CONNECTION_ERROR
    assert result.structured_error.retryable is False
    assert result.structured_error.requires_user_action is True
    assert_wire_shape(result.structured_error)




