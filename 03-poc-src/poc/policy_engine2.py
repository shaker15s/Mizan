"""Policy Engine 2.0 (Phase 7) — attribute-based with deterministic decisions.

Extends the simple user→allowed_tools policy in `poc/authz.py` with attribute
evaluation. The v1 implementation remains the baseline; v2 adds risk-aware
decisions and returns (decision, rule_id, policy_version, policy_hash, reason)
so decisions are versioned and auditable.

This module is backward-compatible: the legacy ``PolicyEngine`` in ``authz.py``
is untouched; v2 is exposed as ``PolicyEngine2`` and is wired into the gateway
progressively.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from poc.execution.action import (
    RISK_R0,
    RISK_R1,
    RISK_R2,
    RISK_R3,
    RISK_R4,
    RiskAssessment,
)
from poc.tool_contracts import ToolRegistry, get_registry


POLICY_VERSION = "2.0.0"


@dataclass(frozen=True)
class PolicyDecision2:
    """A versioned, hash-bound authorization decision."""

    decision: str                # "allow" | "deny" | "require_approval" | "require_step_up"
    rule_id: str
    policy_version: str
    policy_hash: str
    reason: str
    requires_approval: bool
    required_approval_level: str | None = None  # "self" | "manager" | "mfa" | "admin"


def _policy_hash(policy_snippet: Any) -> str:
    canonical = json.dumps(policy_snippet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class PolicyEngine2:
    """Attribute-based policy evaluation.

    Inputs:
      * actor (user_id, tenant_id, role, branch_id)
      * tool contract (name, readOnly, risk_level, requiresConfirmation)
      * risk assessment (level, factors)
      * channel (web/voice/api/whatsapp/automation)
    """

    def __init__(
        self,
        policy_path: Path | str = Path(__file__).resolve().parents[1] / "users.yaml",
        registry: ToolRegistry | None = None,
    ) -> None:
        self.registry = registry or get_registry()
        from poc.authz import PolicyEngine
        self._legacy = PolicyEngine(policy_path=policy_path, registry=self.registry)
        # Load raw yaml for hash; reuse the legacy loader path by re-reading.
        with Path(policy_path).open("r", encoding="utf-8") as fh:
            self._raw_policy = yaml.safe_load(fh)
        self.policy_hash = _policy_hash(self._raw_policy)

    def evaluate(
        self,
        *,
        user_id: str,
        tenant_id: str,
        tool_name: str,
        tool_version: str,
        risk: RiskAssessment | None = None,
        channel: str = "web",
    ) -> PolicyDecision2:
        # Run legacy decision first (fail-closed baseline).
        legacy = self._legacy.evaluate({
            "user_id": user_id, "tenant_id": tenant_id,
            "tool_name": tool_name, "tool_version": tool_version,
        })
        if legacy.decision == "denied":
            return PolicyDecision2(
                decision="deny",
                rule_id=legacy.policy_rule_id,
                policy_version=POLICY_VERSION,
                policy_hash=self.policy_hash,
                reason=legacy.reason,
                requires_approval=False,
            )

        contract = self.registry.get(tool_name)
        risk_level = risk.level if risk is not None else contract.get("risk_level", "R0")

        # Map risk → decision
        if risk_level == RISK_R0:
            return PolicyDecision2(
                decision="allow",
                rule_id="RULE_R0_READ_ALLOWED",
                policy_version=POLICY_VERSION,
                policy_hash=self.policy_hash,
                reason="read_operation_r0",
                requires_approval=False,
            )
        if risk_level == RISK_R1:
            return PolicyDecision2(
                decision="allow",
                rule_id="RULE_R1_LOW_RISK_ALLOWED",
                policy_version=POLICY_VERSION,
                policy_hash=self.policy_hash,
                reason="low_risk_reversible",
                requires_approval=False,
            )
        if risk_level == RISK_R2:
            return PolicyDecision2(
                decision="require_approval",
                rule_id="RULE_R2_USER_CONFIRMATION",
                policy_version=POLICY_VERSION,
                policy_hash=self.policy_hash,
                reason="consequential_mutation_requires_user_confirmation",
                requires_approval=True,
                required_approval_level="self",
            )
        if risk_level == RISK_R3:
            return PolicyDecision2(
                decision="require_approval",
                rule_id="RULE_R3_MANAGER_APPROVAL",
                policy_version=POLICY_VERSION,
                policy_hash=self.policy_hash,
                reason="high_impact_requires_manager_approval",
                requires_approval=True,
                required_approval_level="manager",
            )
        if risk_level == RISK_R4:
            return PolicyDecision2(
                decision="require_step_up",
                rule_id="RULE_R4_STEP_UP",
                policy_version=POLICY_VERSION,
                policy_hash=self.policy_hash,
                reason="highly_sensitive_requires_step_up",
                requires_approval=True,
                required_approval_level="mfa",
            )
        return PolicyDecision2(
            decision="deny",
            rule_id="RULE_UNKNOWN_RISK",
            policy_version=POLICY_VERSION,
            policy_hash=self.policy_hash,
            reason="unknown_risk_denied",
            requires_approval=False,
        )
