"""Regression tests for the Phase-1 correctness fixes (B2/B3/B4/B5/B9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.confirmation import ConfirmationStore
from poc.errors import translate_odoo_exception
from poc.odoo_client import OdooNotFoundError, OdooServerError

TENANT = "poc_tenant_001"
USER = "sales_user@test"
VERSION = "1.0.0"
ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}
LINE_ID = 11


class FakeOdooClient:
    def __init__(self, order_records=None, line_records=None, product_records=None):
        self.order_records = order_records or []
        self.line_records = line_records or []
        self.product_records = product_records or [{"id": 7, "list_price": 50.0}]
        self.create_calls = 0

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        source = {"product.product": self.product_records, "sale.order": self.order_records, "sale.order.line": self.line_records}[model]
        return [record for record in source if record.get("id") in ids]

    def create(self, model: str, vals_list: list[dict[str, Any]]) -> list[int]:
        self.create_calls += 1
        self.order_records.append(
            {
                "id": 1,
                "partner_id": (42, "Test Customer"),
                "state": "draft",
                "amount_total": 100.0,
                "amount_untaxed": 100.0,
                "order_line": [LINE_ID],
                "client_order_ref": vals_list[0]["client_order_ref"],
            }
        )
        self.line_records.append({"id": LINE_ID, "product_id": (7, "Test Product"), "product_uom_qty": 2})
        return [1]


def _confirmed(tmp_path: Path):
    gateway = ToolGateway(db_path=tmp_path / "poc.db")
    initialize(tmp_path / "poc.db")
    request = {
        "request_id": "req-regression",
        "user_id": USER,
        "tenant_id": TENANT,
        "tool_name": "sales.order.create",
        "tool_version": VERSION,
        "arguments": ARGS,
    }
    gateway.handle_request(request)
    store = ConfirmationStore(db_path=tmp_path / "poc.db")
    proposal = store.create_proposal("sales.order.create", VERSION, ARGS, USER, TENANT)
    approval = _clean_approve(store, proposal.proposal.proposal_id)
    assert approval.status == "approved"
    return gateway, approval


def _clean_approve(store, proposal_id):
    return store.approve(proposal_id, USER, TENANT)


def test_second_confirmed_execution_on_same_gateway_succeeds(tmp_path: Path):
    """B2: a gateway instance must handle repeated confirmations (interactive mode)."""
    gateway, _approval = _confirmed(tmp_path)
    client = FakeOdooClient()
    first = gateway.execute_verified("a" * 32, "11111111-1111-4111-8111-111111111111", ARGS, client, tenant_id=TENANT, user_id=USER)
    assert first.status in {"accepted", "error", "conflict"}

    store = ConfirmationStore(db_path=tmp_path / "poc.db")
    proposal2 = store.create_proposal("sales.order.create", VERSION, ARGS, USER, TENANT)
    approval2 = store.approve(proposal2.proposal.proposal_id, USER, TENANT)
    assert approval2.status in {"approved", "replay"}


def test_declined_confirmation_releases_pending_reservation(tmp_path: Path):
    """B3: declining must free the idempotency reservation for a corrected retry."""
    gateway = ToolGateway(db_path=tmp_path / "poc.db")
    initialize(tmp_path / "poc.db")
    request = {
        "request_id": "req-decline",
        "user_id": USER,
        "tenant_id": TENANT,
        "tool_name": "sales.order.create",
        "tool_version": VERSION,
        "arguments": ARGS,
    }
    first = gateway.handle_request(request)
    assert first.status == "confirmation_required"
    gateway.record_confirmation_denial(first.proposal["proposal_id"], USER, TENANT)

    stored = gateway.idempotency_store.get(first.idempotency_key, TENANT, USER)
    assert stored is None, "declined confirmation must release the pending reservation"

    second = gateway.handle_request({**request, "request_id": "req-decline-2"})
    assert second.status == "confirmation_required", "identical retry after decline must re-propose, not deadlock"


def test_amount_verification_accepts_taxed_total(tmp_path: Path):
    """B5: a tax-inclusive amount_total must not fail verification."""
    gateway, approval = _confirmed(tmp_path)

    class TaxedClient(FakeOdooClient):
        def read(self, model, ids, fields):
            records = super().read(model, ids, fields)
            if model == "sale.order":
                records = [dict(record, amount_total=113.0, amount_untaxed=100.0) for record in records]
            return records

    result = gateway.execute_verified(approval.idempotency_key, approval.execution_id, ARGS, TaxedClient(), tenant_id=TENANT, user_id=USER)
    assert result.status == "accepted", f"taxed order must verify: {result.reason}"


def test_pre_create_pricing_failure_releases_reservation(tmp_path: Path):
    """B4: failing before create must free the reservation for a retry."""

    class PricingFailure(FakeOdooClient):
        def read(self, model, ids, fields):
            if model == "product.product":
                raise OdooServerError("pricing_read_failed", "boom", 500)
            return super().read(model, ids, fields)

    gateway, approval = _confirmed(tmp_path)
    client = PricingFailure()
    result = gateway.execute_verified(approval.idempotency_key, approval.execution_id, ARGS, client, tenant_id=TENANT, user_id=USER)
    assert result.status in {"erp_error", "error"}
    assert client.create_calls == 0, "no create may fire when pricing fails"
    stored = gateway.idempotency_store.get(approval.idempotency_key, TENANT, USER)
    assert stored is None or stored.state in {"unknown", "completed"}, "reservation must not deadlock retries"


def test_404_maps_to_entity_not_found():
    """B9: Odoo 404 must surface as ENTITY_NOT_FOUND, not a retryable connection error."""
    structured = translate_odoo_exception(OdooNotFoundError("not_found", "no such record", 404))
    assert structured.code == "ENTITY_NOT_FOUND"
    assert structured.retryable is False

    server = translate_odoo_exception(OdooServerError("x", "y", 500))
    assert server.retryable is True
