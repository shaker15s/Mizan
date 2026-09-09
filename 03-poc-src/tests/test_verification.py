"""Focused STEP 12 post-write verification tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from poc.confirmation import ConfirmationStore
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.odoo_client import OdooAuthenticationError, OdooTimeoutError
from poc.verification import verify_sales_order_creation


TENANT = "poc_tenant_001"
USER = "sales_user@test"
VERSION = "1.0.0"
ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}
ORDER_ID = 1
LINE_ID = 11


class FakeOdooClient:
    def __init__(
        self,
        order_records: list[dict[str, Any]] | None = None,
        line_records: list[dict[str, Any]] | None = None,
        product_records: list[dict[str, Any]] | None = None,
    ):
        self.order_records = order_records or []
        self.line_records = line_records or []
        self.product_records = product_records or [
            {"id": 7, "list_price": 50.0},
        ]
        self.read_error: Exception | None = None
        self.create_error: Exception | None = None

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        if self.read_error is not None:
            raise self.read_error
        if model == "product.product":
            source = self.product_records
        elif model == "sale.order.line":
            source = self.line_records
        else:
            source = self.order_records
        return [record for record in source if record["id"] in ids]

    def create(self, model: str, vals_list: list[dict[str, Any]]) -> list[int]:
        if self.create_error is not None:
            raise self.create_error
        return [1]


def _line_records(product_id: int = 7, quantity: float = 2) -> list[dict[str, Any]]:
    return [
        {
            "id": LINE_ID,
            "product_id": (product_id, "Test Product"),
            "product_uom_qty": quantity,
        }
    ]


def _verified_record(order_id: int = 1, provenance: str = "a" * 32) -> dict[str, Any]:
    return {
        "id": order_id,
        "partner_id": (42, "Test Customer"),
        "state": "draft",
        "amount_total": 100.0,
        "order_line": [LINE_ID],
        "client_order_ref": provenance,
    }


def _confirmed_execution(tmp_path: Path):
    gateway = ToolGateway(db_path=tmp_path / "poc.db")
    initialize(tmp_path / "poc.db")
    request = {
        "request_id": "req-verification",
        "user_id": USER,
        "tenant_id": TENANT,
        "tool_name": "sales.order.create",
        "tool_version": VERSION,
        "arguments": ARGS,
    }
    reserved = gateway.handle_request(request)
    assert reserved.status == "confirmation_required"
    store = ConfirmationStore(db_path=tmp_path / "poc.db")
    proposal = store.create_proposal("sales.order.create", VERSION, ARGS, USER, TENANT)
    approval = store.approve(proposal.proposal.proposal_id, USER, TENANT)
    assert approval.status == "approved"
    return gateway, approval, proposal


def test_unit_verifier_accepts_expected_state():
    result = verify_sales_order_creation(
        FakeOdooClient([_verified_record()], _line_records()),
        1,
        42,
        ARGS["lines"],
        "a" * 32,
        expected_amount_total=100.0,
    )
    assert result.passed


def test_unit_verifier_accepts_exact_computed_total():
    result = verify_sales_order_creation(
        FakeOdooClient(
            [_verified_record()],
            _line_records(),
        ),
        1,
        42,
        ARGS["lines"],
        "a" * 32,
        expected_amount_total=100.0,
    )
    assert result.passed is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda order, lines: order.update({"partner_id": (43, "Wrong Customer")}),
        lambda order, lines: order.update({"state": "sent"}),
        lambda order, lines: order.update({"amount_total": 500.0}),
        lambda order, lines: order.update({"client_order_ref": "b" * 32}),
        lambda order, lines: lines[0].update({"product_id": (8, "Wrong Product")}),
        lambda order, lines: lines[0].update({"product_uom_qty": 3}),
    ],
    ids=[
        "wrong_customer",
        "wrong_state",
        "wrong_amount",
        "wrong_provenance",
        "wrong_product",
        "wrong_quantity",
    ],
)
def test_unit_verifier_rejects_every_state_mismatch(mutate):
    order = _verified_record()
    lines = _line_records()
    mutate(order, lines)
    result = verify_sales_order_creation(
        FakeOdooClient([order], lines),
        1,
        42,
        ARGS["lines"],
        "a" * 32,
        expected_amount_total=100.0,
    )
    assert not result.passed


def test_unit_verifier_rejects_missing_record():
    result = verify_sales_order_creation(
        FakeOdooClient([], _line_records()),
        1,
        42,
        ARGS["lines"],
        "a" * 32,
    )
    assert not result.passed


def test_successful_write_is_verified_and_audited(tmp_path: Path):
    gateway, approval, _proposal = _confirmed_execution(tmp_path)
    client = FakeOdooClient(
        [_verified_record(ORDER_ID, approval.idempotency_key)],
        _line_records(),
    )
    result = gateway.execute_verified(approval.idempotency_key, approval.execution_id, ARGS, client, tenant_id=TENANT, user_id=USER)

    assert result.status == "accepted"
    assert result.error_code is None
    assert result.structured_error is None
    assert result.result["status"] == "success"
    assert result.result["verified"] is True
    assert result.result["order_id"] == ORDER_ID
    assert result.result["customer_id"] == 42

    record = gateway.idempotency_store.get(approval.idempotency_key, TENANT, USER)
    assert record.state == "completed"
    assert record.external_record_id == str(ORDER_ID)
    audit_rows = gateway.audit_store.list(request_id="verify-" + approval.execution_id)
    assert len(audit_rows) == 1
    assert audit_rows[0]["result_status"] == "accepted"
    assert audit_rows[0]["idempotency_key"] == approval.idempotency_key
    assert audit_rows[0]["execution_id"] == approval.execution_id
    assert gateway.audit_store.verify_chain()["valid"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda order, lines: order.update({"partner_id": (43, "Wrong Customer")}),
        lambda order, lines: order.update({"state": "sent"}),
        lambda order, lines: order.update({"amount_total": 500.0}),
        lambda order, lines: order.update({"client_order_ref": "b" * 32}),
        lambda order, lines: lines[0].update({"product_id": (8, "Wrong Product")}),
        lambda order, lines: lines[0].update({"product_uom_qty": 3}),
    ],
    ids=[
        "wrong_customer",
        "wrong_state",
        "wrong_amount",
        "wrong_provenance",
        "wrong_product",
        "wrong_quantity",
    ],
)
def test_read_back_mismatch_returns_verification_failed_and_audits(tmp_path: Path, mutate):
    gateway, approval, _proposal = _confirmed_execution(tmp_path)
    order = _verified_record(ORDER_ID)
    lines = _line_records()
    mutate(order, lines)
    client = FakeOdooClient([order], lines)
    result = gateway.execute_verified(approval.idempotency_key, approval.execution_id, ARGS, client, tenant_id=TENANT, user_id=USER)

    assert result.status == "error"
    assert result.error_code == "VERIFICATION_FAILED"
    structured = result.structured_error.to_dict()["error"]
    assert structured["code"] == "VERIFICATION_FAILED"
    assert structured["retryable"] is False
    assert structured["requires_user_action"] is True
    stored = gateway.idempotency_store.get(approval.idempotency_key, TENANT, USER)
    # Post-create verification failures reconcile as ambiguous, never as a
    # replayable "completed" success (false-success fix).
    assert stored.state == "unknown"
    audit_rows = gateway.audit_store.list(request_id="verify-" + approval.execution_id)
    assert audit_rows[0]["result_status"] == "error"
    assert audit_rows[0]["error_code"] == "VERIFICATION_FAILED"


def test_read_timeout_is_retryable_erp_connection_error(tmp_path: Path):
    gateway, approval, _proposal = _confirmed_execution(tmp_path)
    client = FakeOdooClient()
    client.read_error = OdooTimeoutError("timeout", "timeout", None)
    result = gateway.execute_verified(approval.idempotency_key, approval.execution_id, ARGS, client, tenant_id=TENANT, user_id=USER)

    error = result.structured_error.to_dict()["error"]
    assert result.error_code == "ERP_CONNECTION_ERROR"
    assert error["retryable"] is True
    assert error["requires_user_action"] is False
    stored = gateway.idempotency_store.get(approval.idempotency_key, TENANT, USER)
    assert stored.state == "unknown"
    audit_rows = gateway.audit_store.list(request_id="verify-" + approval.execution_id)
    assert audit_rows[0]["error_code"] == "ERP_CONNECTION_ERROR"


def test_read_authentication_error_requires_user_action(tmp_path: Path):
    gateway, approval, _proposal = _confirmed_execution(tmp_path)
    client = FakeOdooClient()
    client.read_error = OdooAuthenticationError("auth", "invalid key", None)
    result = gateway.execute_verified(approval.idempotency_key, approval.execution_id, ARGS, client, tenant_id=TENANT, user_id=USER)

    error = result.structured_error.to_dict()["error"]
    assert result.error_code == "ERP_CONNECTION_ERROR"
    assert error["retryable"] is False
    assert error["requires_user_action"] is True


def test_create_timeout_marks_unknown_then_requires_reconciliation(tmp_path: Path):
    gateway, approval, _proposal = _confirmed_execution(tmp_path)
    client = FakeOdooClient()
    client.create_error = OdooTimeoutError("timeout", "timeout", None)
    result = gateway.execute_verified(approval.idempotency_key, approval.execution_id, ARGS, client, tenant_id=TENANT, user_id=USER)

    error = result.structured_error.to_dict()["error"]
    assert result.status == "erp_error"
    assert result.error_code == "ERP_CONNECTION_ERROR"
    assert error["retryable"] is True
    stored = gateway.idempotency_store.get(approval.idempotency_key, TENANT, USER)
    assert stored.state == "unknown"
    audit_rows = gateway.audit_store.list(request_id="verify-" + approval.execution_id)
    assert audit_rows[0]["result_status"] == "ambiguous"
    assert audit_rows[0]["error_code"] == "ERP_CONNECTION_ERROR"

    retry = gateway.handle_request(
        {
            "request_id": "req-retry",
            "user_id": USER,
            "tenant_id": TENANT,
            "tool_name": "sales.order.create",
            "tool_version": VERSION,
            "arguments": ARGS,
            "idempotency_key": approval.idempotency_key,
        }
    )
    assert retry.status == "reconciliation_required"
    structured = retry.structured_error.to_dict()["error"]
    assert structured["code"] == "AMBIGUOUS_OUTCOME"
    assert structured["retryable"] is False
    assert structured["requires_user_action"] is True


def test_verifier_ignores_argument_metadata_when_verifying(tmp_path: Path):
    gateway, approval, _proposal = _confirmed_execution(tmp_path)
    forged = {**ARGS, "verified": False}
    client = FakeOdooClient(
        [_verified_record(ORDER_ID, approval.idempotency_key)],
        _line_records(),
    )
    result = gateway.execute_verified(approval.idempotency_key, approval.execution_id, forged, client, tenant_id=TENANT, user_id=USER)
    assert result.result["verified"] is True
