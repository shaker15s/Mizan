"""Canonical execution state machine for MIZAN.

This package introduces ONE authoritative execution lifecycle. All other state
concepts (gateway status, proposal state, idempotency state, frontend visual
state) become *projections* of this state machine rather than independent
sources of truth.

See:
* docs/CANONICAL_STATE_MACHINE.md — full machine definition
* plan.md §43                       — north-star states
"""

from poc.execution.state_machine import (
    ExecutionStatus,
    ExecutionStage,
    SecurityStatus,
    FinalStatus,
    ExecutionState,
    ExecutionEvent,
    InvalidTransition,
    CANONICAL_TRANSITIONS,
    INITIAL_STATE,
    TERMINAL_STAGES,
    state_machine_version,
)
from poc.execution.action import (
    Actor,
    Action,
    RiskLevel,
    RiskAssessment,
    RISK_R0,
    RISK_R1,
    RISK_R2,
    RISK_R3,
    RISK_R4,
    new_action_id,
    new_execution_id,
    new_trace_id,
)
from poc.execution.lease import (
    ExecutionLease,
    LeaseState,
    LeaseStore,
    LeaseError,
    LeaseNotOwnedError,
    LeaseAlreadyHeldError,
)
from poc.execution.risk import assess_risk
from poc.execution.state_machine import (
    project_to_gateway_status,
    project_to_proposal_state,
    new_execution_identity,
)
from poc.execution.store import ExecutionStore

__all__ = [
    "ExecutionStatus",
    "ExecutionStage",
    "SecurityStatus",
    "FinalStatus",
    "ExecutionState",
    "ExecutionEvent",
    "InvalidTransition",
    "CANONICAL_TRANSITIONS",
    "INITIAL_STATE",
    "TERMINAL_STAGES",
    "state_machine_version",
    "Actor",
    "Action",
    "RiskLevel",
    "RiskAssessment",
    "RISK_R0",
    "RISK_R1",
    "RISK_R2",
    "RISK_R3",
    "RISK_R4",
    "new_action_id",
    "new_execution_id",
    "new_trace_id",
    "ExecutionLease",
    "LeaseState",
    "LeaseStore",
    "LeaseError",
    "LeaseNotOwnedError",
    "LeaseAlreadyHeldError",
    "assess_risk",
    "project_to_gateway_status",
    "project_to_proposal_state",
    "new_execution_identity",
    "ExecutionStore",
]
