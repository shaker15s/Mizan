"""Markdown renderer — the artifact meant for a pull-request comment.

Short enough to read on GitHub, complete enough to argue with: verdict,
governance, criteria table, per-category slices, latency, and every failing
execution inside a collapsible block.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "✅" if value else "❌"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}{suffix}" if isinstance(value, float) else f"{value}{suffix}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _decision_lines(decision: Mapping[str, Any], report: Mapping[str, Any]) -> list[str]:
    """Markdown block for the decision layer, labelled with its provenance."""
    quality = decision.get("decision") or {}
    system = decision.get("system") or {}
    safety = decision.get("safety") or {}
    security = (quality.get("escalation") or {}).get("security") or {}
    ambiguity = quality.get("ambiguity_detection") or {}
    injection = quality.get("injection_detection") or {}
    provider = decision.get("provider") or "?"
    mode = decision.get("mode") or "?"
    profile = decision.get("profile") or (report.get("decision") or {}).get("profile") or "?"
    honesty = (
        "synthetic provider — this checks the machinery, it is **not** a measurement of Jev"
        if decision.get("synthetic")
        else "live provider"
    )
    rows = [
        ("route accuracy", _pct(quality.get("tool_selection_accuracy"))),
        ("top-2 coverage", _pct(quality.get("top2_coverage"))),
        ("abstention precision", _pct(quality.get("abstention_precision"))),
        ("ambiguity precision / recall", f"{_pct(ambiguity.get('precision'))} / {_pct(ambiguity.get('recall'))}"),
        ("injection precision / recall", f"{_pct(injection.get('precision'))} / {_pct(injection.get('recall'))}"),
        ("security escalation precision / recall", f"{_pct(security.get('precision'))} / {_pct(security.get('recall'))}"),
        ("disagreement rate", _pct(quality.get("disagreement", {}).get("rate"))),
        ("fallback rate", _pct(system.get("fallback_rate"))),
        ("provider error rate", _pct(system.get("provider_error_rate"))),
        ("decision p95 (ms)", _fmt((system.get("latency_ms") or {}).get("decision", {}).get("p95"))),
    ]
    block = [
        "",
        "### Decision intelligence · طبقة القرار *(signal only — cannot authorize)*",
        "",
        f"`provider {provider}` · `mode {mode}` · `profile {profile}` · {honesty}",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    block += [f"| {label} | {value} |" for label, value in rows]
    block += [
        "",
        "| safety invariant | value |",
        "|---|---|",
        f"| unauthorized writes | {_fmt(safety.get('unauthorized_writes', 0))} |",
        f"| duplicate orders | {_fmt(safety.get('duplicate_orders', 0))} |",
        f"| expected tool removed by narrowing | {_fmt(safety.get('narrowed_expected_tool_removed', 0))} |",
        f"| risk downgrades | {_fmt(safety.get('escalation_lowered_deterministic_risk', 0))} |",
        f"| quarantines | {_fmt(safety.get('quarantines', 0))} |",
    ]
    return block


def render(report: Mapping[str, Any], *, diff: Mapping[str, Any] | None = None, title: str = "Mizan eval report") -> str:
    meta = report.get("meta") or {}
    metrics = report.get("metrics") or {}
    totals = metrics.get("totals") or {}
    governance = metrics.get("governance") or {}
    model = metrics.get("model") or {}
    answer = metrics.get("answer_quality") or {}
    latency = metrics.get("latency_ms") or {}
    verdict = report.get("verdict") or {}
    label = verdict.get("label", "…")
    icon = "✅" if label == "PASS" else "🟡" if label.startswith("GATE") else "❌"
    environment = meta.get("environment") or {}

    lines: list[str] = [f"## {icon} {title} — `{label}`", ""]
    lines.append(
        f"**{totals.get('cases', 0)} cases · {totals.get('executions', 0)} executions · "
        f"{_fmt(metrics.get('pass_rate'), '%')} passing** · mode `{meta.get('mode')}` · "
        f"model `{meta.get('model')}` · prompt `{meta.get('prompt_version')}` · "
        f"dataset v{meta.get('dataset', {}).get('version')} · commit `{environment.get('commit')}`"
        + (" *(dirty)*" if environment.get("dirty") else "")
    )
    decision = metrics.get("decision_layer") or {}
    if decision.get("enabled"):
        lines += _decision_lines(decision, report)
    lines += ["", "### Governance · الحوكمة", "", "| check | result |", "|---|---|"]
    lines.append(f"| unauthorized successful writes | {_fmt(governance.get('unauthorized_writes'))} |")
    lines.append(f"| duplicate orders from retry | {_fmt(governance.get('duplicate_orders'))} |")
    lines.append(f"| audit coverage | {_pct(governance.get('audit_coverage'))} |")
    lines.append(f"| audit hash chain | {_fmt(governance.get('audit_chain_valid'))} |")
    lines.append(f"| idempotency conflict detected | {_fmt(governance.get('idempotency_conflict_detected'))} |")
    lines.append(f"| policy enforcement | {_pct(governance.get('policy_enforcement_rate'))} |")

    lines += ["", "### Model intelligence", ""]
    if model.get("tool_selection_accuracy") is None:
        lines.append(f"> {model.get('note') or 'Not scored in this mode (a scripted LLM agreeing with the expected tool is circular). Run `--mode live`.'}")
    else:
        lines += ["| metric | value |", "|---|---|"]
        lines.append(f"| tool selection (Arabic) | {_pct(model.get('tool_selection_accuracy'))} |")
        lines.append(f"| parameter accuracy | {_pct(model.get('parameter_accuracy'))} |")
        lines.append(f"| outcome accuracy | {_pct(model.get('outcome_accuracy'))} |")
        lines.append("")
        lines.append("Ground truth: `tests/test_cases.json` · real ERP reads · audit-backed.")

    lines += ["", "### Answer quality", "", "| metric | value |", "|---|---|"]
    lines.append(f"| structured (sections + table + next steps) | {_pct(answer.get('structured_rate'))} |")
    lines.append(f"| grounded numbers (nothing invented) | {_pct(answer.get('grounded_rate'))} |")
    lines.append(f"| non-empty response | {_pct(answer.get('non_empty_rate'))} |")
    lines.append(f"| reasoning / policy leaks | {_fmt(answer.get('reasoning_leaks'))} |")

    lines += ["", "### Latency (per execution)", "", "| channel | n | p50 | p95 | p99 | max |", "|---|---|---|---|---|---|"]
    for key in ("read", "write", "llm", "all"):
        block = latency.get(key) or {}
        lines.append(
            f"| {key} | {block.get('count', 0)} | {_fmt(block.get('p50'), 'ms')} | {_fmt(block.get('p95'), 'ms')} | {_fmt(block.get('p99'), 'ms')} | {_fmt(block.get('max'), 'ms')} |"
        )

    slices = (metrics.get("slices") or {}).get("by_category") or {}
    if slices:
        lines += ["", "### Slices", "", "| category | cases | executions | pass rate | flaky | failure tags |", "|---|---|---|---|---|---|"]
        for key, bucket in sorted(slices.items()):
            tags = ", ".join(f"`{tag}`" for tag in sorted(bucket.get("tags") or {})) or "—"
            lines.append(f"| {key} | {bucket.get('cases')} | {bucket.get('executions')} | {_pct(bucket.get('pass_rate'))} | {bucket.get('flaky')} | {tags} |")

    reliability = metrics.get("reliability") or {}
    if reliability.get("repeat_cases"):
        lines += ["", f"**Stability:** {_pct(reliability.get('pass_k_rate'))} of {reliability.get('repeat_cases')} cases passed on every repeat (pass^{reliability.get('repeat_cases')})."]

    gates = ((report.get("thresholds") or {}).get("evaluated") or [])
    if gates:
        lines += ["", "### Release gates", "", "| criterion | value | required | state |", "|---|---|---|---|"]
        for row in gates:
            state = {"pass": "✅", "fail": "❌", "n/a": "➖"}.get(row.get("state", "n/a"), row.get("state"))
            lines.append(f"| `{row.get('criterion')}` | {_fmt(row.get('value'))} | {row.get('required')} | {state} |")

    failures = report.get("failures") or []
    lines += ["", f"### Failures ({len(failures)})", ""]
    if not failures:
        lines.append("No failing executions. 🎉")
    else:
        for row in failures[:40]:
            lines.append(f"<details><summary>❌ <code>{row.get('case_id')}</code> · {row.get('category')} · {', '.join(row.get('tags') or [])}</summary>")
            lines.append("")
            lines.append(f"> {row.get('input')}")
            lines.append("")
            lines.append(f"- expected tool `{row.get('expected_tool')}` · got `{row.get('actual_tool')}`")
            lines.append(f"- expected outcome `{row.get('expected_outcome')}` · got `{row.get('status')}` (error `{row.get('error_code') or '—'}`)")
            for check in row.get("checks") or []:
                lines.append(f"- **{check.get('name')}**: {check.get('detail')}")
            if row.get("error"):
                lines.append(f"- crash: `{row.get('error')}`")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    if diff and diff.get("available"):
        lines += ["", "### Baseline diff", ""]
        base, cur = diff.get("baseline") or {}, diff.get("current") or {}
        lines.append(f"baseline `{base.get('mode')}`/`{base.get('model')}` @ {base.get('generated_at')} → current `{cur.get('mode')}`/`{cur.get('model')}` @ {cur.get('generated_at')}")
        if not diff.get("comparable"):
            lines.append("")
            lines.append("> ⚠️ modes or dataset versions differ — deltas are informational only.")
        if diff.get("pass_rate_delta") is not None:
            lines.append(f"- pass rate delta: **{diff['pass_rate_delta']:+.2f}pp**")
        for row in diff.get("regressions") or []:
            lines.append(f"- ❌ regression `{row['criterion']}`: {row['before']} → {row['after']} ({row['delta']:+g})")
        for row in diff.get("improvements") or []:
            lines.append(f"- ✅ improvement `{row['criterion']}`: {row['before']} → {row['after']} ({row['delta']:+g})")
        if diff.get("newly_failing"):
            lines.append(f"- newly failing: {', '.join(f'`{i}`' for i in diff['newly_failing'])}")
        if diff.get("newly_passing"):
            lines.append(f"- newly passing: {', '.join(f'`{i}`' for i in diff['newly_passing'])}")
        if not (diff.get("regressions") or diff.get("newly_failing")):
            lines.append("- ✅ no regressions vs baseline")

    artifacts: Sequence[str] = report.get("artifacts") or []
    if artifacts:
        lines += ["", "### Artifacts", ""] + [f"- `{path}`" for path in artifacts]
    return "\n".join(lines) + "\n"


__all__ = ["render"]
