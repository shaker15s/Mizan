"""Execution Lease (Phase 5).

An execution lease is separate from idempotency (plan §15, §J):

* Idempotency = "this semantic operation must not run twice" (content-bound).
* Lease       = "this particular physical attempt currently owns execution
                 rights for this idempotency key" (owner-bound, with heartbeat
                 and expiry).

A lease is NOT permission and NOT idempotency. It identifies execution
ownership so a second caller cannot silently double-execute while a first
caller is mid-flight, and so stuck executions can be detected and reconciled.

The current implementation provides the in-memory/pure-Python semantics and a
SQLite-backed LeaseStore that reuses the existing gateway database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from poc.db.init import DEFAULT_DB_PATH, initialize


DEFAULT_LEASE_TTL_SECONDS = 60
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 10


class LeaseState(str):
    pass


LEASE_ACTIVE = LeaseState("active")
LEASE_EXPIRED = LeaseState("expired")
LEASE_RELEASED = LeaseState("released")
LEASE_COMPLETED = LeaseState("completed")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _payload_hash(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ExecutionLease:
    """A single lease record."""

    lease_id: str
    execution_id: str
    idempotency_key: str
    owner: str                     # logical owner (node/worker identifier)
    payload_hash: str
    approval_id: str | None
    action_id: str | None
    issued_at: str
    heartbeat_at: str
    expires_at: str
    state: LeaseState

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or _utc_now()
        try:
            expires = datetime.strptime(self.expires_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            expires = datetime.strptime(self.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return now >= expires


class LeaseError(RuntimeError):
    pass


class LeaseNotOwnedError(LeaseError):
    pass


class LeaseAlreadyHeldError(LeaseError):
    pass


class _Clock(Protocol):
    def __call__(self) -> datetime: ...


class LeaseStore:
    """SQLite-backed lease storage.

    Uses the same database file as the rest of the gateway for the POC to keep
    the deployment simple; production will split tables by trust domain.
    """

    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        clock: _Clock = _utc_now,
    ) -> None:
        self.db_path = Path(db_path)
        self.ttl_seconds = int(ttl_seconds)
        self.clock = clock
        initialize(self.db_path)
        self._ensure_table()

    # ---- schema ------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_table(self) -> None:
        with self._connect() as conn:
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
            conn.commit()

    # ---- lease lifecycle ---------------------------------------------------
    def acquire(
        self,
        *,
        execution_id: str,
        idempotency_key: str,
        arguments: Mapping[str, Any],
        owner: str,
        approval_id: str | None = None,
        action_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> ExecutionLease:
        """Attempt to acquire a lease for an idempotency key.

        Fails LeaseAlreadyHeldError if another active/unexpired lease owns the
        key. Expired leases are considered abandoned and may be superseded.
        """
        ttl = int(ttl_seconds) if ttl_seconds else self.ttl_seconds
        now = self.clock()
        issued_at = _now_iso()
        heartbeat_at = issued_at
        expires_at = (now + timedelta(seconds=ttl)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        lease_id = str(uuid.uuid4())
        ph = _payload_hash(arguments)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Any active lease for the same key that has NOT expired blocks us.
                existing_rows = conn.execute(
                    """
                    SELECT * FROM execution_leases
                    WHERE idempotency_key = ? AND state = ?
                    """,
                    (idempotency_key, LEASE_ACTIVE),
                ).fetchall()
                for row in existing_rows:
                    existing = self._row_to_lease(row)
                    if not existing.is_expired(now):
                        raise LeaseAlreadyHeldError(
                            f"idempotency_key {idempotency_key!r} has active lease "
                            f"{existing.lease_id} owned by {existing.owner}"
                        )
                    # Expired active lease → mark it expired before issuing ours.
                    conn.execute(
                        "UPDATE execution_leases SET state = ? WHERE lease_id = ?",
                        (LEASE_EXPIRED, existing.lease_id),
                    )
                conn.execute(
                    """
                    INSERT INTO execution_leases
                    (lease_id, execution_id, idempotency_key, owner, payload_hash,
                     approval_id, action_id, issued_at, heartbeat_at, expires_at, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease_id, execution_id, idempotency_key, owner, ph,
                        approval_id, action_id, issued_at, heartbeat_at, expires_at,
                        LEASE_ACTIVE,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return ExecutionLease(
            lease_id=lease_id,
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            owner=owner,
            payload_hash=ph,
            approval_id=approval_id,
            action_id=action_id,
            issued_at=issued_at,
            heartbeat_at=heartbeat_at,
            expires_at=expires_at,
            state=LEASE_ACTIVE,
        )

    def heartbeat(self, lease_id: str, *, owner: str) -> ExecutionLease:
        """Renew the heartbeat for an active lease. Owner must match."""
        now = self.clock()
        hb = _now_iso()
        expires = (now + timedelta(seconds=self.ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM execution_leases WHERE lease_id = ?",
                    (lease_id,),
                ).fetchone()
                if row is None:
                    raise LeaseNotOwnedError(f"lease {lease_id} not found")
                lease = self._row_to_lease(row)
                if lease.owner != owner:
                    raise LeaseNotOwnedError(f"lease {lease_id} owned by {lease.owner}, not {owner}")
                if lease.state != LEASE_ACTIVE:
                    raise LeaseNotOwnedError(f"lease {lease_id} is in state {lease.state}")
                conn.execute(
                    "UPDATE execution_leases SET heartbeat_at = ?, expires_at = ? WHERE lease_id = ?",
                    (hb, expires, lease_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        refreshed = self.get(lease_id)
        assert refreshed is not None
        return refreshed

    def complete(self, lease_id: str, *, owner: str) -> ExecutionLease:
        """Mark a lease as completed (post-verification success)."""
        return self._transition(lease_id, owner=owner, to=LEASE_COMPLETED, allowed_from={LEASE_ACTIVE})

    def release(self, lease_id: str, *, owner: str) -> ExecutionLease:
        """Release a lease explicitly (e.g. on clean failure before any side effect)."""
        return self._transition(lease_id, owner=owner, to=LEASE_RELEASED, allowed_from={LEASE_ACTIVE})

    def expire_stale(self) -> int:
        """Mark any active lease past expires_at as LEASE_EXPIRED. Returns count."""
        now_str = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    "UPDATE execution_leases SET state = ? WHERE state = ? AND expires_at < ?",
                    (LEASE_EXPIRED, LEASE_ACTIVE, now_str),
                )
                count = cursor.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    def get(self, lease_id: str) -> ExecutionLease | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
        return None if row is None else self._row_to_lease(row)

    def active_for_key(self, idempotency_key: str) -> ExecutionLease | None:
        """Return the currently-active lease for a key, or None. Expired rows are filtered."""
        now = self.clock()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_leases WHERE idempotency_key = ? AND state = ? ORDER BY issued_at DESC LIMIT 1",
                (idempotency_key, LEASE_ACTIVE),
            ).fetchall()
        for row in rows:
            lease = self._row_to_lease(row)
            if not lease.is_expired(now):
                return lease
        return None

    # ---- internal ----------------------------------------------------------
    def _transition(
        self,
        lease_id: str,
        *,
        owner: str,
        to: LeaseState,
        allowed_from: set[LeaseState],
    ) -> ExecutionLease:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM execution_leases WHERE lease_id = ?",
                    (lease_id,),
                ).fetchone()
                if row is None:
                    raise LeaseNotOwnedError(f"lease {lease_id} not found")
                lease = self._row_to_lease(row)
                if lease.owner != owner:
                    raise LeaseNotOwnedError(f"lease {lease_id} owned by {lease.owner}, not {owner}")
                if lease.state not in allowed_from:
                    raise LeaseNotOwnedError(
                        f"lease {lease_id} cannot transition from {lease.state} to {to}"
                    )
                conn.execute(
                    "UPDATE execution_leases SET state = ? WHERE lease_id = ?",
                    (to, lease_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        refreshed = self.get(lease_id)
        assert refreshed is not None
        return refreshed

    @staticmethod
    def _row_to_lease(row: sqlite3.Row) -> ExecutionLease:
        return ExecutionLease(
            lease_id=row["lease_id"],
            execution_id=row["execution_id"],
            idempotency_key=row["idempotency_key"],
            owner=row["owner"],
            payload_hash=row["payload_hash"],
            approval_id=row["approval_id"],
            action_id=row["action_id"],
            issued_at=row["issued_at"],
            heartbeat_at=row["heartbeat_at"],
            expires_at=row["expires_at"],
            state=LeaseState(row["state"]),
        )
