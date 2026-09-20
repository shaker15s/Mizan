"""Idempotent initializer for the POC gateway SQLite store.

Creates ``data/poc_gateway.db`` (WAL mode) with the three gateway tables
defined by the POC specification:

- ``audit_log``          — 02-poc/SECURITY_MODEL.md §8 (append-only; this
                           module NEVER issues UPDATE/DELETE against it)
- ``idempotency_keys``   — 02-poc/TECHNICAL_DESIGN.md §7
- ``proposals``          — 02-poc/SECURITY_MODEL.md §5.1

Guarantees:
- Idempotent: safe to run repeatedly; existing tables and rows are preserved.
- No fake audit or business records are ever inserted.
- No DROP statements exist in this module (grep-verifiable).

Usage (from ``03-poc-src/``)::

    python -m poc.db.init
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# data/poc_gateway.db lives at 03-poc-src/data/ regardless of the caller's cwd.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "poc_gateway.db"

TABLES = ("audit_log", "idempotency_keys", "proposals")

AUDIT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,           -- = trace_id of the user utterance
    tool_call_id TEXT NOT NULL,         -- unique per tool invocation
    trace_id TEXT NOT NULL,             -- request correlation id (aliases request_id in POC)
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,              -- platform user id
    odoo_user TEXT NOT NULL,            -- Odoo identity actually used at the ERP boundary
    model_id TEXT,
    model_version TEXT,
    tool_name TEXT NOT NULL,
    tool_version TEXT,                  -- semantic version string active at call time (matches operation_hash binding)
    arguments_hash TEXT NOT NULL,       -- SHA256 of canonical arguments (== idempotency source hash for writes)
    sanitized_arguments TEXT,           -- JSON: allowlisted safe fields only
    policy_decision TEXT NOT NULL,      -- 'allowed' | 'denied' | 'confirmation_required'
    proposal_id TEXT,
    operation_hash TEXT,
    approval_id TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    result_status TEXT NOT NULL,        -- 'success' | 'error' | 'verification_failed' | 'ambiguous'
    error_code TEXT,
    external_system TEXT,
    external_record_id TEXT,
    idempotency_key TEXT,
    execution_id TEXT,                  -- gateway-generated UUID v4; correlates one physical attempt (distinct from idempotency_key)
    reconciliation TEXT,                -- NULL | 'adopted' | 'reexecuted' | 'manual_review'
    previous_hash TEXT NOT NULL,
    own_hash TEXT NOT NULL,
    created_at TEXT NOT NULL              -- application-supplied UTC timestamp (normative clock discipline)
)
"""

IDEMPOTENCY_KEYS_DDL = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    tenant_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    request_hash TEXT NOT NULL,   -- SHA256(tool binding + user + tenant + canonical args)
    execution_id TEXT NOT NULL,   -- gateway-generated UUID v4, unique per attempt
    state      TEXT NOT NULL,     -- 'pending' | 'completed' | 'unknown'
    result     TEXT,              -- JSON string of normalized tool result (when completed)
    external_record_id TEXT,      -- e.g. sale.order id/name, when known
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tenant_id, user_id, key)
)
"""

PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    proposal_version INTEGER NOT NULL DEFAULT 1, -- server-incremented on each amend (Phase 4)
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    arguments TEXT NOT NULL,           -- JSON serialized
    operation_hash TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    state TEXT NOT NULL,               -- proposed | confirmed | executing | completed | failed
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,          -- created_at + CONFIRMATION_EXPIRY_SECONDS
    confirmed_at TEXT,
    executed_at TEXT,
    superseded_by TEXT                -- proposal_id that replaces this one after amend (Phase 4)
)
"""

_DDL_BY_TABLE = {
    "audit_log": AUDIT_LOG_DDL,
    "idempotency_keys": IDEMPOTENCY_KEYS_DDL,
    "proposals": PROPOSALS_DDL,
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply additive in-place migrations. Always idempotent."""
    # Migration 2026-09-20: proposal_version + superseded_by columns (Phase 4).
    props_cols = _columns(conn, "proposals") if "proposals" in _existing_tables(conn) else set()
    if props_cols and "proposal_version" not in props_cols:
        conn.execute("ALTER TABLE proposals ADD COLUMN proposal_version INTEGER NOT NULL DEFAULT 1")
    if props_cols and "superseded_by" not in props_cols:
        conn.execute("ALTER TABLE proposals ADD COLUMN superseded_by TEXT")
    # Migration 2026-09-20: execution_leases table (Phase 5).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_leases (
            lease_id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL,
            owner TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            approval_id TEXT,
            action_id TEXT,
            issued_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_leases_key_state ON execution_leases(idempotency_key, state)"
    )


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in rows}


def initialize(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Create/verify the gateway store. Idempotent; never deletes data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        before = _existing_tables(conn)
        for ddl in _DDL_BY_TABLE.values():
            conn.execute(ddl)
        conn.commit()
        after = _existing_tables(conn)
        created = sorted(after & set(TABLES) - before)
        existing = sorted((after & set(TABLES)) - set(created))
        row_counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 (fixed names)
            for table in TABLES
        }
        # In-place schema migrations (additive; never drop/rename).
        _run_migrations(conn)
        conn.commit()
        after_migration = _existing_tables(conn)
        return {
            "db_path": str(db_path),
            "journal_mode": journal_mode,
            "created_tables": created,
            "preexisting_tables": existing,
            "row_counts": row_counts,
            "tables_present": sorted(after_migration),
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    db_path = Path(args[0]) if args else DEFAULT_DB_PATH
    report = initialize(db_path)
    print(f"gateway store : {report['db_path']}")
    print(f"journal_mode  : {report['journal_mode']}")
    print(f"created tables: {', '.join(report['created_tables']) or '(none - already present)'}")
    print(f"existing      : {', '.join(report['preexisting_tables']) or '(none)'}")
    counts = ", ".join(f"{t}={n}" for t, n in report["row_counts"].items())
    print(f"row counts    : {counts}")
    missing = [t for t in TABLES if t not in report["created_tables"] + report["preexisting_tables"]]
    if missing:
        print(f"ERROR: missing tables after init: {missing}", file=sys.stderr)
        return 1
    if report["journal_mode"].lower() != "wal":
        print("ERROR: WAL mode not active", file=sys.stderr)
        return 1
    print("OK: all gateway tables present, WAL active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
