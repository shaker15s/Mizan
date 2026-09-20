"""Tests for the deterministic risk engine (Phase 7 precursor)."""

from __future__ import annotations

from poc.execution import RISK_R0, RISK_R2, RISK_R3, RISK_R4
from poc.execution.risk import assess_risk


def _contract(**overrides):
    base = {
        "readOnly": True,
        "destructive": False,
        "requiresConfirmation": False,
        "risk_level": "R0",
    }
    base.update(overrides)
    return base


def test_read_tool_is_r0():
    c = _contract()
    risk = assess_risk(c, {"query": "ahmed"})
    assert risk.level == RISK_R0
    assert risk.privilege_level == "read"
    assert risk.external_side_effect is False


def test_write_is_r2_requires_confirmation():
    c = _contract(readOnly=False, requiresConfirmation=True, risk_level="R2")
    risk = assess_risk(
        c,
        {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 5}]},
    )
    assert risk.level == RISK_R2
    assert risk.external_side_effect is True
    assert "mutation_requires_confirmation" in risk.factors


def test_large_quantity_escalates_to_r3():
    c = _contract(readOnly=False, requiresConfirmation=True, risk_level="R2")
    risk = assess_risk(
        c,
        {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2000}]},
    )
    assert risk.level == RISK_R3
    assert risk.financial_impact == "high"
    assert risk.privilege_level == "elevated"


def test_destructive_escalates_to_r4():
    c = _contract(readOnly=False, destructive=True, requiresConfirmation=True, risk_level="R2")
    risk = assess_risk(
        c,
        {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 1}]},
    )
    assert risk.level == RISK_R4
    assert risk.reversibility == "irreversible"
    assert "destructive_operation" in risk.factors
