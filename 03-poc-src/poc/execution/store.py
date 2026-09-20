"""Persistence for canonical execution state (Phase 2 continuation).

Stores one row per execution, keyed by execution_id. Events are appended
to an event_log table so a full replayable history exists.

Design notes:
* Uses the same gateway SQLite file as the rest of the POC for simplicity.
* Separate from audit_log: audit captures governance decisions; this
  captures state-machine transitions. Production can move these to an
  immutable evidence store later.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from poc.db.init import DEFAULT_DB_PATH, initialize
from poc.execution.state_machine import (
    ExecutionEvent,
    ExecutionStage,
    ExecutionState,
    ExecutionStatus,
    FinalStatus,
    INITIAL_STATE,
    SecurityStatus,
)


EXECUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    action_id TEXT,
    trace_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tool_name TEXT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    security TEXT NOT NULL,
    final TEXT,
    created_at TEXT NOT NULL,
    last_updated TEXT NOT NULL
)
"""

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS execution_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    status TEXT NOT NULL,
    security TEXT NOT NULL,
    final TEXT,
    actor TEXT,
    reason TEXT,
    evidence_id TEXT,
    FOREIGN KEY (execution_id) REFERENCES executions(execution_id)
)
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(db_path: Path) -> None:
    initialize(db_path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(EXECUTIONS_DDL)
            conn.execute(EVENTS_DDL)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_execution ON execution_events(execution_id, event_id)"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


class ExecutionStore:
    """Persists ExecutionState snapshots and event history."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        _ensure_schema(self.db_path)

    def create(self, state: ExecutionState) -> None:
        """Insert a brand-new execution row. State must be at IDLE or similar."""
        with _connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO executions
                    (execution_id, action_id, trace_id, tenant_id, user_id, tool_name,
                     stage, status, security, final, created_at, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.execution_id,
                        state.action_id,
                        state.trace_id,
                        "",  # tenant/user/tool filled in on update
                        "",
                        "",
                        state.stage.value,
                        state.status.value,
                        state.security.value,
                        state.final.value if state.final else None,
                        state.last_updated,
                        state.last_updated,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def update_identity(self, execution_id: str, *, tenant_id: str, user_id: str, tool_name: str | None = None, trace_id: str | None = None, action_id: str | None = None) -> None:
        with _connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                updates = {"tenant_id": tenant_id, "user_id": user_id}
                if tool_name is not None:
                    updates["tool_name"] = tool_name
                if trace_id is not None:
                    updates["trace_id"] = trace_id
                if action_id is not None:
                    updates["action_id"] = action_id
                sets = ", ".join(f"{k} = ?" for k in updates)
                values = list(updates.values()) + [execution_id]
                conn.execute(f"UPDATE executions SET {sets} WHERE execution_id = ?", values)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def save(self, state: ExecutionState) -> None:
        """Update the snapshot row and append new events to the log."""
        existing = self.load(state.execution_id) if state.execution_id else None
        if existing is None:
            self.create(state)
            existing = INITIAL_STATE()
            existing.execution_id = state.execution_id
            existing.trace_id = state.trace_id
            existing.action_id = state.action_id
        # Events not yet persisted are those beyond len(existing.history)
        new_transitions = state.history[len(existing.history):] if existing.history else state.history
        with _connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE executions
                    SET stage = ?, status = ?, security = ?, final = ?, last_updated = ?
                    WHERE execution_id = ?
                    """,
                    (
                        state.stage.value,
                        state.status.value,
                        state.security.value,
                        state.final.value if state.final else None,
                        state.last_updated,
                        state.execution_id,
                    ),
                )
                for t in new_transitions:
                    conn.execute(
                        """
                        INSERT INTO execution_events
                        (execution_id, timestamp, event, from_stage, to_stage,
                         status, security, final, actor, reason, evidence_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            state.execution_id,
                            t.timestamp,
                            t.event.value,
                            t.from_stage.value,
                            t.to_stage.value,
                            t.status.value,
                            t.security.value,
                            t.final.value if t.final else None,
                            t.actor,
                            t.reason,
                            t.evidence_id,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def load(self, execution_id: str) -> ExecutionState | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None:
                return None
            event_rows = conn.execute(
                "SELECT * FROM execution_events WHERE execution_id = ? ORDER BY event_id",
                (execution_id,),
            ).fetchall()
        from poc.execution.state_machine import ExecutionTransition  # local to avoid cycle at import time
        history = []
        for er in event_rows:
            history.append(ExecutionTransition(
                timestamp=er["timestamp"],
                event=ExecutionEvent(er["event"]),
                from_stage=ExecutionStage(er["from_stage"]) if er["from_stage"] else ExecutionStage.IDLE,
                to_stage=ExecutionStage(er["to_stage"]),
                status=ExecutionStatus(er["status"]),
                security=SecurityStatus(er["security"]),
                final=FinalStatus(er["final"]) if er["final"] else None,
                actor=er["actor"],
                reason=er["reason"] or "",
                evidence_id=er["evidence_id"],
                trace_id=row["trace_id"],
                execution_id=execution_id,
                action_id=row["action_id"],
            ))
        state = ExecutionState(
            stage=ExecutionStage(row["stage"]),
            status=ExecutionStatus(row["status"]),
            security=SecurityStatus(row["security"]),
            final=FinalStatus(row["final"]) if row["final"] else None,
            history=tuple(history),
            execution_id=row["execution_id"],
            action_id=row["action_id"],
            trace_id=row["trace_id"],
            last_updated=row["last_updated"],
        )
        return state

    def list_recent(self, *, tenant_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with _connect(self.db_path) as conn:
            if tenant_id:
                rows = conn.execute(
                    "SELECT execution_id, trace_id, tenant_id, user_id, tool_name, stage, status, security, final, last_updated FROM executions WHERE tenant_id = ? ORDER BY last_updated DESC LIMIT ?",
                    (tenant_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT execution_id, trace_id, tenant_id, user_id, tool_name, stage, status, security, final, last_updated FROM executions ORDER BY last_updated DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]
