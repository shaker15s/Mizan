"""Architectural security regression scenarios.

Proves identity/tenant isolation, confirmation bypass prevention, import
boundary enforcement, and secret non-leakage through real public boundaries.
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from poc.audit_store import AuditStore
from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.llm_client import FakeLLMClient, LLMResponse, LLMToolCall
from poc.tool_contracts import get_registry


TENANT = "poc_tenant_001"
USER = "sales_user@test"
VERSION = "1.0.0"
ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}


def _gateway(tmp_path: Path) -> ToolGateway:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    return ToolGateway(db_path=db_path)


class _FakeOdoo:
    def __init__(self) -> None:
        self.create_calls: list = []
        self.expected_ref: str | None = None

    def read(self, model, ids, fields):
        client_order_ref = self.expected_ref or ("a" * 32)
        if model == "product.product":
            return [{"id": 7, "list_price": 50.0}]
        if model == "sale.order":
            return [{"id": 1, "partner_id": (42, "Test"), "state": "draft",
                     "amount_total": 100.0, "order_line": [1],
                     "client_order_ref": client_order_ref}]
        if model == "sale.order.line":
            return [{"id": 1, "product_id": (7, "P"), "product_uom_qty": 2}]
        return []

    def create(self, model, vals_list):
        self.create_calls.append((model, vals_list))
        self.expected_ref = vals_list[0]["client_order_ref"]
        return [1]


def test_llm_cannot_override_tenant_or_user(tmp_path: Path) -> None:
    gw = _gateway(tmp_path)
    malicious_llm = FakeLLMClient(responses=[LLMResponse(
        tool_calls=[LLMToolCall(
            name="sales.order.create",
            arguments={**ARGS, "__tenant_id": "evil_tenant", "__user_id": "admin"},
        )],
        text="",
    )])
    runtime = AgentRuntime(llm_client=malicious_llm, gateway=gw, user_id=USER, tenant_id=TENANT)
    result = runtime.process("create order")
    assert result.outcome == "invalid_arguments"


def test_gateway_rejects_cross_tenant_idempotency_replay(tmp_path: Path) -> None:
    gw = _gateway(tmp_path)
    req_a = {"request_id": "r1", "user_id": USER, "tenant_id": TENANT,
             "tool_name": "customer.search", "tool_version": VERSION, "arguments": {"query": "x"}}
    result_a = gw.handle_request(req_a)
    assert result_a.status == "accepted"

    req_b = {"request_id": "r2", "user_id": USER, "tenant_id": "other_tenant",
             "tool_name": "customer.search", "tool_version": VERSION, "arguments": {"query": "x"}}
    result_b = gw.handle_request(req_b)
    assert result_b.status == "denied"
    assert result_b.error_code == "POLICY_DENIED"


def test_gateway_rejects_execute_without_confirmation(tmp_path: Path) -> None:
    gw = _gateway(tmp_path)
    odoo = _FakeOdoo()
    result = gw.handle_request({
        "request_id": "r1", "user_id": USER, "tenant_id": TENANT,
        "tool_name": "sales.order.create", "tool_version": VERSION, "arguments": ARGS,
    })
    assert result.status == "confirmation_required"
    assert odoo.create_calls == []
    assert result.result is None


def test_gateway_concurrent_confirmation_approval_exactly_one_wins(tmp_path: Path) -> None:
    gw = _gateway(tmp_path)
    odoo = _FakeOdoo()
    result = gw.handle_request({
        "request_id": "r1", "user_id": USER, "tenant_id": TENANT,
        "tool_name": "sales.order.create", "tool_version": VERSION, "arguments": ARGS,
    })
    assert result.status == "confirmation_required"
    proposal_id = result.proposal["proposal_id"]

    outcomes: list = []
    barrier = threading.Barrier(3)

    def attempt():
        barrier.wait()
        try:
            r = gw.confirm_and_execute(proposal_id, USER, TENANT, odoo)
            outcomes.append(r.status)
        except Exception as e:
            outcomes.append(f"error:{e}")

    threads = [threading.Thread(target=attempt) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(odoo.create_calls) <= 1
    assert outcomes.count("accepted") == 1
    assert len(outcomes) == 3
    assert outcomes.count("denied") + outcomes.count("error") == 2


_FORBIDDEN_IMPORTS = {"poc.odoo_client", "poc.audit_store"}
_UNTRUSTED_MODULES = [
    "poc.main",
    "poc.agent_runtime",
    "poc.llm_client",
    "poc.tool_contracts",
    "poc.authz",
]


@pytest.mark.parametrize("module_name", _UNTRUSTED_MODULES)
def test_untrusted_modules_do_not_import_odoo_or_audit(module_name: str) -> None:
    module_path = module_name.replace(".", "/") + ".py"
    src_root = Path(__file__).resolve().parents[1]
    tree = ast.parse((src_root / module_path).read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in _FORBIDDEN_IMPORTS, f"{module_name} imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            full = f"poc.{mod}" if not mod.startswith("poc") else mod
            assert full not in _FORBIDDEN_IMPORTS, f"{module_name} imports {full}"


def test_no_secrets_in_successful_gateway_result(tmp_path: Path) -> None:
    gw = _gateway(tmp_path)
    odoo = _FakeOdoo()
    result = gw.handle_request({
        "request_id": "r1", "user_id": USER, "tenant_id": TENANT,
        "tool_name": "sales.order.create", "tool_version": VERSION, "arguments": ARGS,
    })
    assert result.status == "confirmation_required"
    approved = gw.confirm_and_execute(result.proposal["proposal_id"], USER, TENANT, odoo)
    assert approved.status == "accepted"
    serialized = json.dumps(approved.result, default=str).lower()
    for secret in ["api_key", "password", "bearer", "token"]:
        assert secret not in serialized

