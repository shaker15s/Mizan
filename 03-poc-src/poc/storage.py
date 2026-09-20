"""Phase 11 — Production Data Layer.

Repository abstraction so Mizan can run on SQLite (single-node POC / pilot)
today, and on PostgreSQL (multi-node production) later without rewiring the
rest of the codebase. The plan explicitly says PostgreSQL/Redis are only to
be introduced when justified by operational evidence, so we formalise the
interface now and ship the SQLite backend as the default.

Design:
- ``Storage`` is a small, explicit interface: connect, migrate, and access
  audit / proposals / idempotency / sessions repositories.
- ``SQLiteStorage`` wraps the existing stores (audit_store / confirmation /
  idempotency / sessions) behind a single lifecycle manager. Each store
  already self-initialises its own schema idempotently on construction.
- ``open_storage()`` is the single entry point used by bootstrap.py.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator, Protocol, Self

from . import audit_store, confirmation, idempotency
from . import sessions as _sessions

LOGGER = logging.getLogger("mizan.storage")


class AuditRepository(Protocol):
    def append(self, *args: Any, **kwargs: Any) -> Any: ...
    def verify_chain(self) -> dict[str, Any]: ...
    def rows(self, *args: Any, **kwargs: Any) -> list[Any]: ...


class ProposalRepository(Protocol):
    def issue(self, *args: Any, **kwargs: Any) -> Any: ...
    def get(self, proposal_id: str) -> Any: ...
    def confirm(self, proposal_id: str, *args: Any, **kwargs: Any) -> Any: ...
    def decline(self, proposal_id: str) -> Any: ...


class IdempotencyRepository(Protocol):
    def get(self, key: str, *args: Any, **kwargs: Any) -> Any: ...
    def put(self, *args: Any, **kwargs: Any) -> None: ...


class SessionRepository(Protocol):
    def get_or_create(self, *args: Any, **kwargs: Any) -> Any: ...
    def append(self, *args: Any, **kwargs: Any) -> None: ...
    def history(self, *args: Any, **kwargs: Any) -> list[Any]: ...


class Storage(Protocol):
    audit: AuditRepository
    proposals: ProposalRepository
    idempotency: IdempotencyRepository
    sessions: SessionRepository

    def migrate(self) -> None: ...


class SQLiteStorage:
    """Single-file storage backend. Safe for single-node pilot deploys."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> Self:
        with self._lock:
            if self._conn is not None:
                return self
            conn = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._conn = conn
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_migrations (
                    version     TEXT PRIMARY KEY,
                    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    description TEXT NOT NULL
                )
                """
            )
            # Constructors below self-initialise their own tables idempotently.
            self.audit = audit_store.AuditStore(self.db_path)
            self.proposals = confirmation.ConfirmationStore(self.db_path)
            self.idempotency = idempotency.IdempotencyStore(self.db_path)
            self.sessions = _sessions.SessionStore()
            self._record("001", "stores initialised (self-migrated)")
            LOGGER.info("SQLite storage opened: %s", self.db_path)
            return self

    def _record(self, version: str, description: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR IGNORE INTO _schema_migrations(version, description) VALUES (?, ?)",
            (version, description),
        )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def migrate(self) -> None:
        """Hook for future explicit migrations. Existing stores auto-migrate."""
        return None

    def transaction(self) -> Iterator[sqlite3.Connection]:
        assert self._conn is not None, "connect() first"
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    audit: AuditRepository
    proposals: ProposalRepository
    idempotency: IdempotencyRepository
    sessions: SessionRepository


def open_storage(db_path: Path | str) -> SQLiteStorage:
    """Open and migrate the default (SQLite) storage backend."""
    return SQLiteStorage(db_path).connect()


__all__ = [
    "Storage",
    "SQLiteStorage",
    "open_storage",
]
