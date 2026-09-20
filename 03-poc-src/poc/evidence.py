"""Evidence events (Phase 8 precursor).

Typed evidence events with cryptographic linkage via SHA-256 chaining,
stored in a dedicated table. Events are appended-only and never modified.

This is the Evidence Graph precursor: every important server-side decision
emits an event linked by trace_id/execution_id/action_id/parent_event_id.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from poc.db.init import DEFAULT_DB_PATH, initialize


class EvidenceType(str, Enum):
    INTENT = "intent"
    PLAN = "plan"
    ENTITY_RESOLUTION = "entity_resolution"
    POLICY_DECISION = "policy_decision"
    APPROVAL = "approval"
    LEASE = "lease"
    TOOL_CALL = "tool_call"
    ERP_REQUEST = "erp_request"
    ERP_RESPONSE = "erp_response"
    VERIFICATION = "verification"
    RECONCILIATION = "reconciliation"
    USER_VISIBLE_CLAIM = "user_visible_claim"
    STATE_TRANSITION = "state_transition"


EVIDENCE_DDL = """
CREATE TABLE IF NOT EXISTS evidence_events (
    evidence_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    trace_id TEXT,
    execution_id TEXT,
    action_id TEXT,
    parent_event_id TEXT,
    tool_call_id TEXT,
    approval_id TEXT,
    policy_version TEXT,
    policy_hash TEXT,
    tool_name TEXT,
    tool_version TEXT,
    payload TEXT NOT NULL,                  -- JSON: non-sensitive, allowlisted content
    previous_hash TEXT NOT NULL,
    own_hash TEXT NOT NULL
)
"""

GENESIS_HASH = "0" * 64
MAX_PAYLOAD_BYTES = 64 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _ensure_schema(db_path: Path) -> None:
    initialize(db_path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(EVIDENCE_DDL)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_trace ON evidence_events(trace_id, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_exec ON evidence_events(execution_id, timestamp)"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


@dataclass(frozen=True)
class EvidenceEvent:
    evidence_id: str
    event_type: EvidenceType
    timestamp: str
    trace_id: str | None
    execution_id: str | None
    action_id: str | None
    parent_event_id: str | None
    payload: Mapping[str, Any]
    own_hash: str


class EvidenceStore:
    """Append-only hash-chained evidence event log."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        _ensure_schema(self.db_path)

    def _last_own_hash(self, conn: sqlite3.Connection) -> str:
        # Order by the autoincrement rowid (insertion order), not by UUID string.
        row = conn.execute(
            "SELECT own_hash FROM evidence_events ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return row["own_hash"] if row else GENESIS_HASH

    def append(
        self,
        *,
        event_type: EvidenceType,
        payload: Mapping[str, Any],
        trace_id: str | None = None,
        execution_id: str | None = None,
        action_id: str | None = None,
        parent_event_id: str | None = None,
        tool_call_id: str | None = None,
        approval_id: str | None = None,
        policy_version: str | None = None,
        policy_hash: str | None = None,
        tool_name: str | None = None,
        tool_version: str | None = None,
        timestamp: str | None = None,
    ) -> EvidenceEvent:
        import uuid
        evidence_id = str(uuid.uuid4())
        ts = timestamp or _utc_now()
        payload_json = _canonical(dict(payload))
        if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("evidence payload exceeds size limit")
        with _connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                previous = self._last_own_hash(conn)
                hash_payload = _canonical({
                    "evidence_id": evidence_id,
                    "event_type": event_type.value,
                    "timestamp": ts,
                    "trace_id": trace_id,
                    "execution_id": execution_id,
                    "action_id": action_id,
                    "parent_event_id": parent_event_id,
                    "tool_call_id": tool_call_id,
                    "approval_id": approval_id,
                    "policy_version": policy_version,
                    "policy_hash": policy_hash,
                    "tool_name": tool_name,
                    "tool_version": tool_version,
                    "payload": payload_json,
                    "previous_hash": previous,
                })
                own_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()
                conn.execute(
                    """
                    INSERT INTO evidence_events
                    (evidence_id, event_type, timestamp, trace_id, execution_id,
                     action_id, parent_event_id, tool_call_id, approval_id,
                     policy_version, policy_hash, tool_name, tool_version,
                     payload, previous_hash, own_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id, event_type.value, ts, trace_id, execution_id,
                        action_id, parent_event_id, tool_call_id, approval_id,
                        policy_version, policy_hash, tool_name, tool_version,
                        payload_json, previous, own_hash,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return EvidenceEvent(
            evidence_id=evidence_id,
            event_type=event_type,
            timestamp=ts,
            trace_id=trace_id,
            execution_id=execution_id,
            action_id=action_id,
            parent_event_id=parent_event_id,
            payload=dict(payload),
            own_hash=own_hash,
        )

    def verify_chain(self) -> dict[str, Any]:
        """Verify hash chain integrity for all evidence events."""
        with _connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM evidence_events ORDER BY rowid").fetchall()
        prev = GENESIS_HASH
        for row in rows:
            if row["previous_hash"] != prev:
                return {"valid": False, "reason": "broken_previous_link", "evidence_id": row["evidence_id"]}
            stored_payload = row["payload"]
            hash_payload = _canonical({
                "evidence_id": row["evidence_id"],
                "event_type": row["event_type"],
                "timestamp": row["timestamp"],
                "trace_id": row["trace_id"],
                "execution_id": row["execution_id"],
                "action_id": row["action_id"],
                "parent_event_id": row["parent_event_id"],
                "tool_call_id": row["tool_call_id"],
                "approval_id": row["approval_id"],
                "policy_version": row["policy_version"],
                "policy_hash": row["policy_hash"],
                "tool_name": row["tool_name"],
                "tool_version": row["tool_version"],
                "payload": stored_payload,
                "previous_hash": row["previous_hash"],
            })
            expected = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()
            if row["own_hash"] != expected:
                return {"valid": False, "reason": "invalid_own_hash", "evidence_id": row["evidence_id"]}
            prev = row["own_hash"]
        return {"valid": True, "events": len(rows)}

    def for_execution(self, execution_id: str) -> list[dict[str, Any]]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM evidence_events WHERE execution_id = ? ORDER BY rowid",
                (execution_id,),
            ).fetchall()
        return [dict(r) for r in rows]
