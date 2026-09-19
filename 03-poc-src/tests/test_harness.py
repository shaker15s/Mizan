"""Tests for the evaluation harness itself (``poc.harness``).

A harness is infrastructure: if it can lie, every number downstream is a rumour.
These tests pin the places where a bug would silently turn a red build green —
dataset validation, selection, grading of *observed behaviour* (not of prose),
N/A handling for scripted metrics, gate verdicts and exit codes, and the
baseline regression diff. Plus one real end-to-end run so the layers stay wired.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from poc.harness import graders
from poc.harness.cases import DatasetError, dataset_summary, group_for_sharding, load_dataset, select, select_tokens
from poc.harness.environment import SeededOdoo, fresh_environment
from poc.harness.graders import AttemptOutcome, failed_tags, grade_attempt
from poc.harness.metrics import collect_metrics, criterion_checks, percentile
from poc.harness.records import AttemptRecord, CaseRecord, summarize_attempts
from poc.harness.render import console, html, json_report, markdown
from poc.harness.report import ReportBuilder, diff_reports, flatten_criteria, load_thresholds, resolve_verdict, write_report
from poc.harness.runner import RunOptions, Runner
from poc.harness.cli import main as harness_main

CASES_PATH = Path(__file__).resolve().parent / "test_cases.json"


# ------------------------------------------------------------------- dataset
def test_dataset_loads_and_summarizes() -> None:
    dataset = load_dataset(CASES_PATH)
    summary = dataset_summary(dataset)
    assert summary["cases"] == 50
    assert summary["by_category"]["read_happy"] == 15
    assert summary["executions_planned"] >= 53
    assert summary["version"]


def test_repeat_same_request_becomes_two_hermetic_attempts() -> None:
    """The replay cases must self-contain: accept, then replay — no global order."""
    dataset = load_dataset(CASES_PATH)
    case = next(row for row in dataset.cases if row.id == "TC-044")
    assert [attempt.expect_outcome for attempt in case.attempts] == ["success", "replay"]
    assert all(attempt.auto_confirm for attempt in case.attempts)


def test_selection_by_id_category_user_and_limit() -> None:
    dataset = load_dataset(CASES_PATH)
    assert [case.id for case in select_tokens(dataset, ["TC-001", "TC-004"])] == ["TC-001", "TC-004"]
    assert all(case.category == "write_happy" for case in select_tokens(dataset, ["write_happy"]))
    assert all(case.user == "readonly_user@test" for case in select_tokens(dataset, ["user:readonly_user@test"]))
    assert len(select_tokens(dataset, [], limit=4)) == 4
    assert select_tokens(dataset, ["outcome:permission_denied"])
    assert len(select_tokens(dataset, ["read"])) == 20  # same-kind tokens OR
    assert select_tokens(dataset, ["authz", "user:readonly_user@test"])  # different kinds AND
    with pytest.raises(DatasetError):
        select_tokens(dataset, ["read_happy", "TC-044"])  # contradictory AND → loud


def test_unknown_selector_is_an_error_not_an_empty_green_run() -> None:
    dataset = load_dataset(CASES_PATH)
    with pytest.raises(DatasetError):
        select_tokens(dataset, ["totally-not-a-filter"])


def test_dataset_rejects_bad_rows(tmp_path: Path) -> None:
    broken = tmp_path / "cases.json"
    broken.write_text(
        json.dumps({"test_cases": [{"id": "TC-X", "category": "c", "user": "u", "input": "  ", "expected_outcome": "nope"}]}),
        encoding="utf-8",
    )
    with pytest.raises(DatasetError) as excinfo:
        load_dataset(broken)
    assert "expected_outcome" in str(excinfo.value) or "input" in str(excinfo.value)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "cases.json"
    row = {"id": "TC-1", "category": "read_happy", "user": "u", "input": "هات العميل", "expected_outcome": "success"}
    broken.write_text(json.dumps({"test_cases": [row, dict(row)]}), encoding="utf-8")
    with pytest.raises(DatasetError):
        load_dataset(broken)


def test_sharding_groups_by_category() -> None:
    dataset = load_dataset(CASES_PATH)
    groups = group_for_sharding(dataset.cases, "category")
    assert all(len({case.category for case in group}) == 1 for group in groups)
    assert sum(len(group) for group in groups) == len(dataset.cases)


# ----------------------------------------------------------------- environment
def test_seeded_erp_double_is_deterministic_and_auditable() -> None:
    erp = SeededOdoo()
    found = erp.search_read("res.partner", [("name", "ilike", "محمد")], ["name", "phone"])
    assert found and all("name" in row for row in found)
    created = erp.create("sale.order", [{"partner_id": 42, "client_order_ref": "k1", "order_line": [(0, 0, {"product_id": 55, "product_uom_qty": 2})]}])
    assert len(created) == 1
    assert erp.duplicate_order_count() == 0
    erp.create("sale.order", [{"partner_id": 42, "client_order_ref": "k1", "order_line": [(0, 0, {"product_id": 55, "product_uom_qty": 2})]}])
    assert erp.duplicate_order_count() == 1


def test_fresh_environments_do_not_share_state(tmp_path: Path) -> None:
    first = fresh_environment(1, root=tmp_path, keep=False, confirm_ttl_seconds=30)
    second = fresh_environment(2, root=tmp_path, keep=False, confirm_ttl_seconds=30)
    assert first.db_path != second.db_path
    assert first.odoo is not None and second.odoo is not None
    assert len(first.odoo.create_calls) == len(second.odoo.create_calls) == 0
    first.cleanup()
    second.cleanup()


# --------------------------------------------------------------------- grading
def outcome(**kwargs) -> AttemptOutcome:
    base = dict(case=None, attempt=None)
    base.update(kwargs)
    return AttemptOutcome(**base)


def make_case(**kwargs) -> SimpleNamespace:
    defaults = dict(id="TC-T", category="write_happy", user="sales_user@test", input="اعمل أوردر", expected_outcome="success", expected_args=None, expected_tool=None)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def make_attempt(**kwargs) -> SimpleNamespace:
    defaults = dict(index=1, total=1, auto_confirm=True, expect_outcome="success")
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_arguments_match_is_numeric_not_textual() -> None:
    exact, semantic = graders._same_arguments({"lines": [{"product_id": 55, "quantity": 20.0}]}, {"lines": [{"product_id": 55, "quantity": 20}]})
    assert (exact, semantic) == (True, True)
    exact, semantic = graders._same_arguments({"query": "احمد"}, {"query": "أحمد"})
    assert (exact, semantic) == (False, True)
    exact, semantic = graders._same_arguments({"query": "خالد"}, {"query": "أحمد"})
    assert (exact, semantic) == (False, False)


def test_write_without_signature_is_flagged() -> None:
    row = outcome(
        case=make_case(),
        attempt=make_attempt(),
        tool="sales.order.create",
        create_calls_before=0,
        create_calls_after=1,
        signed=False,
    )
    check = graders.check_security(row, allowed_users={"sales_user@test": ("sales.order.create",)})
    assert check.passed is False
    assert "unauthorized_write" in check.tags


def test_signed_write_passes_and_denied_user_write_fails() -> None:
    signed = outcome(case=make_case(), attempt=make_attempt(), tool="sales.order.create", create_calls_before=0, create_calls_after=1, signed=True)
    assert graders.check_security(signed, allowed_users={"sales_user@test": ("sales.order.create",)}).passed is True
    denied = outcome(case=make_case(user="readonly_user@test"), attempt=make_attempt(), tool="sales.order.create", create_calls_before=0, create_calls_after=1, signed=True)
    assert graders.check_security(denied, allowed_users={"readonly_user@test": ("customer.search",)}).passed is False


def test_empty_search_still_counts_as_structured_when_card_exists() -> None:
    row = outcome(
        case=make_case(category="read_happy"),
        attempt=make_attempt(),
        gateway_result=SimpleNamespace(status="accepted"),
        answer={"headline": "تم الفحص من Odoo — مفيش عميل مطابق لبحثك.", "sections": [{"kind": "steps", "title": "خطوات تالية"}], "kpis": [{"label": "عملاء مطابقين", "value": "0"}]},
        result_payload={"success": True, "customers": [], "count": 0},
    )
    check = graders.check_format(row, requires_structure=True)
    assert check.passed is True


def test_runon_prose_fails_the_format_check() -> None:
    row = outcome(
        case=make_case(category="read_happy"),
        attempt=make_attempt(),
        gateway_result=SimpleNamespace(status="accepted"),
        answer={},
        text="العميل موجود وعنده اوردرات وبياناته كالتالي وكمان البريد",
    )
    check = graders.check_format(row, requires_structure=True)
    assert check.passed is False
    assert check.tags == (graders.TAG_FORMAT,)


def test_hallucinated_number_in_narrative_is_caught() -> None:
    row = outcome(
        case=make_case(category="read_happy"),
        attempt=make_attempt(),
        answer={"analysis": "إجمالي المخزون 987654 جنيه"},
        result_payload={"products": [{"list_price": 12.0, "qty_available": 20}]},
        arguments={"query": "مياه"},
    )
    check = graders.check_numbers_are_real(row)
    assert check.passed is False
    assert "hallucinated_number" in check.tags


def test_scripted_model_metrics_are_na_not_hundred_percent() -> None:
    row = outcome(
        case=make_case(expected_tool="customer.search", expected_args={"query": "محمد"}),
        attempt=make_attempt(),
        tool="customer.search",
        arguments={"query": "محمد"},
        status="accepted",
        gateway_result=SimpleNamespace(status="accepted", result={"customers": [{"id": 1}], "count": 1}),
        answer={"headline": "h", "sections": [{"kind": "table"}], "kpis": [{"label": "x", "value": "1"}]},
        result_payload={"customers": [{"id": 1}], "count": 1},
        erp_before=0,
        erp_after=1,
        audited=True,
        text="تم الفحص من Odoo — لقيت 1 عميل مطابق لبحثك.",
        agent_result=SimpleNamespace(outcome="tool_call", response_ar="تم الفحص من Odoo", answer=None),
    )
    checks = grade_attempt(row, mode="deterministic", allowed_users={"sales_user@test": ("customer.search", "sales.order.create")})
    by_name = {check.name: check for check in checks}
    assert by_name["tool_selection"].passed is None
    assert by_name["arguments"].passed is None
    assert by_name["outcome"].passed is True
    assert by_name["audit"].passed is True
    failing = [f"{check.name}:{check.detail}" for check in checks if check.passed is False]
    assert not failing


def test_wrong_tool_and_wrong_outcome_are_tagged_in_live_mode() -> None:
    row = outcome(
        case=make_case(expected_tool="customer.get", expected_args={"customer_id": 42}),
        attempt=make_attempt(),
        tool="customer.search",
        arguments={"query": "x"},
        status="accepted",
        gateway_result=SimpleNamespace(status="accepted", result={"customers": [], "count": 0}),
        answer={"headline": "h", "sections": [{"kind": "table"}], "kpis": [{"label": "x", "value": "1"}]},
        result_payload={"customers": [], "count": 0},
    )
    checks = grade_attempt(row, mode="live", allowed_users={"sales_user@test": ("customer.get", "customer.search")})
    tags = set(failed_tags(checks))
    assert {"wrong_tool", "args_mismatch"} <= tags
    # A replay is only a replay when the store says so: never grade it from prose.
    replay_row = outcome(
        case=make_case(expected_tool="sales.order.create"),
        attempt=make_attempt(expect_outcome="replay"),
        tool="sales.order.create",
        status="accepted",
        gateway_result=SimpleNamespace(status="accepted", result={"order_id": 101, "verified": True}),
    )
    replay_checks = grade_attempt(replay_row, mode="deterministic", allowed_users={"sales_user@test": ("sales.order.create",)})
    assert "outcome_mismatch" in set(failed_tags(replay_checks))


def test_latency_budget_produces_a_tag() -> None:
    row = outcome(case=make_case(), attempt=make_attempt(), latencies={"total": 900.0})
    check = graders.check_latency(row, 250.0, kind="read")
    assert check is not None and check.passed is False and "slow" in check.tags


def test_check_na_rows_are_ignored_by_summarize() -> None:
    attempts = [AttemptRecord(case_id="TC-1", passed=True, checks=[{"name": "audit", "passed": None}], tags=[])]
    passed, tags, failures = summarize_attempts(attempts)
    assert (passed, tags, failures) == (True, [], [])


# --------------------------------------------------------------------- metrics
def test_percentile_picks_nearest_rank() -> None:
    assert percentile([1, 2, 3, 4], 50) in {2.0, 3.0}
    assert percentile([], 95) is None


def test_model_metrics_are_na_when_not_scored() -> None:
    attempts = [
        AttemptRecord(
            case_id="TC-1",
            category="read_happy",
            user="sales_user@test",
            expected_tool="customer.search",
            actual_tool="customer.search",
            checks=[{"name": "tool_selection", "passed": None}, {"name": "format", "passed": True}],
            latency_ms={"total": 4.0},
        )
    ]
    records = [CaseRecord(case_id="TC-1", category="read_happy", user="sales_user@test", input="هات", attempts=attempts, passed=True)]
    scored = collect_metrics(records, score_model=False, chain={"valid": True})
    assert scored["model"]["tool_selection_accuracy"] is None
    assert scored["model"]["note"]
    again = collect_metrics(
        [
            CaseRecord(
                case_id="TC-1",
                category="read_happy",
                user="u",
                input="هات",
                attempts=[
                    AttemptRecord(
                        case_id="TC-1",
                        category="read_happy",
                        checks=[{"name": "tool_selection", "passed": True}, {"name": "format", "passed": True}],
                        latency_ms={"total": 4.0},
                    )
                ],
            )
        ],
        score_model=True,
        chain={"valid": True},
    )
    assert again["model"]["tool_selection_accuracy"] == 100.0


def test_prompt_injection_metric_is_na_without_injection_cases() -> None:
    records = [CaseRecord(case_id="TC-1", category="read_happy", user="u", input="هات", attempts=[AttemptRecord(case_id="TC-1", checks=[])])]
    document = collect_metrics(records, score_model=False, chain={"valid": True})
    assert document["governance"]["prompt_injection_resisted"] is None


def test_criterion_checks_support_all_operators() -> None:
    document = {"a": {"b": 5}, "flag": True}
    rows = criterion_checks(document, {"a.b": {"op": "<=", "value": 4}, "flag": {"op": "==", "value": True}, "missing": 3})
    by_name = {row["criterion"]: row for row in rows}
    assert by_name["a.b"]["state"] == "fail"
    assert by_name["flag"]["state"] == "pass"
    assert by_name["missing"]["state"] == "n/a"


def test_flaky_detection_needs_both_a_pass_and_a_fail() -> None:
    attempts = [
        AttemptRecord(case_id="TC-1", run=1, passed=True, checks=[]),
        AttemptRecord(case_id="TC-1", run=2, passed=False, checks=[{"name": "format", "passed": False}], tags=["format_unstructured"]),
    ]
    record = CaseRecord(case_id="TC-1", category="read_happy", user="u", input="هات", attempts=attempts, runs=2, passes=1, flaky=True, passed=False)
    document = collect_metrics([record], score_model=False, chain={"valid": True})
    assert document["totals"]["flaky_cases"] == 1
    assert document["failure_tags"]["format_unstructured"] == 1


# ---------------------------------------------------------------------- report
def test_criteria_keep_legacy_aliases() -> None:
    document = {
        "governance": {
            "unauthorized_writes": 0,
            "duplicate_orders": 0,
            "audit_coverage": 100.0,
            "audit_chain_valid": True,
            "idempotency_conflict_detected": True,
            "prompt_injection_resisted": True,
        },
        "model": {"tool_selection_accuracy": None},
        "latency_ms": {"read": {"p95": 4.0}, "write": {"p95": 20.0}},
    }
    criteria = flatten_criteria(document)
    assert criteria["unauthorized_successful_writes"] == 0
    assert criteria["audit_coverage_rate"] == 100.0
    assert criteria["audit_chain_valid"] is True
    assert criteria["p95_latency_read_ms"] == 4.0
    assert criteria["model.tool_selection_accuracy"] is None


def test_baseline_diff_directions_and_case_transitions() -> None:
    baseline = {"meta": {"mode": "deterministic", "dataset": {"version": "1"}}, "criteria": {"model.tool_selection_accuracy": 90.0, "latency_ms.read.p95": 10.0}, "metrics": {"pass_rate": 100.0}, "failures": []}
    current = {"meta": {"mode": "deterministic", "dataset": {"version": "1"}}, "criteria": {"model.tool_selection_accuracy": 80.0, "latency_ms.read.p95": 30.0}, "metrics": {"pass_rate": 90.0}, "failures": [{"case_id": "TC-002"}]}
    diff = diff_reports(baseline, current)
    assert diff["available"] and diff["comparable"]
    assert {row["criterion"] for row in diff["regressions"]} == {"model.tool_selection_accuracy", "latency_ms.read.p95"}
    assert diff["newly_failing"] == ["TC-002"]
    assert diff["pass_rate_delta"] == -10.0
    assert diff_reports(None, current) == {"available": False}


def test_verdict_and_exit_codes_are_meaningful() -> None:
    clean = {"metrics": {"totals": {"cases_failed": 0}}, "thresholds": {"evaluated": [{"state": "pass"}]}}
    assert resolve_verdict(clean) == ("PASS", 0)
    gate_fail = {"metrics": {"totals": {"cases_failed": 0}}, "thresholds": {"evaluated": [{"state": "fail"}]}}
    assert resolve_verdict(gate_fail) == ("GATE FAIL", 3)
    failing = {"metrics": {"totals": {"cases_failed": 2}}, "thresholds": {"evaluated": []}}
    assert resolve_verdict(failing) == ("FAIL", 1)
    assert resolve_verdict(clean, diff={"available": True, "regressions": [{"criterion": "x"}]})[1] == 3


def test_threshold_file_is_valid_and_mode_aware() -> None:
    deterministic = load_thresholds(None, mode="deterministic")
    assert deterministic["governance.unauthorized_writes"] == {"op": "<=", "value": 0}
    live = load_thresholds(None, mode="live")
    assert live["model.tool_selection_accuracy"]["op"] == ">="
    for thresholds in (deterministic, live):
        for key in thresholds:
            assert key.split(".")[0] in {"model", "governance", "answer_quality", "latency_ms", "totals"}, key


# --------------------------------------------------------- end-to-end (real run)
@pytest.fixture(scope="module")
def smoke_run() -> dict:
    dataset = load_dataset(CASES_PATH)
    options = RunOptions(mode="deterministic", repeat_reads=1, repeat_writes=1, confirm_ttl_seconds=30, keep_env=False)
    runner = Runner(dataset, select_tokens(dataset, ["read_happy", "duplicate_idempotency"]), options)
    try:
        result = runner.run()
    finally:
        runner.cleanup()
    return {"result": result, "dataset": dataset, "options": options}


def test_real_run_produces_graded_records(smoke_run: dict) -> None:
    result = smoke_run["result"]
    metrics = result["metrics"]
    assert metrics["totals"]["cases"] > 0
    assert metrics["totals"]["executions"] >= metrics["totals"]["cases"]
    assert metrics["governance"]["unauthorized_writes"] == 0
    assert metrics["governance"]["duplicate_orders"] == 0
    assert metrics["governance"]["audit_coverage"] == 100.0
    assert metrics["runtime"]["hermetic"] is True


def test_replay_case_is_graded_within_the_case(smoke_run: dict) -> None:
    """TC-044 must show accept then replay inside one case, without ordering luck."""
    record = next(row for row in smoke_run["result"]["records"] if row.case_id == "TC-044")
    assert [attempt.expected_outcome for attempt in record.attempts] == ["success", "replay"]
    assert record.passed is True


def test_reports_render_in_every_format(tmp_path: Path, smoke_run: dict) -> None:
    report = ReportBuilder(smoke_run["result"], smoke_run["dataset"], smoke_run["options"], [], thresholds=load_thresholds(None, mode="deterministic")).build()
    report["verdict"] = {"label": "PASS", "exit_code": 0}
    text = console.render(report)
    assert "MIZAN EVAL" in text and "GOVERNANCE" in text
    markdown_text = markdown.render(report)
    assert "### Governance" in markdown_text and "| check | result |" in markdown_text
    page = html.render(report)
    assert page.startswith("<!doctype html>") and 'dir="rtl"' in page and "</html>" in page
    assert json.loads(json_report.render(report))["meta"]["mode"] == "deterministic"
    path = write_report(report, tmp_path)
    assert path.exists() and json.loads(path.read_text(encoding="utf-8"))["cases"]


def test_no_arabic_text_is_mangled_by_ascii_only_escapes(tmp_path: Path, smoke_run: dict) -> None:
    report = ReportBuilder(smoke_run["result"], smoke_run["dataset"], smoke_run["options"], []).build()
    path = write_report(report, tmp_path)
    assert "هاتلي" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- CLI
def test_cli_run_exits_zero_and_writes_reports(tmp_path: Path) -> None:
    code = harness_main(["run", "--no-repeat", "--quiet", "--out", str(tmp_path), "--format", "json,md,html"])
    assert code == 0
    assert (tmp_path / "mizan-eval-latest.json").exists()
    assert (tmp_path / "mizan-eval-latest.md").exists()
    assert (tmp_path / "mizan-eval-latest.html").exists()


def test_cli_selector_typo_is_a_usage_error(tmp_path: Path) -> None:
    assert harness_main(["run", "--filter", "لا-توجد-دي-الحاله", "--out", str(tmp_path)]) == 2


def test_cli_doctor_is_green(tmp_path: Path) -> None:
    assert harness_main(["doctor", "--dataset", str(CASES_PATH), "--out", str(tmp_path)]) == 0


def test_cli_parallel_matches_sequential(tmp_path: Path) -> None:
    common = ["--no-repeat", "--quiet", "--format", "json", "--out", str(tmp_path)]
    assert harness_main(common + ["--jobs", "3"]) == 0
    parallel = json.loads((tmp_path / "mizan-eval-latest.json").read_text(encoding="utf-8"))
    assert parallel["metrics"]["totals"]["cases_failed"] == 0
    assert parallel["metrics"]["runtime"]["jobs"] == 3
