"""Typed Action Envelope (Phase 3).

The Action type is the canonical internal representation that flows between
AgentRuntime → Policy → Risk → Approval → Lease → Gateway → Verification →
Evidence. Model-generated values NEVER override server-owned identity/policy
fields (plan §10 invariant).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping


def new_action_id() -> str:
    return str(uuid.uuid4())


def new_execution_id() -> str:
    return str(uuid.uuid4())


def new_trace_id() -> str:
    return str(uuid.uuid4())


class RiskLevel(str):
    """Risk classification (plan §13). String-based so it serializes cleanly."""


RISK_R0 = RiskLevel("R0")  # read / no side effect
RISK_R1 = RiskLevel("R1")  # low-impact reversible mutation
RISK_R2 = RiskLevel("R2")  # consequential mutation requiring confirmation
RISK_R3 = RiskLevel("R3")  # high-impact requiring elevated approval
RISK_R4 = RiskLevel("R4")  # highly sensitive / high-value / regulated


@dataclass(frozen=True)
class Actor:
    """Server-owned identity triple. Never supplied by the model or user input."""

    user_id: str
    tenant_id: str
    branch_id: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class Versions:
    """Version snapshot at action creation — used for evidence/audit."""

    tool_version: str
    policy_version: str
    prompt_version: str | None = None
    tool_registry_version: str | None = None
    state_machine_version: str | None = None
    runtime_version: str | None = None


@dataclass(frozen=True)
class RiskAssessment:
    """Deterministic risk classification (plan §13). Factors remain explainable."""

    level: RiskLevel
    financial_impact: str = "unknown"   # "none" | "low" | "medium" | "high"
    reversibility: str = "unknown"     # "reversible" | "partial" | "irreversible"
    privilege_level: str = "unknown"   # "read" | "standard_write" | "elevated"
    sensitive_data: bool = False
    external_side_effect: bool = False
    factors: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdempotencyBinding:
    """Server-computed idempotency binding (carried but never chosen by model)."""

    key: str
    request_fingerprint: str
    semantic_key: str | None = None  # e.g. tenant:user:tool:canonical_args


@dataclass
class Action:
    """Typed action envelope (plan §10).

    Invariants:
      * identity fields (actor) are set by the server, never from model output.
      * versions are set by the server at creation time.
      * arguments are validated against tool inputSchema before construction
        completes.
      * risk is computed deterministically by the Risk engine, not by the LLM.
    """

    action_id: str
    execution_id: str
    trace_id: str
    actor: Actor
    intent: str
    operation: str
    tool_name: str
    arguments: dict[str, Any]
    entities: dict[str, Any] = field(default_factory=dict)
    risk: RiskAssessment | None = None
    versions: Versions | None = None
    idempotency: IdempotencyBinding | None = None
    policy_context: dict[str, Any] = field(default_factory=dict)
    approval_context: dict[str, Any] = field(default_factory=dict)
    channel: str = "web"
    created_at: str | None = None
    natural_language_input: str | None = None
    natural_language_language: str | None = None  # "ar-EG" | "ar-MSA" | …

    def to_public_dict(self) -> dict[str, Any]:
        """Safe projection for logging/API — no secrets, no oversized fields."""
        return {
            "action_id": self.action_id,
            "execution_id": self.execution_id,
            "trace_id": self.trace_id,
            "actor": {
                "user_id": self.actor.user_id,
                "tenant_id": self.actor.tenant_id,
                "branch_id": self.actor.branch_id,
                "role": self.actor.role,
            },
            "intent": self.intent,
            "operation": self.operation,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "entities": dict(self.entities),
            "risk": None if self.risk is None else {
                "level": str(self.risk.level),
                "financial_impact": self.risk.financial_impact,
                "reversibility": self.risk.reversibility,
                "privilege_level": self.risk.privilege_level,
                "sensitive_data": self.risk.sensitive_data,
                "external_side_effect": self.risk.external_side_effect,
                "factors": list(self.risk.factors),
            },
            "versions": None if self.versions is None else {
                "tool_version": self.versions.tool_version,
                "policy_version": self.versions.policy_version,
                "prompt_version": self.versions.prompt_version,
                "tool_registry_version": self.versions.tool_registry_version,
                "state_machine_version": self.versions.state_machine_version,
                "runtime_version": self.versions.runtime_version,
            },
            "channel": self.channel,
        }

    @staticmethod
    def from_tool_call(
        *,
        actor: Actor,
        tool_name: str,
        arguments: Mapping[str, Any],
        intent: str,
        trace_id: str,
        action_id: str | None = None,
        execution_id: str | None = None,
        natural_language_input: str | None = None,
        natural_language_language: str = "ar-EG",
        channel: str = "web",
    ) -> "Action":
        """Helper to build an Action from a validated (tool_name, arguments) pair.

        Callers MUST have already validated arguments against the tool's
        inputSchema before calling this helper.
        """
        return Action(
            action_id=action_id or new_action_id(),
            execution_id=execution_id or new_execution_id(),
            trace_id=trace_id,
            actor=actor,
            intent=intent,
            operation=tool_name,
            tool_name=tool_name,
            arguments=dict(arguments),
            natural_language_input=natural_language_input,
            natural_language_language=natural_language_language,
            channel=channel,
        )
