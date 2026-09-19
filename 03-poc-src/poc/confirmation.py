"""Deterministic confirmation lifecycle state machine for the POC.

Manages proposal creation, binding, approval, expiry, and revalidation.
All state transitions are atomic via SQLite BEGIN IMMEDIATE. Performs no
Odoo execution, no LLM calls, and no agent runtime work.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from poc.authz import CONFIRMATION_REQUIRED, PolicyEngine
from poc.db.init import DEFAULT_DB_PATH
from poc.tool_contracts import ToolRegistry, get_registry
from poc.errors import translate_error
from poc.idempotency import IdempotencyStore, compute_idempotency_key

CONFIRMATION_EXPIRY_SECONDS = 300

PROPOSED = "proposed"
CONFIRMED = "confirmed"
EXECUTING = "executing"
COMPLETED = "completed"
FAILED = "failed"

APPROVED = "approved"
ALREADY_CONFIRMED = "already_confirmed"
EXPIRED = "expired"
REPLAY = "replay"
HASH_MISMATCH = "hash_mismatch"
NOT_FOUND = "not_found"
IDENTITY_MISMATCH = "identity_mismatch"
TENANT_MISMATCH = "tenant_mismatch"
TOOL_MISSING = "tool_missing"
VERSION_MISMATCH = "version_mismatch"
POLICY_DENIED = "policy_denied"
NOT_CONFIRMATION_REQUIRED = "not_confirmation_required"
DECLINED = "declined"

_PERM_DENIED = "PERMISSION_DENIED"
_EXPIRED_CODE = "CONFIRMATION_EXPIRED"
_REPLAY_CODE = "CONFIRMATION_REPLAY"
_HASH_CODE = "CONFIRMATION_HASH_MISMATCH"
_TOOL_CODE = "TOOL_NOT_FOUND"
_VERSION_CODE = "TOOL_VERSION_MISMATCH"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_operation_hash(
    tool_name: str,
    tool_version: str,
    arguments: dict,
    user_id: str,
    tenant_id: str,
    created_at: str,
) -> str:
    """Compute the normative operation hash per SECURITY_MODEL.md §6."""
    payload = _canonical_json(
        {
            "tool_name": tool_name,
            "tool_version": tool_version,
            "arguments": arguments,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "created_at": created_at,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    tool_name: str
    tool_version: str
    arguments: dict
    operation_hash: str
    user_id: str
    tenant_id: str
    state: str
    created_at: str
    expires_at: str
    confirmed_at: str | None = None
    executed_at: str | None = None


@dataclass(frozen=True)
class ApprovalResult:
    status: str
    error_code: str | None
    reason: str
    proposal_id: str
    tool_name: str
    tool_version: str
    user_id: str
    tenant_id: str
    operation_hash: str
    state: str
    idempotency_key: str | None = None
    execution_id: str | None = None
    arguments: dict | None = None

    @property
    def structured_error(self):
        return translate_error(self.error_code, self.reason)


@dataclass(frozen=True)
class CreationResult:
    status: str
    proposal: Proposal | None
    error_code: str | None
    reason: str
    idempotency_key: str | None = None

    @property
    def structured_error(self):
        return translate_error(self.error_code, self.reason)


class ConfirmationStore:
    """Single server-side confirmation state machine boundary."""

    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        registry: ToolRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        expiry_seconds: int | None = None,
    ) -> None:
        self.db_path = db_path
        self.registry = registry or get_registry()
        self.policy_engine = policy_engine or PolicyEngine()
        # Operators may shorten/lengthen the signature window at runtime; the
        # default stays the pinned design constant so audits remain comparable.
        self.expiry_seconds = int(expiry_seconds) if expiry_seconds else CONFIRMATION_EXPIRY_SECONDS

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def create_proposal(
        self,
        tool_name: str,
        tool_version: str,
        arguments: dict,
        user_id: str,
        tenant_id: str,
    ) -> CreationResult:
        """Create a proposal only for confirmation-required tools with allowed authz."""
        try:
            contract = self.registry.get(tool_name)
        except KeyError:
            return CreationResult(status=NOT_FOUND, proposal=None, error_code=_TOOL_CODE, reason="unknown_tool")
        if contract["tool_version"] != tool_version:
            return CreationResult(status=VERSION_MISMATCH, proposal=None, error_code=_VERSION_CODE, reason="version_mismatch")
        if contract["readOnly"] is True and not contract["requiresConfirmation"]:
            return CreationResult(status=NOT_CONFIRMATION_REQUIRED, proposal=None, error_code=NOT_CONFIRMATION_REQUIRED, reason="read_only_tool_no_confirmation")
        decision = self.policy_engine.evaluate(
            {"user_id": user_id, "tenant_id": tenant_id, "tool_name": tool_name, "tool_version": tool_version}
        )
        if decision.decision != CONFIRMATION_REQUIRED:
            return CreationResult(status=POLICY_DENIED, proposal=None, error_code=_PERM_DENIED, reason=decision.reason)
        created_at = _utc_now()
        operation_hash = compute_operation_hash(tool_name, tool_version, arguments, user_id, tenant_id, created_at)
        proposal_id = str(uuid.uuid4())
        idempotency_key = compute_idempotency_key(tenant_id, user_id, tool_name, arguments)
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.expiry_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO proposals (proposal_id, tool_name, tool_version, arguments, operation_hash, user_id, tenant_id, state, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (proposal_id, tool_name, tool_version, json.dumps(arguments, ensure_ascii=False), operation_hash, user_id, tenant_id, PROPOSED, created_at, expires_at),
            )
            conn.commit()
        finally:
            conn.close()
        proposal = Proposal(
            proposal_id=proposal_id, tool_name=tool_name, tool_version=tool_version,
            arguments=arguments, operation_hash=operation_hash, user_id=user_id,
            tenant_id=tenant_id, state=PROPOSED, created_at=created_at, expires_at=expires_at,
        )
        return CreationResult(status=APPROVED, proposal=proposal, error_code=None, reason="proposal_created")

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return Proposal(
            proposal_id=row["proposal_id"], tool_name=row["tool_name"],
            tool_version=row["tool_version"], arguments=json.loads(row["arguments"]),
            operation_hash=row["operation_hash"], user_id=row["user_id"],
            tenant_id=row["tenant_id"], state=row["state"],
            created_at=row["created_at"], expires_at=row["expires_at"],
            confirmed_at=row["confirmed_at"], executed_at=row["executed_at"],
        )

    def decline(self, proposal_id: str, user_id: str, tenant_id: str) -> ApprovalResult:
        """Atomically deny a proposed operation and preserve proposal binding."""
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            return ApprovalResult(
                status=NOT_FOUND,
                error_code=NOT_FOUND,
                reason="proposal_not_found",
                proposal_id=proposal_id,
                tool_name="",
                tool_version="",
                user_id=user_id,
                tenant_id=tenant_id,
                operation_hash="",
                state="",
            )

        def _fail(status: str, code: str, reason: str) -> ApprovalResult:
            return ApprovalResult(
                status=status,
                error_code=code,
                reason=reason,
                proposal_id=proposal.proposal_id,
                tool_name=proposal.tool_name,
                tool_version=proposal.tool_version,
                user_id=user_id,
                tenant_id=tenant_id,
                operation_hash=proposal.operation_hash,
                state=proposal.state,
            )

        if user_id != proposal.user_id:
            return _fail(IDENTITY_MISMATCH, _PERM_DENIED, "user_identity_mismatch")
        if tenant_id != proposal.tenant_id:
            return _fail(TENANT_MISMATCH, _PERM_DENIED, "tenant_mismatch")
        if proposal.state != PROPOSED:
            return _fail(REPLAY, _REPLAY_CODE, f"proposal_already_{proposal.state}")

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE proposals SET state = ? WHERE proposal_id = ? AND state = ?",
                (FAILED, proposal.proposal_id, PROPOSED),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                return _fail(REPLAY, _REPLAY_CODE, "concurrent_confirmation_detected")
            conn.commit()
        finally:
            conn.close()

        return ApprovalResult(
            status=DECLINED,
            error_code=None,
            reason="proposal_declined_by_user",
            proposal_id=proposal.proposal_id,
            tool_name=proposal.tool_name,
            tool_version=proposal.tool_version,
            user_id=proposal.user_id,
            tenant_id=proposal.tenant_id,
            operation_hash=proposal.operation_hash,
            state=FAILED,
            idempotency_key=compute_idempotency_key(
                proposal.tenant_id, proposal.user_id, proposal.tool_name, proposal.arguments
            ),
            arguments=proposal.arguments,
        )

    def approve(self, proposal_id: str, user_id: str, tenant_id: str) -> ApprovalResult:
        """Perform the full 9-point re-authorization checklist then atomically confirm."""
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            return ApprovalResult(status=NOT_FOUND, error_code=NOT_FOUND, reason="proposal_not_found", proposal_id=proposal_id, tool_name="", tool_version="", user_id=user_id, tenant_id=tenant_id, operation_hash="", state="")

        def _fail(status: str, code: str, reason: str) -> ApprovalResult:
            return ApprovalResult(status=status, error_code=code, reason=reason, proposal_id=proposal.proposal_id, tool_name=proposal.tool_name, tool_version=proposal.tool_version, user_id=user_id, tenant_id=tenant_id, operation_hash=proposal.operation_hash, state=proposal.state)

        if user_id != proposal.user_id:
            return _fail(IDENTITY_MISMATCH, _PERM_DENIED, "user_identity_mismatch")
        if tenant_id != proposal.tenant_id:
            return _fail(TENANT_MISMATCH, _PERM_DENIED, "tenant_mismatch")
        try:
            contract = self.registry.get(proposal.tool_name)
        except KeyError:
            return _fail(TOOL_MISSING, _TOOL_CODE, "tool_removed_from_registry")
        if contract["tool_version"] != proposal.tool_version:
            return _fail(VERSION_MISMATCH, _VERSION_CODE, "tool_version_changed_since_proposal")
        decision = self.policy_engine.evaluate(
            {"user_id": user_id, "tenant_id": tenant_id, "tool_name": proposal.tool_name, "tool_version": proposal.tool_version}
        )
        if decision.decision == "denied":
            return _fail(POLICY_DENIED, _PERM_DENIED, "policy_revoked_since_proposal")
        if proposal.state != PROPOSED:
            return _fail(REPLAY, _REPLAY_CODE, f"proposal_already_{proposal.state}")
        now = datetime.now(timezone.utc)
        expires = datetime.strptime(proposal.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if now >= expires:
            return _fail(EXPIRED, _EXPIRED_CODE, "proposal_expired")
        recomputed = compute_operation_hash(proposal.tool_name, proposal.tool_version, proposal.arguments, proposal.user_id, proposal.tenant_id, proposal.created_at)
        if recomputed != proposal.operation_hash:
            return _fail(HASH_MISMATCH, _HASH_CODE, "operation_hash_mismatch_tampered_arguments")
        confirmed_at = _utc_now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE proposals SET state = ?, confirmed_at = ? WHERE proposal_id = ? AND state = ?",
                (CONFIRMED, confirmed_at, proposal.proposal_id, PROPOSED),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                return _fail(REPLAY, _REPLAY_CODE, "concurrent_confirmation_detected")
            conn.commit()
        finally:
            conn.close()
        record = IdempotencyStore(self.db_path).get(
            compute_idempotency_key(tenant_id, user_id, proposal.tool_name, proposal.arguments),
            tenant_id,
            user_id,
        )
        return ApprovalResult(
            status=APPROVED,
            error_code=None,
            reason="confirmed_atomically",
            proposal_id=proposal.proposal_id,
            tool_name=proposal.tool_name,
            tool_version=proposal.tool_version,
            user_id=user_id,
            tenant_id=tenant_id,
            operation_hash=proposal.operation_hash,
            state=CONFIRMED,
            idempotency_key=compute_idempotency_key(tenant_id, user_id, proposal.tool_name, proposal.arguments),
            execution_id=record.execution_id if record is not None else None,
            arguments=proposal.arguments,
        )
