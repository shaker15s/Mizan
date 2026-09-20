"""Tests for Proposal Versioning (Phase 4).

Every edit increments the version and invalidates prior approval.
"""

from __future__ import annotations

from poc.confirmation import ConfirmationStore
from poc.tool_contracts import get_registry
from poc.authz import PolicyEngine
from poc.db.init import DEFAULT_DB_PATH
import poc.db.init as db_init


def _fresh_store(tmp_path):
    db_path = tmp_path / "test.db"
    db_init.initialize(db_path)
    return ConfirmationStore(
        db_path=db_path,
        registry=get_registry(),
        policy_engine=PolicyEngine(),
        expiry_seconds=300,
    )


def test_new_proposal_is_version_1(tmp_path):
    store = _fresh_store(tmp_path)
    created = store.create_proposal(
        tool_name="sales.order.create",
        tool_version="1.0.0",
        arguments={"customer_id": 42, "lines": [{"product_id": 7, "quantity": 5}]},
        user_id="sales_user@test",
        tenant_id="poc_tenant_001",
    )
    assert created.proposal is not None
    assert created.proposal.proposal_version == 1
    assert created.proposal.superseded_by is None


def test_successor_bumps_version_and_supersedes(tmp_path):
    store = _fresh_store(tmp_path)
    user, tenant = "sales_user@test", "poc_tenant_001"
    created = store.create_proposal(
        tool_name="sales.order.create",
        tool_version="1.0.0",
        arguments={"customer_id": 42, "lines": [{"product_id": 7, "quantity": 5}]},
        user_id=user,
        tenant_id=tenant,
    )
    pid = created.proposal.proposal_id
    successor = store.create_successor_proposal(
        predecessor_id=pid,
        new_arguments={"customer_id": 42, "lines": [{"product_id": 7, "quantity": 15}]},
        user_id=user,
        tenant_id=tenant,
    )
    assert successor.proposal is not None
    assert successor.proposal.proposal_id != pid
    assert successor.proposal.proposal_version == 2
    # Predecessor should be failed (superseded).
    predecessor = store.get_proposal(pid)
    assert predecessor is not None
    assert predecessor.state == "failed"
    assert predecessor.superseded_by == successor.proposal.proposal_id
    # Successor carries the new arguments.
    assert successor.proposal.arguments["lines"][0]["quantity"] == 15


def test_successor_rejects_wrong_user(tmp_path):
    store = _fresh_store(tmp_path)
    created = store.create_proposal(
        tool_name="sales.order.create",
        tool_version="1.0.0",
        arguments={"customer_id": 42, "lines": [{"product_id": 7, "quantity": 5}]},
        user_id="sales_user@test",
        tenant_id="poc_tenant_001",
    )
    bad = store.create_successor_proposal(
        predecessor_id=created.proposal.proposal_id,
        new_arguments={"customer_id": 42, "lines": [{"product_id": 7, "quantity": 15}]},
        user_id="other@test",
        tenant_id="poc_tenant_001",
    )
    assert bad.status == "identity_mismatch"
    assert bad.proposal is None


def test_amend_cannot_use_stale_proposal_after_supersede(tmp_path):
    """Once V1 is superseded, approving V1 must fail."""
    store = _fresh_store(tmp_path)
    user, tenant = "sales_user@test", "poc_tenant_001"
    created = store.create_proposal(
        tool_name="sales.order.create",
        tool_version="1.0.0",
        arguments={"customer_id": 42, "lines": [{"product_id": 7, "quantity": 5}]},
        user_id=user,
        tenant_id=tenant,
    )
    v1_id = created.proposal.proposal_id
    successor = store.create_successor_proposal(
        predecessor_id=v1_id,
        new_arguments={"customer_id": 42, "lines": [{"product_id": 7, "quantity": 15}]},
        user_id=user,
        tenant_id=tenant,
    )
    assert successor.proposal is not None
    # Approving V1 after it was superseded should fail (state is failed, not proposed).
    approval = store.approve(v1_id, user, tenant)
    assert approval.status == "replay"
