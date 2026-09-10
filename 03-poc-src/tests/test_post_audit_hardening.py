"""Regression tests for the post-audit hardening pass (final audit findings F-xx).

Covers the hypothesis-critical fixes that the phase-1/phase-2 regression files
do not: false-success presentation paths (F-04/F-05), mutation provenance on
verification failure (F-07), proposal linkage in audit rows (F-14), expired
proposal reservation release (F-09/expiry), and the full two-write lifecycle
on one gateway instance (F-12).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from poc.agent_runtime import AgentResult
from poc.audit_store import AuditStore
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.main import _build_payload

TENANT = "poc_tenant_001"
USER = "sales_user@test"
VERSION = "1.0.0"
ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}


class EchoOdoo:
    """Fake that echoes the actual create payload back on read."""

    def __init__(self) -> None:
        self.created_refs: dict[int, str] = {}
        self.created_partner: dict[int, int] = {}

    def read(self, model, ids, fields):
        if model == "product.product":
            return [{"id": i, "list_price": 50.0} for i in ids]
        if model == "sale.order":
            return [
                {"id": i, "partner_id": (self.created_partner.get(i), "T"), "state": "draft",
                 "amount_total": 100.0, "amount_untaxed": 100.0, "order_line": [11],
                 "client_order_ref": self.created_refs.get(i, "")}
                for i in ids if i in self.created_refs
            ]
        if model == "sale.order.line":
            return [{"id": i, "product_id": (7, "P"), "product_uom_qty": 2} for i in ids]
        return []

    def create(self, model, vals):
        created = []
        for vals_item in vals:
            order_id = 900 + len(self.created_refs) + 1
            self.created_refs[order_id] = vals_item.get("client_order_ref", "")
            self.created_partner[order_id] = vals_item.get("partner_id")
            created.append(order_id)
        return created


def _request(request_id: str, arguments: dict | None = None) -> dict:
    return {
        "request_id": request_id,
        "user_id": USER,
        "tenant_id": TENANT,
        "tool_name": "sales.order.create",
        "tool_version": VERSION,
        "arguments": arguments or ARGS,
    }


def _gateway(tmp_path: Path) -> ToolGateway:
    db_path = tmp_path / "poc.db"
    initialize(db_path)
    return ToolGateway(db_path=db_path)


def _expire_proposal(db_path: Path, proposal_id: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE proposals SET expires_at = ? WHERE proposal_id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), proposal_id),
    )
    conn.commit()
    conn.close()


def test_declined_confirmation_allows_identical_retry(tmp_path: Path):
    """F-09: decline must release the reservation so the same order can be re-proposed."""
    gateway = _gateway(tmp_path)
    first = gateway.handle_request(_request("r1"))
    assert first.status == "confirmation_required"
    gateway.record_confirmation_denial(first.proposal["proposal_id"], USER, TENANT)
    retry = gateway.handle_request(_request("r2"))
    assert retry.status == "confirmation_required", "identical retry after decline must re-propose"


def test_expired_confirmation_releases_reservation_for_retry(tmp_path: Path):
    """F-09: an expired proposal can never execute; its reservation must not deadlock the key."""
    gateway = _gateway(tmp_path)
    first = gateway.handle_request(_request("r1"))
    assert first.status == "confirmation_required"
    _expire_proposal(tmp_path / "poc.db", first.proposal["proposal_id"])
    outcome = gateway.confirm_and_execute(first.proposal["proposal_id"], USER, TENANT, EchoOdoo())
    assert outcome.status == "denied"
    retry = gateway.handle_request(_request("r2"))
    assert retry.status == "confirmation_required", "identical retry after expiry must re-propose"


def test_two_sequential_confirmed_writes_on_one_gateway(tmp_path: Path):
    """F-12: an interactive session reuses one gateway; the second write must execute."""
    gateway = _gateway(tmp_path)
    client = EchoOdoo()
    first_req = gateway.handle_request(_request("r1"))
    first = gateway.confirm_and_execute(first_req.proposal["proposal_id"], USER, TENANT, client)
    assert first.status == "accepted"
    second_req = gateway.handle_request(_request("r2", dict(ARGS, customer_id=43)))
    second = gateway.confirm_and_execute(second_req.proposal["proposal_id"], USER, TENANT, client)
    assert second.status == "accepted", f"second write failed: {second.error_code} {second.reason}"
    assert len(client.created_refs) == 2


def test_taxed_correct_order_passes_verification(tmp_path: Path):
    """F-06: amount_total may include Odoo-owned taxes; the untaxed reference must govern."""

    class TaxedEcho(EchoOdoo):
        def read(self, model, ids, fields):
            records = super().read(model, ids, fields)
            if model == "sale.order":
                records = [dict(record, amount_total=113.0, amount_untaxed=100.0) for record in records]
            return records

    gateway = _gateway(tmp_path)
    reserved = gateway.handle_request(_request("r1"))
    result = gateway.confirm_and_execute(reserved.proposal["proposal_id"], USER, TENANT, TaxedEcho())
    assert result.status == "accepted", f"taxed correct order must verify: {result.reason}"


def test_verification_failure_retains_created_order_id(tmp_path: Path):
    """F-07: when the order exists but fails read-back, its id stays in the audit trail."""

    class WrongLine(EchoOdoo):
        def read(self, model, ids, fields):
            records = super().read(model, ids, fields)
            if model == "sale.order.line":
                records = [dict(record, product_id=(99, "Wrong")) for record in records]
            return records

    gateway = _gateway(tmp_path)
    client = WrongLine()
    reserved = gateway.handle_request(_request("r1"))
    result = gateway.confirm_and_execute(reserved.proposal["proposal_id"], USER, TENANT, client)
    assert result.status == "error"
    created_id = next(iter(client.created_refs))
    rows = AuditStore(tmp_path / "poc.db").list(limit=100)
    verify_rows = [row for row in rows if str(row["request_id"]).startswith("verify-")]
    assert verify_rows, "execution must be audited"
    assert verify_rows[-1]["external_record_id"] == str(created_id)
    assert verify_rows[-1]["error_code"] == "VERIFICATION_FAILED"


def test_proposal_audit_row_links_proposal_and_hash(tmp_path: Path):
    """F-14: the confirmation_required audit row must carry proposal_id + operation_hash."""
    gateway = _gateway(tmp_path)
    reserved = gateway.handle_request(_request("r1"))
    assert reserved.proposal is not None
    rows = AuditStore(tmp_path / "poc.db").list(limit=10)
    row = rows[-1]
    assert row["proposal_id"] == reserved.proposal["proposal_id"]
    assert row["operation_hash"] == reserved.proposal["operation_hash"]


def test_text_only_model_response_is_not_success():
    """F-04: model prose without a tool call must never set success=true."""
    payload = _build_payload(AgentResult(outcome="text_only", response_ar="تم إنشاء الطلب بنجاح", gateway_result=None))
    assert payload["success"] is not True
    assert payload["result"] is None


def test_accepted_read_without_result_is_not_success(tmp_path: Path):
    """F-05: accepted-but-never-executed reads must not report success."""
    gateway = _gateway(tmp_path)
    read_result = gateway.handle_request({
        "request_id": "r1", "user_id": USER, "tenant_id": TENANT,
        "tool_name": "customer.search", "tool_version": VERSION, "arguments": {"query": "x"},
    })
    assert read_result.status == "accepted"
    assert read_result.result is None
    payload = _build_payload(AgentResult(outcome="tool_call", response_ar="x", gateway_result=read_result))
    assert payload["success"] is not True
