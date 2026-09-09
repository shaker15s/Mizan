"""Golden end-to-end scenario: full mutation lifecycle with audit chain proof.

Exercises: FakeLLM -> AgentRuntime -> ToolGateway -> Confirmation -> Idempotency ->
FakeOdoo -> Verification -> Audit -> Structured Result.

Proves architectural invariants at each boundary. No production code changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from poc.audit_store import AuditStore
from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.idempotency import IdempotencyStore
from poc.odoo_client import OdooTimeoutError

from poc.llm_client import FakeLLMClient, LLMResponse, LLMToolCall


TENANT = "poc_tenant_001"
USER = "sales_user@test"
VERSION = "1.0.0"
ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}


class FakeOdooClient:
    """Simulates Odoo CRUD with state tracking for side-effect assertions."""

    def __init__(self) -> None:
        self.products: dict[int, dict[str, Any]] = {7: {"id": 7, "list_price": 50.0}}
        self.partners: dict[int, dict[str, Any]] = {42: {"id": 42, "name": "Test Partner", "email": None, "phone": None}}
        self.orders: dict[int, dict[str, Any]] = {}
        self.order_lines: dict[int, dict[str, Any]] = {}
        self.create_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self._next_id = 100

    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int | None = None) -> list[dict[str, Any]]:
        query = next(
            (triplet[2] for triplet in domain if isinstance(triplet, (list, tuple)) and len(triplet) == 3 and triplet[0] == "name"),
            "",
        )
        if model == "res.partner":
            source = self.partners
        elif model == "product.product":
            source = self.products
        else:
            return []
        matches = [dict(record) for record in source.values() if query.lower() in str(record.get("name", "")).lower()]
        return matches[:limit] if limit else matches

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        if model == "product.product":
            return [self.products[i] for i in ids if i in self.products]
        if model == "sale.order":
            return [
                {**{k: self.orders[i].get(k) for k in fields}, "id": i}
                for i in ids
                if i in self.orders
            ]
        if model == "sale.order.line":
            return [
                {**{k: self.order_lines[i].get(k) for k in fields}, "id": i}
                for i in ids
                if i in self.order_lines
            ]
        return []

    def create(self, model: str, vals_list: list[dict[str, Any]]) -> list[int]:
        self.create_calls.append((model, list(vals_list)))
        created_ids = []
        for vals in vals_list:
            self._next_id += 1
            order_id = self._next_id
            line_ids = []
            for line_spec in vals.get("order_line", []):

                if isinstance(line_spec, list) and line_spec[0] == 0:
        
                    line_id = order_id * 100 + len(line_ids)

                    self.order_lines[line_id] = {
                        "id": line_id,
                        "product_id": (line_spec[2]["product_id"], "Test Product"),
                        "product_uom_qty": line_spec[2]["product_uom_qty"],
                    }
                    line_ids.append(line_id)
            subtotal = sum(
                self.products.get(l[2]["product_id"], {}).get("list_price", 0.0) * l[2]["product_uom_qty"]
                for l in vals.get("order_line", [])
                if isinstance(l, list) and l[0] == 0
            )
            self.orders[order_id] = {
                "id": order_id,
                "partner_id": (vals["partner_id"], "Test Customer"),
                "state": "draft",
                "amount_total": subtotal,
                "order_line": line_ids,
                "client_order_ref": vals.get("client_order_ref", ""),
            }
            created_ids.append(order_id)
        return created_ids


def _fake_llm_response() -> LLMResponse:
    return LLMResponse(tool_calls=[LLMToolCall(name="sales.order.create", arguments=ARGS)], text="")


def _build_runtime(tmp_path: Path, odoo_client: FakeOdooClient) -> tuple[AgentRuntime, ToolGateway]:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    gateway = ToolGateway(db_path=db_path)
    runtime = AgentRuntime(
        llm_client=FakeLLMClient(responses=[_fake_llm_response()]),
        gateway=gateway,
        user_id=USER,
        tenant_id=TENANT,
        odoo_client_factory=lambda u, t: odoo_client,
    )
    return runtime, gateway


def test_golden_path_full_mutation_lifecycle(tmp_path: Path) -> None:
    odoo = FakeOdooClient()
    runtime, gateway = _build_runtime(tmp_path, odoo)

    result = runtime.process("create sales order for customer 42 product 7 qty 2")
    assert result.outcome == "tool_call"
    gw = result.gateway_result
    assert gw is not None
    assert gw.status == "confirmation_required"
    assert gw.requires_confirmation is True
    assert gw.proposal is not None
    assert gw.proposal["tool_name"] == "sales.order.create"
    assert gw.proposal["arguments"] == ARGS
    assert gw.audit_id is not None

    proposal_id = gw.proposal["proposal_id"]
    confirmed = runtime.confirm(proposal_id)
    assert confirmed.outcome == "confirmed_execution"
    cgw = confirmed.gateway_result
    assert cgw is not None
    assert cgw.status == "accepted"
    assert cgw.result is not None
    assert cgw.result["verified"] is True
    assert len(odoo.orders) > 0
    assert len(odoo.create_calls) == 1

    audit = AuditStore(db_path=tmp_path / "poc.db")
    audit_rows = audit.list()
    completed_rows = [row for row in audit_rows if row["result_status"] == "accepted"]
    assert completed_rows
    assert completed_rows[-1]["idempotency_key"] == cgw.idempotency_key
    chain = audit.verify_chain()
    assert chain["valid"] is True

    payload_str = json.dumps(cgw.result, default=str).lower()
    for secret in ["api_key", "password", "bearer", "token"]:
        assert secret not in payload_str


def test_golden_path_read_only_request(tmp_path: Path) -> None:
    odoo = FakeOdooClient()
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    gateway = ToolGateway(db_path=db_path)
    runtime = AgentRuntime(
        llm_client=FakeLLMClient(responses=[LLMResponse(
            tool_calls=[LLMToolCall(name="customer.search", arguments={"query": "Test"})],
            text="",
        )]),
        gateway=gateway,
        user_id=USER,
        tenant_id=TENANT,
        odoo_client_factory=lambda u, t: odoo,
    )
    result = runtime.process("search customer Test")
    assert result.outcome == "tool_call"
    gw = result.gateway_result
    assert gw is not None
    assert gw.status == "accepted"
    assert gw.requires_confirmation is False
    assert gw.audit_id is not None
    audit = AuditStore(db_path=db_path)
    assert audit.verify_chain()["valid"] is True


def test_golden_path_duplicate_request_replays_not_recreates(tmp_path: Path) -> None:
    odoo = FakeOdooClient()
    runtime, gateway = _build_runtime(tmp_path, odoo)

    result = runtime.process("create sales order customer 42 product 7 qty 2")
    gw = result.gateway_result
    assert gw is not None and gw.proposal is not None
    confirmed = runtime.confirm(gw.proposal["proposal_id"])
    cgw = confirmed.gateway_result
    assert cgw is not None and cgw.status == "accepted"
    first_key = cgw.idempotency_key

    runtime2 = AgentRuntime(
        llm_client=FakeLLMClient(responses=[_fake_llm_response()]),
        gateway=gateway,
        user_id=USER,
        tenant_id=TENANT,
        odoo_client_factory=lambda u, t: odoo,
    )
    replay = runtime2.process("create sales order customer 42 product 7 qty 2")
    rgw = replay.gateway_result
    assert rgw is not None
    assert rgw.status == "replay"
    assert len(odoo.create_calls) == 1
    assert rgw.idempotency_key == first_key

