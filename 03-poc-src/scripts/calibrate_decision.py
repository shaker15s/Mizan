"""Calibration sweep for the decision-layer thresholds (plan §13, §33).

Runs the golden cases with a synthetic provider of known quality and a grid of
candidate thresholds, then reports — for every candidate — the things a release
decision actually depends on:

* **safety**: failed golden cases, unauthorized writes, duplicate orders;
* **utility**: how often the signal was used (narrowing, escalation) rather than
  discarded by a conservative gate;
* **honesty**: detection precision/recall against the scripted ground truth.

The output is a committed JSON artifact plus a Markdown table. Nothing here
publishes a number about Jev itself: synthetic profiles describe the machinery
under a stated quality assumption, which is exactly why the calibration is
labelled ``synthetic``.

Usage::

    python scripts/calibrate_decision.py                       # default grid
    python scripts/calibrate_decision.py --profile realistic --split calibration
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from poc.decision.profiles import DEFAULT_SCENARIOS_PATH, DEFAULT_SPLIT_PATH  # noqa: E402
from poc.harness.cases import load_dataset  # noqa: E402
from poc.harness.runner import DETERMINISTIC, RunOptions, Runner  # noqa: E402

CALIBRATION_PATH = SRC_ROOT / "data" / "decision-calibration.json"
REPORT_PATH = SRC_ROOT / "data" / "reports" / "decision-calibration.md"

DEFAULT_GRID = {
    "route_min_confidence": (0.75, 0.80, 0.85, 0.90),
    "route_min_margin": (0.20, 0.35, 0.50),
}


def load_cases(split: str) -> list:
    dataset = load_dataset(SRC_ROOT / "tests" / "test_cases.json")
    document = json.loads(DEFAULT_SPLIT_PATH.read_text(encoding="utf-8"))
    assignment = document["assignment"]
    cases = [case for case in dataset.cases if assignment.get(case.id) == split]
    if not cases:
        raise SystemExit(f"[calibration] no cases assigned to split {split!r}")
    return cases


def run_candidate(cases: list, *, profile: str, overrides: dict) -> dict:
    options = RunOptions(
        mode=DETERMINISTIC,
        repeat_reads=1,
        repeat_writes=1,
        decision_provider="mock",
        decision_mode="advisory",
        decision_profile=profile,
        decision_scenarios=DEFAULT_SCENARIOS_PATH,
        decision_thresholds=None,
    )
    # The overrides travel through the runner's DecisionRunConfig (no hidden globals).
    runner = Runner(dataset=None, cases=cases, options=options)  # type: ignore[arg-type]
    runner.decision.threshold_overrides = overrides
    started = time.perf_counter()
    try:
        result = runner.run()
    finally:
        runner.cleanup()
    metrics = result["metrics"]
    decision = (metrics.get("decision_layer") or {})
    decision_metrics = decision.get("decision") or {}
    summary = {
        "overrides": dict(overrides),
        "profile": profile,
        "wall_ms": round((time.perf_counter() - started) * 1000, 1),
        "cases": metrics["totals"]["cases"],
        "cases_failed": metrics["totals"]["cases_failed"],
        "executions": metrics["totals"]["executions"],
        "tool_selection_accuracy": decision_metrics.get("tool_selection_accuracy"),
        "top2_coverage": decision_metrics.get("top2_coverage"),
        "abstention_rate": decision_metrics.get("abstention_rate"),
        "abstention_precision": decision_metrics.get("abstention_precision"),
        "injection_recall": (decision_metrics.get("injection_detection") or {}).get("recall"),
        "ambiguity_recall": (decision_metrics.get("ambiguity_detection") or {}).get("recall"),
        "security_escalation_precision": ((decision_metrics.get("escalation") or {}).get("security") or {}).get("precision"),
        "security_escalation_recall": ((decision_metrics.get("escalation") or {}).get("security") or {}).get("recall"),
        "narrowing_rate": None,
        "fallback_rate": (decision.get("system") or {}).get("fallback_rate"),
        "decision_p50_ms": ((decision.get("system") or {}).get("latency_ms") or {}).get("decision", {}).get("p50"),
        "safety": decision.get("safety"),
    }
    narrowing = [
        row for row in (decision.get("cases") or []) if (row.get("final_route") == "narrowed")
    ]
    total_rows = len(decision.get("cases") or [])
    summary["narrowing_rate"] = round(len(narrowing) / total_rows * 100, 2) if total_rows else None
    return summary


def render_table(rows: list[dict]) -> str:
    header = ("confidence", "margin", "failed", "tool acc", "top2", "narrow%", "inject rec", "amb rec", "esc prec")
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        overrides = row["overrides"]
        lines.append(
            "| "
            + " | ".join(
                str(value)
                for value in (
                    overrides.get("route_min_confidence"),
                    overrides.get("route_min_margin"),
                    row["cases_failed"],
                    row["tool_selection_accuracy"],
                    row["top2_coverage"],
                    row["narrowing_rate"],
                    row["injection_recall"],
                    row["ambiguity_recall"],
                    row["security_escalation_precision"],
                )
            )
            + " |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="realistic", choices=("oracle", "realistic", "adversarial"))
    parser.add_argument("--split", default="calibration", choices=("calibration", "validation", "heldout"))
    parser.add_argument("--confidences", default=",".join(str(value) for value in DEFAULT_GRID["route_min_confidence"]))
    parser.add_argument("--margins", default=",".join(str(value) for value in DEFAULT_GRID["route_min_margin"]))
    parser.add_argument("--out", default=str(CALIBRATION_PATH))
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    confidences = [float(value) for value in args.confidences.split(",") if value.strip()]
    margins = [float(value) for value in args.margins.split(",") if value.strip()]
    cases = load_cases(args.split)
    grid = [{"route_min_confidence": confidence, "route_min_margin": margin} for confidence, margin in itertools.product(confidences, margins)]

    rows: list[dict] = []
    for index, overrides in enumerate(grid, start=1):
        print(f"[calibration] {index}/{len(grid)} {overrides}", flush=True)
        rows.append(run_candidate(cases, profile=args.profile, overrides=overrides))

    safe = [row for row in rows if row["cases_failed"] == 0]
    # Among candidates that are equally safe and equally useful, the *most
    # conservative* gate wins: safety is monotonic in conservatism (plan §42),
    # utility is not — so a tie is broken in favour of the higher threshold.
    ranked = sorted(
        safe or rows,
        key=lambda row: (
            row["cases_failed"],
            -(row["narrowing_rate"] or 0.0),
            -(row["top2_coverage"] or 0.0),
            -(row["overrides"].get("route_min_confidence") or 0.0),
            -(row["overrides"].get("route_min_margin") or 0.0),
        ),
    )
    recommended = ranked[0] if ranked else None
    document = {
        "version": "1.0.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": {"path": "tests/test_cases.json", "split": args.split, "cases": len(cases)},
        "provider": {"kind": "mock", "profile": args.profile, "synthetic": True},
        "grid": {"route_min_confidence": confidences, "route_min_margin": margins},
        "rows": rows,
        "recommended": {
            **(recommended["overrides"] if recommended else {}),
            "reason": (
                "zero failed golden cases, then maximal narrowing, then the most conservative "
                "threshold among the tied candidates; must be re-checked on the held-out split "
                "before quoting any number"
            ),
        },
        "caveats": [
            "Synthetic provider: this calibrates the machinery's thresholds, not Jev's behaviour.",
            "Deterministic mode scripts the LLM from the expectation, so a narrowed tool set is not re-planned by the model.",
            "Thresholds are a starting point for a real-provider calibration run; the held-out split is the only source of quoted numbers.",
        ],
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = (
        f"# Decision threshold calibration ({args.profile}, {args.split} split)\n\n"
        f"Generated {document['generated_at']} · synthetic provider (mock/{args.profile}) · "
        f"{len(cases)} cases per candidate.\n\n"
        f"{render_table(rows)}\n\n"
        f"Recommended: confidence ≥ {document['recommended'].get('route_min_confidence')}, "
        f"margin ≥ {document['recommended'].get('route_min_margin')} — "
        f"{document['recommended']['reason']}.\n\n"
        "Caveats:\n" + "\n".join(f"- {item}" for item in document["caveats"]) + "\n"
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"[calibration] wrote {out_path} and {report_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
