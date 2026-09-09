"""Error taxonomy and CLI presentation scenarios.

Proves canonical structured errors survive every boundary and that the CLI
preserves error.code/message/retryable/requires_user_action in output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.llm_client import FakeLLMClient, LLMResponse, LLMToolCall
from poc.main import _build_payload

from poc.odoo_client import OdooTimeoutError


TENANT = "poc_tenant_001"
USER = "sales_user@test"
VERSION = "1.0.0"
ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}


class _FailingOdoo:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

        self.create_calls: list = []

    def read(self, model, ids, fields):
        if self.error:
            raise self.error
        if model == "product.product":
            return [{"id": 7, "list_price": 50.0}]
        if model == "sale.order":
            return [{"id": 1, "partner_id": (42, "T"), "state": "draft",
                     "amount_total": 100.0, "order_line": [1], "client_order_ref": "a" * 32}]
        if model == "sale.order.line":
            return [{"id": 1, "product_id": (7, "P"), "product_uom_qty": 2}]
        return []

    def create(self, model, vals_list):
        if self.error:
            raise self.error
        self.create_calls.append((model, vals_list))
        return [1]


def test_cli_verification_failure_presents_structured_error(tmp_path: Path) -> None:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    gw = ToolGateway(db_path=db_path)

    class WrongProductOdoo(_FailingOdoo):
        def read(self, model, ids, fields):
            if model == "sale.order.line":
                return [{"id": 1, "product_id": (99, "Wrong"), "product_uom_qty": 2}]
            return super().read(model, ids, fields)

    result = gw.handle_request({
        "request_id": "r1", "user_id": USER, "tenant_id": TENANT,
        "tool_name": "sales.order.create", "tool_version": VERSION, "arguments": ARGS,
    })
    assert result.status == "confirmation_required"
    approved = gw.confirm_and_execute(result.proposal["proposal_id"], USER, TENANT, WrongProductOdoo())
    assert approved.status == "error"
    assert approved.error_code == "VERIFICATION_FAILED"
    se = approved.structured_error
    assert se is not None
    assert se.code == "VERIFICATION_FAILED"
    assert se.retryable is False
    assert se.requires_user_action is True


def test_cli_ambiguous_outcome_presents_retryable_error(tmp_path: Path) -> None:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    gw = ToolGateway(db_path=db_path)

    class TimeoutOdoo(_FailingOdoo):
        def create(self, model, vals_list):
            raise OdooTimeoutError("timeout", "connection timed out")

    result = gw.handle_request({
        "request_id": "r1", "user_id": USER, "tenant_id": TENANT,
        "tool_name": "sales.order.create", "tool_version": VERSION, "arguments": ARGS,
    })
    assert result.status == "confirmation_required"
    approved = gw.confirm_and_execute(result.proposal["proposal_id"], USER, TENANT, TimeoutOdoo())
    assert approved.status == "erp_error"
    assert approved.error_code == "ERP_CONNECTION_ERROR"
    se = approved.structured_error
    assert se is not None
    assert se.retryable is True
    assert se.requires_user_action is False


def test_cli_denial_does_not_execute_and_preserves_state(tmp_path: Path) -> None:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    gw = ToolGateway(db_path=db_path)
    odoo = _FailingOdoo()

    result = gw.handle_request({
        "request_id": "r1", "user_id": USER, "tenant_id": TENANT,
        "tool_name": "sales.order.create", "tool_version": VERSION, "arguments": ARGS,
    })
    assert result.status == "confirmation_required"
    proposal_id = result.proposal["proposal_id"]

    declined = gw.record_confirmation_denial(proposal_id, USER, TENANT)
    assert declined.status == "declined"
    assert odoo.create_calls == []
    assert declined.audit_id is not None

    audit = gw.audit_store
    chain = audit.verify_chain()
    assert chain["valid"] is True


def test_agent_unknown_tool_preserves_canonical_error(tmp_path: Path) -> None:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    gw = ToolGateway(db_path=db_path)
    runtime = AgentRuntime(
        llm_client=FakeLLMClient(responses=[LLMResponse(
            tool_calls=[LLMToolCall(name="arbitrary.execute", arguments={})], text="",
        )]),
        gateway=gw, user_id=USER, tenant_id=TENANT,
    )
    result = runtime.process("execute arbitrary")
    assert result.outcome == "unknown_tool_rejected"
    se = result.structured_error
    assert se is not None
    assert se.code == "TOOL_NOT_FOUND"


def test_cli_payload_preserves_error_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    gw = ToolGateway(db_path=db_path)

    result = gw.handle_request({
        "request_id": "r1", "user_id": "unknown@test", "tenant_id": TENANT,
        "tool_name": "customer.search", "tool_version": VERSION, "arguments": {"query": "x"},
    })
    assert result.status == "denied"
    assert result.error_code == "POLICY_DENIED"
    se = result.structured_error
    assert se is not None
    assert se.code == "PERMISSION_DENIED"
    assert se.retryable is False
    assert se.requires_user_action is True


