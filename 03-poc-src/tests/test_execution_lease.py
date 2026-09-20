"""Tests for execution leases (Phase 5)."""

from __future__ import annotations

import pytest

from poc.execution import LeaseAlreadyHeldError, LeaseNotOwnedError, LeaseState, LeaseStore


def _fresh_store(tmp_path):
    return LeaseStore(db_path=tmp_path / "leases.db", ttl_seconds=30)


def test_acquire_and_get(tmp_path):
    store = _fresh_store(tmp_path)
    lease = store.acquire(
        execution_id="e1",
        idempotency_key="k" + "a" * 31,
        arguments={"x": 1},
        owner="node-1",
    )
    assert lease.state == LeaseState("active")
    got = store.get(lease.lease_id)
    assert got is not None
    assert got.execution_id == "e1"


def test_active_for_key_returns_current(tmp_path):
    store = _fresh_store(tmp_path)
    key = "k" + "b" * 31
    store.acquire(execution_id="e1", idempotency_key=key, arguments={"x": 1}, owner="node-1")
    active = store.active_for_key(key)
    assert active is not None
    assert active.execution_id == "e1"


def test_second_active_acquire_rejected(tmp_path):
    store = _fresh_store(tmp_path)
    key = "k" + "c" * 31
    store.acquire(execution_id="e1", idempotency_key=key, arguments={"x": 1}, owner="node-1")
    with pytest.raises(LeaseAlreadyHeldError):
        store.acquire(execution_id="e2", idempotency_key=key, arguments={"x": 1}, owner="node-2")


def test_heartbeat_renews_expiry(tmp_path):
    store = _fresh_store(tmp_path)
    key = "k" + "d" * 31
    lease = store.acquire(execution_id="e1", idempotency_key=key, arguments={"x": 1}, owner="node-1")
    import time
    time.sleep(0)
    renewed = store.heartbeat(lease.lease_id, owner="node-1")
    assert renewed.heartbeat_at >= lease.heartbeat_at
    assert renewed.expires_at >= lease.expires_at


def test_heartbeat_wrong_owner_rejected(tmp_path):
    store = _fresh_store(tmp_path)
    key = "k" + "e" * 31
    lease = store.acquire(execution_id="e1", idempotency_key=key, arguments={"x": 1}, owner="node-1")
    with pytest.raises(LeaseNotOwnedError):
        store.heartbeat(lease.lease_id, owner="node-2")


def test_complete_transitions_state(tmp_path):
    store = _fresh_store(tmp_path)
    key = "k" + "f" * 31
    lease = store.acquire(execution_id="e1", idempotency_key=key, arguments={"x": 1}, owner="node-1")
    done = store.complete(lease.lease_id, owner="node-1")
    assert done.state == LeaseState("completed")


def test_release_allows_new_lease(tmp_path):
    store = _fresh_store(tmp_path)
    key = "k" + "g" * 31
    lease = store.acquire(execution_id="e1", idempotency_key=key, arguments={"x": 1}, owner="node-1")
    store.release(lease.lease_id, owner="node-1")
    # second acquire succeeds now
    second = store.acquire(execution_id="e2", idempotency_key=key, arguments={"x": 1}, owner="node-1")
    assert second.execution_id == "e2"


def test_expired_lease_is_superseded(tmp_path):
    from datetime import datetime, timedelta, timezone

    store = _fresh_store(tmp_path)
    key = "k" + "h" * 31
    fixed_now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def clock_at(t):
        return lambda: t

    store.clock = clock_at(fixed_now)
    store.ttl_seconds = 10
    lease = store.acquire(execution_id="e1", idempotency_key=key, arguments={"x": 1}, owner="node-1")

    # advance clock past TTL
    store.clock = clock_at(fixed_now + timedelta(seconds=20))
    # expire_stale should mark it EXPIRED
    count = store.expire_stale()
    assert count == 1
    # Now a new lease can be acquired.
    new_lease = store.acquire(execution_id="e2", idempotency_key=key, arguments={"x": 1}, owner="node-2")
    assert new_lease.execution_id == "e2"
