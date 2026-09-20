"""Tests for Policy Engine 2.0 (Phase 7)."""

from __future__ import annotations

from poc.execution import RISK_R0, RISK_R2, RISK_R3, RISK_R4, RiskAssessment
from poc.policy_engine2 import PolicyEngine2


def _risk(level):
    return RiskAssessment(
        level=level,
        financial_impact="low" if level in (RISK_R0, RISK_R2) else "high",
        reversibility="reversible",
        privilege_level="read" if level == RISK_R0 else "write",
        sensitive_data=False,
        external_side_effect=(level != RISK_R0),
        factors=tuple(),
    )


def test_read_r0_is_allowed(tmp_path):
    eng = PolicyEngine2()
    d = eng.evaluate(
        user_id="readonly_user@test", tenant_id="poc_tenant_001",
        tool_name="customer.search", tool_version="1.0.0",
        risk=_risk(RISK_R0),
    )
    assert d.decision == "allow"
    assert d.policy_version == "2.0.0"
    assert d.requires_approval is False


def test_write_r2_requires_self_confirmation(tmp_path):
    eng = PolicyEngine2()
    d = eng.evaluate(
        user_id="sales_user@test", tenant_id="poc_tenant_001",
        tool_name="sales.order.create", tool_version="1.0.0",
        risk=_risk(RISK_R2),
    )
    assert d.decision == "require_approval"
    assert d.required_approval_level == "self"
    assert d.requires_approval is True


def test_large_qty_r3_requires_manager(tmp_path):
    eng = PolicyEngine2()
    d = eng.evaluate(
        user_id="sales_user@test", tenant_id="poc_tenant_001",
        tool_name="sales.order.create", tool_version="1.0.0",
        risk=_risk(RISK_R3),
    )
    assert d.decision == "require_approval"
    assert d.required_approval_level == "manager"


def test_r4_requires_step_up(tmp_path):
    eng = PolicyEngine2()
    d = eng.evaluate(
        user_id="sales_user@test", tenant_id="poc_tenant_001",
        tool_name="sales.order.create", tool_version="1.0.0",
        risk=_risk(RISK_R4),
    )
    assert d.decision == "require_step_up"
    assert d.required_approval_level == "mfa"


def test_unknown_user_denied(tmp_path):
    eng = PolicyEngine2()
    d = eng.evaluate(
        user_id="nonexistent@test", tenant_id="poc_tenant_001",
        tool_name="customer.search", tool_version="1.0.0",
        risk=_risk(RISK_R0),
    )
    assert d.decision == "deny"
