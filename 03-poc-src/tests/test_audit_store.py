"""Focused POC tests for the SQLite-backed append-only audit store."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from poc.audit_store import (
    AuditStore,
    compute_arguments_hash,
)


def base_record(index: int = 1) -> dict:
    return {
        "request_id": f"req-{index}",
        "tool_call_id": f"call-{index}",
        "tenant_id": "poc_tenant_001",
        "user_id": "sales_user@test",
        "odoo_user": "sales_user@test",
        "tool_name": "customer.search",
        "tool_version": "1.0.0",
        "arguments": {"query": "Acme"},
        "policy_decision": "allowed",
        "start_time": "2026-09-08T00:00:00Z",
        "end_time": "2026-09-08T00:00:01Z",
        "result_status": "success",
    }


def tamper(db_path: Path, sql: str, params: tuple) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_initialize_creates_schema_and_is_idempotent(tmp_path: Path):
    store = AuditStore(tmp_path / "poc_gateway.db")
    first = store.initialize()
    second = store.initialize()
    assert first["journal_mode"].lower() == "wal"
    assert second["created_tables"] == []
    assert second["preexisting_tables"] == ["audit_log", "idempotency_keys", "proposals"]
    assert second["row_counts"]["audit_log"] == 0


def test_append_creates_one_record(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    record = store.append(base_record())
    assert record["audit_id"] == 1
    assert record["request_id"] == "req-1"
    assert record["sanitized_arguments"] == json.dumps(
        {"query": "Acme"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def test_multiple_records_form_correct_sha256_chain(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    first = store.append(base_record(1))
    second = store.append(base_record(2))
    assert first["previous_hash"] == "0" * 64
    assert second["previous_hash"] == first["own_hash"]
    assert len(first["own_hash"]) == 64
    assert len(second["own_hash"]) == 64
    assert first["own_hash"] != second["own_hash"]


def test_genesis_representation_is_explicit(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    first = store.append(base_record())
    assert first["previous_hash"] == "0" * 64


def test_arguments_hash_is_deterministic_and_order_insensitive():
    first = compute_arguments_hash({"query": "Acme", "customer_id": 9})
    second = compute_arguments_hash({"customer_id": 9, "query": "Acme"})
    assert first == second


def test_different_arguments_produce_different_hashes():
    first = compute_arguments_hash({"query": "Acme"})
    second = compute_arguments_hash({"query": "Globex"})
    assert first != second


def test_verify_chain_succeeds_for_intact_chain(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    for index in range(1, 5):
        store.append(base_record(index))
    result = store.verify_chain()
    assert result == {"valid": True, "reason": None, "audit_id": None}


def test_verify_chain_detects_modified_record_content(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    for index in range(1, 4):
        store.append(base_record(index))
    tamper(
        db_path,
        "UPDATE audit_log SET sanitized_arguments = ? WHERE audit_id = ?",
        (json.dumps({"query": "Changed"}), 2),
    )
    result = store.verify_chain()
    assert result["valid"] is False
    assert result["reason"] == "invalid_own_hash"
    assert result["audit_id"] == 2


def test_verify_chain_detects_modified_record_hash(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    for index in range(1, 4):
        store.append(base_record(index))
    tamper(db_path, "UPDATE audit_log SET own_hash = ? WHERE audit_id = ?", ("f" * 64, 2))
    result = store.verify_chain()
    assert result["valid"] is False
    assert result["reason"] == "invalid_own_hash"


def test_verify_chain_detects_broken_previous_hash(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    for index in range(1, 4):
        store.append(base_record(index))
    tamper(
        db_path,
        "UPDATE audit_log SET previous_hash = ? WHERE audit_id = ?",
        ("e" * 64, 2),
    )
    result = store.verify_chain()
    assert result["valid"] is False
    assert result["reason"] == "broken_previous_hash_link"


def test_verify_chain_detects_missing_record_in_chain(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    for index in range(1, 4):
        store.append(base_record(index))
    tamper(db_path, "DELETE FROM audit_log WHERE audit_id = ?", (2,))
    result = store.verify_chain()
    assert result["valid"] is False
    assert result["reason"] == "broken_previous_hash_link"


def test_public_api_has_no_update_or_delete_methods():
    store = AuditStore()
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")
    assert not hasattr(store, "upsert")


def test_known_credential_and_pii_fields_are_never_persisted(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    record = base_record()
    record["arguments"] = {
        "query": "Acme",
        "api_key": "definitely-not-real-key",
        "authorization": "bearer definitely-not-real-token",
        "password": "definitely-not-real-password",
        "email": "person@example.com",
        "phone": "01000000000",
    }
    appended = store.append(record)
    assert appended["sanitized_arguments"] is None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT sanitized_arguments FROM audit_log WHERE audit_id = 1").fetchone()
    finally:
        conn.close()
    stored = str(row[0])
    assert "definitely-not-real-key" not in stored
    assert "bearer definitely-not-real-token" not in stored
    assert "definitely-not-real-password" not in stored
    assert "person@example.com" not in stored
    assert "01000000000" not in stored


def test_allowed_write_arguments_are_persisted(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    record = base_record()
    record["tool_name"] = "sales.order.create"
    record["arguments"] = {
        "customer_id": 9,
        "lines": [{"product_id": 15, "quantity": 2, "secret": "removed"}],
    }
    appended = store.append(record)
    assert appended["sanitized_arguments"] is None


def test_reinitialization_preserves_existing_data(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    appended = store.append(base_record())
    store.initialize()
    records = store.list()
    assert [record["audit_id"] for record in records] == [appended["audit_id"]]


def test_concurrent_successive_appends_preserve_chain(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(store.append, base_record(index)) for index in range(1, 13)]
        records = [future.result() for future in futures]
    assert len(records) == 12
    result = store.verify_chain()
    assert result == {"valid": True, "reason": None, "audit_id": None}


def test_missing_required_fields_are_rejected(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    record = base_record()
    del record["tenant_id"]
    with pytest.raises(ValueError, match="Missing required audit fields: tenant_id"):
        store.append(record)


def test_unknown_audit_fields_are_rejected(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    store = AuditStore(db_path)
    store.initialize()
    record = base_record()
    record["arbitrary_secret"] = "not-allowed"
    with pytest.raises(ValueError, match="Unknown audit fields: arbitrary_secret"):
        store.append(record)