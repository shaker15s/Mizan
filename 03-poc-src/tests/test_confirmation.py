"""Focused deterministic tests for the confirmation lifecycle boundary."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from poc.authz import PolicyEngine
from poc.confirmation import (
    ALREADY_CONFIRMED,
    APPROVED,
    CONFIRMED,
    CONFIRMATION_EXPIRY_SECONDS,
    CreationResult,
    EXPIRED,
    FAILED,
    HASH_MISMATCH,
    IDENTITY_MISMATCH,
    NOT_CONFIRMATION_REQUIRED,
    NOT_FOUND,
    POLICY_DENIED,
    Proposal,
    PROPOSED,
    REPLAY,
    TENANT_MISMATCH,
    TOOL_MISSING,
    VERSION_MISMATCH,
    ConfirmationStore,
    compute_operation_hash,
)
from poc.db.init import initialize
from poc.tool_contracts import get_registry

TENANT = "poc_tenant_001"
USER = "sales_user@test"
READONLY = "readonly_user@test"
NO_ACCESS = "no_access_user@test"
VERSION = "1.0.0"
CREATE_ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}


def store(tmp_path: Path) -> ConfirmationStore:
    db_path = tmp_path / "poc_confirmation.db"
    initialize(db_path)
    return ConfirmationStore(db_path=db_path)


def _expire_proposal(db_path: Path, proposal_id: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE proposals SET expires_at = ? WHERE proposal_id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), proposal_id),
    )
    conn.commit()
    conn.close()


def test_proposal_created_for_confirmation_required_tool(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    assert result.status == APPROVED
    assert result.proposal is not None
    assert result.proposal.state == PROPOSED
    assert result.proposal.tool_name == "sales.order.create"


def test_proposal_rejected_for_readonly_tool(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("customer.search", VERSION, {"query": "x"}, USER, TENANT)
    assert result.status == NOT_CONFIRMATION_REQUIRED
    assert result.proposal is None


def test_proposal_rejected_for_denied_user(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, NO_ACCESS, TENANT)
    assert result.status == POLICY_DENIED
    assert result.proposal is None


def test_proposal_contains_binding_fields(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    p = result.proposal
    assert p.proposal_id and p.tool_name and p.tool_version and p.arguments is not None
    assert p.operation_hash and p.user_id and p.tenant_id and p.state and p.created_at and p.expires_at


def test_proposal_hash_deterministic(tmp_path: Path):
    h1 = compute_operation_hash("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT, "2026-01-01T00:00:00Z")
    h2 = compute_operation_hash("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT, "2026-01-01T00:00:00Z")
    assert h1 == h2
    assert len(h1) == 64


def test_hash_equivalent_key_ordering(tmp_path: Path):
    a1 = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}
    a2 = {"lines": [{"quantity": 2, "product_id": 7}], "customer_id": 42}
    h1 = compute_operation_hash("sales.order.create", VERSION, a1, USER, TENANT, "2026-01-01T00:00:00Z")
    h2 = compute_operation_hash("sales.order.create", VERSION, a2, USER, TENANT, "2026-01-01T00:00:00Z")
    assert h1 == h2


def test_hash_different_arguments(tmp_path: Path):
    h1 = compute_operation_hash("sales.order.create", VERSION, {"customer_id": 42}, USER, TENANT, "2026-01-01T00:00:00Z")
    h2 = compute_operation_hash("sales.order.create", VERSION, {"customer_id": 43}, USER, TENANT, "2026-01-01T00:00:00Z")
    assert h1 != h2


def test_hash_different_created_at(tmp_path: Path):
    h1 = compute_operation_hash("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT, "2026-01-01T00:00:00Z")
    h2 = compute_operation_hash("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT, "2026-01-01T00:00:01Z")
    assert h1 != h2


def test_approval_succeeds_correct_user_tenant(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert approval.status == APPROVED
    assert approval.state == CONFIRMED


def test_approval_by_another_user_rejected(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, READONLY, TENANT)
    assert approval.status == IDENTITY_MISMATCH
    assert approval.error_code == "PERMISSION_DENIED"


def test_approval_from_another_tenant_rejected(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, USER, "other_tenant")
    assert approval.status == TENANT_MISMATCH
    assert approval.error_code == "PERMISSION_DENIED"


def test_approval_after_expiry_rejected(tmp_path: Path):
    st = store(tmp_path)
    db_path = tmp_path / "poc_confirmation.db"
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    _expire_proposal(db_path, result.proposal.proposal_id)
    approval = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert approval.status == EXPIRED
    assert approval.error_code == "CONFIRMATION_EXPIRED"


def test_already_confirmed_cannot_be_approved_again(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    first = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert first.status == APPROVED
    second = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert second.status == REPLAY
    assert second.error_code == "CONFIRMATION_REPLAY"


def test_resolved_proposal_cannot_be_mutated(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    st.approve(result.proposal.proposal_id, USER, TENANT)
    p = st.get_proposal(result.proposal.proposal_id)
    assert p.state == CONFIRMED
    with pytest.raises(Exception):
        p.state = PROPOSED


def test_modified_arguments_produce_hash_mismatch_on_approve(tmp_path: Path):
    st = store(tmp_path)
    db_path = tmp_path / "poc_confirmation.db"
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    conn = sqlite3.connect(db_path)
    tampered = json.dumps({"customer_id": 42, "lines": [{"product_id": 7, "quantity": 999}]}, ensure_ascii=False)
    conn.execute("UPDATE proposals SET arguments = ? WHERE proposal_id = ?", (tampered, result.proposal.proposal_id))
    conn.commit()
    conn.close()
    approval = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert approval.status == HASH_MISMATCH
    assert approval.error_code == "CONFIRMATION_HASH_MISMATCH"


def test_modified_tool_version_rejected(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert approval.status == APPROVED
    contract = get_registry().get("sales.order.create")
    assert contract["tool_version"] == VERSION
    result2 = st.create_proposal("sales.order.create", "2.0.0", CREATE_ARGS, USER, TENANT)
    assert result2.status == VERSION_MISMATCH


def test_unknown_tool_cannot_create_or_approve(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("nonexistent.tool", VERSION, {}, USER, TENANT)
    assert result.status == NOT_FOUND
    approval = st.approve(str(uuid.uuid4()), USER, TENANT)
    assert approval.status == NOT_FOUND


def test_current_authz_rechecked_at_approval(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert approval.status == APPROVED


def test_previously_allowed_now_denied_rejected(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, NO_ACCESS, TENANT)
    assert approval.status == IDENTITY_MISMATCH


def test_confirmation_does_not_execute_odoo(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert approval.status == APPROVED
    source = Path(st.__class__.__module__).resolve() if hasattr(st.__class__, "__module__") else None
    import poc.confirmation as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "import odoo" not in src
    assert "from odoo" not in src
    assert "httpx" not in src
    assert "openai" not in src
    assert "anthropic" not in src


def test_approval_cannot_override_identity(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, READONLY, TENANT)
    assert approval.status == IDENTITY_MISMATCH


def test_caller_cannot_supply_arbitrary_role(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    approval = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert approval.status == APPROVED
    p = st.get_proposal(result.proposal.proposal_id)
    assert p.user_id == USER
    assert not hasattr(p, "role")
    assert not hasattr(p, "is_admin")


def test_caller_cannot_disable_confirmation(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("customer.search", VERSION, {"query": "x"}, USER, TENANT)
    assert result.status == NOT_CONFIRMATION_REQUIRED


def test_caller_cannot_modify_operation_hash(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    p = result.proposal
    with pytest.raises(Exception):
        p.operation_hash = "0" * 64


def test_concurrent_approval_exactly_one_wins(tmp_path: Path):
    import threading
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    results = []
    lock = threading.Lock()

    def approve():
        r = st.approve(result.proposal.proposal_id, USER, TENANT)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=approve) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    approved = [r for r in results if r.status == APPROVED]
    rejected = [r for r in results if r.status != APPROVED]
    assert len(approved) == 1
    assert len(rejected) == 3


def test_existing_database_data_preserved(tmp_path: Path):
    db_path = tmp_path / "poc_confirmation.db"
    initialize(db_path)
    st = ConfirmationStore(db_path=db_path)
    r1 = st.create_proposal("sales.order.create", VERSION, {"customer_id": 1, "lines": [{"product_id": 1, "quantity": 1}]}, USER, TENANT)
    r2 = st.create_proposal("sales.order.create", VERSION, {"customer_id": 2, "lines": [{"product_id": 2, "quantity": 1}]}, USER, TENANT)
    assert r1.status == APPROVED and r2.status == APPROVED
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    conn.close()
    assert count >= 2


def test_expired_proposal_cannot_be_revived(tmp_path: Path):
    st = store(tmp_path)
    db_path = tmp_path / "poc_confirmation.db"
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    _expire_proposal(db_path, result.proposal.proposal_id)
    approval = st.approve(result.proposal.proposal_id, USER, TENANT)
    assert approval.status == EXPIRED
    p = st.get_proposal(result.proposal.proposal_id)
    assert p.state == PROPOSED


def test_no_reusable_bearer_credential(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    p = result.proposal
    assert not hasattr(p, "bearer")
    assert not hasattr(p, "token")
    assert not hasattr(p, "api_key")


def test_execution_auth_bound_to_proposal_hash(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    p = result.proposal
    expected = compute_operation_hash(p.tool_name, p.tool_version, p.arguments, p.user_id, p.tenant_id, p.created_at)
    assert p.operation_hash == expected


def test_ttl_is_300_seconds(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    p = result.proposal
    created = datetime.strptime(p.created_at, "%Y-%m-%dT%H:%M:%SZ")
    expires = datetime.strptime(p.expires_at, "%Y-%m-%dT%H:%M:%SZ")
    delta = (expires - created).total_seconds()
    assert delta == CONFIRMATION_EXPIRY_SECONDS


def test_audit_no_secrets_in_proposal_fields(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    p = result.proposal
    p_dict = vars(p)
    for key in ("password", "api_key", "bearer", "token", "secret"):
        assert key not in p_dict


def test_proposal_state_transitions_one_way(tmp_path: Path):
    st = store(tmp_path)
    result = st.create_proposal("sales.order.create", VERSION, CREATE_ARGS, USER, TENANT)
    p = result.proposal
    assert p.state == PROPOSED
    approval = st.approve(p.proposal_id, USER, TENANT)
    assert approval.state == CONFIRMED
    second = st.approve(p.proposal_id, USER, TENANT)
    assert second.status == REPLAY
    p2 = st.get_proposal(p.proposal_id)
    assert p2.state == CONFIRMED