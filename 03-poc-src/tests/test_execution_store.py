"""Tests for the execution state persistence layer."""

from __future__ import annotations

from poc.execution import (
    ExecutionEvent,
    ExecutionStage,
    ExecutionState,
    ExecutionStore,
    INITIAL_STATE,
)
from poc.execution.state_machine import new_execution_identity


def _fresh_state():
    trace_id, action_id, execution_id = new_execution_identity()
    s = INITIAL_STATE()
    s.trace_id = trace_id
    s.action_id = action_id
    s.execution_id = execution_id
    for ev in [
        ExecutionEvent.USER_MESSAGE_RECEIVED,
        ExecutionEvent.INTENT_PARSED,
        ExecutionEvent.PLAN_BUILT,
        ExecutionEvent.SECURITY_SCREENED,
        ExecutionEvent.POLICY_EVALUATED,
    ]:
        s = s.apply(ev)
    return s


def test_create_save_and_load_roundtrip(tmp_path):
    db_path = tmp_path / "exec.db"
    store = ExecutionStore(db_path=db_path)
    s = _fresh_state()
    store.create(s)
    store.update_identity(s.execution_id, tenant_id="t", user_id="u", tool_name="sales.order.create",
                          trace_id=s.trace_id, action_id=s.action_id)
    store.save(s)
    loaded = store.load(s.execution_id)
    assert loaded is not None
    assert loaded.stage == s.stage
    assert loaded.execution_id == s.execution_id
    assert len(loaded.history) == len(s.history)
    # Apply another event, save, reload.
    s2 = s.apply(ExecutionEvent.PROPOSAL_ISSUED)
    store.save(s2)
    loaded2 = store.load(s.execution_id)
    assert loaded2 is not None
    assert loaded2.stage == ExecutionStage.AWAITING_CONFIRMATION
    assert len(loaded2.history) == len(s2.history)


def test_list_recent_filters_by_tenant(tmp_path):
    db_path = tmp_path / "exec.db"
    store = ExecutionStore(db_path=db_path)
    for i in range(3):
        s = _fresh_state()
        store.create(s)
        store.update_identity(s.execution_id, tenant_id=f"t{i % 2}", user_id="u",
                              trace_id=s.trace_id, action_id=s.action_id)
        store.save(s)
    all_execs = store.list_recent(limit=10)
    assert len(all_execs) == 3
    t0 = store.list_recent(tenant_id="t0", limit=10)
    assert len(t0) == 2
    t1 = store.list_recent(tenant_id="t1", limit=10)
    assert len(t1) == 1
