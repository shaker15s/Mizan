"""Canonical Execution State Machine (Phase 2).

This is the single authoritative lifecycle for every MIZAN execution. It
replaces the seven overlapping state concepts identified in docs/CURRENT_STATE.md §5.

Design goals:
* One set of states and one transition table — no more drift between gateway
  status / proposal state / idempotency state / frontend visual state.
* Every transition carries an explicit event, actor, reason, timestamp, and
  evidence reference so audits can reconstruct *why* a state changed.
* Terminal states are terminal: once SUCCEEDED / FAILED / CANCELLED /
  REJECTED / AMBIGUOUS / RECONCILIATION_REQUIRED / NO_OP / UNKNOWN, no more
  transitions.
* The machine is pure-Python and deterministic — it does not itself perform
  I/O. A separate ExecutionStore (poc/execution/store.py) persists state.
  This lets tests cover every transition in-memory.

States are grouped into four parallel dimensions that move together
(plan §43):

    stage  — where in the lifecycle the execution is
    status — execution-level status aligned with plan §22 truthfulness contract
    security — authorization/security classification
    final  — filled only when the execution reaches a terminal outcome

The (stage, status) pair is what the frontend, policy engine, and harness
grade against. Security is tracked independently so that policy/approval
decisions are a first-class part of the trace.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

STATE_MACHINE_VERSION = "1.1.0"


def state_machine_version() -> str:
    """Return the semantic version of the canonical state machine."""
    return STATE_MACHINE_VERSION


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# States (enums so typos are caught at import time, not at audit time)
# ---------------------------------------------------------------------------

class ExecutionStage(str, Enum):
    """Where in the lifecycle an execution is."""

    # Interaction
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    STREAMING = "streaming"
    RESOLVING = "resolving"
    NEEDS_CLARIFICATION = "needs_clarification"

    # Execution pipeline
    RECEIVED = "received"
    PLANNED = "planned"
    DECISION_SCREENING = "decision_screening"
    POLICY_CHECKING = "policy_checking"
    BLOCKED = "blocked"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AUTHORIZED = "authorized"
    LEASE_ACQUIRED = "lease_acquired"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"

    # Failure / recovery
    FAILED = "failed"
    RETRYING = "retrying"
    AMBIGUOUS = "ambiguous"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    PARTIALLY_COMPLETED = "partially_completed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"

    # Terminal (final)
    SUCCESS = "success"
    NO_OP = "no_op"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ExecutionStatus(str, Enum):
    """User-facing truthful semantics (plan §22)."""

    SUCCESS = "success"
    PENDING = "pending"
    BLOCKED = "blocked"
    DENIED = "denied"
    CANCELLED = "cancelled"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    NO_OP = "no_op"
    UNKNOWN = "unknown"


class SecurityStatus(str, Enum):
    """Security / authorization classification."""

    UNTRUSTED = "untrusted"
    SCREENED = "screened"
    AUTHORIZED = "authorized"
    DENIED = "denied"
    STEP_UP_REQUIRED = "step_up_required"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"


class FinalStatus(str, Enum):
    """Populated only once a terminal stage is reached."""

    SUCCESS = "success"
    NO_OP = "no_op"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    RECONCILIATION_REQUIRED = "reconciliation_required"


# ---------------------------------------------------------------------------
# Events — the only way to transition
# ---------------------------------------------------------------------------

class ExecutionEvent(str, Enum):
    """Events that drive transitions."""

    # Interaction
    USER_MESSAGE_RECEIVED = "user_message_received"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CLARIFICATION_RECEIVED = "clarification_received"
    CONVERSATION_ONLY = "conversation_only"
    CANCELLED_BY_USER = "cancelled_by_user"

    # Pipeline
    INTENT_PARSED = "intent_parsed"
    PLAN_BUILT = "plan_built"
    POLICY_EVALUATED = "policy_evaluated"

    # Decision intelligence screening (plan §41). Only these four outcomes exist:
    # a usable signal, an uncertainty signal, an escalation signal, and a
    # recorded disagreement. None of them is an authorization decision.
    DECISION_REQUESTED = "decision_requested"
    DECISION_COMPLETED = "decision_completed"
    DECISION_UNCERTAIN = "decision_uncertain"
    DECISION_ESCALATED = "decision_escalated"
    DECISION_DISAGREEMENT = "decision_disagreement"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    PROPOSAL_ISSUED = "proposal_issued"
    CONFIRMATION_APPROVED = "confirmation_approved"
    CONFIRMATION_DECLINED = "confirmation_declined"
    PROPOSAL_EXPIRED = "proposal_expired"
    PROPOSAL_EDITED = "proposal_edited"  # re-enters PLANNED
    LEASE_GRANTED = "lease_granted"
    LEASE_EXPIRED = "lease_expired"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_TRANSIENT_FAILURE = "execution_transient_failure"
    EXECUTION_HARD_FAILURE = "execution_hard_failure"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    SUCCESS_CONFIRMED = "success_confirmed"

    # Recovery
    RETRY_SCHEDULED = "retry_scheduled"
    AMBIGUOUS_OUTCOME = "ambiguous_outcome"
    RECONCILIATION_STARTED = "reconciliation_started"
    RECONCILIATION_ADOPTED = "reconciliation_adopted"
    RECONCILIATION_REQUIRES_MANUAL = "reconciliation_requires_manual"
    PARTIAL_COMPLETION_DETECTED = "partial_completion_detected"
    COMPENSATION_STARTED = "compensation_started"
    COMPENSATION_COMPLETED = "compensation_completed"

    # Security
    SECURITY_SCREENED = "security_screened"
    SECURITY_DENIED = "security_denied"
    STEP_UP_REQUESTED = "step_up_requested"
    APPROVAL_REVOKED = "approval_revoked"
    QUARANTINE = "quarantine"


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

# Every entry is (current_stage, event) -> (next_stage, status_after, security_after, final_after)
# If final_after is not None, the transition lands in a terminal state; further
# transitions raise InvalidTransition.
TransitionResult = tuple[ExecutionStage, ExecutionStatus, SecurityStatus, FinalStatus | None]

CANONICAL_TRANSITIONS: dict[tuple[ExecutionStage, ExecutionEvent], TransitionResult] = {
    # --- Idle → interaction --------------------------------------------------
    (ExecutionStage.IDLE, ExecutionEvent.USER_MESSAGE_RECEIVED): (
        ExecutionStage.LISTENING, ExecutionStatus.PENDING, SecurityStatus.UNTRUSTED, None,
    ),

    # --- Listening / thinking / resolving ------------------------------------
    (ExecutionStage.LISTENING, ExecutionEvent.INTENT_PARSED): (
        ExecutionStage.THINKING, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.THINKING, ExecutionEvent.CONVERSATION_ONLY): (
        ExecutionStage.NO_OP, ExecutionStatus.NO_OP, SecurityStatus.SCREENED, FinalStatus.NO_OP,
    ),
    (ExecutionStage.THINKING, ExecutionEvent.CLARIFICATION_REQUESTED): (
        ExecutionStage.NEEDS_CLARIFICATION, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.NEEDS_CLARIFICATION, ExecutionEvent.CLARIFICATION_RECEIVED): (
        ExecutionStage.RESOLVING, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.NEEDS_CLARIFICATION, ExecutionEvent.CANCELLED_BY_USER): (
        ExecutionStage.CANCELLED, ExecutionStatus.CANCELLED, SecurityStatus.SCREENED, FinalStatus.CANCELLED,
    ),
    (ExecutionStage.RESOLVING, ExecutionEvent.PLAN_BUILT): (
        ExecutionStage.PLANNED, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.THINKING, ExecutionEvent.PLAN_BUILT): (
        ExecutionStage.PLANNED, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),

    # --- RECEIVED → PLANNED → POLICY_CHECKING -------------------------------
    (ExecutionStage.PLANNED, ExecutionEvent.SECURITY_SCREENED): (
        ExecutionStage.POLICY_CHECKING, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),

    # --- Decision screening (additive; the pipeline may also skip straight
    #     from PLANNED to POLICY_CHECKING when the decision layer is off) -----
    (ExecutionStage.PLANNED, ExecutionEvent.DECISION_REQUESTED): (
        ExecutionStage.DECISION_SCREENING, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.DECISION_SCREENING, ExecutionEvent.DECISION_COMPLETED): (
        ExecutionStage.POLICY_CHECKING, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.DECISION_SCREENING, ExecutionEvent.DECISION_DISAGREEMENT): (
        ExecutionStage.POLICY_CHECKING, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.DECISION_SCREENING, ExecutionEvent.DECISION_UNCERTAIN): (
        ExecutionStage.NEEDS_CLARIFICATION, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.DECISION_SCREENING, ExecutionEvent.DECISION_ESCALATED): (
        ExecutionStage.POLICY_CHECKING, ExecutionStatus.PENDING, SecurityStatus.STEP_UP_REQUIRED, None,
    ),
    (ExecutionStage.DECISION_SCREENING, ExecutionEvent.QUARANTINE): (
        ExecutionStage.BLOCKED, ExecutionStatus.BLOCKED, SecurityStatus.QUARANTINED, None,
    ),
    (ExecutionStage.DECISION_SCREENING, ExecutionEvent.SECURITY_DENIED): (
        ExecutionStage.REJECTED, ExecutionStatus.DENIED, SecurityStatus.QUARANTINED, FinalStatus.REJECTED,
    ),
    # A decision signal that went missing degrades to the ordinary path instead
    # of blocking the turn (plan §43: a Jev outage is not a MIZAN outage).
    (ExecutionStage.DECISION_SCREENING, ExecutionEvent.EXECUTION_TRANSIENT_FAILURE): (
        ExecutionStage.POLICY_CHECKING, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),

    # --- Policy outcomes -----------------------------------------------------
    (ExecutionStage.POLICY_CHECKING, ExecutionEvent.POLICY_EVALUATED): (
        ExecutionStage.AUTHORIZED, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.POLICY_CHECKING, ExecutionEvent.BLOCKED_BY_POLICY): (
        ExecutionStage.BLOCKED, ExecutionStatus.BLOCKED, SecurityStatus.DENIED, None,
    ),
    (ExecutionStage.POLICY_CHECKING, ExecutionEvent.SECURITY_DENIED): (
        ExecutionStage.REJECTED, ExecutionStatus.DENIED, SecurityStatus.DENIED, FinalStatus.REJECTED,
    ),
    (ExecutionStage.POLICY_CHECKING, ExecutionEvent.STEP_UP_REQUESTED): (
        ExecutionStage.BLOCKED, ExecutionStatus.BLOCKED, SecurityStatus.STEP_UP_REQUIRED, None,
    ),
    (ExecutionStage.BLOCKED, ExecutionEvent.POLICY_EVALUATED): (
        ExecutionStage.AUTHORIZED, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.BLOCKED, ExecutionEvent.SECURITY_DENIED): (
        ExecutionStage.REJECTED, ExecutionStatus.DENIED, SecurityStatus.DENIED, FinalStatus.REJECTED,
    ),
    (ExecutionStage.BLOCKED, ExecutionEvent.CANCELLED_BY_USER): (
        ExecutionStage.CANCELLED, ExecutionStatus.CANCELLED, SecurityStatus.DENIED, FinalStatus.CANCELLED,
    ),

    # --- Approval / confirmation --------------------------------------------
    (ExecutionStage.AUTHORIZED, ExecutionEvent.PROPOSAL_ISSUED): (
        ExecutionStage.AWAITING_CONFIRMATION, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.AUTHORIZED, ExecutionEvent.LEASE_GRANTED): (
        ExecutionStage.LEASE_ACQUIRED, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.AWAITING_CONFIRMATION, ExecutionEvent.CONFIRMATION_APPROVED): (
        ExecutionStage.AUTHORIZED, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.AWAITING_CONFIRMATION, ExecutionEvent.PROPOSAL_EDITED): (
        ExecutionStage.PLANNED, ExecutionStatus.PENDING, SecurityStatus.SCREENED, None,
    ),
    (ExecutionStage.AWAITING_CONFIRMATION, ExecutionEvent.CONFIRMATION_DECLINED): (
        ExecutionStage.CANCELLED, ExecutionStatus.CANCELLED, SecurityStatus.AUTHORIZED, FinalStatus.CANCELLED,
    ),
    (ExecutionStage.AWAITING_CONFIRMATION, ExecutionEvent.PROPOSAL_EXPIRED): (
        ExecutionStage.BLOCKED, ExecutionStatus.BLOCKED, SecurityStatus.REVOKED, None,
    ),
    (ExecutionStage.AWAITING_CONFIRMATION, ExecutionEvent.CANCELLED_BY_USER): (
        ExecutionStage.CANCELLED, ExecutionStatus.CANCELLED, SecurityStatus.AUTHORIZED, FinalStatus.CANCELLED,
    ),
    (ExecutionStage.AWAITING_CONFIRMATION, ExecutionEvent.APPROVAL_REVOKED): (
        ExecutionStage.REJECTED, ExecutionStatus.DENIED, SecurityStatus.REVOKED, FinalStatus.REJECTED,
    ),

    # --- Lease & execution --------------------------------------------------
    (ExecutionStage.LEASE_ACQUIRED, ExecutionEvent.EXECUTION_STARTED): (
        ExecutionStage.EXECUTING, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.LEASE_ACQUIRED, ExecutionEvent.LEASE_EXPIRED): (
        ExecutionStage.BLOCKED, ExecutionStatus.BLOCKED, SecurityStatus.REVOKED, None,
    ),
    (ExecutionStage.EXECUTING, ExecutionEvent.EXECUTION_TRANSIENT_FAILURE): (
        ExecutionStage.RETRYING, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.EXECUTING, ExecutionEvent.EXECUTION_HARD_FAILURE): (
        ExecutionStage.FAILED, ExecutionStatus.FAILED, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.EXECUTING, ExecutionEvent.AMBIGUOUS_OUTCOME): (
        ExecutionStage.AMBIGUOUS, ExecutionStatus.AMBIGUOUS, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.EXECUTING, ExecutionEvent.VERIFICATION_STARTED): (
        ExecutionStage.VERIFYING, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.RETRYING, ExecutionEvent.EXECUTION_STARTED): (
        ExecutionStage.EXECUTING, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.RETRYING, ExecutionEvent.EXECUTION_HARD_FAILURE): (
        ExecutionStage.FAILED, ExecutionStatus.FAILED, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.RETRYING, ExecutionEvent.CANCELLED_BY_USER): (
        ExecutionStage.CANCELLED, ExecutionStatus.CANCELLED, SecurityStatus.AUTHORIZED, FinalStatus.CANCELLED,
    ),

    # --- Verification -------------------------------------------------------
    (ExecutionStage.VERIFYING, ExecutionEvent.VERIFICATION_PASSED): (
        ExecutionStage.COMPLETED, ExecutionStatus.SUCCESS, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.VERIFYING, ExecutionEvent.VERIFICATION_FAILED): (
        ExecutionStage.FAILED, ExecutionStatus.FAILED, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.VERIFYING, ExecutionEvent.AMBIGUOUS_OUTCOME): (
        ExecutionStage.AMBIGUOUS, ExecutionStatus.AMBIGUOUS, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.VERIFYING, ExecutionEvent.PARTIAL_COMPLETION_DETECTED): (
        ExecutionStage.PARTIALLY_COMPLETED, ExecutionStatus.AMBIGUOUS, SecurityStatus.AUTHORIZED, None,
    ),

    # --- Completion to terminal ---------------------------------------------
    (ExecutionStage.COMPLETED, ExecutionEvent.SUCCESS_CONFIRMED): (
        ExecutionStage.SUCCESS, ExecutionStatus.SUCCESS, SecurityStatus.AUTHORIZED, FinalStatus.SUCCESS,
    ),

    # --- Failure terminal ---------------------------------------------------
    (ExecutionStage.FAILED, ExecutionEvent.CANCELLED_BY_USER): (
        ExecutionStage.CANCELLED, ExecutionStatus.CANCELLED, SecurityStatus.AUTHORIZED, FinalStatus.CANCELLED,
    ),

    # --- Ambiguity / reconciliation -----------------------------------------
    (ExecutionStage.AMBIGUOUS, ExecutionEvent.RECONCILIATION_STARTED): (
        ExecutionStage.RECONCILIATION_REQUIRED, ExecutionStatus.RECONCILIATION_REQUIRED, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.PARTIALLY_COMPLETED, ExecutionEvent.COMPENSATION_STARTED): (
        ExecutionStage.COMPENSATING, ExecutionStatus.PENDING, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.COMPENSATING, ExecutionEvent.COMPENSATION_COMPLETED): (
        ExecutionStage.COMPENSATED, ExecutionStatus.FAILED, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.COMPENSATED, ExecutionEvent.EXECUTION_HARD_FAILURE): (
        ExecutionStage.UNKNOWN, ExecutionStatus.UNKNOWN, SecurityStatus.AUTHORIZED, FinalStatus.UNKNOWN,
    ),
    (ExecutionStage.RECONCILIATION_REQUIRED, ExecutionEvent.RECONCILIATION_ADOPTED): (
        ExecutionStage.COMPLETED, ExecutionStatus.SUCCESS, SecurityStatus.AUTHORIZED, None,
    ),
    (ExecutionStage.RECONCILIATION_REQUIRED, ExecutionEvent.RECONCILIATION_REQUIRES_MANUAL): (
        ExecutionStage.UNKNOWN, ExecutionStatus.RECONCILIATION_REQUIRED, SecurityStatus.AUTHORIZED, FinalStatus.RECONCILIATION_REQUIRED,
    ),

    # --- Quarantine (plan §43 security) -------------------------------------
    (ExecutionStage.DECISION_SCREENING, ExecutionEvent.CANCELLED_BY_USER): (
        ExecutionStage.CANCELLED, ExecutionStatus.CANCELLED, SecurityStatus.SCREENED, FinalStatus.CANCELLED,
    ),
    (ExecutionStage.POLICY_CHECKING, ExecutionEvent.QUARANTINE): (
        ExecutionStage.BLOCKED, ExecutionStatus.BLOCKED, SecurityStatus.QUARANTINED, None,
    ),
    (ExecutionStage.AWAITING_CONFIRMATION, ExecutionEvent.QUARANTINE): (
        ExecutionStage.BLOCKED, ExecutionStatus.BLOCKED, SecurityStatus.QUARANTINED, None,
    ),
    (ExecutionStage.EXECUTING, ExecutionEvent.QUARANTINE): (
        ExecutionStage.BLOCKED, ExecutionStatus.BLOCKED, SecurityStatus.QUARANTINED, None,
    ),
}


# Terminal stages are those whose mapped FinalStatus is not None.
TERMINAL_STAGES: frozenset[ExecutionStage] = frozenset(
    {
        ExecutionStage.SUCCESS,
        ExecutionStage.NO_OP,
        ExecutionStage.CANCELLED,
        ExecutionStage.REJECTED,
        ExecutionStage.UNKNOWN,
    }
)


def INITIAL_STATE() -> "ExecutionState":
    """Return a fresh idle state ready to accept a message."""
    return ExecutionState(
        stage=ExecutionStage.IDLE,
        status=ExecutionStatus.PENDING,
        security=SecurityStatus.UNTRUSTED,
        final=None,
        history=(),
    )


# ---------------------------------------------------------------------------
# ExecutionState + transition logic
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionTransition:
    """Record of a single state transition for audit and event streaming."""

    timestamp: str
    event: ExecutionEvent
    from_stage: ExecutionStage
    to_stage: ExecutionStage
    status: ExecutionStatus
    security: SecurityStatus
    final: FinalStatus | None
    actor: str
    reason: str
    evidence_id: str | None
    trace_id: str | None
    execution_id: str | None
    action_id: str | None


class InvalidTransition(RuntimeError):
    """Raised when an event is applied in a stage with no legal transition."""

    def __init__(self, stage: ExecutionStage, event: ExecutionEvent) -> None:
        super().__init__(f"event {event.value!r} is not valid in stage {stage.value!r}")
        self.stage = stage
        self.event = event


@dataclass
class ExecutionState:
    """Authoritative state of a single execution."""

    stage: ExecutionStage
    status: ExecutionStatus
    security: SecurityStatus
    final: FinalStatus | None
    history: tuple[ExecutionTransition, ...] = field(default_factory=tuple)
    execution_id: str | None = None
    action_id: str | None = None
    trace_id: str | None = None
    last_updated: str = field(default_factory=_utc_now)

    # ---- query helpers -----------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES or self.final is not None

    @property
    def is_pending_user_action(self) -> bool:
        """True when the execution is waiting for a human (clarification, confirmation)."""
        return self.stage in {
            ExecutionStage.NEEDS_CLARIFICATION,
            ExecutionStage.AWAITING_CONFIRMATION,
            ExecutionStage.RECONCILIATION_REQUIRED,
            ExecutionStage.BLOCKED
            and self.security == SecurityStatus.STEP_UP_REQUIRED,
        }

    @property
    def can_emit_frontend_progress(self) -> bool:
        """True when the frontend rail should show the pipeline as active."""
        return self.stage in {
            ExecutionStage.THINKING,
            ExecutionStage.RESOLVING,
            ExecutionStage.PLANNED,
            ExecutionStage.POLICY_CHECKING,
            ExecutionStage.AUTHORIZED,
            ExecutionStage.LEASE_ACQUIRED,
            ExecutionStage.EXECUTING,
            ExecutionStage.VERIFYING,
        }

    # ---- transition --------------------------------------------------------
    def apply(
        self,
        event: ExecutionEvent,
        *,
        actor: str = "system",
        reason: str = "",
        evidence_id: str | None = None,
        timestamp: str | None = None,
    ) -> "ExecutionState":
        """Apply an event and return the next state (immutable).

        The current state is not mutated.
        """
        if self.is_terminal:
            raise InvalidTransition(self.stage, event)
        key = (self.stage, event)
        target = CANONICAL_TRANSITIONS.get(key)
        if target is None:
            raise InvalidTransition(self.stage, event)
        next_stage, next_status, next_security, next_final = target
        ts = timestamp or _utc_now()
        transition = ExecutionTransition(
            timestamp=ts,
            event=event,
            from_stage=self.stage,
            to_stage=next_stage,
            status=next_status,
            security=next_security,
            final=next_final,
            actor=actor,
            reason=reason,
            evidence_id=evidence_id,
            trace_id=self.trace_id,
            execution_id=self.execution_id,
            action_id=self.action_id,
        )
        return ExecutionState(
            stage=next_stage,
            status=next_status,
            security=next_security,
            final=next_final,
            history=(*self.history, transition),
            execution_id=self.execution_id,
            action_id=self.action_id,
            trace_id=self.trace_id,
            last_updated=ts,
        )

    # ---- projection helpers ------------------------------------------------
    def to_public_dict(self) -> dict[str, Any]:
        """JSON-safe projection for the frontend/API (no internal history leak)."""
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "security": self.security.value,
            "final": self.final.value if self.final is not None else None,
            "is_terminal": self.is_terminal,
            "pending_user_action": self.is_pending_user_action,
            "last_updated": self.last_updated,
            "execution_id": self.execution_id,
            "action_id": self.action_id,
            "trace_id": self.trace_id,
        }


def project_to_gateway_status(state: ExecutionState) -> str:
    """Map canonical state back to the existing gateway status strings.

    This is a COMPATIBILITY adapter so the existing ToolGateway and web_server
    keep working without changes while we migrate callers. As callers move
    onto ExecutionState directly, this function will shrink and disappear.
    """
    stage = state.stage
    if stage == ExecutionStage.SUCCESS:
        return "accepted"
    if stage == ExecutionStage.NO_OP:
        return "accepted"
    if stage == ExecutionStage.AWAITING_CONFIRMATION:
        return "confirmation_required"
    if stage == ExecutionStage.CANCELLED:
        return "declined"
    if stage == ExecutionStage.REJECTED:
        return "denied"
    if stage == ExecutionStage.BLOCKED:
        if state.security == SecurityStatus.DENIED:
            return "denied"
        if state.security == SecurityStatus.STEP_UP_REQUIRED:
            return "denied"
        return "in_progress"
    if stage == ExecutionStage.FAILED:
        return "erp_error"
    if stage == ExecutionStage.AMBIGUOUS:
        return "reconciliation_required"
    if stage == ExecutionStage.RECONCILIATION_REQUIRED:
        return "reconciliation_required"
    if stage in {
        ExecutionStage.EXECUTING,
        ExecutionStage.VERIFYING,
        ExecutionStage.LEASE_ACQUIRED,
        ExecutionStage.RETRYING,
    }:
        return "in_progress"
    return "accepted"


def project_to_proposal_state(state: ExecutionState) -> str | None:
    """Map canonical stage back to the existing proposals.state value.

    Returns None when the execution is not in the confirmation window.
    """
    stage = state.stage
    if stage == ExecutionStage.AWAITING_CONFIRMATION:
        return "proposed"
    if stage == ExecutionStage.AUTHORIZED and state.history:
        # After CONFIRMATION_APPROVED, the proposal is "confirmed"
        last = state.history[-1]
        if last.event == ExecutionEvent.CONFIRMATION_APPROVED:
            return "confirmed"
    if stage == ExecutionStage.CANCELLED:
        return "failed"
    if stage in {
        ExecutionStage.EXECUTING,
        ExecutionStage.VERIFYING,
        ExecutionStage.LEASE_ACQUIRED,
    }:
        return "executing"
    if stage == ExecutionStage.COMPLETED or stage == ExecutionStage.SUCCESS:
        return "completed"
    if stage == ExecutionStage.FAILED:
        return "failed"
    return None


def new_execution_identity() -> tuple[str, str, str]:
    """Generate fresh trace_id, action_id, execution_id UUIDs."""
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
