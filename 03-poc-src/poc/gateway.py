"""Deterministic Tool Gateway orchestration boundary for the POC.

The gateway validates a structured request, resolves the server-owned tool
contract, validates arguments, evaluates authorization, applies idempotency
state for mutating tools, preserves confirmation semantics, and writes audit
records. It performs no Odoo execution, LLM calls, confirmation execution, or
agent runtime work.
"""

from __future__ import annotations

import json
import hashlib
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from poc.audit_store import AuditStore
from poc.authz import ALLOWED, PolicyEngine
from poc.confirmation import (
    ApprovalResult,
    ConfirmationStore,
    DECLINED,
    EXPIRED,
    NOT_FOUND,
    REPLAY,
)
from poc.db.init import DEFAULT_DB_PATH
from poc.idempotency import (
    AMBIGUOUS,
    CONFLICT,
    IDEMPOTENCY_CONFLICT,
    IdempotencyStore,
    IdempotencyStoreError,
    IllegalIdempotencyTransition,
    RECONCILIATION_REQUIRED,
    RECONCILIATION_REQUIRED_ERROR,
    IN_PROGRESS,
    REPLAYED,
    RESERVED,
    compute_idempotency_key,
    compute_request_fingerprint,
)
from poc.normalization import normalize_arabic
from poc.tool_contracts import ToolRegistry, get_registry
from poc.errors import (
    ENTITY_NOT_FOUND,
    StructuredError,
    translate_error,
    translate_odoo_exception,
)
from poc.verification import verify_sales_order_creation
from poc.execution import (
    Action,
    Actor,
    ExecutionState,
    ExecutionStore,
    RiskAssessment,
    assess_risk,
)
from poc.execution.state_machine import (
    ExecutionEvent,
    ExecutionStage,
    ExecutionStatus,
    SecurityStatus,
    INITIAL_STATE,
    project_to_gateway_status,
    new_execution_identity,
    state_machine_version,
)
from poc.execution.lease import ExecutionLease, LeaseAlreadyHeldError, LeaseError, LeaseNotOwnedError, LeaseState, LeaseStore, LEASE_ACTIVE, LEASE_COMPLETED, LEASE_EXPIRED, LEASE_RELEASED
from poc.evidence import EvidenceStore, EvidenceType
from poc.policy_engine2 import PolicyDecision2, PolicyEngine2

ACCEPTED = "accepted"
DENIED = "denied"
VALIDATION_ERROR = "validation_error"
CONFIRMATION_REQUIRED = "confirmation_required"
REPLAY = "replay"
DECLINED = "declined"

INVALID_REQUEST = "INVALID_REQUEST"
UNKNOWN_TOOL = "UNKNOWN_TOOL"
UNSUPPORTED_TOOL_VERSION = "UNSUPPORTED_TOOL_VERSION"
INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
POLICY_DENIED = "POLICY_DENIED"
CONFIRMATION_REQUIRED_ERROR = "CONFIRMATION_REQUIRED"
IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
AUDIT_WRITE_FAILED = "AUDIT_WRITE_FAILED"
CONFIRMATION_DECLINED = "CONFIRMATION_DECLINED"

_REQUEST_FIELDS = frozenset(
    {
        "request_id",
        "user_id",
        "tenant_id",
        "tool_name",
        "tool_version",
        "arguments",
        "idempotency_key",
    }
)
_REQUIRED_REQUEST_FIELDS = frozenset(
    {
        "request_id",
        "user_id",
        "tenant_id",
        "tool_name",
        "tool_version",
        "arguments",
    }
)
_IDEMPOTENCY_KEY_PATTERN = "^[0-9a-f]{32}$"
_REQUEST_ID_PATTERN = "^[A-Za-z0-9_.:@-]{1,128}$"


@dataclass(frozen=True)
class ToolGatewayRequest:
    """Strict, authenticated tool invocation envelope."""

    request_id: str
    user_id: str
    tenant_id: str
    tool_name: str
    tool_version: str
    arguments: Mapping[str, Any]
    idempotency_key: str | None = None

    @classmethod
    def from_mapping(cls, request: Mapping[str, Any]) -> "ToolGatewayRequest":
        if not isinstance(request, dict):
            raise InvalidGatewayRequest("request must be a mapping")
        missing = _REQUIRED_REQUEST_FIELDS - set(request)
        if missing:
            raise InvalidGatewayRequest("request has missing required fields")
        unknown = set(request) - _REQUEST_FIELDS
        if unknown:
            raise InvalidGatewayRequest("request has unsupported fields")
        return cls(
            request_id=request["request_id"],
            user_id=request["user_id"],
            tenant_id=request["tenant_id"],
            tool_name=request["tool_name"],
            tool_version=request["tool_version"],
            arguments=request["arguments"],
            idempotency_key=request.get("idempotency_key"),
        )


@dataclass(frozen=True)
class GatewayResult:
    """Immutable, machine-readable gateway decision."""

    status: str
    error_code: str | None
    reason: str
    request_id: str
    user_id: str
    tenant_id: str
    tool_name: str
    tool_version: str
    policy_decision: str
    idempotency_key: str | None
    execution_id: str | None
    result: Mapping[str, Any] | None
    audit_id: int | None
    requires_confirmation: bool
    proposal: Mapping[str, Any] | None = None
    structured_error_override: StructuredError | None = None

    @property
    def structured_error(self):
        return self.structured_error_override or translate_error(self.error_code, self.reason)


class InvalidGatewayRequest(ValueError):
    """Raised only by request parsing; never returned as the primary result."""


@dataclass
class _Context:
    record: ToolGatewayRequest
    tool_call_id: str
    policy_decision: str = "denied"
    policy_version: str | None = None
    policy_hash: str | None = None
    idempotency_key: str | None = None
    execution_id: str | None = None
    execution: ExecutionState | None = None
    last_evidence_id: str | None = None
    replay_result: Mapping[str, Any] | None = None
    error_code: str | None = None
    requires_confirmation: bool = False
    odoo_client: Any = None
    risk: RiskAssessment | None = None
    lease_id: str | None = None

    def ev(self, gateway: "ToolGateway", event_type: EvidenceType, payload: Mapping[str, Any]) -> str:
        """Append an evidence event, chaining it to the last event in this context."""
        evt = gateway.evidence_store.append(
            event_type=event_type,
            payload=dict(payload),
            trace_id=self.record.request_id,
            execution_id=self.execution_id,
            parent_event_id=self.last_evidence_id,
            actor_id=self.record.user_id,
            tenant_id=self.record.tenant_id,
            policy_version=getattr(self, "policy_version", None),
            policy_hash=getattr(self, "policy_hash", None),
            tool_name=self.record.tool_name,
            tool_version=self.record.tool_version,
            tool_call_id=self.tool_call_id,
        )
        self.last_evidence_id = evt.evidence_id
        return evt.evidence_id

    def apply(self, gateway: "ToolGateway", event: ExecutionEvent, *, reason: str = "", persist: bool = True) -> None:
        """Apply a canonical state transition, persist it, and emit STATE_TRANSITION evidence."""
        if self.execution is None:
            return
        prev_stage = self.execution.stage.value
        next_state = self.execution.apply(event, actor=self.record.user_id, reason=reason)
        self.execution = next_state
        self.ev(gateway, EvidenceType.STATE_TRANSITION, {
            "event": event.value,
            "from_stage": prev_stage,
            "to_stage": next_state.stage.value,
            "status": next_state.status.value,
            "security": next_state.security.value,
            "final": next_state.final.value if next_state.final else None,
            "reason": reason,
            "state_machine_version": state_machine_version(),
        })
        if persist:
            gateway.execution_store.save(next_state)


def _audit_provenance(receipt: Any) -> dict[str, Any]:
    """Traceability fields a caller needs to point at the audit row it created.

    The cockpit shows these on the executed card, and an operator reconciling a
    write by hand starts from exactly this pair (audit id + verify request id).
    """
    if not isinstance(receipt, Mapping):
        return {}
    out: dict[str, Any] = {}
    audit_id = receipt.get("audit_id")
    if isinstance(audit_id, int) and not isinstance(audit_id, bool):
        out["audit_id"] = audit_id
    request_id = receipt.get("request_id")
    if isinstance(request_id, str) and request_id:
        out["request_id"] = request_id
    return out

class ToolGateway:
    """Single server-side authorization and control-plane boundary."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        policy_engine2: PolicyEngine2 | None = None,
        idempotency_store: IdempotencyStore | None = None,
        audit_store: AuditStore | None = None,
        confirmation_store: ConfirmationStore | None = None,
        execution_store: ExecutionStore | None = None,
        evidence_store: EvidenceStore | None = None,
        lease_store: LeaseStore | None = None,
        db_path: Path = DEFAULT_DB_PATH,
        confirm_ttl_seconds: int | None = None,
        lease_ttl_seconds: int = 300,
        lease_heartbeat_seconds: int = 60,
    ) -> None:
        self.registry = registry or get_registry()
        self.policy_engine = policy_engine or PolicyEngine()
        self.policy_engine2 = policy_engine2 or PolicyEngine2()
        self.idempotency_store = idempotency_store or IdempotencyStore(db_path)
        self.audit_store = audit_store or AuditStore(db_path)
        self.confirmation_store = confirmation_store or ConfirmationStore(
            db_path=db_path,
            registry=self.registry,
            policy_engine=self.policy_engine,
            expiry_seconds=confirm_ttl_seconds,
        )
        self.execution_store = execution_store or ExecutionStore(db_path)
        self.evidence_store = evidence_store or EvidenceStore(db_path)
        self.lease_store = lease_store or LeaseStore(
            db_path=db_path,
            ttl_seconds=lease_ttl_seconds,
        )
        self.lease_heartbeat_seconds = int(lease_heartbeat_seconds)
        self.audit_store.initialize()
        # Evict any leases left dangling by a previous process (fail-safe on boot).
        try:
            self.lease_store.expire_stale()
        except Exception:
            pass

    def _build_odoo_payload(self, arguments: Mapping[str, Any], idempotency_key: str) -> list[dict[str, Any]]:
        order_lines = [
            [0, 0, {"product_id": line["product_id"], "product_uom_qty": line["quantity"]}]
            for line in arguments["lines"]
        ]
        return [
            {
                "partner_id": arguments["customer_id"],
                "client_order_ref": idempotency_key,
                "order_line": order_lines,
            }
        ]

    def _verification_success(self, arguments: Mapping[str, Any], record: Any, order_id: int) -> Any:
        result = {
            "status": "success",
            "verified": True,
            "order_id": order_id,
            "customer_id": arguments["customer_id"],
            "lines": [dict(line) for line in arguments["lines"]],
        }
        return self._verification_outcome("accepted", None, "verified_sales_order_created", result, str(order_id))

    def _verification_failure(
        self,
        arguments: Mapping[str, Any],
        record: Any,
        reason: str,
        external_record_id: str | None = None,
        error_code: str = "VERIFICATION_FAILED",
    ) -> Any:
        return self._verification_outcome(
            "error",
            error_code,
            reason,
            None,
            external_record_id=external_record_id,
        )

    def _odoo_read_failure(self, arguments: Mapping[str, Any], record: Any, structured: Any) -> Any:
        return self._verification_outcome("error", structured.code, structured.message, None, structured_error=structured)

    def _odoo_write_failure(self, arguments: Mapping[str, Any], record: Any, structured: Any) -> Any:
        return self._verification_outcome("error", structured.code, structured.message, None, structured_error=structured)

    def _verification_outcome(
        self,
        status: str,
        error_code: str | None,
        reason: str,
        result: Mapping[str, Any] | None,
        external_record_id: str | None = None,
        structured_error: StructuredError | None = None,
    ) -> Any:
        return {
            "status": status,
            "error_code": error_code,
            "reason": reason,
            "result": result,
            "external_record_id": external_record_id,
            "reconciliation": None,
            "structured_error": structured_error,
        }

    def _verification_audit_record(
        self,
        record: Any,
        arguments: Mapping[str, Any],
        verification: Any,
    ) -> dict[str, Any]:
        return {
            "request_id": f"verify-{record.execution_id}",
            "tool_call_id": f"verify-{record.execution_id}",
            "tenant_id": record.tenant_id,
            "user_id": record.user_id,
            "odoo_user": record.user_id,
            "tool_name": record.tool_name,
            "tool_version": record.tool_version,
            "arguments": dict(arguments),
            "policy_decision": "confirmation_required",
            "proposal_id": None,
            "operation_hash": None,
            "approval_id": None,
            "start_time": _utc_now(),
            "end_time": _utc_now(),
            "result_status": verification["status"],
            "error_code": verification["error_code"],
            "external_system": "odoo",
            "external_record_id": verification["external_record_id"],
            "idempotency_key": record.idempotency_key,
            "execution_id": record.execution_id,
        }

    def _empty_result(
        self,
        *,
        status: str,
        error_code: str | None,
        reason: str,
        result: Mapping[str, Any] | None = None,
        structured_error: StructuredError | None = None,
        idempotency_key: str | None = None,
        user_id: str = "",
        tenant_id: str = "",
        execution_id: str | None = None,
        audit_id: int | None = None,
        request_id: str = "",
    ) -> GatewayResult:
        return GatewayResult(
            status=status,
            error_code=error_code,
            reason=reason,
            request_id=request_id,
            user_id=user_id,
            tenant_id=tenant_id,
            tool_name="sales.order.create",
            tool_version=self.registry.get("sales.order.create")["tool_version"],
            policy_decision="confirmation_required",
            idempotency_key=idempotency_key,
            execution_id=execution_id,
            result=result,
            audit_id=audit_id,
            requires_confirmation=False,
            structured_error_override=structured_error,
        )

    def execute_verified(
        self,
        idempotency_key: str,
        execution_id: str,
        arguments: Mapping[str, Any],
        odoo_client: Any,
        tenant_id: str,
        user_id: str,
        *,
        _context: _Context | None = None,
        _proposal_id: str | None = None,
        _approval_id: str | None = None,
    ) -> GatewayResult:
        """Execute the confirmed mutation, verify its ERP state, then persist.

        When `_context` is supplied (confirm path), we drive the canonical state
        machine (LEASE_GRANTED→EXECUTING→VERIFYING→SUCCESS/FAILED/AMBIGUOUS) and
        emit LEASE / TOOL_CALL / ERP_REQUEST / ERP_RESPONSE / VERIFICATION /
        RECONCILIATION evidence events. Without `_context` (standalone/internal
        calls) the method behaves exactly as before, preserving backward compat
        for tests that invoke it directly.
        """
        ctx = _context
        record = self.idempotency_store.get(idempotency_key, tenant_id, user_id)
        if record is None or record.execution_id != execution_id or record.state != "pending":
            if ctx is not None:
                try:
                    ctx.apply(self, ExecutionEvent.EXECUTION_HARD_FAILURE, reason="reservation_not_owned")
                except Exception:
                    pass
            return self._empty_result(
                status=CONFLICT,
                error_code=IDEMPOTENCY_CONFLICT,
                reason="confirmed execution reservation is no longer owned",
                user_id=user_id,
                tenant_id=tenant_id,
            )

        arguments_for_audit = dict(arguments)
        create_attempted = False
        create_returned = False
        validation_rejected = False
        lease: ExecutionLease | None = None

        # ---- Acquire execution lease (Phase 5) --------------------------------
        if ctx is not None:
            # If a lease is already active for this key we're in a concurrent
            # confirmation race. The first owner wins; everyone else is told
            # to wait (fail-closed — no double-execution).
            active = self.lease_store.active_for_key(idempotency_key)
            if active is not None and active.execution_id != execution_id:
                if ctx is not None:
                    try:
                        ctx.apply(self, ExecutionEvent.LEASE_EXPIRED, reason="lease_already_held")
                    except Exception:
                        pass
                return self._empty_result(
                    status=IN_PROGRESS,
                    error_code=IDEMPOTENCY_IN_PROGRESS,
                    reason="execution_lease_already_held",
                    user_id=user_id, tenant_id=tenant_id,
                    execution_id=execution_id,
                    idempotency_key=idempotency_key,
                )
            owner = f"gateway:{socket.gethostname()}:{os.getpid()}"
            try:
                lease = self.lease_store.acquire(
                    execution_id=execution_id,
                    idempotency_key=idempotency_key,
                    arguments=arguments,
                    owner=owner,
                    approval_id=_approval_id,
                    action_id=ctx.execution.action_id if ctx.execution else None,
                )
            except LeaseAlreadyHeldError:
                return self._empty_result(
                    status=IN_PROGRESS,
                    error_code=IDEMPOTENCY_IN_PROGRESS,
                    reason="execution_lease_already_held",
                    user_id=user_id, tenant_id=tenant_id,
                    execution_id=execution_id,
                    idempotency_key=idempotency_key,
                )
            ctx.lease_id = lease.lease_id
            ctx.ev(self, EvidenceType.LEASE, {
                "lease_id": lease.lease_id,
                "owner": owner,
                "ttl_seconds": self.lease_store.ttl_seconds,
                "state": LEASE_ACTIVE,
            })
            try:
                ctx.apply(self, ExecutionEvent.CONFIRMATION_APPROVED, reason="user_confirmed")
                ctx.apply(self, ExecutionEvent.LEASE_GRANTED, reason="lease_acquired")
                ctx.apply(self, ExecutionEvent.EXECUTION_STARTED, reason="execution_started")
            except Exception:
                pass
            ctx.ev(self, EvidenceType.TOOL_CALL, {
                "tool_name": record.tool_name,
                "tool_version": record.tool_version,
                "arguments": dict(arguments),
                "proposal_id": _proposal_id,
            })

        def _finalize_lease(target_state: LeaseState) -> None:
            """Release or complete the lease on our way out."""
            if lease is None or ctx is None:
                return
            try:
                owner = lease.owner
                if target_state == LEASE_COMPLETED:
                    self.lease_store.complete(lease.lease_id, owner=owner)
                elif target_state == LEASE_RELEASED:
                    self.lease_store.release(lease.lease_id, owner=owner)
                # LEASE_EXPIRED is handled by expire_stale() on next boot/poll.
            except (LeaseNotOwnedError, LeaseError):
                pass

        try:
            product_ids = sorted({line["product_id"] for line in arguments["lines"]})
            # Product existence pre-check (TOOL_CONTRACTS guardrail): the read
            # doubles as the pre-create existence probe, so a nonexistent
            # product fails BEFORE any ERP mutation with ENTITY_NOT_FOUND.
            if ctx is not None:
                ctx.ev(self, EvidenceType.ERP_REQUEST, {
                    "model": "product.product",
                    "method": "read",
                    "ids": product_ids,
                    "phase": "pre_check",
                })
            product_records = odoo_client.read("product.product", product_ids, ["list_price"])
            if ctx is not None:
                ctx.ev(self, EvidenceType.ERP_RESPONSE, {
                    "model": "product.product",
                    "records_returned": len(product_records) if isinstance(product_records, list) else 0,
                })
            found_ids = {
                int(product_record["id"])
                for product_record in product_records
                if isinstance(product_record, Mapping) and "id" in product_record
                and not isinstance(product_record["id"], bool)
            }
            if not set(product_ids) <= found_ids:
                verification = self._verification_failure(
                    arguments,
                    record,
                    "One or more requested products do not exist in the ERP.",
                    error_code=ENTITY_NOT_FOUND,
                )
            else:
                payload = self._build_odoo_payload(arguments, record.idempotency_key)
                create_attempted = True
                if ctx is not None:
                    try:
                        ctx.apply(self, ExecutionEvent.VERIFICATION_STARTED, reason="pre_checks_passed")
                    except Exception:
                        pass
                    ctx.ev(self, EvidenceType.ERP_REQUEST, {
                        "model": "sale.order",
                        "method": "create",
                        "line_count": len(arguments["lines"]),
                        "customer_id": arguments["customer_id"],
                        "idempotency_key": record.idempotency_key,
                    })
                created_ids = odoo_client.create("sale.order", payload)
                create_returned = True
                if ctx is not None:
                    ctx.ev(self, EvidenceType.ERP_RESPONSE, {
                        "model": "sale.order",
                        "created_ids": created_ids if isinstance(created_ids, list) else None,
                    })
                if not isinstance(created_ids, list) or len(created_ids) != 1 or not isinstance(created_ids[0], int):
                    verification = self._verification_failure(
                        arguments,
                        record,
                        "Odoo returned an invalid creation response.",
                    )
                else:
                    try:
                        passed = verify_sales_order_creation(
                            odoo_client,
                            created_ids[0],
                            arguments["customer_id"],
                            arguments["lines"],
                            record.idempotency_key,
                        )
                    except Exception as error:
                        structured = translate_odoo_exception(error)
                        verification = self._odoo_read_failure(arguments, record, structured)
                    else:
                        if ctx is not None:
                            ctx.ev(self, EvidenceType.VERIFICATION, {
                                "order_id": created_ids[0],
                                "passed": bool(passed.passed),
                                "error": getattr(passed, "error", None),
                            })
                        if passed.passed:
                            verification = self._verification_success(arguments, record, created_ids[0])
                        else:
                            # The order exists in Odoo (we just read it back);
                            # keep its id as provenance for reconciliation (F-07).
                            verification = self._verification_failure(
                                arguments,
                                record,
                                passed.error,
                                external_record_id=str(created_ids[0]),
                            )
        except Exception as error:
            structured = translate_odoo_exception(error)
            if structured.retryable:
                if create_attempted:
                    # The create may have committed; the outcome is genuinely
                    # ambiguous and the key must never re-execute.
                    self.idempotency_store.fail(
                        record.idempotency_key,
                        request_fingerprint=record.request_fingerprint,
                        tool_name=record.tool_name,
                        tool_version=record.tool_version,
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        execution_id=record.execution_id,
                        outcome=AMBIGUOUS,
                        error_code=structured.code,
                    )
                    if ctx is not None:
                        try:
                            ctx.apply(self, ExecutionEvent.AMBIGUOUS_OUTCOME, reason=structured.message)
                        except Exception:
                            pass
                        ctx.ev(self, EvidenceType.RECONCILIATION, {
                            "reason": "retryable_after_create",
                            "error_code": structured.code,
                        })
                    _finalize_lease(LEASE_EXPIRED)
                else:
                    # Nothing reached Odoo: release the reservation so the
                    # identical request can be retried after the transient
                    # failure instead of deadlocking on reconciliation.
                    try:
                        self.idempotency_store.release(
                            record.idempotency_key,
                            tenant_id=record.tenant_id,
                            user_id=record.user_id,
                            execution_id=record.execution_id,
                        )
                    except (IdempotencyStoreError, IllegalIdempotencyTransition):
                        self.idempotency_store.fail(
                            record.idempotency_key,
                            request_fingerprint=record.request_fingerprint,
                            tool_name=record.tool_name,
                            tool_version=record.tool_version,
                            tenant_id=record.tenant_id,
                            user_id=record.user_id,
                            execution_id=record.execution_id,
                            outcome=AMBIGUOUS,
                            error_code=structured.code,
                        )
                    if ctx is not None:
                        try:
                            ctx.apply(self, ExecutionEvent.EXECUTION_TRANSIENT_FAILURE, reason=structured.message)
                        except Exception:
                            pass
                    _finalize_lease(LEASE_RELEASED)
                verification = {
                    "status": "ambiguous",
                    "reason": structured.message,
                    "error_code": structured.code,
                    "external_record_id": None,
                }
                receipt = self.audit_store.append(
                    self._verification_audit_record(record, arguments_for_audit, verification)
                )
                return self._empty_result(
                    status="erp_error",
                    error_code=structured.code,
                    reason=structured.message,
                    structured_error=structured,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    **_audit_provenance(receipt)
                )
            # Odoo's own validation rejection (422) happens before any record
            # is written; treat it as a clean not-committed failure.
            validation_rejected = (
                create_attempted
                and not create_returned
                and structured.code == "ERP_VALIDATION_ERROR"
            )
            verification = self._odoo_write_failure(arguments, record, structured)
            if ctx is not None:
                try:
                    ctx.apply(self, ExecutionEvent.EXECUTION_HARD_FAILURE, reason=structured.message)
                except Exception:
                    pass
            _finalize_lease(LEASE_RELEASED if validation_rejected else LEASE_EXPIRED)

        if verification["status"] == "error":
            # Definitive failure. Whether ERP state may have changed decides
            # the retry policy: not-committed failures release for retry;
            # uncertain ones reconcile as ambiguous instead of replaying an
            # error as success (B4 fix) and instead of risking a duplicate.
            committed_uncertain = create_returned or (create_attempted and not validation_rejected)
            if committed_uncertain:
                self.idempotency_store.fail(
                    record.idempotency_key,
                    request_fingerprint=record.request_fingerprint,
                    tool_name=record.tool_name,
                    tool_version=record.tool_version,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    execution_id=record.execution_id,
                    outcome=AMBIGUOUS,
                    error_code=verification["error_code"] or "UNKNOWN_ERROR",
                )
                if ctx is not None:
                    try:
                        ctx.apply(self, ExecutionEvent.VERIFICATION_FAILED, reason=verification.get("reason", "verification_failed"))
                    except Exception:
                        pass
                    ctx.ev(self, EvidenceType.RECONCILIATION, {
                        "reason": "verification_failed_after_write",
                        "external_record_id": verification.get("external_record_id"),
                        "error_code": verification.get("error_code"),
                    })
                _finalize_lease(LEASE_EXPIRED)
            else:
                try:
                    self.idempotency_store.release(
                        record.idempotency_key,
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        execution_id=record.execution_id,
                    )
                except (IdempotencyStoreError, IllegalIdempotencyTransition):
                    self.idempotency_store.fail(
                        record.idempotency_key,
                        request_fingerprint=record.request_fingerprint,
                        tool_name=record.tool_name,
                        tool_version=record.tool_version,
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        execution_id=record.execution_id,
                        outcome=AMBIGUOUS,
                        error_code=verification["error_code"] or "UNKNOWN_ERROR",
                    )
                if ctx is not None:
                    try:
                        ctx.apply(self, ExecutionEvent.VERIFICATION_FAILED, reason=verification.get("reason", "verification_failed"))
                        ctx.apply(self, ExecutionEvent.EXECUTION_HARD_FAILURE, reason=verification.get("reason", "verification_failed"))
                    except Exception:
                        pass
                _finalize_lease(LEASE_RELEASED)
        else:
            self.idempotency_store.complete(
                record.idempotency_key,
                request_fingerprint=record.request_fingerprint,
                tool_name=record.tool_name,
                tool_version=record.tool_version,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                execution_id=record.execution_id,
                result=verification["result"],
                external_record_id=verification["external_record_id"],
                reconciliation=verification["reconciliation"],
            )
            if ctx is not None:
                try:
                    ctx.apply(self, ExecutionEvent.VERIFICATION_PASSED, reason="verification_passed")
                    ctx.apply(self, ExecutionEvent.SUCCESS_CONFIRMED, reason="execution_completed")
                except Exception:
                    pass
                if verification.get("result"):
                    ctx.ev(self, EvidenceType.USER_VISIBLE_CLAIM, {
                        "status": "success",
                        "order_id": verification["result"].get("order_id"),
                        "external_record_id": verification.get("external_record_id"),
                    })
            _finalize_lease(LEASE_COMPLETED)
        receipt = self.audit_store.append(
            self._verification_audit_record(record, arguments_for_audit, verification)
        )
        return self._empty_result(
            status=verification["status"],
            error_code=verification["error_code"],
            reason=verification["reason"] or "verification_completed",
            result=verification["result"],
            structured_error=verification["structured_error"],
            idempotency_key=record.idempotency_key,
            user_id=user_id,
            tenant_id=tenant_id,
            execution_id=record.execution_id,
            **_audit_provenance(receipt)
        )

    def confirm_and_execute(
        self,
        proposal_id: str,
        user_id: str,
        tenant_id: str,
        odoo_client: Any,
    ) -> GatewayResult:
        """Approve the server-created proposal and execute it through the gateway."""
        approval = self.confirmation_store.approve(proposal_id, user_id, tenant_id)
        if approval.status != "approved":
            if approval.status == EXPIRED:
                # An expired proposal can never execute; leaving its pending
                # reservation would permanently block the identical request
                # (audit F-09). Same guard pattern as the denial path.
                proposal = self.confirmation_store.get_proposal(proposal_id)
                if proposal is not None:
                    key = compute_idempotency_key(tenant_id, user_id, proposal.tool_name, proposal.arguments)
                    record = self.idempotency_store.get(key, tenant_id, user_id)
                    if record is not None and record.state == "pending":
                        try:
                            self.idempotency_store.release(
                                key,
                                tenant_id=tenant_id,
                                user_id=user_id,
                                execution_id=record.execution_id,
                            )
                        except (IdempotencyStoreError, IllegalIdempotencyTransition):
                            pass
            return self._approval_failure(approval)
        # Rehydrate a _Context so execute_verified can continue the canonical
        # state machine and evidence chain for this already-reserved execution.
        record = self.idempotency_store.get(approval.idempotency_key, tenant_id, user_id)
        exec_id = approval.execution_id or (record.execution_id if record is not None else None)
        if exec_id is None:
            return self._empty_result(
                status=CONFLICT,
                error_code=IDEMPOTENCY_CONFLICT,
                reason="missing_execution_id_for_confirmation",
                user_id=user_id, tenant_id=tenant_id,
                idempotency_key=approval.idempotency_key,
            )
        envelope = ToolGatewayRequest(
            request_id=f"confirm-{exec_id}",
            user_id=user_id,
            tenant_id=tenant_id,
            tool_name=approval.tool_name,
            tool_version=approval.tool_version,
            arguments=approval.arguments or {},
            idempotency_key=approval.idempotency_key,
        )
        ctx = _Context(record=envelope, tool_call_id=str(uuid.uuid4()), odoo_client=odoo_client,
                       policy_decision="confirmation_required", idempotency_key=approval.idempotency_key,
                       execution_id=exec_id, requires_confirmation=True)
        # Reload existing canonical state from the store if it exists;
        # otherwise initialize at AUTHORIZED (policy was evaluated before
        # the proposal was issued).
        saved = self.execution_store.load(exec_id)
        if saved is not None:
            ctx.execution = saved
        else:
            trace_id, action_id, _ = new_execution_identity()
            ctx.execution = INITIAL_STATE()
            ctx.execution = ExecutionState(
                stage=ExecutionStage.AWAITING_CONFIRMATION,
                status=ExecutionStatus.PENDING,
                security=SecurityStatus.AUTHORIZED,
                final=None,
                history=(),
                execution_id=exec_id,
                action_id=action_id,
                trace_id=envelope.request_id,
            )
            self.execution_store.save(ctx.execution)
        # Emit APPROVAL evidence.
        ctx.ev(self, EvidenceType.APPROVAL, {
            "proposal_id": proposal_id,
            "operation_hash": approval.operation_hash,
            "approved_by": user_id,
            "approval_level": "self",
        })
        return self.execute_verified(
            approval.idempotency_key,
            exec_id,
            approval.arguments,
            odoo_client,
            tenant_id=tenant_id,
            user_id=user_id,
            _context=ctx,
            _proposal_id=proposal_id,
        )

    def record_confirmation_denial(
        self,
        proposal_id: str,
        user_id: str,
        tenant_id: str,
    ) -> GatewayResult:
        """Deny a proposal and append the denial through the gateway audit boundary.

        Denial also releases the pending idempotency reservation so the user can
        correct the arguments and retry without manual SQLite surgery (B3 fix).
        """
        denial = self.confirmation_store.decline(proposal_id, user_id, tenant_id)
        if denial.status != DECLINED:
            return self._approval_failure(denial)
        proposal = self.confirmation_store.get_proposal(proposal_id)
        if proposal is None:
            return self._empty_result(
                status=REPLAY,
                error_code="CONFIRMATION_REPLAY",
                reason="proposal_disappeared_after_denial",
                user_id=user_id,
                tenant_id=tenant_id,
            )
        record = self.idempotency_store.get(denial.idempotency_key, tenant_id, user_id)
        if record is not None and record.state == "pending":
            try:
                self.idempotency_store.release(
                    record.idempotency_key,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    execution_id=record.execution_id,
                )
            except (IdempotencyStoreError, IllegalIdempotencyTransition):
                pass
        arguments_json = json.dumps(
            proposal.arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        end_time = _utc_now()
        try:
            audit_record = self.audit_store.append(
                {
                    "request_id": str(uuid.uuid4()),
                    "tool_call_id": str(uuid.uuid4()),
                    "trace_id": str(uuid.uuid4()),
                    "tenant_id": proposal.tenant_id,
                    "user_id": proposal.user_id,
                    "odoo_user": proposal.user_id,
                    "tool_name": proposal.tool_name,
                    "tool_version": proposal.tool_version,
                    "arguments_hash": hashlib.sha256(arguments_json.encode("utf-8")).hexdigest(),
                    "sanitized_arguments": None,
                    "policy_decision": "confirmation_required",
                    "proposal_id": proposal.proposal_id,
                    "operation_hash": proposal.operation_hash,
                    "start_time": end_time,
                    "end_time": end_time,
                    "result_status": "denied",
                    "error_code": CONFIRMATION_DECLINED,
                    "idempotency_key": denial.idempotency_key,
                    "execution_id": record.execution_id if record is not None else None,
                }
            )
        except Exception:
            return self._empty_result(
                status=DENIED,
                error_code=AUDIT_WRITE_FAILED,
                reason="audit_write_failed",
                user_id=user_id,
                tenant_id=tenant_id,
            )
        result = self._empty_result(
            status=DECLINED,
            error_code=None,
            reason="confirmation_declined_by_user",
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return _replace_audit_id(result, int(audit_record["audit_id"]))

    def _approval_failure(self, result: ApprovalResult) -> GatewayResult:
        if result.error_code == NOT_FOUND:
            return self._empty_result(
                status=DENIED,
                error_code=result.error_code,
                reason=result.reason,
                structured_error=None,
            )
        return self._empty_result(
            status=DENIED,
            error_code=result.error_code,
            reason=result.reason,
            structured_error=result.structured_error,
        )

    def handle_request(
        self,
        request: ToolGatewayRequest | Mapping[str, Any],
        odoo_client: Any = None,
    ) -> GatewayResult:
        """Process one structured request; executes read-only tools when a client is provided."""
        try:
            if isinstance(request, ToolGatewayRequest):
                record = request
            else:
                record = ToolGatewayRequest.from_mapping(request)
        except InvalidGatewayRequest:
            return self._audit_invalid_envelope()

        context = _Context(record=record, tool_call_id=str(uuid.uuid4()), odoo_client=odoo_client)
        result = self._process(context)
        audit_id = self._audit(context, result)
        if audit_id is None:
            return GatewayResult(
                status=DENIED,
                error_code=AUDIT_WRITE_FAILED,
                reason="audit_write_failed",
                request_id=context.record.request_id,
                user_id=context.record.user_id,
                tenant_id=context.record.tenant_id,
                tool_name=context.record.tool_name,
                tool_version=context.record.tool_version,
                policy_decision=context.policy_decision,
                idempotency_key=context.idempotency_key,
                execution_id=context.execution_id,
                result=None,
                audit_id=None,
                requires_confirmation=context.requires_confirmation,
            )
        return result if result.audit_id == audit_id else _replace_audit_id(result, audit_id)

    def _process(self, context: _Context) -> GatewayResult:
        # 1) Create a canonical execution record and INTENT evidence event.
        trace_id, action_id, execution_id = new_execution_identity()
        context.execution_id = context.execution_id or execution_id
        actor = Actor(user_id=context.record.user_id, tenant_id=context.record.tenant_id)
        context.execution = INITIAL_STATE()
        context.execution = ExecutionState(
            stage=context.execution.stage,
            status=context.execution.status,
            security=context.execution.security,
            final=context.execution.final,
            history=context.execution.history,
            execution_id=context.execution_id,
            action_id=action_id,
            trace_id=context.record.request_id,
            last_updated=context.execution.last_updated,
        )
        # Drive the idle -> listening -> thinking -> planned -> policy_checking chain.
        try:
            context.apply(self, ExecutionEvent.USER_MESSAGE_RECEIVED, reason="gateway_entry", persist=False)
            context.apply(self, ExecutionEvent.INTENT_PARSED, reason="envelope_valid", persist=False)
            context.apply(self, ExecutionEvent.PLAN_BUILT, reason="tool_resolved", persist=False)
            context.apply(self, ExecutionEvent.SECURITY_SCREENED, reason="envelope_validated", persist=False)
        except Exception as exc:
            # If any transition is illegal, keep the initial state and continue;
            # the rest of _process will fail-closed on validation.
            pass
        self.execution_store.save(context.execution)
        try:
            self._validate_envelope(context)
        except InvalidGatewayRequest:
            context.apply(self, ExecutionEvent.SECURITY_DENIED, reason="invalid_request_envelope")
            return self._final_result(
                context,
                status=VALIDATION_ERROR,
                error_code=INVALID_REQUEST,
                reason="invalid_request_envelope",
            )

        context.ev(self, EvidenceType.INTENT, {
            "request_id": context.record.request_id,
            "tool_name": context.record.tool_name,
            "tool_version": context.record.tool_version,
            "arguments": dict(context.record.arguments),
        })

        try:
            contract = self.registry.get(context.record.tool_name)
        except KeyError:
            context.apply(self, ExecutionEvent.SECURITY_DENIED, reason="unknown_tool")
            return self._final_result(
                context,
                status=DENIED,
                error_code=UNKNOWN_TOOL,
                reason="unknown_tool",
            )

        if contract["tool_version"] != context.record.tool_version:
            context.apply(self, ExecutionEvent.SECURITY_DENIED, reason="unsupported_tool_version")
            return self._final_result(
                context,
                status=DENIED,
                error_code=UNSUPPORTED_TOOL_VERSION,
                reason="unsupported_tool_version",
            )

        try:
            self._validate_arguments(context, contract)
        except jsonschema.ValidationError as exc:
            context.apply(self, ExecutionEvent.SECURITY_DENIED, reason=f"argument_schema_failed: {exc.message}")
            return self._final_result(
                context,
                status=VALIDATION_ERROR,
                error_code=INVALID_ARGUMENTS,
                reason="argument_schema_failed",
            )

        # Risk assessment (Phase 5): drives which risk-based outcomes apply.
        context.risk = assess_risk(contract, context.record.arguments)
        context.ev(self, EvidenceType.RISK_ASSESSMENT, {
            "level": context.risk.level,
            "financial_impact": context.risk.financial_impact,
            "reversibility": context.risk.reversibility,
            "privilege_level": context.risk.privilege_level,
            "sensitive_data": context.risk.sensitive_data,
            "external_side_effect": context.risk.external_side_effect,
            "factors": list(context.risk.factors),
        })

        # ABAC v2 (Phase 7): attribute-based decision with policy hash/version.
        decision: PolicyDecision2 = self.policy_engine2.evaluate(
            user_id=context.record.user_id,
            tenant_id=context.record.tenant_id,
            tool_name=context.record.tool_name,
            tool_version=context.record.tool_version,
            risk=context.risk,
        )
        # PolicyEngine2.evaluate uses risk.level as a string; align with contract.
        # The contract's declared risk_level is what we classify from, so no
        # adjustment needed here.
        # Map v2 decision onto legacy decision strings.
        if decision.decision == "allow":
            context.policy_decision = ALLOWED
        elif decision.decision == "require_approval":
            context.policy_decision = "confirmation_required"
        else:
            context.policy_decision = "denied"
        context.policy_version = decision.policy_version
        context.policy_hash = decision.policy_hash
        context.ev(self, EvidenceType.POLICY_DECISION, {
            "outcome": decision.decision,
            "rule_id": decision.rule_id,
            "reason": decision.reason,
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
            "requires_approval": decision.requires_approval,
            "required_approval_level": decision.required_approval_level,
            "risk_level": context.risk.level,
        })

        # Legacy engine kept in place as defence-in-depth; if v1 denies what v2
        # allowed, the stricter decision wins (fail closed).
        legacy = self.policy_engine.evaluate({
            "user_id": context.record.user_id,
            "tenant_id": context.record.tenant_id,
            "tool_name": context.record.tool_name,
            "tool_version": context.record.tool_version,
        })
        if legacy.decision == "denied" and decision.decision != "deny":
            context.policy_decision = "denied"
            decision = PolicyDecision2(
                decision="deny",
                rule_id=getattr(legacy, "policy_rule_id", "legacy_override"),
                policy_version=decision.policy_version,
                policy_hash=decision.policy_hash,
                reason=f"legacy_policy_override: {legacy.reason}",
                requires_approval=False,
            )

        # Advance the canonical state machine on non-deny outcomes.
        if decision.decision != "deny" and decision.decision != "require_step_up":
            try:
                context.apply(self, ExecutionEvent.POLICY_EVALUATED, reason=f"policy:{decision.decision}")
            except Exception:
                pass

        if decision.decision == "deny":
            try:
                context.apply(self, ExecutionEvent.SECURITY_DENIED, reason=decision.reason)
            except Exception:
                pass
            return self._final_result(
                context,
                status=DENIED,
                error_code=POLICY_DENIED,
                reason=decision.reason,
            )
        if decision.decision == "require_step_up":
            # R4 MFA step-up is not wired into the cockpit UI yet; fail closed
            # to a clear error instead of silently allowing.
            try:
                context.apply(self, ExecutionEvent.STEP_UP_REQUESTED, reason="mfa_step_up_required")
            except Exception:
                pass
            return self._final_result(
                context,
                status=DENIED,
                error_code="MFA_REQUIRED",
                reason=decision.reason,
            )
        if decision.required_approval_level == "manager":
            # R3 manager approval escalates to a normal confirmation-required
            # flow for the POC; the UI labels it accordingly.
            context.requires_confirmation = True

        if contract["readOnly"] is False:
            return self._process_mutating(context, contract, decision)

        if context.odoo_client is not None:
            # Fail-closed: reads execute only on an explicit allow decision
            # (e.g. a future policy demanding confirmation for a read must not
            # silently execute unconfirmed).
            if decision.decision != "allow":
                return self._final_result(
                    context,
                    status=DENIED,
                    error_code=POLICY_DENIED,
                    reason=decision.reason,
                )
            return self._execute_read(context, contract)

        return self._final_result(
            context,
            status=ACCEPTED,
            error_code=None,
            reason="ready_for_execution",
        )

    def _execute_read(self, context: _Context, contract: Mapping[str, Any]) -> GatewayResult:
        """Execute one read-only tool call through the provided ERP client.

        The client is supplied by the caller inside the server-owned boundary;
        the gateway still owns validation, authorization, and audit for the read.
        """
        record = context.record
        odoo = contract["odoo"]
        model = odoo["model"]
        fields = list(odoo["fields"])
        limit_cap = odoo.get("limit_cap")
        try:
            if odoo["method"] == "search_read":
                query = str(context.record.arguments["query"])
                limit = int(limit_cap) if limit_cap else None
                records = odoo_client_search_read(
                    context.odoo_client, model, [["name", "ilike", query]], fields, limit
                )
                if not records:
                    # ponytail: heuristic fallback ladder for Arabic orthography
                    # (أ/إ/آ, ة/ه, ى/ي) — Odoo ilike matches raw stored names, so a
                    # bare-typed query misses hamza-stored records and vice versa.
                    # Each variant runs only when the previous returned empty
                    # (max 3 Odoo calls, usually 1); a normalizing search backend
                    # is the upgrade path if matching quality matters at scale.
                    for variant in _query_variants(query):
                        records = odoo_client_search_read(
                            context.odoo_client, model, [["name", "ilike", variant]], fields, limit
                        )
                        if records:
                            break
            elif odoo["method"] == "read":
                id_field = "customer_id" if "customer_id" in context.record.arguments else "order_id"
                record_id = int(context.record.arguments[id_field])
                records = context.odoo_client.read(model, [record_id], fields)
            else:
                raise ValueError(f"tool read method {odoo['method']!r} is not executable")
        except Exception as error:
            structured = translate_odoo_exception(error)
            return self._final_result(
                context,
                status="erp_error",
                error_code=structured.code,
                reason=structured.message,
                structured_error=structured,
            )

        if not records:
            if odoo["method"] == "read":
                # Read-by-id returning nothing means the referenced record does
                # not exist; a search with zero hits is a legitimate success.
                return self._final_result(
                    context,
                    status="erp_error",
                    error_code=ENTITY_NOT_FOUND,
                    reason="no_matching_records",
                    structured_error=translate_error(ENTITY_NOT_FOUND, "No matching records were found."),
                )
            return self._final_result(
                context,
                status=ACCEPTED,
                error_code=None,
                reason="read_completed",
                result=_shape_read_result(record.tool_name, []),
            )
        return self._final_result(
            context,
            status=ACCEPTED,
            error_code=None,
            reason="read_completed",
            result=_shape_read_result(record.tool_name, records),
        )

    def _validate_envelope(self, context: _Context) -> None:
        record = context.record
        for value in (record.request_id, record.user_id, record.tenant_id):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise InvalidGatewayRequest("request identity fields are invalid")
        for value in (record.tool_name, record.tool_version):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise InvalidGatewayRequest("tool identity fields are invalid")
        if not isinstance(record.arguments, dict):
            raise InvalidGatewayRequest("arguments must be a mapping")
        if record.idempotency_key is not None:
            if not isinstance(record.idempotency_key, str) or len(record.idempotency_key) != 32:
                raise InvalidGatewayRequest("idempotency_key is invalid")
            try:
                int(record.idempotency_key, 16)
            except ValueError as error:
                raise InvalidGatewayRequest("idempotency_key is invalid") from error
            if record.idempotency_key.lower() != record.idempotency_key:
                raise InvalidGatewayRequest("idempotency_key is invalid")

    def _validate_arguments(self, context: _Context, contract: Mapping[str, Any]) -> None:
        schema = contract["inputSchema"]
        jsonschema.validate(instance=context.record.arguments, schema=schema)

    def _process_mutating(
        self,
        context: _Context,
        contract: Mapping[str, Any],
        policy_decision: Any,
    ) -> GatewayResult:
        computed_key = compute_idempotency_key(
            context.record.tenant_id,
            context.record.user_id,
            context.record.tool_name,
            context.record.arguments,
        )
        if context.record.idempotency_key is not None and context.record.idempotency_key != computed_key:
            return self._final_result(
                context,
                status=VALIDATION_ERROR,
                error_code=INVALID_REQUEST,
                reason="idempotency_key_mismatch",
            )
        fingerprint = compute_request_fingerprint(
            context.record.tenant_id,
            context.record.user_id,
            context.record.tool_name,
            context.record.tool_version,
            context.record.arguments,
        )
        outcome = self.idempotency_store.reserve(
            idempotency_key=computed_key,
            request_fingerprint=fingerprint,
            tool_name=context.record.tool_name,
            tool_version=context.record.tool_version,
            tenant_id=context.record.tenant_id,
            user_id=context.record.user_id,
        )
        context.idempotency_key = computed_key
        # The idempotency store is the authoritative source of execution_id
        # (it allocates the UUID inside reserve()). Rebase our canonical
        # ExecutionState onto that execution_id so later evidence/lease events
        # carry the same id.
        reserved_execution_id = outcome.record.execution_id
        if context.execution is not None and context.execution_id != reserved_execution_id:
            context.execution_id = reserved_execution_id
            context.execution = ExecutionState(
                stage=context.execution.stage,
                status=context.execution.status,
                security=context.execution.security,
                final=context.execution.final,
                history=context.execution.history,
                execution_id=reserved_execution_id,
                action_id=context.execution.action_id,
                trace_id=context.record.request_id,
                last_updated=context.execution.last_updated,
            )
            self.execution_store.save(context.execution)

        if outcome.status == CONFLICT:
            return self._final_result(
                context,
                status=CONFLICT,
                error_code=IDEMPOTENCY_CONFLICT,
                reason=outcome.reason,
            )
        if outcome.status == IN_PROGRESS:
            return self._final_result(
                context,
                status=IN_PROGRESS,
                error_code=IDEMPOTENCY_IN_PROGRESS,
                reason=outcome.reason,
            )
        if outcome.status == RECONCILIATION_REQUIRED:
            return self._final_result(
                context,
                status=RECONCILIATION_REQUIRED,
                error_code=RECONCILIATION_REQUIRED_ERROR,
                reason=outcome.reason,
            )
        if outcome.status == REPLAYED:
            context.replay_result = outcome.record.result
            return self._final_result(
                context,
                status=REPLAY,
                error_code=None,
                reason="completed_result_replayed",
                result=outcome.record.result,
            )
        if outcome.status == RESERVED and contract["requiresConfirmation"] is True:
            context.requires_confirmation = True
            try:
                context.apply(self, ExecutionEvent.PROPOSAL_ISSUED, reason="awaiting_user_confirmation")
            except Exception:
                pass
            return self._final_result(
                context,
                status=CONFIRMATION_REQUIRED,
                error_code=CONFIRMATION_REQUIRED_ERROR,
                reason="confirmation_required_before_execution",
                proposal=self._confirmation_proposal(context),
            )
        if outcome.status == RESERVED:
            return self._final_result(
                context,
                status=ACCEPTED,
                error_code=None,
                reason="ready_for_execution",
            )
        raise ValueError("unsupported idempotency outcome")

    def _final_result(
        self,
        context: _Context,
        *,
        status: str,
        error_code: str | None,
        reason: str,
        result: Mapping[str, Any] | None = None,
        proposal: Mapping[str, Any] | None = None,
        structured_error: StructuredError | None = None,
    ) -> GatewayResult:
        return GatewayResult(
            status=status,
            error_code=error_code,
            reason=reason,
            request_id=context.record.request_id,
            user_id=context.record.user_id,
            tenant_id=context.record.tenant_id,
            tool_name=context.record.tool_name,
            tool_version=context.record.tool_version,
            policy_decision=context.policy_decision,
            idempotency_key=context.idempotency_key,
            execution_id=context.execution_id,
            result=result,
            audit_id=None,
            requires_confirmation=context.requires_confirmation,
            proposal=proposal,
            structured_error_override=structured_error,
        )

    def _confirmation_proposal(self, context: _Context) -> Mapping[str, Any] | None:
        """Build the server-owned proposal payload from the current request."""
        record = context.record
        created = self.confirmation_store.create_proposal(
            tool_name=record.tool_name,
            tool_version=record.tool_version,
            arguments=dict(record.arguments),
            user_id=record.user_id,
            tenant_id=record.tenant_id,
        )
        if created.proposal is None:
            return None
        proposal = created.proposal
        return {
            "proposal_id": proposal.proposal_id,
            "tool_name": proposal.tool_name,
            "tool_version": proposal.tool_version,
            "arguments": dict(proposal.arguments),
            "operation_hash": proposal.operation_hash,
            "user_id": proposal.user_id,
            "tenant_id": proposal.tenant_id,
            "created_at": proposal.created_at,
            "expires_at": proposal.expires_at,
        }

    def _audit(self, context: _Context, result: GatewayResult) -> int | None:
        record = context.record
        end_time = _utc_now()
        if result.status == ACCEPTED:
            result_status = "success"
        elif result.status == REPLAY:
            replay_is_error = isinstance(result.result, Mapping) and result.result.get("status") == "error"
            result_status = "error" if replay_is_error else "success"
        elif result.status in {CONFIRMATION_REQUIRED, IN_PROGRESS, CONFLICT, RECONCILIATION_REQUIRED}:
            result_status = "pending"
        elif result.status == DENIED:
            result_status = "denied"
        else:
            result_status = "error"
        try:
            audit_payload = {
                "request_id": record.request_id,
                "tool_call_id": context.tool_call_id,
                "tenant_id": record.tenant_id,
                "user_id": record.user_id,
                "odoo_user": record.user_id,
                "tool_name": record.tool_name,
                "tool_version": record.tool_version,
                "arguments": dict(record.arguments),
                "policy_decision": context.policy_decision,
                "start_time": end_time,
                "end_time": end_time,
                "result_status": result_status,
                "error_code": result.error_code,
                "idempotency_key": context.idempotency_key,
                "execution_id": context.execution_id,
                "external_record_id": (
                    context.replay_result.get("order_id")
                    if context.replay_result
                    else None
                ),
            }
            if result.proposal is not None:
                # Link the audit row to the proposal it created so the trail
                # answers "what was proposed, under which operation hash" (F-14).
                audit_payload["proposal_id"] = result.proposal.get("proposal_id")
                audit_payload["operation_hash"] = result.proposal.get("operation_hash")
            audit_record = self.audit_store.append(audit_payload)
            return int(audit_record["audit_id"])
        except Exception:
            return None


    def _audit_invalid_envelope(self) -> GatewayResult:
        end_time = _utc_now()
        audit_id = None
        try:
            audit_record = self.audit_store.append(
                {
                    "request_id": "<invalid>",
                    "tool_call_id": str(uuid.uuid4()),
                    "tenant_id": "<unknown>",
                    "user_id": "<unknown>",
                    "odoo_user": "<unknown>",
                    "tool_name": "<unknown>",
                    "tool_version": "",
                    "arguments": {},
                    "policy_decision": "denied",
                    "start_time": end_time,
                    "end_time": end_time,
                    "result_status": "error",
                    "error_code": INVALID_REQUEST,
                }
            )
            audit_id = int(audit_record["audit_id"])
        except Exception:
            pass
        return _empty_invalid_result(audit_id)

def _replace_audit_id(result: GatewayResult, audit_id: int) -> GatewayResult:
    return GatewayResult(
        status=result.status,
        error_code=result.error_code,
        reason=result.reason,
        request_id=result.request_id,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        tool_name=result.tool_name,
        tool_version=result.tool_version,
        policy_decision=result.policy_decision,
        idempotency_key=result.idempotency_key,
        execution_id=result.execution_id,
        result=result.result,
        audit_id=audit_id,
        requires_confirmation=result.requires_confirmation,
        proposal=result.proposal,
        structured_error_override=result.structured_error_override,
    )


def _empty_invalid_result(audit_id: int | None = None) -> GatewayResult:
    return GatewayResult(
        status=VALIDATION_ERROR,
        error_code=INVALID_REQUEST,
        reason="invalid_request_envelope",
        request_id="",
        user_id="",
        tenant_id="",
        tool_name="",
        tool_version="",
        policy_decision="denied",
        idempotency_key=None,
        execution_id=None,
        result=None,
        audit_id=audit_id,
        requires_confirmation=False,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def odoo_client_search_read(client: Any, model: str, domain: list[Any], fields: list[str], limit: int | None) -> list[dict[str, Any]]:
    """Indirect search_read call so tests can stub without importing the client."""
    return client.search_read(model, domain, fields, limit)


def _query_variants(query: str) -> list[str]:
    """Bounded fallback variants for Arabic orthography matching (max 2).

    Odoo ilike matches raw stored names: a bare-typed "احمد" never matches a
    hamza-stored "أحمد" and vice versa. The ladder tries the normalized form
    (hamza/taa/yaa unified) then the hamza-initial form of the first word,
    skipping the definite article "ال". Bounded heuristic — see the ponytail
    comment at the call site.
    """
    variants: list[str] = []
    normalized = normalize_arabic(query)
    if normalized != query:
        variants.append(normalized)
    words = query.split()
    if words and len(words[0]) > 2 and words[0].startswith("ا") and not words[0].startswith("ال"):
        hamza_form = " ".join(["أ" + words[0][1:], *words[1:]])
        if hamza_form != query and hamza_form not in variants:
            variants.append(hamza_form)
    return variants


def _shape_read_result(tool_name: str, records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Project raw Odoo records into the tool output shape defined by the registry."""
    if tool_name == "customer.search":
        customers = [
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "email": record.get("email"),
                "phone": record.get("phone"),
                "street": record.get("street"),
                "city": record.get("city"),
                "credit_limit": record.get("credit_limit"),
            }
            for record in records
        ]
        return {"success": True, "customers": customers, "count": len(customers)}
    if tool_name == "customer.get":
        record = records[0]
        customer = {
            "id": record.get("id"),
            "name": record.get("name"),
            "email": record.get("email"),
            "phone": record.get("phone"),
            "street": record.get("street"),
            "city": record.get("city"),
            "credit_limit": record.get("credit_limit"),
            "vat": record.get("vat"),
        }
        return {"success": True, "customer": customer}
    if tool_name == "product.search":
        products = [
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "default_code": record.get("default_code"),
                "list_price": record.get("list_price"),
                "qty_available": record.get("qty_available"),
            }
            for record in records
        ]
        return {"success": True, "products": products, "count": len(products)}
    if tool_name == "sales.order.get":
        record = records[0]
        partner = record.get("partner_id")
        if isinstance(partner, (list, tuple)):
            partner_id = partner[0] if partner else None
            partner_name = partner[1] if len(partner) > 1 else None
        else:
            partner_id, partner_name = partner, None
        order = {
            "id": record.get("id"),
            "name": record.get("name"),
            "partner_id": partner_id,
            "partner_name": partner_name,
            "state": record.get("state"),
            "amount_total": record.get("amount_total"),
            "amount_untaxed": record.get("amount_untaxed"),
            "date_order": record.get("date_order"),
            "client_order_ref": record.get("client_order_ref"),
        }
        return {"success": True, "order": order}
    return {"success": True, "records": list(records)}

