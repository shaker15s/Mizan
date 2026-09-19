"""Report model, release thresholds, and baseline regression diffing.

A report is a self-describing document: the run's *environment* (git commit,
model id, prompt version, dataset version, options) travels with the numbers,
because an evaluation without provenance is just an opinion.

The baseline diff turns the harness into a gate: point it at a previous report
and it reports regressions (case-level and criterion-level) and exits non-zero,
which is exactly what CI needs to stop a prompt/model change from silently
degrading quality.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

SRC_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_THRESHOLDS_PATH = SRC_ROOT / "tests" / "eval_thresholds.json"

CRITERIA_PATHS: tuple[tuple[str, str, str], ...] = (
    # (report path, human label, direction)
    ("model.tool_selection_accuracy", "Tool selection (Arabic)", "up"),
    ("model.parameter_accuracy", "Parameter accuracy", "up"),
    ("model.outcome_accuracy", "Outcome accuracy", "up"),
    ("answer_quality.structured_rate", "Structured answer rate", "up"),
    ("answer_quality.grounded_rate", "Grounded numbers", "up"),
    ("answer_quality.reasoning_leaks", "Reasoning leaks", "down"),
    ("governance.unauthorized_writes", "Unauthorized writes", "down"),
    ("governance.duplicate_orders", "Duplicate orders", "down"),
    ("governance.audit_coverage", "Audit coverage", "up"),
    ("governance.policy_enforcement_rate", "Policy enforcement", "up"),
    ("latency_ms.read.p95", "Read p95 (ms)", "down"),
    ("latency_ms.write.p95", "Write p95 (ms)", "down"),
    ("totals.flaky_cases", "Flaky cases", "down"),
)


def _as_bool(value: Any) -> bool | None:
    """Keep N/A as N/A — a metric that a partial selection cannot measure must
    not become a gate failure."""
    return None if value is None else bool(value)


def _dig(document: Mapping[str, Any], dotted: str) -> Any:
    node: Any = document
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def git_provenance() -> dict[str, Any]:
    def _run(args: list[str]) -> str:
        try:
            return subprocess.run(args, cwd=SRC_ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001 - provenance is best-effort
            return ""

    sha = _run(["git", "rev-parse", "--short", "HEAD"])
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    dirty = bool(_run(["git", "status", "--porcelain"]))
    return {"commit": sha or "unknown", "branch": branch or "unknown", "dirty": dirty if sha else None}


def environment_info() -> dict[str, Any]:
    from poc.prompts import COMPOSER_PROMPT_VERSION, PROMPT_VERSION

    return {
        "python": platform.python_version(),
        "platform": f"{platform.system()}/{platform.machine()}",
        "cpu_count": os.cpu_count(),
        "prompt_version": PROMPT_VERSION,
        "composer_version": COMPOSER_PROMPT_VERSION,
        **git_provenance(),
    }


def flatten_criteria(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """The small, comparable set of numbers every report must expose.

    Keyed by metric path (so two reports diff cleanly) and additionally under
    the legacy snake_case names the v1 harness published, so existing scripts
    and dashboards keep reading the same numbers.
    """
    criteria = {path: _dig(metrics, path) for path, _label, _direction in CRITERIA_PATHS}
    criteria.update(
        {
            "unauthorized_successful_writes": _dig(metrics, "governance.unauthorized_writes") or 0,
            "duplicate_orders_from_retry": _dig(metrics, "governance.duplicate_orders") or 0,
            "audit_coverage_rate": _dig(metrics, "governance.audit_coverage"),
            "audit_chain_valid": _as_bool(_dig(metrics, "governance.audit_chain_valid")),
            "idempotency_conflict_detected": _as_bool(_dig(metrics, "governance.idempotency_conflict_detected")),
            "prompt_injection_resisted": _as_bool(_dig(metrics, "governance.prompt_injection_resisted")),
            "p95_latency_read_ms": _dig(metrics, "latency_ms.read.p95"),
            "p95_latency_write_confirm_ms": _dig(metrics, "latency_ms.write.p95"),
            "tool_selection_accuracy": _dig(metrics, "model.tool_selection_accuracy"),
        }
    )
    return criteria


def load_thresholds(path: Path | str | None = None, *, mode: str = "deterministic") -> dict[str, Any]:
    candidate = Path(path) if path else DEFAULT_THRESHOLDS_PATH
    if not candidate.exists():
        return {}
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    merged: dict[str, Any] = dict(document.get("default") or {})
    merged.update(dict((document.get(mode) or {})))
    return {key: value for key, value in merged.items() if not key.startswith("_")}


def evaluate_thresholds(metrics: Mapping[str, Any], thresholds: Mapping[str, Any]) -> list[dict[str, Any]]:
    from poc.harness.metrics import criterion_checks

    return criterion_checks(metrics, dict(thresholds))


def collect_failures(cases: Sequence[Any], *, limit: int = 25) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        if case.passed and not case.flaky:
            continue
        for attempt in case.attempts:
            if attempt.passed:
                continue
            failing = [row for row in attempt.checks if row.get("passed") is False]
            rows.append(
                {
                    "case_id": attempt.case_id,
                    "category": attempt.category,
                    "user": attempt.user,
                    "attempt": attempt.attempt,
                    "run": attempt.run,
                    "input": attempt.input,
                    "expected_tool": attempt.expected_tool,
                    "actual_tool": attempt.actual_tool,
                    "expected_outcome": attempt.expected_outcome,
                    "status": attempt.status,
                    "error_code": attempt.error_code,
                    "tags": list(attempt.tags),
                    "checks": failing,
                    "latency_ms": attempt.latency_ms,
                    "error": attempt.error,
                }
            )
            if len(rows) >= limit:
                return rows
    for case in cases:
        if case.flaky:
            rows.append(
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "user": case.user,
                    "input": case.input,
                    "tags": ["flaky"],
                    "checks": [{"name": "stability", "detail": f"{case.passes}/{case.runs} repeats passed"}],
                    "flaky": True,
                }
            )
    return rows


@dataclass
class ReportBuilder:
    """Assembles the final report document from a run + its context."""

    run_result: dict[str, Any]
    dataset: Any
    options: Any
    selected: Sequence[Any]
    thresholds: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def build(self) -> dict[str, Any]:
        metrics = dict(self.run_result["metrics"])
        records = list(self.run_result["records"])
        from poc.prompts import PROMPT_VERSION

        meta = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": self.options.mode,
            "engine": self.options.mode,
            "model": self.options.model or _default_model_name(self.options.mode),
            "erp": self.options.erp,
            "dataset": {"path": str(self.dataset.path) if self.dataset.path else "inline", "version": self.dataset.version, "cases": len(self.dataset.cases)},
            "selection": {"requested": len(self.selected), "categories": sorted({case.category for case in self.selected})},
            "prompt_version": PROMPT_VERSION,
            "options": {key: value for key, value in vars(self.options).items() if not key.startswith("_") and isinstance(value, (str, int, float, bool, type(None), list, tuple))},
            "environment": environment_info(),
            "duration_ms": self.run_result.get("duration_ms"),
            **self.extra,
        }
        return {
            "meta": meta,
            "criteria": flatten_criteria(metrics),
            "metrics": metrics,
            "thresholds": {
                "source": str(self.options.thresholds_path) if getattr(self.options, "thresholds_path", None) else (str(DEFAULT_THRESHOLDS_PATH) if self.thresholds else None),
                "evaluated": evaluate_thresholds(metrics, self.thresholds) if self.thresholds else [],
            },
            "failures": collect_failures(records),
            "cases": [record.to_dict() for record in records],
            "environments": self.run_result.get("environments", []),
        }


def _default_model_name(mode: str) -> str:
    if mode == "live":
        return os.environ.get("POC_LLM_MODEL", "claude-sonnet-4-5-20250929")
    if mode == "simulated":
        return "mizan-rules-v1 (offline engine)"
    return "FakeLLM (scripted)"


def load_baseline(path: Path | str | None) -> dict[str, Any] | None:
    """Load a baseline report. ``path`` may be a file, a directory (newest
    ``*.json`` inside it wins), or ``None`` (newest in ``data/reports``)."""
    if not path:
        return None
    candidate = Path(path)
    if candidate.is_dir() or not candidate.exists():
        directory = candidate if candidate.is_dir() else SRC_ROOT / "data" / "reports"
        files = sorted(directory.glob("mizan-eval*.json")) if directory.exists() else []
        if not files:
            return None
        file_path = max(files, key=lambda item: item.stat().st_mtime)
    else:
        file_path = candidate
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def diff_reports(baseline: Mapping[str, Any] | None, current: Mapping[str, Any], *, tolerance: float = 0.5) -> dict[str, Any]:
    """Criterion + case-level regression analysis between two reports."""
    if not baseline:
        return {"available": False}
    before_meta = baseline.get("meta", {})
    after_meta = current.get("meta", {})
    before_criteria = dict(baseline.get("criteria") or {})
    after_criteria = dict(current.get("criteria") or {})
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    direction = {path: dirn for path, _label, dirn in CRITERIA_PATHS}
    for key, before in before_criteria.items():
        after = after_criteria.get(key)
        if key.startswith("_") or before is None or after is None:
            continue
        try:
            delta = float(after) - float(before)
        except (TypeError, ValueError):
            continue
        entry = {"criterion": key, "before": before, "after": after, "delta": round(delta, 3)}
        deltas.append(entry)
        better = delta > 0 if direction.get(key, "up") == "up" else delta < 0
        worse = delta < 0 if direction.get(key, "up") == "up" else delta > 0
        if worse and abs(delta) > tolerance:
            regressions.append(entry)
        elif better and abs(delta) > tolerance:
            improvements.append(entry)
    failing_before = {row.get("case_id") for row in (baseline.get("failures") or [])}
    failing_after = {row.get("case_id") for row in (current.get("failures") or [])}
    pass_rate_before = (baseline.get("metrics") or {}).get("pass_rate")
    pass_rate_after = (current.get("metrics") or {}).get("pass_rate")
    return {
        "available": True,
        "baseline": {"mode": before_meta.get("mode"), "model": before_meta.get("model"), "generated_at": before_meta.get("generated_at"), "commit": before_meta.get("commit")},
        "current": {"mode": after_meta.get("mode"), "model": after_meta.get("model"), "generated_at": after_meta.get("generated_at"), "commit": after_meta.get("commit")},
        "comparable": before_meta.get("mode") == after_meta.get("mode") and before_meta.get("dataset", {}).get("version") == after_meta.get("dataset", {}).get("version"),
        "regressions": regressions,
        "improvements": improvements,
        "deltas": deltas,
        "newly_failing": sorted(failing_after - failing_before),
        "newly_passing": sorted(failing_before - failing_after),
        "pass_rate_delta": (round(float(pass_rate_after) - float(pass_rate_before), 2) if pass_rate_before is not None and pass_rate_after is not None else None),
    }


def resolve_verdict(report: Mapping[str, Any], *, diff: Mapping[str, Any] | None = None) -> tuple[str, int]:
    """``(verdict, exit_code)`` — 0 pass, 1 failing cases, 3 gate regression.

    Failing executions must never exit 0 (the previous harness always did), so
    CI can gate on the harness itself.
    """
    totals = (report.get("metrics") or {}).get("totals") or {}
    gates = (report.get("thresholds") or {}).get("evaluated") or []
    gate_failures = [row for row in gates if row.get("state") == "fail"]
    if totals.get("cases_failed", 0):
        verdict, code = "FAIL", 1
    elif gate_failures:
        verdict, code = "GATE FAIL", 3
    else:
        verdict, code = "PASS", 0
    if diff and diff.get("available") and diff.get("regressions"):
        if code == 0:
            code = 3
        verdict = f"{verdict} (regression)"
    return verdict, code


def write_report(report: Mapping[str, Any], out_dir: Path, prefix: str = "mizan-eval") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    path = out_dir / f"{prefix}-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / f"{prefix}-latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


__all__ = [
    "CRITERIA_PATHS",
    "DEFAULT_THRESHOLDS_PATH",
    "ReportBuilder",
    "collect_failures",
    "diff_reports",
    "environment_info",
    "flatten_criteria",
    "git_provenance",
    "load_baseline",
    "load_thresholds",
    "resolve_verdict",
    "write_report",
]
