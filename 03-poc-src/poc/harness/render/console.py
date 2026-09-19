"""Console renderer — an ANSI dashboard you can read in one screenful.

Designed for the loop a developer actually runs: one command, verdict at the
top, only the failures in detail, everything else as compact bars. Colour is
disabled automatically when stdout is not a TTY or ``NO_COLOR`` is set.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from typing import Any, Mapping, Sequence

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def display_width(text: str) -> int:
    """Terminal columns a string occupies (ANSI ignored, wide CJK counted ×2)."""
    plain = _ANSI_RE.sub("", str(text))
    total = 0
    for char in plain:
        if unicodedata.combining(char):
            continue
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def fit(text: str, size: int) -> str:
    """Pad or ellipsize to exactly ``size`` display columns."""
    current = display_width(text)
    if current > size:
        keep = ""
        for char in _ANSI_RE.sub("", str(text)):
            if display_width(keep + char) > size - 1:
                break
            keep += char
        return keep + "…"
    return text + " " * (size - current)

RESET, BOLD, DIM, RED, GREEN, YELLOW, CYAN, MAGENTA = "\x1b[0m", "\x1b[1m", "\x1b[2m", "\x1b[31m", "\x1b[32m", "\x1b[33m", "\x1b[36m", "\x1b[35m"

_CATEGORY_LABELS = {
    "read_happy": "read · happy path",
    "read_product": "read · products",
    "write_happy": "write · happy path",
    "authz_denied": "authz · denied",
    "no_access": "authz · no access",
    "duplicate_idempotency": "idempotency",
    "erp_error": "ERP failure",
    "prompt_injection": "injection",
    "ambiguous_entity": "ambiguity",
}


def supports_color(stream: Any = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class Paint:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + RESET


def width(preferred: int = 100) -> int:
    try:
        return max(72, min(preferred, shutil.get_terminal_size((preferred, 24)).columns))
    except OSError:
        return preferred


def rule(paint: Paint, total: int, title: str = "", *, char: str = "─", color: str = DIM) -> str:
    if not title:
        return paint(char * total, color)
    label = f" {title} "
    remaining = max(0, total - display_width(label) - 1)
    return paint(char + label, color) + paint(char * remaining, color)


def bar(ratio: float | None, size: int = 22, *, paint: Paint | None = None) -> str:
    if ratio is None:
        return paint("— not measured —", DIM) if paint else "— not measured —"
    clamped = max(0.0, min(1.0, float(ratio)))
    filled = int(round(clamped * size))
    text = "█" * filled + "░" * (size - filled)
    if paint is not None:
        color = GREEN if clamped >= 0.999 else YELLOW if clamped >= 0.8 else RED
        text = paint(text, color)
    return f"{text} {clamped * 100:.1f}%"


def spark(values: Sequence[float]) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    clean = [float(value) for value in values if value is not None and value >= 0]
    if not clean:
        return "—"
    high = max(clean) or 1.0
    return "".join(blocks[min(len(blocks) - 1, int(value / high * (len(blocks) - 1)))] for value in clean[:48])


def table(rows: Sequence[Sequence[str]], *, align: str = "<<<<", paint: Paint | None = None, headers: Sequence[str] | None = None) -> str:
    if not rows and not headers:
        return ""
    columns = list(headers) if headers else (rows[0] if rows else [])
    body = list(rows[1:] if headers is not None and rows else rows)
    widths = [len(str(col)) for col in columns]
    for row in body:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], len(str(cell)))
    lines: list[str] = []
    if headers:
        header = "  ".join(_pad(str(col), widths[i], align[i] if i < len(align) else "<") for i, col in enumerate(columns))
        lines.append(paint(header, BOLD, CYAN) if paint else header)
    for row in body:
        lines.append("  ".join(_pad(str(cell), widths[i], align[i] if i < len(align) else "<") for i, cell in enumerate(row)))
    return "\n".join(lines)


def _pad(text: str, size: int, align: str = "<") -> str:
    gap = size - display_width(text)
    if gap <= 0:
        return text
    if align.startswith(">"):
        return " " * gap + text
    if align.startswith("^"):
        return " " * (gap // 2) + text + " " * (gap - gap // 2)
    return text + " " * gap


def pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int) and not suffix:
        return f"{value}"
    try:
        return f"{float(value):.1f}{suffix}"
    except (TypeError, ValueError):
        return f"{value}{suffix}"


def render(report: Mapping[str, Any], *, diff: Mapping[str, Any] | None = None, verbose: bool = False, show_cases: bool = True, out_files: Sequence[str] = ()) -> str:
    total = width()
    paint = Paint(supports_color())
    meta = report.get("meta") or {}
    metrics = report.get("metrics") or {}
    totals = metrics.get("totals") or {}
    governance = metrics.get("governance") or {}
    model = metrics.get("model") or {}
    answer = metrics.get("answer_quality") or {}
    latency = metrics.get("latency_ms") or {}
    verdict = (report.get("verdict") or {}).get("label", "…")
    lines: list[str] = []
    add = lines.append

    status_color = {"PASS": GREEN, "GATE FAIL": YELLOW}.get(verdict, RED)
    head = f"⚖  MIZAN EVAL · {meta.get('mode', '?')}"
    tail = f"verdict: {verdict}"
    inner = total - 4
    model_line = (
        f"{totals.get('cases', 0)} cases · {totals.get('executions', 0)} executions · "
        f"{num(metrics.get('runtime', {}).get('wall_ms'), 'ms')} · {totals.get('cases_failed', 0)} failing · "
        f"{totals.get('flaky_cases', 0)} flaky"
    )
    provenance_line = (
        f" model {meta.get('model')} · prompt {meta.get('prompt_version')} · git {meta.get('environment', {}).get('commit')}"
        f"{' (dirty)' if meta.get('environment', {}).get('dirty') else ''} · dataset v{meta.get('dataset', {}).get('version')}"
        f" · mode {meta.get('mode')}"
    )
    add("")
    add("╭" + "─" * (total - 2) + "╮")
    add("│ " + paint(fit(head, inner - display_width(tail) - 1), BOLD, CYAN) + " " + paint(tail, status_color, BOLD) + " │")
    add("│ " + paint(fit(model_line, inner), DIM) + " │")
    add("│ " + paint(fit(provenance_line, inner), DIM) + " │")
    add("╰" + "─" * (total - 2) + "╯")

    # --- governance ---------------------------------------------------------
    add(rule(paint, total, "GOVERNANCE · الحوكمة (always measured)", char="─"))
    rows = [
        ["unauthorized writes", paint(str(governance.get("unauthorized_writes", 0)), GREEN if not governance.get("unauthorized_writes") else RED)],
        ["duplicate orders", paint(str(governance.get("duplicate_orders", 0)), GREEN if not governance.get("duplicate_orders") else RED)],
        ["audit coverage", pct(governance.get("audit_coverage"))],
        ["audit chain", paint("valid", GREEN) if governance.get("audit_chain_valid") else paint(str(governance.get("audit_chain")), RED)],
        ["idempotency conflict", paint("detected", GREEN) if governance.get("idempotency_conflict_detected") else paint("MISSING", RED)],
        ["policy enforcement", pct(governance.get("policy_enforcement_rate"))],
        ["provider success", pct(governance.get("provider_success_rate"))],
    ]
    add(_two_columns(rows, total, paint))

    # --- model intelligence -------------------------------------------------
    add("")
    add(rule(paint, total, "MODEL INTELLIGENCE · ذكاء النموذج", char="─"))
    if model.get("tool_selection_accuracy") is None:
        add(paint("  " + (model.get("note") or "not scored in this mode"), DIM))
        add(paint("  FakeLLM was scripted with the expected tool/args, so 100% here would be circular.", DIM))
    else:
        for label, value in (("tool selection", model.get("tool_selection_accuracy")), ("parameter accuracy", model.get("parameter_accuracy")), ("outcome accuracy", model.get("outcome_accuracy"))):
            add(f"  {label:<22}{bar(value / 100.0 if value is not None else None, paint=paint)}")

    # --- answer quality -----------------------------------------------------
    add("")
    add(rule(paint, total, "ANSWER QUALITY · جودة الرد", char="─"))
    for label, value in (("structured (sections+table)", answer.get("structured_rate")), ("grounded numbers", answer.get("grounded_rate")), ("non-empty response", answer.get("non_empty_rate"))):
        add(f"  {label:<28}{bar(value / 100.0 if value is not None else None, paint=paint)}")
    leaks = answer.get("reasoning_leaks", 0)
    add(f"  {'reasoning leaks':<28}" + (paint("0", GREEN) if not leaks else paint(str(leaks), RED)))

    # --- latency ------------------------------------------------------------
    add("")
    add(rule(paint, total, "LATENCY · الزمن (per execution)", char="─"))
    lat_rows = [["channel", "n", "p50", "p95", "p99", "max", "spark"]]
    for key in ("read", "write", "llm", "all"):
        block = latency.get(key) or {}
        values = []
        lat_rows.append([key, str(block.get("count", 0)), num(block.get("p50")), num(block.get("p95")), num(block.get("p99")), num(block.get("max")), spark(_case_latencies(report, channel=key))])
    add("  " + table(lat_rows, align="<<>>><", paint=paint, headers=lat_rows[0]).replace("\n", "\n  "))

    # --- slices -------------------------------------------------------------
    add("")
    add(rule(paint, total, "SLICES · حسب الفئة", char="─"))
    slice_rows = [["category", "cases", "pass rate", "flaky", "tags"]]
    for key, bucket in sorted((metrics.get("slices") or {}).get("by_category", {}).items()):
        slice_rows.append([_CATEGORY_LABELS.get(key, key), str(bucket.get("cases")), pct(bucket.get("pass_rate")), str(bucket.get("flaky")), ",".join(sorted(bucket.get("tags") or {})) or "—"])
    add("  " + table(slice_rows, align="<<>><<", paint=paint, headers=slice_rows[0]).replace("\n", "\n  "))
    users = (metrics.get("slices") or {}).get("by_user") or {}
    if users:
        user_rows = [["user", "cases", "pass rate", "unauthorized writes"]]
        for key, bucket in sorted(users.items()):
            user_rows.append([key, str(bucket.get("cases")), pct(bucket.get("pass_rate")), str(bucket.get("unauthorized_writes"))])
        add("")
        add(rule(paint, total, "SLICES · حسب المستخدم", char="─"))
        add("  " + table(user_rows, align="<<><", paint=paint, headers=user_rows[0]).replace("\n", "\n  "))

    # --- threshold gates ----------------------------------------------------
    gates = ((report.get("thresholds") or {}).get("evaluated") or [])
    if gates:
        add("")
        add(rule(paint, total, "RELEASE GATES · بوابات الإصدار", char="─"))
        for row in gates:
            mark = {"pass": paint("✓", GREEN), "fail": paint("✗", RED), "n/a": paint("–", DIM)}[row.get("state", "n/a")]
            required = "required " + str(row.get("required"))
            add(f"  {mark} {_pad(str(row.get('criterion')), 40)}{_pad(num(row.get('value')), 9, '>')}  {paint(required, DIM)}")

    # --- cases --------------------------------------------------------------
    if show_cases:
        add("")
        add(rule(paint, total, "CASES", char="─"))
        add(_case_lines(report, paint, verbose=verbose))

    # --- failures -----------------------------------------------------------
    failures = report.get("failures") or []
    if failures:
        add("")
        add(rule(paint, total, f"FAILURES · {len(failures)}", color=RED, char="─"))
        for row in failures[: 12 if not verbose else 100]:
            add(paint(f"  ✗ {row.get('case_id')} · {row.get('category')} · run {row.get('run', 1)} attempt {row.get('attempt', 1)}", RED, BOLD))
            add(paint(f"    input: {row.get('input')}", DIM))
            if row.get("expected_tool") or row.get("actual_tool"):
                add(f"    tool: expected {row.get('expected_tool')} · got {paint(str(row.get('actual_tool')), RED if row.get('expected_tool') != row.get('actual_tool') else GREEN)}")
            if row.get("expected_outcome") or row.get("status"):
                add(f"    outcome: expected {row.get('expected_outcome')} · got {paint(str(row.get('status')), RED if not str(row.get('status') or '').startswith(str(row.get('expected_outcome'))) else GREEN)}")
            if row.get("tags"):
                add(f"    tags:  {paint(', '.join(row['tags']), YELLOW)}")
            for check in row.get("checks") or []:
                add(paint(f"    → {check.get('name')}: {check.get('detail')}", YELLOW))
    else:
        add("")
        add(paint("  ✓ no failing executions", GREEN, BOLD))

    # --- baseline diff ------------------------------------------------------
    if diff and diff.get("available"):
        add("")
        add(rule(paint, total, "BASELINE DIFF · مقارنة بالأساس", char="─"))
        base, cur = diff.get("baseline") or {}, diff.get("current") or {}
        add(paint(f"  baseline: {base.get('mode')}/{base.get('model')} @ {base.get('generated_at')} · current: {cur.get('mode')}/{cur.get('model')} @ {cur.get('generated_at')}", DIM))
        if not diff.get("comparable"):
            add(paint("  ⚠ modes or dataset versions differ — deltas are informational only", YELLOW))
        if diff.get("pass_rate_delta") is not None:
            delta = diff["pass_rate_delta"]
            color = GREEN if delta > 0 else RED if delta < 0 else DIM
            add(f"  pass rate: {paint(f'{delta:+.2f}pp', color)}")
        for row in diff.get("regressions") or []:
            add(paint(f"  ✗ regression {row['criterion']}: {row['before']} → {row['after']} ({row['delta']:+g})", RED))
        for row in diff.get("improvements") or []:
            add(paint(f"  ✓ improvement {row['criterion']}: {row['before']} → {row['after']} ({row['delta']:+g})", GREEN))
        if diff.get("newly_failing"):
            add(paint(f"  newly failing: {', '.join(diff['newly_failing'])}", RED))
        if diff.get("newly_passing"):
            add(paint(f"  newly passing: {', '.join(diff['newly_passing'])}", GREEN))
        if not (diff.get("regressions") or diff.get("newly_failing")):
            add(paint("  ✓ no regressions", GREEN))

    # --- artifacts ----------------------------------------------------------
    if out_files:
        add("")
        add(rule(paint, total, "ARTIFACTS", char="─"))
        for path in out_files:
            add(paint(f"  → {path}", MAGENTA))
    add("")
    return "\n".join(lines)


def _case_latencies(report: Mapping[str, Any], *, channel: str) -> list[float]:
    values: list[float] = []
    for case in report.get("cases") or []:
        for attempt in case.get("attempts") or []:
            block = attempt.get("latency_ms") or {}
            if channel == "all":
                values.append(float(block.get("total", 0.0)))
            elif channel == "llm":
                if block.get("llm"):
                    values.append(float(block["llm"]))
            elif channel in ("read", "write"):
                from poc.harness.metrics import READ_CATEGORIES, WRITE_CATEGORIES

                if (case.get("category") in READ_CATEGORIES) == (channel == "read"):
                    values.append(float(block.get("total", 0.0)))
    return values


def _two_columns(rows: Sequence[Sequence[str]], total: int, paint: Paint) -> str:
    half = (total - 6) // 2
    out: list[str] = []
    for index in range(0, len(rows), 2):
        left = rows[index]
        right = rows[index + 1] if index + 1 < len(rows) else ["", ""]
        out.append(f"  {_pad(str(left[0]), half)}{str(left[1]):<12}  {_pad(str(right[0]), half)}{str(right[1])}")
    return "\n".join(out)


def _case_lines(report: Mapping[str, Any], paint: Paint, *, verbose: bool) -> str:
    groups: dict[str, list[str]] = {}
    for case in report.get("cases") or []:
        glyph = paint("✓", GREEN) if case.get("passed") else paint("✗", RED)
        flaky = paint(" ±", YELLOW) if case.get("flaky") else ""
        repeats = f" ×{case.get('runs')}" if (case.get("runs") or 1) > 1 else ""
        lat = f"{case.get('latency_ms', 0):.0f}ms"
        groups.setdefault(case.get("category", "?"), []).append(f"{glyph} {case.get('case_id')}{repeats}{flaky} {_pad(str(case.get('user')), 18)} {lat.rjust(7)}")
    lines: list[str] = []
    for category in sorted(groups):
        rows = groups[category]
        if not verbose:
            compact = "  ".join(row.split()[0] + row.split()[1] for row in rows)
            lines.append(f"  {_CATEGORY_LABELS.get(category, category):<24}{paint(f'[{len(rows)}]', DIM)} {compact}")
        else:
            lines.append(f"  {_CATEGORY_LABELS.get(category, category)}")
            lines.extend(f"    {row}" for row in rows)
    return "\n".join(lines)


__all__ = ["render", "rule", "bar", "spark", "supports_color", "table", "Paint"]
