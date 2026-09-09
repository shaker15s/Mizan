"""Focused deterministic tests for the POC idempotency state machine."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from poc.idempotency import (
    ADOPTED,
    AMBIGUOUS,
    CONFLICT,
    DEFINITIVE_ERROR,
    IDEMPOTENCY_CONFLICT,
    IN_PROGRESS,
    InvalidIdempotencyRequest,
    IdempotencyStore,
    IdempotencyStoreError,
    IllegalIdempotencyTransition,
    RECONCILIATION_REQUIRED,
    RECONCILIATION_REQUIRED_ERROR,
    RESERVED,
    REPLAYED,
    compute_idempotency_key,
    compute_request_fingerprint,
)
from poc.db.init import initialize

TENANT = "poc_tenant_001"
USER = "sales_user@test"
OTHER_USER = "readonly_user@test"
TOOL = "sales.order.create"
VERSION = "1.0.0"
ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}


def make_store(tmp_path: Path) -> IdempotencyStore:
    return IdempotencyStore(tmp_path / "poc_gateway.db")


def request_binding(arguments=ARGS):
    return {
        "tool_name": TOOL,
        "tool_version": VERSION,
        "tenant_id": TENANT,
        "user_id": USER,
        "request_fingerprint": compute_request_fingerprint(
            TENANT, USER, TOOL, VERSION, arguments
        ),
    }


def reserve(store: IdempotencyStore, arguments=ARGS, **overrides):
    tenant_id = overrides.get("tenant_id", TENANT)
    user_id = overrides.get("user_id", USER)
    tool_name = overrides.get("tool_name", TOOL)
    tool_version = overrides.get("tool_version", VERSION)
    payload = request_binding(arguments)
    payload.update(overrides)
    payload["request_fingerprint"] = compute_request_fingerprint(
        tenant_id, user_id, tool_name, tool_version, arguments
    )
    if "idempotency_key" not in payload:
        payload["idempotency_key"] = compute_idempotency_key(
            tenant_id, user_id, tool_name, arguments
        )
    return store.reserve(**payload)


def test_store_initializes_and_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    first = initialize(db_path)
    second = initialize(db_path)
    assert first["journal_mode"].lower() == "wal"
    assert second["created_tables"] == []
    store = IdempotencyStore(db_path)
    assert store.db_path == db_path


def test_new_key_reserves_successfully(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    assert outcome.status == RESERVED
    assert outcome.error_code is None
    assert outcome.record.state == "pending"
    assert outcome.record.tool_name == TOOL
    assert outcome.record.tool_version == VERSION


def test_same_key_same_fingerprint_pending_is_in_progress(tmp_path: Path):
    store = make_store(tmp_path)
    first = reserve(store)
    second = reserve(store)
    assert first.status == RESERVED
    assert second.status == IN_PROGRESS
    assert second.error_code == IDEMPOTENCY_CONFLICT
    assert second.record.execution_id == first.record.execution_id


def test_same_key_different_fingerprint_is_conflict(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    conflicting = reserve(
        store,
        arguments={"customer_id": 43, "lines": []},
        idempotency_key=outcome.record.idempotency_key,
    )
    assert conflicting.status == CONFLICT
    assert conflicting.error_code == IDEMPOTENCY_CONFLICT


def test_completion_creates_replayable_result(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    completed = store.complete(
        idempotency_key=outcome.record.idempotency_key,
        **request_binding(),
        execution_id=outcome.record.execution_id,
        result={"status": "success", "sale_order_id": 88},
        external_record_id="88",
    )
    assert completed.state == "completed"
    assert completed.result == {"status": "success", "sale_order_id": 88}
    assert completed.external_record_id == "88"
    replay = reserve(store)
    assert replay.status == REPLAYED
    assert replay.error_code is None
    assert replay.record.result == completed.result
    assert replay.record.execution_id == completed.execution_id
    assert replay.record.external_record_id == "88"


def test_replay_does_not_create_new_reservation(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    store.complete(
        outcome.record.idempotency_key,
        **request_binding(),
        execution_id=outcome.record.execution_id,
        result={"status": "success"},
    )
    replay = reserve(store)
    assert replay.status == REPLAYED
    assert replay.record.execution_id == outcome.record.execution_id


def test_cross_tenant_does_not_cross_replay(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    other_tenant = reserve(
        store,
        tenant_id="poc_tenant_002",
        idempotency_key=outcome.record.idempotency_key,
    )
    assert other_tenant.status == RESERVED
    assert other_tenant.record.tenant_id == "poc_tenant_002"
    assert store.get(outcome.record.idempotency_key, TENANT, USER) is not None
    assert store.get(outcome.record.idempotency_key, "poc_tenant_002", USER) is not None


def test_cross_user_does_not_cross_replay(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    other_user = reserve(
        store,
        user_id=OTHER_USER,
        idempotency_key=outcome.record.idempotency_key,
    )
    assert other_user.status == RESERVED
    assert other_user.record.user_id == OTHER_USER
    assert store.get(outcome.record.idempotency_key, TENANT, USER) is not None
    assert store.get(outcome.record.idempotency_key, TENANT, OTHER_USER) is not None
    expected_fingerprint = compute_request_fingerprint(TENANT, OTHER_USER, TOOL, VERSION, ARGS)
    assert other_user.record.request_fingerprint == expected_fingerprint
    assert other_user.record.request_fingerprint != outcome.record.request_fingerprint


def test_different_tool_version_does_not_replay(tmp_path: Path):
    store = make_store(tmp_path)
    reserve(store)
    outcome = reserve(store, tool_version="1.1.0")
    assert outcome.status == CONFLICT
    assert outcome.error_code == IDEMPOTENCY_CONFLICT


def test_empty_and_invalid_keys_are_rejected(tmp_path: Path):
    store = make_store(tmp_path)
    fingerprint = request_binding()["request_fingerprint"]
    for key in ("", "short", "not-a-key", "Z" * 32, 123):
        with pytest.raises(InvalidIdempotencyRequest):
            store.reserve(key, fingerprint, TOOL, VERSION, TENANT, USER)


def test_invalid_fingerprints_are_rejected(tmp_path: Path):
    store = make_store(tmp_path)
    key = compute_idempotency_key(TENANT, USER, TOOL, ARGS)
    for fingerprint in ("", "short", "Z" * 64, 123):
        with pytest.raises(InvalidIdempotencyRequest):
            store.reserve(key, fingerprint, TOOL, VERSION, TENANT, USER)


def test_canonically_equivalent_arguments_have_same_fingerprint_and_key():
    left = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}
    right = {"lines": [{"quantity": 2, "product_id": 7}], "customer_id": 42}
    assert compute_request_fingerprint(TENANT, USER, TOOL, VERSION, left) == compute_request_fingerprint(
        TENANT, USER, TOOL, VERSION, right
    )
    assert compute_idempotency_key(TENANT, USER, TOOL, left) == compute_idempotency_key(
        TENANT, USER, TOOL, right
    )


def test_semantically_different_arguments_have_different_fingerprints():
    left = compute_request_fingerprint(TENANT, USER, TOOL, VERSION, ARGS)
    right = compute_request_fingerprint(TENANT, USER, TOOL, VERSION, {"customer_id": 43})
    assert left != right


def test_definitive_error_is_replayed(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    failed = store.fail(
        outcome.record.idempotency_key,
        **request_binding(),
        execution_id=outcome.record.execution_id,
        outcome=DEFINITIVE_ERROR,
        error_code="ERP_VALIDATION_ERROR",
    )
    assert failed.state == "completed"
    assert failed.result == {"status": "error", "error_code": "ERP_VALIDATION_ERROR"}
    replay = reserve(store)
    assert replay.status == REPLAYED
    assert replay.record.result == failed.result


def test_ambiguous_outcome_requires_reconciliation(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    unknown = store.fail(
        outcome.record.idempotency_key,
        **request_binding(),
        execution_id=outcome.record.execution_id,
        outcome=AMBIGUOUS,
        error_code="ERP_CONNECTION_ERROR",
    )
    assert unknown.state == "unknown"
    assert unknown.result is None
    retry = reserve(store)
    assert retry.status == RECONCILIATION_REQUIRED
    assert retry.error_code == RECONCILIATION_REQUIRED_ERROR
    adopted = store.complete(
        outcome.record.idempotency_key,
        **request_binding(),
        execution_id=outcome.record.execution_id,
        result={"status": "success", "sale_order_id": 91},
        external_record_id="91",
        reconciliation=ADOPTED,
    )
    assert adopted.state == "completed"
    assert adopted.external_record_id == "91"
    final = reserve(store)
    assert final.status == REPLAYED
    assert final.record.result == adopted.result


def test_completed_record_cannot_be_overwritten(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    store.complete(
        outcome.record.idempotency_key,
        **request_binding(),
        execution_id=outcome.record.execution_id,
        result={"status": "success"},
    )
    with pytest.raises(IllegalIdempotencyTransition):
        store.complete(
            outcome.record.idempotency_key,
            **request_binding(),
            execution_id=outcome.record.execution_id,
            result={"status": "success", "overwritten": True},
        )


def test_illegal_state_transitions_are_rejected(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    binding = request_binding()
    key = outcome.record.idempotency_key

    with pytest.raises(IllegalIdempotencyTransition):
        store.complete(key, **binding, execution_id="00000000-0000-4000-8000-000000000000", result={})
    with pytest.raises(IllegalIdempotencyTransition):
        store.complete(key, **binding, execution_id=outcome.record.execution_id, result={}, reconciliation=ADOPTED)
    store.fail(
        key,
        **binding,
        execution_id=outcome.record.execution_id,
        outcome=AMBIGUOUS,
        error_code="ERP_CONNECTION_ERROR",
    )
    with pytest.raises(IllegalIdempotencyTransition):
        store.complete(key, **binding, execution_id=outcome.record.execution_id, result={})
    with pytest.raises(IllegalIdempotencyTransition):
        store.fail(
            key,
            **binding,
            execution_id=outcome.record.execution_id,
            outcome=AMBIGUOUS,
            error_code="ERP_CONNECTION_ERROR",
        )


def test_request_binding_mismatch_is_rejected(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    tampered = request_binding()
    tampered["request_fingerprint"] = "0" * 64
    with pytest.raises(IdempotencyStoreError):
        store.complete(
            outcome.record.idempotency_key,
            **tampered,
            execution_id=outcome.record.execution_id,
            result={},
        )


def test_modified_arguments_do_not_replay(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    modified = reserve(
        store,
        arguments={"customer_id": 42, "lines": [{"product_id": 7, "quantity": 3}]},
        idempotency_key=outcome.record.idempotency_key,
    )
    assert modified.status == CONFLICT


def test_secret_injection_into_result_is_rejected(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    for result in (
        {"token": "bearer-value"},
        {"nested": {"password": "secret"}},
        {"prompt": "full user prompt"},
        {"messages": [{"role": "system", "content": "trace"}]},
    ):
        with pytest.raises(InvalidIdempotencyRequest):
            store.complete(
                outcome.record.idempotency_key,
                **request_binding(),
                execution_id=outcome.record.execution_id,
                result=result,
            )
    persisted = store.get(outcome.record.idempotency_key, TENANT, USER)
    assert persisted.state == "pending"
    assert persisted.result is None


def test_existing_database_data_remains_intact(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    initialize(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO proposals
            (proposal_id, tool_name, tool_version, arguments, operation_hash, user_id,
             tenant_id, state, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("proposal-existing", TOOL, VERSION, "{}", "hash", USER, TENANT, "proposed", "now", "later"),
        )
    store = IdempotencyStore(db_path)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM proposals").fetchone()[0] == 1
        columns = {row[1] for row in connection.execute("PRAGMA table_info(idempotency_keys)")}
        assert {"tool_name", "tool_version"}.issubset(columns)


def test_source_has_no_odoo_or_llm_dependencies():
    from pathlib import Path

    source = Path("poc/idempotency.py").read_text(encoding="utf-8")
    forbidden = (
        "import odoo",
        "from odoo",
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import urllib",
        "from urllib",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
    )
    for term in forbidden:
        assert term not in source.lower()


def test_concurrent_same_key_reservations_have_exactly_one_owner(tmp_path: Path):
    store = make_store(tmp_path)
    thread_count = 16
    barrier = threading.Barrier(thread_count)
    outcomes: list = [None] * thread_count

    def worker(index: int) -> None:
        barrier.wait()
        outcomes[index] = reserve(store)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    owners = [outcome for outcome in outcomes if outcome.status == RESERVED]
    non_owners = [outcome for outcome in outcomes if outcome.status != RESERVED]
    assert len(owners) == 1
    assert all(outcome.status == IN_PROGRESS for outcome in non_owners)
    assert all(outcome.error_code == IDEMPOTENCY_CONFLICT for outcome in non_owners)
    execution_ids = {outcome.record.execution_id for outcome in outcomes}
    assert len(execution_ids) == 1

    with sqlite3.connect(store.db_path) as connection:
        rows = connection.execute(
            """
            SELECT count(*), count(DISTINCT state), min(state), max(state)
            FROM idempotency_keys
            WHERE tenant_id = ? AND user_id = ? AND key = ?
            """,
            (TENANT, USER, compute_idempotency_key(TENANT, USER, TOOL, ARGS)),
        ).fetchone()
    assert rows[0] == 1
    assert rows[1] == 1
    assert rows[2] == "pending"


def test_concurrent_different_fingerprints_have_one_owner_and_conflicts(tmp_path: Path):
    store = make_store(tmp_path)
    thread_count = 12
    barrier = threading.Barrier(thread_count)
    outcomes: list = [None] * thread_count

    def worker(index: int) -> None:
        barrier.wait()
        outcomes[index] = reserve(
            store,
            arguments={"customer_id": 100 + index, "lines": []},
            idempotency_key="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    owners = [outcome for outcome in outcomes if outcome.status == RESERVED]
    conflicts = [outcome for outcome in outcomes if outcome.status == CONFLICT]
    assert len(owners) == 1
    assert len(conflicts) == thread_count - 1
    assert all(outcome.error_code == IDEMPOTENCY_CONFLICT for outcome in conflicts)


def test_concurrent_completion_attempt_cannot_overwrite(tmp_path: Path):
    store = make_store(tmp_path)
    outcome = reserve(store)
    binding = request_binding()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def attempt(result_value: dict) -> None:
        barrier.wait()
        try:
            store.complete(
                outcome.record.idempotency_key,
                **binding,
                execution_id=outcome.record.execution_id,
                result=result_value,
            )
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=attempt, args=({"status": "success", "attempt": 1},)),
        threading.Thread(target=attempt, args=({"status": "success", "attempt": 2},)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(errors) == 1
    assert isinstance(errors[0], IllegalIdempotencyTransition)
    record = store.get(outcome.record.idempotency_key, TENANT, USER)
    assert record.result["attempt"] in (1, 2)
