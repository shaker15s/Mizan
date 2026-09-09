"""Regression tests for Phase-2 read-tool execution (B1)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.llm_client import FakeLLMClient, LLMResponse, LLMToolCall
from poc.odoo_client import OdooTimeoutError

TENANT = "poc_tenant_001"
USER = "sales_user@test"


class ReadableOdoo:
    """Minimal fake with search_read/read for the read path."""

    def __init__(self) -> None:
        self.partners = [
            {"id": 42, "name": "Acme Corporation", "email": "a@acme.com", "phone": "0100"},
            {"id": 43, "name": "Acme Retail", "email": None, "phone": None},
        ]
        self.products = [{"id": 7, "name": "Desk", "list_price": 50.0, "qty_available": 10}]
        self.search_calls = 0

    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int | None = None) -> list[dict[str, Any]]:
        self.search_calls += 1
        query = next(
            (t[2] for t in domain if isinstance(t, (list, tuple)) and len(t) == 3 and t[0] == "name"),
            "",
        )
        source = {"res.partner": self.partners, "product.product": self.products}.get(model, [])
        matches = [dict(record) for record in source if query.lower() in str(record.get("name", "")).lower()]
        return matches[:limit] if limit else matches

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        if model == "res.partner":
            return [dict(record) for record in self.partners if record["id"] in ids]
        if model == "sale.order":
            return [
                {"id": 100, "name": "S00100", "partner_id": [42, "Acme Corporation"], "state": "draft", "amount_total": 100.0}
                for record_id in [100] if record_id in ids
            ]
        return []

    def create(self, model: str, vals_list: list[dict[str, Any]]) -> list[int]:
        raise AssertionError("reads must never create")


def _runtime(tmp_path: Path, odoo: ReadableOdoo, call: LLMToolCall, user_id: str = USER) -> AgentRuntime:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    return AgentRuntime(
        llm_client=FakeLLMClient(responses=[LLMResponse(tool_calls=[call])]),
        gateway=ToolGateway(db_path=db_path),
        user_id=user_id,
        tenant_id=TENANT,
        odoo_client_factory=lambda u, t: odoo,
    )


def _call(name: str, args: dict) -> LLMToolCall:
    return LLMToolCall(name=name, arguments=args, call_id=str(uuid.uuid4()))


def test_customer_search_executes_against_erp(tmp_path: Path):
    odoo = ReadableOdoo()
    rt = _runtime(tmp_path, odoo, _call("customer.search", {"query": "Acme"}))
    result = rt.process("ابحث عن Acme")
    assert odoo.search_calls == 1
    assert result.gateway_result.status == "accepted"
    assert result.gateway_result.result["count"] == 2
    assert result.gateway_result.result["customers"][0]["name"] == "Acme Corporation"
    assert result.gateway_result.audit_id is not None
    assert "لقيت 2 عميل" in result.response_ar


def test_product_search_executes_with_limit_cap(tmp_path: Path):
    odoo = ReadableOdoo()
    rt = _runtime(tmp_path, odoo, _call("product.search", {"query": "desk"}))
    result = rt.process("دوّر على Desk")
    assert odoo.search_calls == 1
    assert result.gateway_result.result["products"][0]["list_price"] == 50.0


def test_read_without_factory_stays_ready_for_execution(tmp_path: Path):
    """Back-compat: gateways/tests without a client keep the accepted-only shape."""
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    rt = AgentRuntime(
        llm_client=FakeLLMClient(responses=[LLMResponse(tool_calls=[_call("customer.search", {"query": "x"})])]),
        gateway=ToolGateway(db_path=db_path),
        user_id=USER,
        tenant_id=TENANT,
    )
    result = rt.process("search")
    assert result.gateway_result.status == "accepted"
    assert result.gateway_result.result is None


def test_read_denied_user_never_touches_erp(tmp_path: Path):
    odoo = ReadableOdoo()
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    rt = AgentRuntime(
        llm_client=FakeLLMClient(responses=[LLMResponse(tool_calls=[_call("customer.search", {"query": "x"})])]),
        gateway=ToolGateway(db_path=db_path),
        user_id="no_access_user@test",
        tenant_id=TENANT,
        odoo_client_factory=lambda u, t: odoo,
    )
    result = rt.process("search")
    assert result.gateway_result.status == "denied"
    assert odoo.search_calls == 0, "denied reads must not reach the ERP"


def test_read_timeout_surfaces_as_retryable_error(tmp_path: Path):
    class TimeoutOdoo(ReadableOdoo):
        def search_read(self, model, domain, fields, limit=None):
            raise OdooTimeoutError("timeout", "timed out")

    rt = _runtime(tmp_path, TimeoutOdoo(), _call("customer.search", {"query": "x"}))
    result = rt.process("search")
    assert result.gateway_result.status == "erp_error"
    assert result.structured_error.code == "ERP_CONNECTION_ERROR"
    assert result.structured_error.retryable is True


def test_customer_get_not_found_is_entity_not_found(tmp_path: Path):
    rt = _runtime(tmp_path, ReadableOdoo(), _call("customer.get", {"customer_id": 999}))
    result = rt.process("جيب بيانات العميل 999")
    assert result.gateway_result.status == "erp_error"
    assert result.gateway_result.structured_error.code == "ENTITY_NOT_FOUND"
    assert result.gateway_result.structured_error.retryable is False
