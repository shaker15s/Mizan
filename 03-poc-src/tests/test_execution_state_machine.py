"""Tests for the canonical execution state machine (Phase 2)."""

from __future__ import annotations

import pytest

from poc.execution import (
    CANONICAL_TRANSITIONS,
    ExecutionEvent,
    ExecutionStage,
    ExecutionStatus,
    FinalStatus,
    INITIAL_STATE,
    InvalidTransition,
    SecurityStatus,
    state_machine_version,
    project_to_gateway_status,
    project_to_proposal_state,
)


class TestStateMachineMetadata:
    def test_version_is_semver(self):
        v = state_machine_version()
        parts = v.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_transition_table_keys_use_valid_enums(self):
        for (stage, event), target in CANONICAL_TRANSITIONS.items():
            assert isinstance(stage, ExecutionStage)
            assert isinstance(event, ExecutionEvent)
            next_stage, status, security, final = target
            assert isinstance(next_stage, ExecutionStage)
            assert isinstance(status, ExecutionStatus)
            assert isinstance(security, SecurityStatus)
            assert final is None or isinstance(final, FinalStatus)

    def test_initial_state_is_idle(self):
        s = INITIAL_STATE()
        assert s.stage == ExecutionStage.IDLE
        assert s.status == ExecutionStatus.PENDING
        assert s.security == SecurityStatus.UNTRUSTED
        assert s.final is None
        assert not s.is_terminal


class TestHappyPathWrite:
    def test_full_write_lifecycle(self):
        s = INITIAL_STATE()
        s.trace_id = "t"
        s.action_id = "a"
        s.execution_id = "e"

        path = [
            (ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionStage.LISTENING),
            (ExecutionEvent.INTENT_PARSED, ExecutionStage.THINKING),
            (ExecutionEvent.PLAN_BUILT, ExecutionStage.PLANNED),
            (ExecutionEvent.SECURITY_SCREENED, ExecutionStage.POLICY_CHECKING),
            (ExecutionEvent.POLICY_EVALUATED, ExecutionStage.AUTHORIZED),
            (ExecutionEvent.PROPOSAL_ISSUED, ExecutionStage.AWAITING_CONFIRMATION),
            (ExecutionEvent.CONFIRMATION_APPROVED, ExecutionStage.AUTHORIZED),
            (ExecutionEvent.LEASE_GRANTED, ExecutionStage.LEASE_ACQUIRED),
            (ExecutionEvent.EXECUTION_STARTED, ExecutionStage.EXECUTING),
            (ExecutionEvent.VERIFICATION_STARTED, ExecutionStage.VERIFYING),
            (ExecutionEvent.VERIFICATION_PASSED, ExecutionStage.COMPLETED),
            (ExecutionEvent.SUCCESS_CONFIRMED, ExecutionStage.SUCCESS),
        ]
        expected_events = []
        for evt, expected_stage in path:
            s = s.apply(evt, actor=("user:u1" if evt == ExecutionEvent.CONFIRMATION_APPROVED else "system"))
            expected_events.append(evt)
            assert s.stage == expected_stage, f"after {evt.value}"
        assert s.is_terminal
        assert s.final == FinalStatus.SUCCESS
        assert s.security == SecurityStatus.AUTHORIZED
        events = [t.event for t in s.history]
        assert events == expected_events

    def test_terminal_state_rejects_events(self):
        s = INITIAL_STATE()
        s = s.apply(ExecutionEvent.USER_MESSAGE_RECEIVED)
        s = s.apply(ExecutionEvent.INTENT_PARSED)
        # CONVERSATION_ONLY is valid from THINKING
        s_noop = s.apply(ExecutionEvent.CONVERSATION_ONLY)
        assert s_noop.stage == ExecutionStage.NO_OP
        assert s_noop.is_terminal
        with pytest.raises(InvalidTransition):
            s_noop.apply(ExecutionEvent.USER_MESSAGE_RECEIVED)


class TestPolicyDenial:
    def test_policy_deny_terminates(self):
        s = INITIAL_STATE()
        s = s.apply(ExecutionEvent.USER_MESSAGE_RECEIVED)
        s = s.apply(ExecutionEvent.INTENT_PARSED)
        s = s.apply(ExecutionEvent.PLAN_BUILT)
        s = s.apply(ExecutionEvent.SECURITY_SCREENED)
        s = s.apply(ExecutionEvent.SECURITY_DENIED, reason="cross-tenant attempt")
        assert s.stage == ExecutionStage.REJECTED
        assert s.status == ExecutionStatus.DENIED
        assert s.is_terminal
        assert s.final == FinalStatus.REJECTED


class TestProposalLifecycle:
    def test_decline_cancels(self):
        s = _to_stage(ExecutionStage.AWAITING_CONFIRMATION)
        s = s.apply(ExecutionEvent.CONFIRMATION_DECLINED, actor="user:u1")
        assert s.stage == ExecutionStage.CANCELLED
        assert s.final == FinalStatus.CANCELLED

    def test_edit_reenters_planned(self):
        s = _to_stage(ExecutionStage.AWAITING_CONFIRMATION)
        s = s.apply(ExecutionEvent.PROPOSAL_EDITED, reason="user changed qty 15→20")
        assert s.stage == ExecutionStage.PLANNED
        assert s.security == SecurityStatus.SCREENED

    def test_expiry_blocks(self):
        s = _to_stage(ExecutionStage.AWAITING_CONFIRMATION)
        s = s.apply(ExecutionEvent.PROPOSAL_EXPIRED)
        assert s.stage == ExecutionStage.BLOCKED
        assert s.security == SecurityStatus.REVOKED


class TestAmbiguityAndRecovery:
    def test_ambiguity_after_write_goes_to_ambiguous(self):
        s = _to_stage(ExecutionStage.EXECUTING)
        s = s.apply(ExecutionEvent.AMBIGUOUS_OUTCOME, reason="timeout after create")
        assert s.stage == ExecutionStage.AMBIGUOUS
        assert s.status == ExecutionStatus.AMBIGUOUS
        assert not s.is_terminal

    def test_reconciliation_manual_is_terminal(self):
        s = _to_stage(ExecutionStage.EXECUTING)
        s = s.apply(ExecutionEvent.AMBIGUOUS_OUTCOME)
        s = s.apply(ExecutionEvent.RECONCILIATION_STARTED)
        assert s.stage == ExecutionStage.RECONCILIATION_REQUIRED
        s = s.apply(ExecutionEvent.RECONCILIATION_REQUIRES_MANUAL)
        assert s.is_terminal
        assert s.final == FinalStatus.RECONCILIATION_REQUIRED

    def test_reconciliation_adopted_completes(self):
        s = _to_stage(ExecutionStage.EXECUTING)
        s = s.apply(ExecutionEvent.AMBIGUOUS_OUTCOME)
        s = s.apply(ExecutionEvent.RECONCILIATION_STARTED)
        s = s.apply(ExecutionEvent.RECONCILIATION_ADOPTED, evidence_id="odoo:42")
        assert s.stage == ExecutionStage.COMPLETED
        assert s.status == ExecutionStatus.SUCCESS


class TestRetry:
    def test_transient_failure_retries_then_succeeds(self):
        s = _to_stage(ExecutionStage.EXECUTING)
        s = s.apply(ExecutionEvent.EXECUTION_TRANSIENT_FAILURE)
        assert s.stage == ExecutionStage.RETRYING
        s = s.apply(ExecutionEvent.EXECUTION_STARTED)
        assert s.stage == ExecutionStage.EXECUTING
        s = s.apply(ExecutionEvent.VERIFICATION_STARTED)
        s = s.apply(ExecutionEvent.VERIFICATION_PASSED)
        s = s.apply(ExecutionEvent.SUCCESS_CONFIRMED)
        assert s.stage == ExecutionStage.SUCCESS

    def test_hard_failure_finally_fails(self):
        s = _to_stage(ExecutionStage.RETRYING)
        s = s.apply(ExecutionEvent.EXECUTION_HARD_FAILURE)
        assert s.stage == ExecutionStage.FAILED


class TestPublicProjection:
    def test_to_public_dict(self):
        s = _to_stage(ExecutionStage.EXECUTING)
        d = s.to_public_dict()
        assert d["stage"] == "executing"
        assert d["status"] == "pending"
        assert d["security"] == "authorized"
        assert d["is_terminal"] is False
        # No history / private fields leaked.
        assert "history" not in d


class TestGatewayProjection:
    """project_to_gateway_status keeps legacy code working during migration."""

    def test_success(self):
        s = _to_stage(ExecutionStage.SUCCESS)
        assert project_to_gateway_status(s) == "accepted"

    def test_awaiting_confirmation(self):
        s = _to_stage(ExecutionStage.AWAITING_CONFIRMATION)
        assert project_to_gateway_status(s) == "confirmation_required"

    def test_rejected(self):
        s = _to_stage(ExecutionStage.REJECTED)
        assert project_to_gateway_status(s) == "denied"

    def test_ambiguous(self):
        s = _to_stage(ExecutionStage.AMBIGUOUS)
        assert project_to_gateway_status(s) == "reconciliation_required"

    def test_executing(self):
        s = _to_stage(ExecutionStage.EXECUTING)
        assert project_to_gateway_status(s) == "in_progress"


class TestProposalProjection:
    def test_proposed(self):
        assert project_to_proposal_state(_to_stage(ExecutionStage.AWAITING_CONFIRMATION)) == "proposed"

    def test_confirmed(self):
        s = _to_stage(ExecutionStage.AWAITING_CONFIRMATION)
        s = s.apply(ExecutionEvent.CONFIRMATION_APPROVED)
        assert project_to_proposal_state(s) == "confirmed"

    def test_completed(self):
        s = _to_stage(ExecutionStage.SUCCESS)
        assert project_to_proposal_state(s) == "completed"


def _to_stage(target: ExecutionStage):
    """Drive the machine from IDLE to target via a happy-path script."""
    # A dictionary of simple happy-path plans keyed by target.
    plans: dict[ExecutionStage, list[ExecutionEvent]] = {
        ExecutionStage.LISTENING: [ExecutionEvent.USER_MESSAGE_RECEIVED],
        ExecutionStage.THINKING: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED],
        ExecutionStage.NEEDS_CLARIFICATION: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.CLARIFICATION_REQUESTED],
        ExecutionStage.RESOLVING: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.CLARIFICATION_REQUESTED, ExecutionEvent.CLARIFICATION_RECEIVED],
        ExecutionStage.PLANNED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT],
        ExecutionStage.POLICY_CHECKING: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED],
        ExecutionStage.AUTHORIZED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED],
        ExecutionStage.AWAITING_CONFIRMATION: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED],
        ExecutionStage.LEASE_ACQUIRED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED],
        ExecutionStage.EXECUTING: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED, ExecutionEvent.EXECUTION_STARTED],
        ExecutionStage.VERIFYING: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED, ExecutionEvent.EXECUTION_STARTED, ExecutionEvent.VERIFICATION_STARTED],
        ExecutionStage.COMPLETED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED, ExecutionEvent.EXECUTION_STARTED, ExecutionEvent.VERIFICATION_STARTED, ExecutionEvent.VERIFICATION_PASSED],
        ExecutionStage.SUCCESS: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED, ExecutionEvent.EXECUTION_STARTED, ExecutionEvent.VERIFICATION_STARTED, ExecutionEvent.VERIFICATION_PASSED, ExecutionEvent.SUCCESS_CONFIRMED],
        ExecutionStage.RETRYING: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED, ExecutionEvent.EXECUTION_STARTED, ExecutionEvent.EXECUTION_TRANSIENT_FAILURE],
        ExecutionStage.FAILED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED, ExecutionEvent.EXECUTION_STARTED, ExecutionEvent.EXECUTION_HARD_FAILURE],
        ExecutionStage.AMBIGUOUS: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED, ExecutionEvent.EXECUTION_STARTED, ExecutionEvent.AMBIGUOUS_OUTCOME],
        ExecutionStage.RECONCILIATION_REQUIRED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.LEASE_GRANTED, ExecutionEvent.EXECUTION_STARTED, ExecutionEvent.AMBIGUOUS_OUTCOME, ExecutionEvent.RECONCILIATION_STARTED],
        ExecutionStage.BLOCKED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.BLOCKED_BY_POLICY],
        ExecutionStage.REJECTED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.SECURITY_DENIED],
        ExecutionStage.CANCELLED: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.PLAN_BUILT, ExecutionEvent.SECURITY_SCREENED, ExecutionEvent.POLICY_EVALUATED, ExecutionEvent.PROPOSAL_ISSUED, ExecutionEvent.CONFIRMATION_DECLINED],
        ExecutionStage.NO_OP: [ExecutionEvent.USER_MESSAGE_RECEIVED, ExecutionEvent.INTENT_PARSED, ExecutionEvent.CONVERSATION_ONLY],
    }
    path = plans.get(target)
    if path is None:
        raise AssertionError(f"no plan for target {target}")
    s = INITIAL_STATE()
    for ev in path:
        actor = "user:u1" if ev in {ExecutionEvent.CONFIRMATION_APPROVED, ExecutionEvent.CONFIRMATION_DECLINED} else "system"
        s = s.apply(ev, actor=actor)
    assert s.stage == target, f"expected {target.value}, got {s.stage.value}"
    return s


def _apply_all(state, events):
    for ev in events:
        state = state.apply(ev)
    return state
