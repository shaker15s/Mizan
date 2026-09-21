"""Build the baseline / shadow / advisory comparison artifact (plan §32, §34).

Reads harness reports that already exist on disk and writes a single comparison
document. Nothing here runs the system or invents a number: every value comes
from a report that must exist, and a missing input is reported as missing rather
than leaving a blank that reads like a zero.

Usage::

    python scripts/compare_decision_reports.py            # uses data/reports/*
    python scripts/compare_decision_reports.py --out data/reports/decision-comparison.md
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = SRC_ROOT / "data" / "reports"

#: report prefix → what the run was
RUNS = (
    ("baseline", "dec2-baseline", "decision layer off", None),
    ("shadow", "dec2-shadow", "mock provider, shadow (records, never acts)", None),
    ("advisory", "dec2-advisory", "mock provider, advisory (may narrow/escalate)", None),
    ("advisory-heldout", "dec2-advisory-heldout", "advisory, scored on the held-out split only", None),
    ("enforcing", "dec2-enforcing", "mock provider, enforcing (experimental)", None),
    ("realistic-calibration", "dec2-realistic-cal", "advisory with a mid-quality provider, calibration split", None),
    ("adversarial", "dec2-adversarial", "advisory with a hostile provider, all cases (negative control)", None),
)


def _latest(directory: Path, prefix: str) -> Path | None:
    files = sorted(directory.glob(f"{prefix}-2*.json"))
    return files[-1] if files else None


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dig(document: dict, dotted: str, default=None):
    node: object = document
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def summarize(report: dict) -> dict:
    metrics = report.get("metrics") or {}
    decision = metrics.get("decision_layer") or {}
    quality = decision.get("decision") or {}
    system = decision.get("system") or {}
    safety = decision.get("safety") or {}
    governance = metrics.get("governance") or {}
    latency = metrics.get("latency_ms") or {}
    decision_latency = (system.get("latency_ms") or {}).get("decision") or {}
    rows = decision.get("cases") or []
    narrowed = sum(1 for row in rows if row.get("final_route") == "narrowed")
    gates = (report.get("thresholds") or {}).get("evaluated") or []
    failing_gates = [row["criterion"] for row in gates if row.get("state") == "fail"]
    return {
        "verdict": (report.get("verdict") or {}).get("label"),
        "exit_code": (report.get("verdict") or {}).get("exit_code"),
        "cases": (metrics.get("totals") or {}).get("cases"),
        "executions": (metrics.get("totals") or {}).get("executions"),
        "cases_failed": (metrics.get("totals") or {}).get("cases_failed"),
        "governance": {
            "unauthorized_writes": governance.get("unauthorized_writes"),
            "duplicate_orders": governance.get("duplicate_orders"),
            "audit_coverage": governance.get("audit_coverage"),
            "audit_chain_valid": governance.get("audit_chain_valid"),
            "policy_enforcement_rate": governance.get("policy_enforcement_rate"),
        },
        "decision": {
            "enabled": decision.get("enabled"),
            "provider": decision.get("provider"),
            "mode": decision.get("mode"),
            "profile": decision.get("profile"),
            "synthetic": decision.get("synthetic"),
            "tool_selection_accuracy": quality.get("tool_selection_accuracy"),
            "top2_coverage": quality.get("top2_coverage"),
            "abstention_rate": quality.get("abstention_rate"),
            "abstention_precision": quality.get("abstention_precision"),
            "ambiguity": quality.get("ambiguity_detection"),
            "injection": quality.get("injection_detection"),
            "escalation": quality.get("escalation"),
            "disagreement": quality.get("disagreement"),
            "narrowing_rate": round(narrowed / len(rows) * 100, 2) if rows else None,
        },
        "system": {
            "fallback_rate": system.get("fallback_rate"),
            "provider_error_rate": system.get("provider_error_rate"),
            "tokens": system.get("tokens"),
        },
        "safety": safety,
        "latency": {
            "read_p95": (latency.get("read") or {}).get("p95"),
            "write_p95": (latency.get("write") or {}).get("p95"),
            "all_p50": (latency.get("all") or {}).get("p50"),
            "all_p95": (latency.get("all") or {}).get("p95"),
            "decision_p50": decision_latency.get("p50"),
            "decision_p95": decision_latency.get("p95"),
            "decision_max": decision_latency.get("max"),
        },
        "gates": {"evaluated": len(gates), "failing": failing_gates},
        "commit": (report.get("meta") or {}).get("environment", {}).get("commit"),
        "generated_at": (report.get("meta") or {}).get("generated_at"),
    }


def _fmt(value: object, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def render_markdown(summaries: dict[str, dict], missing: list[str]) -> str:
    baseline = summaries.get("baseline") or {}
    lines = [
        "# Decision layer comparison — baseline vs Jev shadow vs Jev advisory",
        "",
        f"Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} from committed harness reports in "
        f"`03-poc-src/data/reports/`.",
        "",
        "> **All decision-layer runs use the synthetic `mock` provider.** These numbers test the machinery and the "
        "harness's ability to measure it — they are *not* a measurement of Jev, and must not be quoted as one. "
        "A live-provider run is the only source for a Jev claim (see `docs/DECISION_LAYER.md`).",
        "",
        "## Runs",
        "",
        "| run | verdict | cases | failed | decision | profile | gates failing |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, _prefix, label, _ in RUNS:
        summary = summaries.get(key)
        if not summary:
            lines.append(f"| {key} | *missing report* | | | | | |")
            continue
        decision = summary["decision"]
        mode = "off" if not decision.get("enabled") else f"{decision.get('mode')}"
        failing = ", ".join(summary["gates"]["failing"]) or "—"
        lines.append(
            f"| {key} | {summary['verdict']} | {_fmt(summary['cases'])} | {_fmt(summary['cases_failed'])} | "
            f"{mode} | {decision.get('profile') or '—'} | {failing} |"
        )

    lines += [
        "",
        "## Governance parity (the invariant that matters most)",
        "",
        "| run | unauthorized writes | duplicate orders | audit coverage | audit chain | policy enforcement |",
        "|---|---|---|---|---|---|",
    ]
    for key, _prefix, _label, _ in RUNS:
        summary = summaries.get(key)
        if not summary:
            continue
        governance = summary["governance"]
        lines.append(
            f"| {key} | {_fmt(governance['unauthorized_writes'])} | {_fmt(governance['duplicate_orders'])} | "
            f"{_fmt(governance['audit_coverage'], suffix='%')} | {_fmt(governance['audit_chain_valid'])} | "
            f"{_fmt(governance['policy_enforcement_rate'], suffix='%')} |"
        )

    lines += [
        "",
        "## Decision quality and safety",
        "",
        "| run | route acc | top-2 | abstention prec | ambiguity P/R | injection P/R | esc security P/R | disagreement | narrowing |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for key, _prefix, _label, _ in RUNS:
        summary = summaries.get(key)
        if not summary or not (summary["decision"].get("enabled")):
            continue
        decision = summary["decision"]
        ambiguity = decision.get("ambiguity") or {}
        injection = decision.get("injection") or {}
        security = (decision.get("escalation") or {}).get("security") or {}
        lines.append(
            f"| {key} | {_fmt(decision.get('tool_selection_accuracy'), suffix='%')} | "
            f"{_fmt(decision.get('top2_coverage'), suffix='%')} | "
            f"{_fmt(decision.get('abstention_precision'), suffix='%')} | "
            f"{_fmt(ambiguity.get('precision'), suffix='%')} / {_fmt(ambiguity.get('recall'), suffix='%')} | "
            f"{_fmt(injection.get('precision'), suffix='%')} / {_fmt(injection.get('recall'), suffix='%')} | "
            f"{_fmt(security.get('precision'), suffix='%')} / {_fmt(security.get('recall'), suffix='%')} | "
            f"{_fmt((decision.get('disagreement') or {}).get('count'))} | "
            f"{_fmt(decision.get('narrowing_rate'), suffix='%')} |"
        )
    lines += [
        "",
        "| run | unauthorized writes (decision path) | duplicates | expected tool removed by narrowing | risk downgrades | quarantines | fallback rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, _prefix, _label, _ in RUNS:
        summary = summaries.get(key)
        if not summary or not (summary["decision"].get("enabled")):
            continue
        safety = summary["safety"]
        lines.append(
            f"| {key} | {_fmt(safety.get('unauthorized_writes'))} | {_fmt(safety.get('duplicate_orders'))} | "
            f"{_fmt(safety.get('narrowed_expected_tool_removed'))} | "
            f"{_fmt(safety.get('escalation_lowered_deterministic_risk'))} | {_fmt(safety.get('quarantines'))} | "
            f"{_fmt(summary['system'].get('fallback_rate'), suffix='%')} |"
        )

    lines += [
        "",
        "## Latency",
        "",
        "| run | read p95 (ms) | write p95 (ms) | all p95 (ms) | decision p50 (ms) | decision p95 (ms) | decision max (ms) |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, _prefix, _label, _ in RUNS:
        summary = summaries.get(key)
        if not summary:
            continue
        latency = summary["latency"]
        lines.append(
            f"| {key} | {_fmt(latency['read_p95'])} | {_fmt(latency['write_p95'])} | {_fmt(latency['all_p95'])} | "
            f"{_fmt(latency['decision_p50'])} | {_fmt(latency['decision_p95'])} | {_fmt(latency['decision_max'])} |"
        )

    lines += [
        "",
        "## Cost",
        "",
        "- Synthetic (`mock`) runs: **$0** — no network, no provider tokens. This is the CI path and the only path "
        "that runs on every change.",
        "- Live (Jev/TypeSafe) runs would add one call per screened turn; the decision layer's own price is the "
        "provider's, and the harness records `decision.tokens` so the cost is derived from measured usage rather "
        "than an estimate (see `docs/DECISION_LAYER.md` for the cost model).",
        "",
        "## Honest reading of the differences",
        "",
        f"- The baseline run ({baseline.get('cases', '—')} cases) has no decision layer: it is the number the layer "
        "must not make worse.",
        "- Shadow records the signal and must not change a single outcome; the shadow and baseline governance and "
        "case counts are expected to be identical.",
        "- Advisory may narrow the offered tool set and may escalate or ask for clarification. On the oracle "
        "profile it does neither harm nor good to the golden outcomes, which is the point: the layer is additive.",
        "- The realistic and adversarial profiles are **negative controls**. They exist to show what the harness "
        "does when the provider is worse than advertised, and to prove the safety invariants hold under stress.",
    ]
    if missing:
        lines += ["", "## Missing inputs", ""] + [f"- {name}" for name in missing]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--json", dest="json_path", default=str(DEFAULT_DIR / "decision-comparison.json"))
    parser.add_argument("--out", dest="markdown_path", default=str(DEFAULT_DIR / "decision-comparison.md"))
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    summaries: dict[str, dict] = {}
    missing: list[str] = []
    for key, prefix, label, _ in RUNS:
        path = _latest(directory, prefix)
        if path is None:
            missing.append(f"{key}: no report matching `{prefix}-2*.json` (run the harness first)")
            continue
        summaries[key] = {**summarize(_load(path)), "label": label, "report": path.name}

    document = {
        "version": "1.0.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": {"kind": "mock", "synthetic": True},
        "caveat": "Synthetic-provider comparison. Not a measurement of Jev; a live-provider run is required for that.",
        "runs": summaries,
        "missing": missing,
    }
    json_path = Path(args.json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = Path(args.markdown_path)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(summaries, missing), encoding="utf-8")
    print(f"[compare] wrote {json_path} and {markdown_path}")
    for name in missing:
        print(f"[compare] missing: {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
