"""Phase 5+8b — lease is acquired during confirm_and_execute and protects against
concurrent duplicate execution. Evidence events are emitted for every stage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.execution.lease import LeaseStore, LEASE_COMPLETED
from poc.evidence import EvidenceStore
from poc.idempotency import compute_idempotency_key


LINE_ID = 9001


class FakeOdooClient:
    """Verifier-shaped fake: returns fully-populated order + line records so
    verify_sales_order_creation succeeds deterministically."""

    def __init__(self, product_id: int = 7, quantity: float = 2, order_id: int = 1):
        self.product_id = product_id
        self.quantity = quantity
        self.order_id = order_id
        self.create_calls = 0
        self.read_calls = 0

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        self.read_calls += 1
        if model == "product.product":
            return [{"id": self.product_id, "list_price": 50.0} for i in ids if i == self.product_id]
        if model == "sale.order.line":
            return [
                {
                    "id": LINE_ID,
                    "product_id": (self.product_id, "Test Product"),
                    "product_uom_qty": self.quantity,
                }
                for i in ids if i == LINE_ID
            ]
        # sale.order
        if ids and ids[0] == self.order_id and self.create_calls > 0:
            return [{
                "id": self.order_id,
                "partner_id": (getattr(self, "_partner_id", 42), "Test Customer"),
                "state": "draft",
                "amount_total": 100.0,
                "amount_untaxed": 100.0,
                "order_line": [LINE_ID],
                "client_order_ref": getattr(self, "_ref", None),
            }]
        return []

    def create(self, model: str, vals_list: list[dict[str, Any]]) -> list[int]:
        self.create_calls += 1
        assert model == "sale.order"
        # Capture idempotency key into the fake record so the read-back sees it.
        self._ref = vals_list[0].get("client_order_ref")
        self._partner_id = vals_list[0].get("partner_id")
        return [self.order_id]


def _propose_and_confirm(gateway: ToolGateway, odoo: FakeOdooClient, quantity: float = 2):
    args = {"customer_id": 42, "lines": [{"product_id": odoo.product_id, "quantity": quantity}]}
    res = gateway.handle_request({
        "request_id": "r1", "user_id": "sales_user@test", "tenant_id": "poc_tenant_001",
        "tool_name": "sales.order.create", "tool_version": "1.0.0", "arguments": args,
    })
    assert res.status == "confirmation_required", f"unexpected: {res.status} {res.error_code} {res.reason}"
    proposal = res.proposal
    assert proposal is not None
    confirmed = gateway.confirm_and_execute(
        proposal["proposal_id"], "sales_user@test", "poc_tenant_001", odoo
    )
    return confirmed, proposal, args


def test_confirm_acquires_completes_lease_and_emits_evidence(tmp_path: Path):
    db_path = tmp_path / "t.db"
    initialize(db_path)
    gateway = ToolGateway(db_path=db_path)
    odoo = FakeOdooClient()
    result, proposal, args = _propose_and_confirm(gateway, odoo)
    assert result.status == "accepted", f"{result.status} {result.error_code} {result.reason}"
    assert result.result is not None
    assert result.result["order_id"] == odoo.order_id

    lease_store = LeaseStore(db_path)
    rows = list(lease_store._connect().execute("SELECT * FROM execution_leases"))
    assert len(rows) == 1
    assert rows[0]["state"] == LEASE_COMPLETED

    evidence = EvidenceStore(db_path)
    assert evidence.verify_chain()["valid"] is True
    types = {ev["event_type"] for ev in evidence.for_execution(result.execution_id)}
    for expected in ("lease", "approval", "tool_call", "erp_request", "erp_response",
                     "verification", "user_visible_claim"):
        assert expected in types, f"missing {expected} evidence: {types}"
    # state_transition is emitted via ctx.apply inside execute_verified.
    assert "state_transition" in types


def test_preexisting_active_lease_blocks_duplicate_execution(tmp_path: Path):
    db_path = tmp_path / "t.db"
    initialize(db_path)
    gateway = ToolGateway(db_path=db_path)
    odoo = FakeOdooClient(order_id=88)
    args = {"customer_id": 42, "lines": [{"product_id": odoo.product_id, "quantity": 2}]}
    res = gateway.handle_request({
        "request_id": "r1", "user_id": "sales_user@test", "tenant_id": "poc_tenant_001",
        "tool_name": "sales.order.create", "tool_version": "1.0.0", "arguments": args,
    })
    assert res.status == "confirmation_required"
    pid = res.proposal["proposal_id"]

    # Pre-acquire a lease on the same idempotency key (simulating a concurrent
    # worker that beat us to execution).
    key = compute_idempotency_key("poc_tenant_001", "sales_user@test", "sales.order.create", args)
    gateway.lease_store.acquire(
        execution_id="other-execution-id", idempotency_key=key,
        arguments=args, owner="intruder-node",
    )

    dup = gateway.confirm_and_execute(pid, "sales_user@test", "poc_tenant_001", odoo)
    assert dup.status == "in_progress"
    assert dup.error_code == "IDEMPOTENCY_IN_PROGRESS"
    # Odoo create must NOT have been called — lease block prevents double-write.
    assert odoo.create_calls == 0
