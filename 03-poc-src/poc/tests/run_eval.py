"""Compatibility shim: ``poc.tests.run_eval`` → the rebuilt harness (``poc.harness``).

The harness was rewritten from scratch (v2.0) because the old one was slow,
printed a wall of numbers nobody acted on, and always exited 0 — so CI could
never fail on it. Everything moved to :mod:`poc.harness`:

    python -m poc.harness                 # ← use this
    python -m poc.tests.run_eval …        # ← still works, forwards here

This module keeps the documented entry point (README, STEP15 report,
TECHNICAL_DESIGN) and the legacy ``run_eval(mode, cases_path, report_path, …)``
call shape, including the legacy report keys (``criteria``/``case_results``), so
existing scripts and notebooks keep working while the new CLI is the default.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]  # 03-poc-src (where data/ and tests/ live)
LEGACY_SRC_ROOT = Path(__file__).resolve().parents[1]  # kept for old imports

__all__ = ["main", "run_eval", "legacy_report"]


def run_eval(
    mode: str = "deterministic",
    cases_path: Path | str | None = None,
    report_path: Path | str | None = None,
    *,
    repeat_reads: int = 3,
    model: str | None = None,
    erp: str = "seeded",
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the full evaluation through the new harness and return a report.

    Return value is the v2 report document *merged* with the v1 keys
    (``criteria``, ``case_results``, ``total_executions``, ``test_set_version``)
    so callers written against the old harness keep working.
    """
    from poc.harness.cases import load_dataset, select_tokens
    from poc.harness.report import ReportBuilder
    from poc.harness.runner import RunOptions, Runner

    dataset = load_dataset(Path(cases_path) if cases_path else SRC_ROOT / "tests" / "test_cases.json")
    options = RunOptions(
        mode=mode,
        erp=erp,
        model=model,
        repeat_reads=repeat_reads,
        repeat_writes=1,
        confirm_ttl_seconds=30,
    )
    runner = Runner(dataset, list(dataset.cases), options)
    try:
        result = runner.run(progress=None if verbose else _silent)
    finally:
        runner.cleanup()
    report = legacy_report(ReportBuilder(result, dataset, options, list(dataset.cases), thresholds={}).build())
    if report_path is not None:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["criteria"], ensure_ascii=False, indent=2))
    failed = report["failures"]
    for row in failed[:20]:
        print(f"FAIL {row['case_id']} run={row.get('run')} tool={row.get('actual_tool')} {row.get('tags')}")
    print(f"{len(failed)} failing executions of {report['total_executions']}")
    return report


def legacy_report(report: dict[str, Any]) -> dict[str, Any]:
    """Add the v1 top-level keys (``criteria`` aliases + ``case_results``) on top
    of the v2 document, so scripts written against the old harness keep working."""
    criteria = dict(report.get("criteria") or {})
    model = (report.get("metrics") or {}).get("model") or {}
    reliability = (report.get("metrics") or {}).get("reliability") or {}
    criteria.setdefault("tool_selection_accuracy", model.get("tool_selection_accuracy"))
    criteria.setdefault("parameter_accuracy_semantic", model.get("parameter_accuracy"))
    criteria.setdefault("parameter_accuracy_exact", model.get("parameter_accuracy"))
    criteria.setdefault("schema_validity_rate", model.get("parameter_accuracy"))
    criteria.setdefault("read_pass3_rate", reliability.get("pass_k_rate"))
    if (report.get("meta") or {}).get("mode") == "deterministic":
        criteria["model_metrics_note"] = (
            "N/A in deterministic mode: the LLM is scripted with expected outputs; run "
            "--mode simulated (rules engine) or --mode live to measure model intelligence."
        )
    case_results = [
        {
            "case_id": attempt["case_id"],
            "category": attempt["category"],
            "user": attempt["user"],
            "run": attempt["run"],
            "attempt": attempt["attempt"],
            "input": attempt["input"],
            "expected_tool": attempt["expected_tool"],
            "actual_tool": attempt["actual_tool"],
            "arguments": attempt["arguments"],
            "args_match": _check_state(attempt, "arguments"),
            "exact_args_match": _check_state(attempt, "arguments"),
            "schema_valid": _check_state(attempt, "arguments") is not False,
            "outcome_met": _check_state(attempt, "outcome") is not False,
            "outcome_detail": _check_detail(attempt, "outcome"),
            "audited": attempt["audited"],
            "unauthorized_write": "unauthorized_write" in (attempt.get("tags") or []),
            "duplicate_order": "duplicate_order" in (attempt.get("tags") or []),
            "latency_ms": attempt["latency_ms"],
            "status": attempt["status"],
            "response_ar": attempt["text"],
        }
        for case in report.get("cases") or []
        for attempt in case.get("attempts") or []
    ]
    merged = dict(report)
    merged.update(
        {
            "mode": (report.get("meta") or {}).get("mode"),
            "model": (report.get("meta") or {}).get("model"),
            "erp": (report.get("meta") or {}).get("erp"),
            "test_set_version": (report.get("meta") or {}).get("dataset", {}).get("version"),
            "total_executions": (report.get("metrics") or {}).get("totals", {}).get("executions", len(case_results)),
            "criteria": criteria,
            "case_results": case_results,
        }
    )
    return merged


def _check_state(attempt: dict[str, Any], name: str) -> bool | None:
    for row in attempt.get("checks") or []:
        if row.get("name") == name:
            return row.get("passed")
    return None


def _check_detail(attempt: dict[str, Any], name: str) -> str:
    for row in attempt.get("checks") or []:
        if row.get("name") == name:
            return str(row.get("detail") or "")
    return ""


def _silent(*_args: Any, **_kwargs: Any) -> None:
    return None


def main(argv: list[str] | None = None) -> int:
    """Translate the legacy CLI into the new one (unknown flags pass through)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    forwarded: list[str] = []
    report_to: Path | None = None
    out_dir = SRC_ROOT / "data" / "reports"
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--test-cases":
            forwarded += ["--dataset", argv[index + 1]]
            index += 2
            continue
        if token == "--report":
            report_to = Path(argv[index + 1])
            if not report_to.is_absolute():
                report_to = SRC_ROOT / report_to
            index += 2
            continue
        if token == "--out":
            out_dir = Path(argv[index + 1])
            forwarded += ["--out", str(out_dir)]
            index += 2
            continue
        if token in {"--live-report", "--verbose"}:
            forwarded.append("--verbose")
            index += 1
            continue
        if token == "--no-report":
            index += 1
            continue
        forwarded.append(token)
        index += 1

    from poc.harness.cli import main as harness_main

    code = harness_main(forwarded)
    if report_to is not None:
        latest = out_dir / "mizan-eval-latest.json"
        if latest.exists():
            document = json.loads(latest.read_text(encoding="utf-8"))
            report_to.parent.mkdir(parents=True, exist_ok=True)
            report_to.write_text(json.dumps(legacy_report(document), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[run_eval] report copied to {report_to}")
    return code


if __name__ == "__main__":
    sys.exit(main())
