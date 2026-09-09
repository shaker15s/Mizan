"""Audit integrity verifier — reads the gateway store read-only and validates
the hash chain per 02-poc/TEST_PLAN.md §7.

Usage:
    python -m poc.tests.verify_audit --db-path data/poc_gateway.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_REQUIRED_FIELDS = (
    "request_id",
    "tool_call_id",
    "tenant_id",
    "user_id",
    "tool_name",
    "tool_version",
    "policy_decision",
    "result_status",
    "previous_hash",
    "own_hash",
)


def _chain_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY audit_id").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the POC audit hash chain (read-only).")
    parser.add_argument("--db-path", default="data/poc_gateway.db", help="Path to the gateway SQLite store")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(json.dumps({"valid": False, "error": f"audit store not found: {db_path}"}))
        return 1

    try:
        from poc.audit_store import AuditStore

        chain = AuditStore(db_path).verify_chain()
    except Exception as error:  # noqa: BLE001 — verifier must not crash on corrupt stores
        print(json.dumps({"valid": False, "error": str(error)}))
        return 1

    rows = _chain_rows(db_path)
    missing_fields = [
        str(row.get("audit_id"))
        for row in rows
        if any(row.get(field) in (None, "") for field in _REQUIRED_FIELDS)
    ]
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    leaked_secrets = [token for token in ("ODOO_API_KEY", "ANTHROPIC_API_KEY", "bearer ") if token.lower() in serialized.lower()]

    report = {
        "db_path": str(db_path),
        "total_records": len(rows),
        "chain_valid": bool(chain.get("valid")),
        "first_broken_link": chain.get("audit_id") if not chain.get("valid") else None,
        "chain_reason": chain.get("reason"),
        "records_missing_required_fields": missing_fields,
        "detected_secret_material": leaked_secrets,
        "valid": bool(chain.get("valid")) and not missing_fields and not leaked_secrets,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
