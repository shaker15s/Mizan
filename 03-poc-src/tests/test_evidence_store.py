"""Tests for the typed evidence event store (Phase 8)."""

from __future__ import annotations

from poc.evidence import EvidenceStore, EvidenceType


def test_append_and_verify_chain(tmp_path):
    from poc.db.init import initialize
    db_path = tmp_path / "ev.db"
    initialize(db_path)
    store = EvidenceStore(db_path=db_path)
    assert store.verify_chain() == {"valid": True, "events": 0}
    e1 = store.append(
        event_type=EvidenceType.INTENT,
        payload={"intent": "create_sales_order", "language": "ar-EG"},
        trace_id="t1", execution_id="e1",
    )
    e2 = store.append(
        event_type=EvidenceType.POLICY_DECISION,
        payload={"decision": "require_approval", "rule": "RULE_R2_USER_CONFIRMATION"},
        trace_id="t1", execution_id="e1", parent_event_id=e1.evidence_id,
        policy_version="2.0.0",
    )
    e3 = store.append(
        event_type=EvidenceType.VERIFICATION,
        payload={"passed": True, "order_id": 42},
        trace_id="t1", execution_id="e1", parent_event_id=e2.evidence_id,
    )
    assert store.verify_chain()["valid"] is True
    events = store.for_execution("e1")
    assert len(events) == 3
    assert events[0]["event_type"] == "intent"
    assert events[2]["event_type"] == "verification"


def test_payload_too_large_rejected(tmp_path):
    from poc.db.init import initialize
    db_path = tmp_path / "ev.db"
    initialize(db_path)
    store = EvidenceStore(db_path=db_path)
    with pytest.raises(ValueError):
        store.append(
            event_type=EvidenceType.INTENT,
            payload={"blob": "x" * (128 * 1024)},
            trace_id="t",
        )


import pytest
