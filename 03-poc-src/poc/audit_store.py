"""SQLite-backed append-only audit store for the POC.

This module persists audit records and maintains the tamper-evident hash chain
specified by ``02-poc/SECURITY_MODEL.md``. It performs no authorization,
confirmation, verification, LLM, or Odoo operations.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from poc.db.init import DEFAULT_DB_PATH, initialize

AUDIT_COLUMNS = (
    "audit_id",
    "request_id",
    "tool_call_id",
    "trace_id",
    "tenant_id",
    "user_id",
    "odoo_user",
    "model_id",
    "model_version",
    "tool_name",
    "tool_version",
    "arguments_hash",
    "sanitized_arguments",
    "policy_decision",
    "proposal_id",
    "operation_hash",
    "approval_id",
    "start_time",
    "end_time",
    "result_status",
    "error_code",
    "external_system",
    "external_record_id",
    "idempotency_key",
    "execution_id",
    "reconciliation",
    "previous_hash",
    "own_hash",
    "created_at",
)
REQUIRED_COLUMNS = (
    "request_id",
    "tool_call_id",
    "tenant_id",
    "user_id",
    "odoo_user",
    "tool_name",
    "policy_decision",
    "start_time",
    "end_time",
    "result_status",
)
OPTIONAL_COLUMNS = (
    "execution_id",
    "model_id",
    "model_version",
    "proposal_id",
    "operation_hash",
    "approval_id",
    "error_code",
    "external_system",
    "external_record_id",
    "idempotency_key",
)
POLICY_DECISIONS = {"allowed", "denied", "confirmation_required"}
ALLOWED_ARGUMENT_KEYS = {"query", "customer_id", "order_id", "limit", "lines"}
ALLOWED_LINE_KEYS = {"product_id", "quantity"}
GENESIS_HASH = "0" * 64


class AuditIntegrityError(RuntimeError):
    """Raised when the persisted audit chain is broken or tampered with."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_arguments_hash(arguments: Mapping[str, Any] | None) -> str:
    """Compute the deterministic SHA-256 hash of sanitized tool arguments."""
    payload = _canonical_json(dict(arguments) if arguments is not None else None)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sanitize_line(line: Any) -> dict[str, Any] | None:
    if not isinstance(line, dict):
        return None
    unknown_line_fields = [key for key in line if key not in ALLOWED_LINE_KEYS]
    if unknown_line_fields:
        return None
    return {key: line[key] for key in sorted(line)}


def _sanitize_arguments(arguments: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Apply the normative POC argument allowlist.

    Credential-bearing, free-text, PII, prompt, and unknown fields are never
    retained. If any allowed write line contains an unknown field, the caller
    gets a hash-only audit record.
    """
    if arguments is None:
        return None
    if not isinstance(arguments, dict):
        return None
    unknown_fields = [key for key in arguments if key not in ALLOWED_ARGUMENT_KEYS]
    if unknown_fields:
        return None
    if "lines" in arguments:
        lines = arguments["lines"]
        if not isinstance(lines, list) or not lines:
            return None
        sanitized_lines = [_sanitize_line(line) for line in lines]
        if any(line is None for line in sanitized_lines):
            return None
        sanitized = dict(arguments)
        sanitized["lines"] = sanitized_lines
        return sanitized
    return dict(arguments)


def _row_hash_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        # str() normalizes any int/float the caller passed so the recomputed
        # hash always matches the TEXT-affinity value SQLite stores back.
        key: str(value) if value is not None and not isinstance(value, (str, dict, list)) else value
        for key, value in (
            (key, row[key]) for key in AUDIT_COLUMNS if key not in {"audit_id", "previous_hash", "own_hash"}
        )
    }


def compute_row_hash(payload_columns: Mapping[str, Any], previous_hash: str) -> str:
    """Compute a row hash using the normative canonical serialization."""
    payload = _canonical_json(_row_hash_payload(payload_columns))
    return hashlib.sha256((previous_hash + payload).encode("utf-8")).hexdigest()


class AuditStore:
    """Small append-oriented audit persistence API backed by SQLite WAL."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> dict[str, Any]:
        """Create the store idempotently without deleting or replacing data."""
        return initialize(self.db_path)

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Validate, hash, and transactionally append one audit record."""
        if not isinstance(record, dict):
            raise TypeError("record must be a mapping")

        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous_hash = self._last_own_hash(conn)
            row = self._prepare_row(record, previous_hash)
            insertable = [key for key in AUDIT_COLUMNS if key != "audit_id"]
            placeholders = ", ".join("?" for _ in insertable)
            columns = ", ".join(insertable)
            cursor = conn.execute(
                f"INSERT INTO audit_log ({columns}) VALUES ({placeholders})",
                [row[key] for key in insertable],
            )
            audit_id = int(cursor.lastrowid)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(audit_id)

    def get(self, audit_id: int) -> dict[str, Any]:
        """Return one structured audit record by database ID."""
        if isinstance(audit_id, bool) or not isinstance(audit_id, int) or audit_id < 1:
            raise ValueError("audit_id must be a positive integer")
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE audit_id = ?", (audit_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(f"Audit record not found: {audit_id}")
        return dict(row)

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return ordered audit records as structured dictionaries."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must not be negative")
        sql = "SELECT * FROM audit_log"
        params: list[Any] = []
        if request_id is not None:
            sql += " WHERE request_id = ?"
            params.append(request_id)
        sql += " ORDER BY audit_id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def verify_chain(self) -> dict[str, Any]:
        """Verify every stored row and its predecessor linkage."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY audit_id").fetchall()
        finally:
            conn.close()
        previous_hash = GENESIS_HASH
        for row in rows:
            record = dict(row)
            if record["previous_hash"] != previous_hash:
                return {
                    "valid": False,
                    "reason": "broken_previous_hash_link",
                    "audit_id": record["audit_id"],
                }
            expected_hash = compute_row_hash(record, previous_hash)
            if record["own_hash"] != expected_hash:
                return {
                    "valid": False,
                    "reason": "invalid_own_hash",
                    "audit_id": record["audit_id"],
                }
            previous_hash = record["own_hash"]
        return {"valid": True, "reason": None, "audit_id": None}

    def _last_own_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute(
            "SELECT own_hash FROM audit_log ORDER BY audit_id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS_HASH

    def _prepare_row(self, record: Mapping[str, Any], previous_hash: str) -> dict[str, Any]:
        unknown = set(record) - {
            *(key for key in AUDIT_COLUMNS if key != "audit_id"),
            "arguments",
            "odoo_user_id",
            "reconciliation_status",
        }
        if unknown:
            raise ValueError(f"Unknown audit fields: {', '.join(sorted(unknown))}")
        missing = [key for key in REQUIRED_COLUMNS if record.get(key) in (None, "")]
        if missing:
            raise ValueError(f"Missing required audit fields: {', '.join(missing)}")

        arguments = record.get("arguments", record.get("sanitized_arguments"))
        sanitized_arguments = _sanitize_arguments(arguments)
        arguments_hash = record.get("arguments_hash") or compute_arguments_hash(sanitized_arguments)
        if not isinstance(arguments_hash, str) or len(arguments_hash) != 64:
            raise ValueError("arguments_hash must be a 64-character SHA-256 value")

        policy_decision = record["policy_decision"]
        if policy_decision not in POLICY_DECISIONS:
            raise ValueError("policy_decision is invalid")

        created_at = record.get("created_at") or _utc_now()
        row: dict[str, Any] = {key: None for key in AUDIT_COLUMNS}
        for key in REQUIRED_COLUMNS:
            row[key] = record[key]
        for key in OPTIONAL_COLUMNS:
            row[key] = record.get(key)
        row["trace_id"] = record.get("trace_id", record["request_id"])
        row["tool_version"] = record.get("tool_version")
        row["sanitized_arguments"] = (
            _canonical_json(sanitized_arguments) if sanitized_arguments is not None else None
        )
        row["arguments_hash"] = arguments_hash
        row["reconciliation"] = record.get(
            "reconciliation", record.get("reconciliation_status")
        )
        row["created_at"] = created_at
        row["previous_hash"] = previous_hash
        row["own_hash"] = compute_row_hash(row, previous_hash)
        return row