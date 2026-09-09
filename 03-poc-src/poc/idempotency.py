"""SQLite-backed idempotency store for mutating POC operations.

The store is persistence-only. It does not execute tools, contact Odoo, call
an LLM, authorize requests, or integrate with the audit chain. Keys are
server-calculated opaque identifiers; fingerprints bind the semantic request,
identity, tool name, and tool version deterministically.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from poc.db.init import DEFAULT_DB_PATH, initialize

RESERVED = "reserved"
REPLAYED = "replayed"
IN_PROGRESS = "in_progress"
CONFLICT = "conflict"
RECONCILIATION_REQUIRED = "reconciliation_required"

STATE_PENDING = "pending"
STATE_COMPLETED = "completed"
STATE_UNKNOWN = "unknown"
VALID_STATES = frozenset({STATE_PENDING, STATE_COMPLETED, STATE_UNKNOWN})

DEFINITIVE_ERROR = "definitive_error"
AMBIGUOUS = "ambiguous"
ADOPTED = "adopted"

IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
RECONCILIATION_REQUIRED_ERROR = "RECONCILIATION_REQUIRED"

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_TOOL_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "credentials",
        "password",
        "prompt",
        "messages",
        "secret",
        "token",
    }
)
_MAX_RESULT_BYTES = 64 * 1024


class IdempotencyStoreError(RuntimeError):
    """Raised when the persisted idempotency state cannot be used safely."""


class InvalidIdempotencyRequest(ValueError):
    """Raised for malformed or unsafe idempotency operation input."""


class IllegalIdempotencyTransition(IdempotencyStoreError):
    """Raised when a persisted state transition is not authorized by the design."""


@dataclass(frozen=True)
class IdempotencyRecord:
    tenant_id: str
    user_id: str
    idempotency_key: str
    request_fingerprint: str
    tool_name: str
    tool_version: str
    execution_id: str
    state: str
    result: Mapping[str, Any] | None
    external_record_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class IdempotencyOutcome:
    status: str
    error_code: str | None
    reason: str
    record: IdempotencyRecord


def canonical_json(value: Any) -> str:
    """Return stable JSON for JSON-compatible request semantics."""
    return json.dumps(
        _validate_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def compute_request_fingerprint(
    tenant_id: str,
    user_id: str,
    tool_name: str,
    tool_version: str,
    arguments: Mapping[str, Any],
) -> str:
    """Compute the SHA-256 request fingerprint used for conflict detection."""
    payload = {
        "arguments": _validate_json_value(arguments),
        "tenant_id": _identifier("tenant_id", tenant_id),
        "tool_name": _tool_name(tool_name),
        "tool_version": _tool_version(tool_version),
        "user_id": _identifier("user_id", user_id),
    }
    encoded = canonical_json(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_idempotency_key(
    tenant_id: str,
    user_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> str:
    """Derive the authoritative 32-hex content-addressable idempotency key."""
    material = "".join(
        (
            _identifier("tenant_id", tenant_id),
            _identifier("user_id", user_id),
            _tool_name(tool_name),
            canonical_json(arguments),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _validate_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidIdempotencyRequest("JSON object keys must be strings")
            result[key] = _validate_json_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_validate_json_value(item) for item in value]
    raise InvalidIdempotencyRequest("arguments must contain only JSON-compatible values")


def _identifier(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise InvalidIdempotencyRequest(f"{name} must be a non-empty bounded identifier")
    return value


def _tool_name(value: Any) -> str:
    if not isinstance(value, str) or not _TOOL_NAME_PATTERN.fullmatch(value):
        raise InvalidIdempotencyRequest("tool_name must be a bounded tool identifier")
    return value


def _tool_version(value: Any) -> str:
    if not isinstance(value, str) or not _TOOL_VERSION_PATTERN.fullmatch(value):
        raise InvalidIdempotencyRequest("tool_version must be a bounded version identifier")
    return value


def _key(value: Any) -> str:
    if not isinstance(value, str) or not _IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise InvalidIdempotencyRequest("idempotency_key must be a server-calculated 32-hex value")
    return value


def _fingerprint(value: Any) -> str:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise InvalidIdempotencyRequest("request_fingerprint must be a SHA-256 hex value")
    return value


def _execution_id(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidIdempotencyRequest("execution_id must be a UUID v4 string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise InvalidIdempotencyRequest("execution_id must be a UUID v4 string") from error
    if parsed.version != 4 or str(parsed) != value:
        raise InvalidIdempotencyRequest("execution_id must be a UUID v4 string")
    return value


def _external_record_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 256:
        raise InvalidIdempotencyRequest("external_record_id must be a bounded string")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validated_result(value: Any) -> str:
    if not isinstance(value, Mapping):
        raise InvalidIdempotencyRequest("result must be a mapping")
    if _contains_secret_key(value):
        raise InvalidIdempotencyRequest("result contains a forbidden secret or trace field")
    encoded = canonical_json(value).encode("utf-8")
    if len(encoded) > _MAX_RESULT_BYTES:
        raise InvalidIdempotencyRequest("result exceeds the bounded storage limit")
    return encoded.decode("utf-8")


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _SENSITIVE_KEYS or _contains_secret_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_key(item) for item in value)
    return False


def _record_from_row(row: sqlite3.Row) -> IdempotencyRecord:
    raw_result = row["result"]
    result: Mapping[str, Any] | None = None
    if raw_result is not None:
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError as error:
            raise IdempotencyStoreError("persisted idempotency result is invalid JSON") from error
        if not isinstance(parsed, dict):
            raise IdempotencyStoreError("persisted idempotency result is not an object")
        result = parsed
    state = row["state"]
    if state not in VALID_STATES:
        raise IdempotencyStoreError(f"unsupported persisted idempotency state: {state!r}")
    tool_name = row["tool_name"]
    tool_version = row["tool_version"]
    if not isinstance(tool_name, str) or not isinstance(tool_version, str):
        raise IdempotencyStoreError("persisted idempotency record lacks tool binding")
    return IdempotencyRecord(
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        idempotency_key=row["key"],
        request_fingerprint=row["request_hash"],
        tool_name=tool_name,
        tool_version=tool_version,
        execution_id=row["execution_id"],
        state=state,
        result=result,
        external_record_id=row["external_record_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _matches_request(
    record: IdempotencyRecord,
    request_fingerprint: str,
    tool_name: str,
    tool_version: str,
) -> bool:
    return (
        record.request_fingerprint == request_fingerprint
        and record.tool_name == tool_name
        and record.tool_version == tool_version
    )


class IdempotencyStore:
    """Transactional SQLite idempotency state machine."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.initialize()

    def initialize(self) -> None:
        initialize(self.db_path)
        self._ensure_tool_binding_columns()

    def reserve(
        self,
        idempotency_key: str,
        request_fingerprint: str,
        tool_name: str,
        tool_version: str,
        tenant_id: str,
        user_id: str,
    ) -> IdempotencyOutcome:
        key = _key(idempotency_key)
        fingerprint = _fingerprint(request_fingerprint)
        tool_name = _tool_name(tool_name)
        tool_version = _tool_version(tool_version)
        tenant_id = _identifier("tenant_id", tenant_id)
        user_id = _identifier("user_id", user_id)
        execution_id = str(uuid.uuid4())
        now = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO idempotency_keys
                    (
                        tenant_id, user_id, key, request_hash, execution_id,
                        state, result, external_record_id, tool_name, tool_version,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        user_id,
                        key,
                        fingerprint,
                        execution_id,
                        STATE_PENDING,
                        tool_name,
                        tool_version,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM idempotency_keys
                    WHERE tenant_id = ? AND user_id = ? AND key = ?
                    """,
                    (tenant_id, user_id, key),
                ).fetchone()
                if row is None:
                    raise IdempotencyStoreError("idempotency reservation row disappeared")
                record = _record_from_row(row)
                if inserted.rowcount == 1:
                    connection.commit()
                    return IdempotencyOutcome(RESERVED, None, "new reservation owned", record)

                if not _matches_request(record, fingerprint, tool_name, tool_version):
                    outcome = IdempotencyOutcome(
                        CONFLICT,
                        IDEMPOTENCY_CONFLICT,
                        "idempotency key is bound to a different request",
                        record,
                    )
                elif record.state == STATE_PENDING:
                    outcome = IdempotencyOutcome(
                        IN_PROGRESS,
                        IDEMPOTENCY_CONFLICT,
                        "request is already in progress",
                        record,
                    )
                elif record.state == STATE_COMPLETED:
                    outcome = IdempotencyOutcome(
                        REPLAYED,
                        None,
                        "completed result replayed",
                        record,
                    )
                elif record.state == STATE_UNKNOWN:
                    outcome = IdempotencyOutcome(
                        RECONCILIATION_REQUIRED,
                        RECONCILIATION_REQUIRED_ERROR,
                        "ambiguous outcome requires external reconciliation",
                        record,
                    )
                else:
                    raise IdempotencyStoreError("unsupported idempotency state")
                connection.commit()
                return outcome
            except Exception:
                connection.rollback()
                raise

    def complete(
        self,
        idempotency_key: str,
        request_fingerprint: str,
        tool_name: str,
        tool_version: str,
        tenant_id: str,
        user_id: str,
        execution_id: str,
        result: Mapping[str, Any],
        external_record_id: str | None = None,
        reconciliation: str | None = None,
    ) -> IdempotencyRecord:
        key = _key(idempotency_key)
        fingerprint = _fingerprint(request_fingerprint)
        tool_name = _tool_name(tool_name)
        tool_version = _tool_version(tool_version)
        tenant_id = _identifier("tenant_id", tenant_id)
        user_id = _identifier("user_id", user_id)
        execution_id = _execution_id(execution_id)
        external_record_id = _external_record_id(external_record_id)
        encoded_result = _validated_result(result)
        now = _utc_now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._locked_record(
                    connection,
                    key,
                    request_fingerprint=fingerprint,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                if record.execution_id != execution_id:
                    raise IllegalIdempotencyTransition(
                        "completion execution_id does not match the reservation"
                    )
                if record.state == STATE_PENDING:
                    if reconciliation is not None:
                        raise IllegalIdempotencyTransition(
                            "a pending reservation cannot be marked as reconciled"
                        )
                    state = STATE_COMPLETED
                elif record.state == STATE_UNKNOWN:
                    if reconciliation != ADOPTED:
                        raise IllegalIdempotencyTransition(
                            "an unknown outcome can complete only through adopted reconciliation"
                        )
                    if external_record_id is None:
                        raise InvalidIdempotencyRequest(
                            "adopted reconciliation requires external_record_id"
                        )
                    state = STATE_COMPLETED
                else:
                    raise IllegalIdempotencyTransition(
                        f"completion is not allowed from state {record.state!r}"
                    )

                connection.execute(
                    """
                    UPDATE idempotency_keys
                    SET state = ?, result = ?, external_record_id = ?, updated_at = ?
                    WHERE tenant_id = ? AND user_id = ? AND key = ?
                    """,
                    (
                        state,
                        encoded_result,
                        external_record_id,
                        now,
                        tenant_id,
                        user_id,
                        key,
                    ),
                )
                updated = self._locked_record(
                    connection,
                    key,
                    request_fingerprint=fingerprint,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise

    def fail(
        self,
        idempotency_key: str,
        request_fingerprint: str,
        tool_name: str,
        tool_version: str,
        tenant_id: str,
        user_id: str,
        execution_id: str,
        outcome: str,
        error_code: str,
    ) -> IdempotencyRecord:
        if outcome == DEFINITIVE_ERROR:
            if not isinstance(error_code, str) or not error_code or len(error_code) > 128:
                raise InvalidIdempotencyRequest("error_code must be a bounded string")
            return self.complete(
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                tool_name=tool_name,
                tool_version=tool_version,
                tenant_id=tenant_id,
                user_id=user_id,
                execution_id=execution_id,
                result={"status": "error", "error_code": error_code},
            )
        if outcome != AMBIGUOUS:
            raise InvalidIdempotencyRequest("outcome must be definitive_error or ambiguous")

        key = _key(idempotency_key)
        fingerprint = _fingerprint(request_fingerprint)
        tool_name = _tool_name(tool_name)
        tool_version = _tool_version(tool_version)
        tenant_id = _identifier("tenant_id", tenant_id)
        user_id = _identifier("user_id", user_id)
        execution_id = _execution_id(execution_id)
        if not isinstance(error_code, str) or not error_code or len(error_code) > 128:
            raise InvalidIdempotencyRequest("error_code must be a bounded string")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._locked_record(
                    connection,
                    key,
                    request_fingerprint=fingerprint,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                if record.execution_id != execution_id:
                    raise IllegalIdempotencyTransition(
                        "failure execution_id does not match the reservation"
                    )
                if record.state != STATE_PENDING:
                    raise IllegalIdempotencyTransition(
                        f"ambiguous failure is not allowed from state {record.state!r}"
                    )
                connection.execute(
                    """
                    UPDATE idempotency_keys
                    SET state = ?, result = NULL, updated_at = ?
                    WHERE tenant_id = ? AND user_id = ? AND key = ?
                    """,
                    (STATE_UNKNOWN, _utc_now(), tenant_id, user_id, key),
                )
                updated = self._locked_record(
                    connection,
                    key,
                    request_fingerprint=fingerprint,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise

    def get(
        self,
        idempotency_key: str,
        tenant_id: str,
        user_id: str,
    ) -> IdempotencyRecord | None:
        key = _key(idempotency_key)
        tenant_id = _identifier("tenant_id", tenant_id)
        user_id = _identifier("user_id", user_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM idempotency_keys
                WHERE tenant_id = ? AND user_id = ? AND key = ?
                """,
                (tenant_id, user_id, key),
            ).fetchone()
        return None if row is None else _record_from_row(row)

    def release(
        self,
        idempotency_key: str,
        tenant_id: str,
        user_id: str,
        execution_id: str,
    ) -> bool:
        """Delete a pending reservation so an identical request can be retried.

        Used when a confirmation is declined or expires before execution; only
        the reservation owner may release, and only while still pending.
        """
        key = _key(idempotency_key)
        tenant_id = _identifier("tenant_id", tenant_id)
        user_id = _identifier("user_id", user_id)
        execution_id = _execution_id(execution_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    DELETE FROM idempotency_keys
                    WHERE tenant_id = ? AND user_id = ? AND key = ?
                      AND execution_id = ? AND state = ?
                    """,
                    (tenant_id, user_id, key, execution_id, STATE_PENDING),
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_tool_binding_columns(self) -> None:
        with self._connect() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(idempotency_keys)")}
            if not {"tool_name", "tool_version"}.issubset(columns):
                connection.execute("BEGIN IMMEDIATE")
                try:
                    refreshed = {
                        row[1] for row in connection.execute("PRAGMA table_info(idempotency_keys)")
                    }
                    if "tool_name" not in refreshed:
                        connection.execute(
                            "ALTER TABLE idempotency_keys ADD COLUMN tool_name TEXT"
                        )
                    if "tool_version" not in refreshed:
                        connection.execute(
                            "ALTER TABLE idempotency_keys ADD COLUMN tool_version TEXT"
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

    def _locked_record(
        self,
        connection: sqlite3.Connection,
        key: str,
        *,
        request_fingerprint: str,
        tool_name: str,
        tool_version: str,
        tenant_id: str,
        user_id: str,
    ) -> IdempotencyRecord:
        row = connection.execute(
            """
            SELECT * FROM idempotency_keys
            WHERE tenant_id = ? AND user_id = ? AND key = ?
            """,
            (tenant_id, user_id, key),
        ).fetchone()
        if row is None:
            raise IdempotencyStoreError("idempotency record not found")
        record = _record_from_row(row)
        if not _matches_request(record, request_fingerprint, tool_name, tool_version):
            raise IdempotencyStoreError("idempotency request binding mismatch")
        return record
