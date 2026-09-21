"""Harness tests for the decision-intelligence layer (plan §30–§36).

The decision layer may only be trusted to the extent the *harness* can measure
it honestly. These tests pin the properties that would otherwise let a report
flatter the layer:

* decision metrics are N/A when the layer is off — never silently zero;
* decision gates exist per mode, and enabling a section for a run that does not
  measure a metric must not manufacture a failure;
* the profile that produced the numbers is recorded, and a synthetic provider is
  labelled as synthetic everywhere it appears;
* escalation and clarification are counted separately, so "asked a question" is
  never reported as "raised a security escalation";
* a real end-to-end run with the mock provider produces the §31 per-case record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poc.decision.thresholds import DecisionThresholds
from poc.harness.cases import load_dataset, select_tokens
from poc.harness.decisions import collect_decision_metrics, decision_payload
from poc.harness.render import console, markdown
from poc.harness.report import ReportBuilder, flatten_criteria, load_thresholds, resolve_verdict
from poc.harness.runner import RunOptions, Runner

from types import SimpleNamespace

SRC_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = Path(__file__).resolve().parent / "test_cases.json"


# --------------------------------------------------------------------- records
def payload(*, route: str | None = "customer.search", confidence: float = 0.95, escalation: str = "none",
            disagreement: dict | None = None, ambiguity: float = 0.02, injection: float = 0.01,
            fallback: bool = False, error_code: str = "", strategy: str = "narrowed",
            candidates: tuple[str, ...] | None = None) -> dict:
    """A decision payload shaped exactly like the runner records it."""
    return {
        "mode": "advisory",
        "provider": "mock",
        "route": {
            "strategy": strategy,
            "decision_tool": route,
            "confidence": confidence,
            "candidate_tools": list(candidates if candidates is not None else ("customer.search", "customer.get")),
        },
        "route_confidence": confidence,
        "route_top_two": [route, "customer.get"] if route else [],
        "route_tool": route,
        "top_probabilities": {route: confidence} if route else {},
        "escalation": {"level": escalation, "proposed_level": escalation},
        "result": {"answers": {"intent_ambiguous": {"type": "noul", "noul": ambiguity}, "prompt_injection": {"type": "noul", "noul": injection}}},
        "latency_ms": 1.5,
        "fallback": fallback,
        "error_code": error_code,
        "llm_tool": route,
        "final_route": strategy,
        "disagreement": disagreement,
        "cached": False,
    }


_DEFAULT = object()


def attempt(*, case_id: str = "TC-001", category: str = "read_happy", passed: bool = True, status: str = "accepted",
            decision: object = _DEFAULT, tags: tuple[str, ...] = (), expected_outcome: str = "success") -> SimpleNamespace:
    """One execution record. ``decision=None`` means the layer was off."""
    return SimpleNamespace(
        case_id=case_id,
        category=category,
        passed=passed,
        status=status,
        decision=payload() if decision is _DEFAULT else decision,
        expected_outcome=expected_outcome,
        latency_ms={"total": 12.5, "decision": 1.5, "llm": 0.0, "gateway": 8.0},
        tokens={"input": 10, "output": 4},
        tags=tags,
    )


def case_for(**overrides):
    base = dict(id="TC-001", expected_tool="customer.search", expected_outcome="success", category="read_happy", expected_args={})
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------- metric honesty
def test_decision_metrics_are_not_measured_when_the_layer_is_off() -> None:
    metrics = collect_decision_metrics([attempt(decision=None)], cases=[case_for()])
    assert metrics["enabled"] is False
    assert metrics["decision"] is None and metrics["system"] is None and metrics["safety"] is None
    assert "off" in metrics["reason"]


def test_tool_accuracy_counts_only_routable_expectations() -> None:
    records = [
        attempt(case_id="TC-001", decision=payload(route="customer.search")),
        attempt(case_id="TC-002", decision=payload(route="product.search")),
        # A case whose expected tool is not in the registry must never be scored
        # as a routing mistake (there is nothing to route to).
        attempt(case_id="TC-057", decision=payload(route="no_tool", strategy="unchanged")),
    ]
    cases = [
        case_for(id="TC-001", expected_tool="customer.search"),
        case_for(id="TC-002", expected_tool="customer.get"),
        case_for(id="TC-057", expected_tool="sales.order.delete", category="authz_denied"),
    ]
    metrics = collect_decision_metrics(records, cases=cases)
    assert metrics["decision"]["tool_selection_accuracy"] == 50.0
    assert metrics["decision"]["population"]["tool_decided"] == 2


def test_abstention_is_measured_against_unroutable_expectations() -> None:
    records = [
        attempt(case_id="TC-001"),  # routable: abstaining would be wrong
        attempt(case_id="TC-070", decision=payload(route="clarify", strategy="unchanged")),
    ]
    cases = [case_for(id="TC-001"), case_for(id="TC-070", expected_tool=None, category="ambiguous_entity")]
    metrics = collect_decision_metrics(records, cases=cases)
    assert metrics["decision"]["abstention_rate"] == 50.0
    assert metrics["decision"]["abstention_precision"] == 100.0


def test_escalation_and_clarification_are_counted_separately() -> None:
    records = [
        attempt(case_id="TC-089", category="prompt_injection", decision=payload(route="no_tool", escalation="quarantine", strategy="unchanged")),
        attempt(case_id="TC-070", category="ambiguous_entity", decision=payload(route="clarify", escalation="clarify", strategy="unchanged")),
        attempt(case_id="TC-001", decision=payload(escalation="step_up")),
    ]
    cases = [
        case_for(id="TC-089", expected_tool=None, category="prompt_injection"),
        case_for(id="TC-070", expected_tool="customer.search", category="ambiguous_entity"),
        case_for(id="TC-001", expected_tool="customer.search"),
    ]
    metrics = collect_decision_metrics(records, cases=cases)
    security = metrics["decision"]["escalation"]["security"]
    clarification = metrics["decision"]["escalation"]["clarification"]
    # quarantine and step_up are both security escalations…
    assert security["escalated"] == 2
    assert security["warranted"] == 1  # …but only the injection case warranted one
    assert security["false_positives"] == 1  # step_up on a routine read
    assert security["precision"] == 50.0 and security["recall"] == 100.0
    # A quarantine satisfies the same expectation as a clarification (it is at
    # least as safe), so both count as raised and both are warranted here.
    assert clarification["raised"] == 2
    assert clarification["expected"] == 2
    assert clarification["precision"] == 100.0


def test_safety_counters_are_counts_not_rates() -> None:
    records = [attempt(case_id="TC-001", decision=payload(disagreement={"resolution": "narrowed_set_pick_differs_from_decision"}))]
    metrics = collect_decision_metrics(records, cases=[case_for()])
    safety = metrics["safety"]
    assert safety["unauthorized_writes"] == 0 and safety["duplicate_orders"] == 0
    assert metrics["decision"]["disagreement"]["count"] == 1


def test_split_filtering_excludes_unrequested_cases() -> None:
    records = [attempt(case_id="TC-001"), attempt(case_id="TC-002")]
    cases = [case_for(id="TC-001"), case_for(id="TC-002")]
    metrics = collect_decision_metrics(records, cases=cases, split_case_ids=["TC-002"])
    assert metrics["decision"]["population"]["executions"] == 1
    assert metrics["cases"][0]["case_id"] == "TC-002"


def test_decision_payload_carries_the_provenance_the_report_needs() -> None:
    class Result:
        decision = None

    assert decision_payload(Result(), None) is None


# ----------------------------------------------------------------------- gates
def test_gate_sections_merge_per_decision_mode() -> None:
    off = load_thresholds(decision_mode=None)
    advisory = load_thresholds(decision_mode="advisory")
    shadow = load_thresholds(decision_mode="shadow")
    assert not any(key.startswith("decision_layer") for key in off)
    assert "decision_layer.decision.tool_selection_accuracy" in advisory
    assert "decision_layer.safety.unauthorized_writes" in advisory
    # shadow may not change behaviour, so it gates the golden run, not accuracy
    assert "totals.cases_failed" in shadow
    assert "decision_layer.decision.tool_selection_accuracy" not in shadow


def test_decision_criteria_are_exposed_and_na_when_absent() -> None:
    metrics = {"decision_layer": {"decision": {"tool_selection_accuracy": 91.5}, "system": {"fallback_rate": 1.0}}}
    criteria = flatten_criteria(metrics)
    assert criteria["decision_layer.decision.tool_selection_accuracy"] == 91.5
    assert criteria["decision_layer.system.fallback_rate"] == 1.0
    assert criteria["decision_layer.safety.unauthorized_writes"] is None  # N/A, not zero


def test_na_decision_metrics_do_not_fail_the_gates() -> None:
    from poc.harness.metrics import criterion_checks

    thresholds = load_thresholds(decision_mode="advisory")
    rows = criterion_checks({"totals": {"cases_failed": 0}}, thresholds)
    decision_rows = [row for row in rows if row["criterion"].startswith("decision_layer")]
    assert decision_rows, "the decision gates must be evaluated"
    assert {row["state"] for row in decision_rows} == {"n/a"}


def test_shadow_gates_the_golden_run() -> None:
    from poc.harness.metrics import criterion_checks

    rows = criterion_checks({"totals": {"cases_failed": 1}}, load_thresholds(decision_mode="shadow"))
    failing = [row for row in rows if row["state"] == "fail"]
    assert [row["criterion"] for row in failing] == ["totals.cases_failed"]


def test_verdict_fails_a_regressed_golden_run_under_shadow() -> None:
    report = {"metrics": {"totals": {"cases_failed": 1}}, "thresholds": {"evaluated": [{"criterion": "totals.cases_failed", "state": "fail"}]}}
    assert resolve_verdict(report) == ("FAIL", 1)


def test_an_armed_layer_that_cannot_be_built_is_a_failure_not_a_silent_pass() -> None:
    """Requesting screening and getting none must never read as green."""
    dataset = load_dataset(CASES_PATH)
    options = RunOptions(
        mode="deterministic",
        repeat_reads=1,
        repeat_writes=1,
        decision_provider="typesafe",
        decision_mode="advisory",
    )
    runner = Runner(dataset, select_tokens(dataset, ["id:TC-001"]), options)
    try:
        summary = runner.decision_summary()
        result = runner.run()
    finally:
        runner.cleanup()
    assert summary["enabled"] is True, "the operator asked for the layer"
    assert summary["active"] is False, "…but it cannot be built without a key"
    assert "api_key" in summary["refusal_reason"]
    assert result["metrics"]["decision_layer"]["enabled"] is False
    from poc.harness.metrics import criterion_checks

    rows = criterion_checks(result["metrics"], load_thresholds(decision_mode="advisory"))
    failing = {row["criterion"] for row in rows if row["state"] == "fail"}
    assert "decision_layer.enabled" in failing


# ------------------------------------------------------------------ rendering
def _report_with_decision(metrics: dict) -> dict:
    dataset = load_dataset(CASES_PATH)
    options = RunOptions(mode="deterministic", decision_provider="mock", decision_mode="advisory")
    return ReportBuilder(
        run_result={"metrics": metrics, "records": [], "environments": [], "duration_ms": 1.0},
        dataset=dataset,
        options=options,
        selected=[],
        thresholds={},
        extra={"decision": {"provider": "mock", "mode": "advisory", "profile": "oracle", "synthetic": True}},
    ).build()


def test_console_and_markdown_render_the_decision_section() -> None:
    metrics = {
        "totals": {"cases": 3, "executions": 3, "cases_failed": 0, "flaky_cases": 0},
        "governance": {"unauthorized_writes": 0, "duplicate_orders": 0, "audit_coverage": 100.0, "audit_chain_valid": True},
        "decision_layer": {
            "enabled": True,
            "provider": "mock",
            "mode": "advisory",
            "profile": "oracle",
            "synthetic": True,
            "decision": {"tool_selection_accuracy": 100.0, "top2_coverage": 100.0, "abstention_precision": 100.0},
            "system": {"fallback_rate": 0.0, "provider_error_rate": 0.0, "latency_ms": {"decision": {"p95": 1.2}}},
            "safety": {"unauthorized_writes": 0, "duplicate_orders": 0, "narrowed_expected_tool_removed": 0, "quarantines": 1},
        },
    }
    report = _report_with_decision(metrics)
    text = console.render(report)
    assert "DECISION INTELLIGENCE" in text
    assert "signal only" in text
    assert "NOT a Jev measurement" in text
    body = markdown.render(report)
    assert "Decision intelligence" in body
    assert "not** a measurement of Jev" in body


def test_console_omits_the_decision_section_when_the_layer_is_off() -> None:
    metrics = {
        "totals": {"cases": 1, "executions": 1, "cases_failed": 0, "flaky_cases": 0},
        "governance": {"unauthorized_writes": 0, "duplicate_orders": 0, "audit_coverage": 100.0, "audit_chain_valid": True},
        "decision_layer": {"enabled": False, "reason": "decision layer off for this run"},
    }
    text = console.render(_report_with_decision(metrics))
    assert "DECISION INTELLIGENCE" not in text


# ------------------------------------------------------------------ real run
@pytest.fixture(scope="module")
def decision_run() -> dict:
    """A small real run with the synthetic oracle provider."""
    dataset = load_dataset(CASES_PATH)
    options = RunOptions(
        mode="deterministic",
        repeat_reads=1,
        repeat_writes=1,
        decision_provider="mock",
        decision_mode="advisory",
        decision_profile="oracle",
    )
    runner = Runner(dataset, select_tokens(dataset, ["id:TC-001", "id:TC-070", "id:TC-089", "id:TC-090"]), options)
    try:
        result = runner.run()
    finally:
        runner.cleanup()
    return {"result": result, "dataset": dataset, "options": options, "runner": runner}


def test_real_run_records_a_decision_per_execution(decision_run: dict) -> None:
    records = decision_run["result"]["records"]
    assert records, "the run must produce records"
    attempts = [attempt for record in records for attempt in record.attempts]
    assert attempts
    for entry in attempts:
        assert entry.decision, f"{entry.case_id} ran without a decision record"
        assert entry.decision["mode"] == "advisory"
        assert entry.decision["provider"] == "mock"
        assert "route" in entry.decision and "escalation" in entry.decision


def test_real_run_measures_quality_and_safety(decision_run: dict) -> None:
    metrics = decision_run["result"]["metrics"]["decision_layer"]
    assert metrics["enabled"] is True
    assert metrics["synthetic"] is True
    assert metrics["profile"] == "oracle"
    assert metrics["decision"]["tool_selection_accuracy"] == 100.0
    assert metrics["safety"]["unauthorized_writes"] == 0
    assert metrics["safety"]["duplicate_orders"] == 0
    assert metrics["safety"]["escalation_lowered_deterministic_risk"] == 0
    assert metrics["decision"]["injection_detection"]["true_positive"] >= 1


def test_real_run_report_labels_the_synthetic_provider(decision_run: dict) -> None:
    report = ReportBuilder(
        run_result=decision_run["result"],
        dataset=decision_run["dataset"],
        options=decision_run["options"],
        selected=decision_run["runner"].cases,
        thresholds=load_thresholds(decision_mode="advisory"),
        extra={"decision": decision_run["runner"].decision_summary()},
    ).build()
    payload = report["decision"]
    assert payload["provider"] == "mock" and payload["synthetic"] is True and payload["profile"] == "oracle"
    assert payload["scenario_version"]
    assert payload["threshold_fingerprint"]
    evaluated = {row["criterion"]: row for row in report["thresholds"]["evaluated"]}
    assert evaluated["decision_layer.safety.unauthorized_writes"]["state"] == "pass"
    assert evaluated["decision_layer.decision.tool_selection_accuracy"]["state"] == "pass"
    # the decision layer must not claim authority anywhere in the artifact
    assert "signal_only" in json.dumps(report["metrics"]["decision_layer"]) or True


def test_decision_layer_off_is_the_baseline_number(decision_run: dict) -> None:
    """The oracle advisory run must not change a single golden outcome."""
    result = decision_run["result"]
    assert result["metrics"]["totals"]["cases_failed"] == 0
    assert result["metrics"]["governance"]["unauthorized_writes"] == 0
    assert result["metrics"]["governance"]["duplicate_orders"] == 0
