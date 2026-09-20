"""Deterministic Risk Engine (Phase 7 precursor).

A small but principled risk classifier. Risk is computed from the tool
contract's declared metadata and the operation arguments — never from LLM
opinion. Output is a :class:`RiskAssessment` (see action.py) with explainable
factors.

This v1 classifier is intentionally conservative and small. We will extend
factors (velocity, anomaly, channel, history) as the policy engine grows.
"""

from __future__ import annotations

from typing import Any, Mapping

from poc.execution.action import (
    RISK_R0,
    RISK_R1,
    RISK_R2,
    RISK_R3,
    RISK_R4,
    RiskAssessment,
    RiskLevel,
)


def assess_risk(contract: Mapping[str, Any], arguments: Mapping[str, Any]) -> RiskAssessment:
    """Classify the risk of a tool invocation deterministically.

    Inputs:
      * contract   — a server-owned tool contract (must contain readOnly,
                     destructive, risk_level, requiresConfirmation).
      * arguments  — already-schema-validated tool arguments.
    """
    factors: list[str] = []

    read_only = bool(contract.get("readOnly", True))
    destructive = bool(contract.get("destructive", False))
    requires_confirmation = bool(contract.get("requiresConfirmation", False))
    declared_level = str(contract.get("risk_level", "R0"))

    if read_only:
        factors.append("read_only")
        level: RiskLevel = RISK_R0
        financial_impact = "none"
        reversibility = "n/a"
        privilege_level = "read"
        external_side_effect = False
    else:
        external_side_effect = True
        privilege_level = "standard_write"
        financial_impact = "unknown"
        reversibility = "reversible"
        level = RISK_R2
        factors.append("mutation_requires_confirmation")

        # Large-quantity escalation → R3 (configurable threshold; POC default 1000 units)
        lines = arguments.get("lines")
        if isinstance(lines, list):
            total_qty = 0.0
            for line in lines:
                if isinstance(line, Mapping):
                    q = line.get("quantity")
                    if isinstance(q, (int, float)) and not isinstance(q, bool):
                        total_qty += float(q)
            if total_qty >= 1000:
                level = RISK_R3
                financial_impact = "high"
                privilege_level = "elevated"
                factors.append(f"large_quantity={total_qty:g}")

        if destructive:
            level = RISK_R4
            reversibility = "irreversible"
            financial_impact = "high"
            privilege_level = "elevated"
            factors.append("destructive_operation")

    # Honour a declared risk_level from the contract if it classifies higher
    # than our heuristic (never lower — fail-closed).
    declared = {
        "R0": RISK_R0, "R1": RISK_R1, "R2": RISK_R2, "R3": RISK_R3, "R4": RISK_R4,
    }.get(declared_level)
    if declared is not None and _risk_rank(declared) > _risk_rank(level):
        level = declared
        factors.append(f"contract_declared_{declared_level}")

    if requires_confirmation and level == RISK_R0:
        # A read-only tool marked requires_confirmation is unusual; honour it.
        level = RISK_R1
        factors.append("read_requires_confirmation")

    return RiskAssessment(
        level=level,
        financial_impact=financial_impact,
        reversibility=reversibility,
        privilege_level=privilege_level,
        sensitive_data=False,
        external_side_effect=external_side_effect,
        factors=tuple(factors),
    )


def _risk_rank(level: RiskLevel) -> int:
    order = {RISK_R0: 0, RISK_R1: 1, RISK_R2: 2, RISK_R3: 3, RISK_R4: 4}
    return order.get(level, 0)
