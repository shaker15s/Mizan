"""Command line for the Mizan evaluation harness.

Everything the team needs from one module, and one command memorized:

    python -m poc.harness                     # fast deterministic run + verdict
    python -m poc.harness --mode live         # real model, scored intelligence
    python -m poc.harness doctor              # is my setup trustworthy?
    python -m poc.harness explain TC-044      # why did this fail?

Exit codes are meaningful on purpose: 0 clean, 1 failing executions,
2 usage/setup error, 3 release-gate or baseline regression.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from poc.harness import __version__
from poc.harness.cases import Dataset, DatasetError, dataset_summary, load_dataset, select_tokens
from poc.harness.report import DEFAULT_THRESHOLDS_PATH, ReportBuilder, diff_reports, environment_info, load_baseline, load_thresholds, resolve_verdict, write_report
from poc.harness.render import console, html as html_report, markdown as markdown_report
from poc.harness.runner import DETERMINISTIC, LIVE, SIMULATED, RunOptions, Runner

SRC_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = SRC_ROOT / "data" / "reports"
MODES = (DETERMINISTIC, LIVE, SIMULATED)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m poc.harness",
        description="Mizan evaluation harness — deterministic, fast, and honest about what it can and cannot measure.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("command", nargs="?", default="run", choices=("run", "doctor", "explain", "list"), help="what to do")
    parser.add_argument("target", nargs="?", help="case id for 'explain', or a selector for 'list'")
    parser.add_argument("--mode", choices=MODES, default=DETERMINISTIC, help="deterministic = scripted LLM (CI); live = real provider; simulated = rules engine")
    parser.add_argument("--dataset", default=str(SRC_ROOT / "tests" / "test_cases.json"), help="test-case dataset")
    parser.add_argument("--filter", dest="selectors", action="append", default=[], metavar="SELECTOR", help="case id / category / user / outcome:/tag:/text:/limit:N — same kind ORs, different kinds AND")
    parser.add_argument("--category", action="append", default=[], help="shortcut for --filter <category> (repeatable)")
    parser.add_argument("--user", action="append", default=[], help="shortcut for --filter <user> (repeatable)")
    parser.add_argument("--limit", type=int, help="cap the number of selected cases")
    parser.add_argument("--jobs", type=int, default=1, help="parallel shards (each gets its own store + seeded ERP)")
    parser.add_argument("--repeat-reads", type=int, default=3, help="runs per read case (stability / pass^k)")
    parser.add_argument("--repeat-writes", type=int, default=1, help="runs per write case (keep 1 unless the ERP double is shared)")
    parser.add_argument("--no-repeat", action="store_true", help="single run per case — quickest loop while editing")
    parser.add_argument("--erp", choices=("seeded", "fake"), default="seeded", help="seeded = the same fixture data the eval expects (42-47, 55-59)")
    parser.add_argument("--confirm-ttl", type=int, default=30, help="pending-proposal TTL inside the harness (seconds)")
    parser.add_argument("--model", help="provider model id (live mode); default $POC_LLM_MODEL")
    parser.add_argument("--temperature", type=float, help="override sampling temperature (live)")
    parser.add_argument("--provider-timeout", type=float, default=60.0, help="per-call provider timeout (live)")
    parser.add_argument("--retries", type=int, default=1, help="provider retry budget (live)")
    parser.add_argument("--force-tool-choice", action="store_true", help="force tool choice instead of letting the model decline (diagnostic only)")
    parser.add_argument("--budget-read-ms", type=float, help="fail read executions slower than this")
    parser.add_argument("--budget-write-ms", type=float, help="fail write executions slower than this")
    parser.add_argument("--format", dest="formats", default="console,json,md,html", help="comma list of: console,json,md,html,none")
    parser.add_argument("--out", dest="out_dir", default=str(DEFAULT_OUT_DIR), help="directory for report files")
    parser.add_argument("--name", default="mizan-eval", help="report filename prefix")
    parser.add_argument("--baseline", help="previous report JSON to diff for regressions ('latest' = newest in --out)")
    parser.add_argument("--thresholds", help="JSON file of release thresholds")
    parser.add_argument("--no-gates", action="store_true", help="skip release-gate evaluation")
    parser.add_argument("--fail-under", action="append", default=[], metavar="PATH=VALUE", help="extra gate, e.g. --fail-under model.tool_selection_accuracy=90")
    parser.add_argument("--exit-zero", action="store_true", help="always exit 0 (reporting runs)")
    parser.add_argument("--keep-env", action="store_true", help="do not delete the scratch DBs (debugging)")
    parser.add_argument("--shared-env", dest="shared_env", action="store_true", help="legacy mode: one environment for the whole run (state leaks between cases)")
    parser.add_argument("--verbose", action="store_true", help="per-case list + every failure")
    parser.add_argument("--quiet", action="store_true", help="no progress lines")
    parser.add_argument("--fail-fast", action="store_true", help="stop at the first failing execution")
    parser.add_argument("--progress", action="store_true", help="print one line per execution while running")
    parser.add_argument("--json", action="store_true", help="print the raw report JSON instead of the dashboard")
    # --- decision intelligence (Jev) ---------------------------------------
    parser.add_argument("--decision-provider", choices=("off", "mock", "typesafe"), default="off",
                        help="off = no decision layer (baseline); mock = deterministic offline profiles; typesafe = real Jev API")
    parser.add_argument("--jev-mode", dest="decision_mode", choices=("off", "shadow", "advisory", "enforcing"), default="off",
                        help="shadow = record only; advisory = may narrow/escalate/clarify; enforcing = gated (requires thresholds + key)")
    parser.add_argument("--jev-profile", dest="decision_profile", choices=("oracle", "realistic", "adversarial"), default="oracle",
                        help="synthetic provider quality for --decision-provider mock")
    parser.add_argument("--jev-split", dest="decision_split", choices=("calibration", "validation", "heldout"), default=None,
                        help="restrict scoring to one split of tests/decision_split.json (report records the split)")
    parser.add_argument("--decision-scenarios", default=None, help="scenario document for the mock provider")
    parser.add_argument("--decision-thresholds", default=None, help="JSON file of decision-layer thresholds")
    parser.add_argument("--version", action="version", version=f"mizan-harness {__version__}")
    return parser


def _selector_options(args: argparse.Namespace) -> tuple[list[str], int | None]:
    selectors = [str(value) for value in (args.selectors or [])]
    selectors += [f"category:{value}" for value in (args.category or [])]
    selectors += [f"user:{value}" for value in (args.user or [])]
    if args.command == "list" and args.target:
        selectors.append(args.target)
    if args.command == "run" and args.target:
        selectors.append(args.target)
    return selectors, args.limit


def _options(args: argparse.Namespace) -> RunOptions:
    repeats = (1, 1) if args.no_repeat else (args.repeat_reads, args.repeat_writes)
    return RunOptions(
        mode=args.mode,
        erp=args.erp,
        model=args.model,
        repeat_reads=repeats[0],
        repeat_writes=repeats[1],
        jobs=max(1, int(args.jobs)),
        keep_env=args.keep_env,
        env_root=None,
        confirm_ttl_seconds=args.confirm_ttl,
        read_budget_ms=args.budget_read_ms,
        write_budget_ms=args.budget_write_ms,
        fail_fast=args.fail_fast,
        provider_timeout=args.provider_timeout,
        provider_retries=args.retries,
        temperature=args.temperature,
        force_tool_choice="required" if args.force_tool_choice else None,
        thresholds_path=Path(args.thresholds) if args.thresholds else None,
        allow_cross_case_state=bool(getattr(args, "shared_env", False)),
        decision_provider=getattr(args, "decision_provider", "off") or "off",
        decision_mode=getattr(args, "decision_mode", "off") or "off",
        decision_profile=getattr(args, "decision_profile", "oracle") or "oracle",
        decision_split=getattr(args, "decision_split", None),
        decision_scenarios=Path(args.decision_scenarios) if getattr(args, "decision_scenarios", None) else None,
        decision_thresholds=Path(args.decision_thresholds) if getattr(args, "decision_thresholds", None) else None,
    )


def run_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    selectors, limit = _selector_options(args)
    try:
        dataset = load_dataset(args.dataset)
    except (DatasetError, OSError, json.JSONDecodeError) as exc:
        print(f"[harness] dataset error: {exc}", file=sys.stderr)
        return 2
    if args.decision_mode == "enforcing" and args.decision_provider == "off":
        print("[harness] --jev-mode enforcing needs --decision-provider mock|typesafe.", file=sys.stderr)
        return 2
    if args.mode == LIVE and not args.model and not _provider_configured():
        print("[harness] --mode live needs POC_LLM_API_KEY (or POC_LLM_BASE_URL) in the environment.", file=sys.stderr)
        return 2
    try:
        selected = select_tokens(dataset, selectors, limit=limit)
    except (DatasetError, ValueError) as exc:
        print(f"[harness] {exc}", file=sys.stderr)
        return 2

    options = _options(args)
    runner = Runner(dataset, selected, options)
    progress = _progress_printer(total=len(selected) * max(1, options.repeat_reads)) if (args.progress or (sys.stdout.isatty() and not args.quiet)) else None
    decision_note = "off" if options.decision_provider == "off" or options.decision_mode == "off" else f"{options.decision_provider}/{options.decision_mode}/{options.decision_profile}"
    print(f"[harness] {len(selected)}/{len(dataset.cases)} cases · mode={args.mode} · jobs={options.jobs} · erp={args.erp} · decision={decision_note}", flush=True)
    # An armed gate that silently disarms is worse than no gate: if the operator
    # asked for screening and the layer could not be built, say so loudly here and
    # let the `decision_layer.enabled` gate turn the run red (plan §27, §42).
    armed_refusal = ""
    if options.decision_provider != "off" and options.decision_mode != "off":
        summary = runner.decision_summary()
        if not summary.get("active", False):
            armed_refusal = str(summary.get("refusal_reason") or (summary.get("build_errors") or ["unknown"])[0])[:200]
            print(f"[harness] ⚠ decision layer requested but NOT active: {armed_refusal}", file=sys.stderr)
    try:
        result = runner.run(progress=progress)
    except Exception as exc:  # noqa: BLE001 - a harness crash must be loud, not silent
        print(f"[harness] run aborted: {type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2
    finally:
        runner.cleanup()

    thresholds: dict[str, Any] = {}
    if not args.no_gates:
        thresholds = load_thresholds(
            args.thresholds,
            mode=args.mode,
            decision_mode=(None if getattr(args, "decision_provider", "off") == "off" else getattr(args, "decision_mode", "off")),
        )
        for override in args.fail_under:
            if "=" not in override:
                print(f"[harness] ignoring --fail-under {override!r} (expected path=value)", file=sys.stderr)
                continue
            key, _, raw = override.partition("=")
            try:
                thresholds[key.strip()] = float(raw)
            except ValueError:
                thresholds[key.strip()] = raw.strip()

    report = ReportBuilder(result, dataset, options, selected, thresholds=thresholds).build()
    report["decision"] = runner.decision_summary()
    baseline_path = None if args.baseline in (None, "", "latest") else args.baseline
    baseline = load_baseline(baseline_path if args.baseline else None)
    if args.baseline == "latest":
        baseline = load_baseline(DEFAULT_OUT_DIR if not args.out_dir else Path(args.out_dir))
    diff = diff_reports(baseline, report)
    report["baseline_diff"] = diff
    verdict, code = resolve_verdict(report, diff=diff)
    report["verdict"] = {"label": verdict, "exit_code": code}

    out_dir = Path(args.out_dir)
    formats = [item.strip().lower() for item in args.formats.split(",") if item.strip()]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    planned = {
        "json": out_dir / f"{args.name}-{stamp}.json",
        "md": out_dir / f"{args.name}-latest.md",
        "html": out_dir / f"{args.name}-latest.html",
    }

    def relative(path: Path) -> str:
        return str(path.relative_to(SRC_ROOT)) if path.is_relative_to(SRC_ROOT) else str(path)

    report["artifacts"] = [relative(planned[key]) for key in ("json", "md", "html") if key in formats]
    artifacts = list(report["artifacts"])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        formats = [item for item in formats if item != "console"]
    out_dir.mkdir(parents=True, exist_ok=True)
    if "md" in formats:
        text = markdown_report.render(report, diff=diff)
        planned["md"].write_text(text, encoding="utf-8")
        (out_dir / f"{args.name}-latest.md").write_text(text, encoding="utf-8")
        if args.mode == LIVE:
            print(text)
    if "html" in formats:
        planned["html"].write_text(html_report.render(report, diff=diff), encoding="utf-8")
    if "json" in formats:
        payload = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
        planned["json"].write_text(payload, encoding="utf-8")
        (out_dir / f"{args.name}-latest.json").write_text(payload, encoding="utf-8")
    if "console" in formats and not args.json:
        print(console.render(report, diff=diff, verbose=args.verbose, show_cases=not args.quiet, out_files=artifacts))
    print(f"[harness] verdict: {verdict} · exit {code}" + (f" · reports: {', '.join(artifacts)}" if artifacts else ""))
    return 0 if args.exit_zero else code


def _progress_printer(*, total: int) -> Any:
    state = {"done": 0}

    def progress(case: Any, attempt: Any, *, attempts_total: int = 0) -> str | None:
        state["done"] += 1
        mark = "✓" if attempt.passed else "✗"
        lat = attempt.latency_ms.get("total", 0.0)
        return f"  [{state['done']}] {mark} {attempt.case_id} {attempt.category:<20} {attempt.actual_tool or '—':<18} {lat:6.1f}ms"

    return progress


def _provider_configured() -> bool:
    import os

    return bool(os.environ.get("POC_LLM_API_KEY") or os.environ.get("POC_LLM_BASE_URL") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def doctor(args: argparse.Namespace) -> int:
    """Validate the harness itself — a broken harness must not look like a green run."""
    from poc.harness.render import console as console_render

    paint = console_render.Paint(console_render.supports_color())
    ok = fail = warn = 0

    def line(state: str, label: str, detail: str = "") -> None:
        nonlocal ok, fail, warn
        mark = {"ok": "✓", "warn": "!", "fail": "✗"}[state]
        color = {"ok": console_render.GREEN, "warn": console_render.YELLOW, "fail": console_render.RED}[state]
        ok, fail, warn = (ok + (state == "ok"), fail + (state == "fail"), warn + (state == "warn"))
        print(f"  {paint(mark, color, console_render.BOLD)} {label}{paint(f' — {detail}', console_render.DIM) if detail else ''}")

    print(f"\n{paint('MIZAN HARNESS · doctor', console_render.BOLD, console_render.CYAN)}")
    info = environment_info()
    line("ok" if sys.version_info >= (3, 11) else "fail", "python", sys.version.split()[0])
    try:
        dataset = load_dataset(args.dataset)
        summary = dataset_summary(dataset)
        line("ok", "dataset", f"{summary['cases']} cases · v{summary['version']} · {summary['executions_planned']} planned executions")
        line("ok", "coverage", " · ".join(f"{key}:{value}" for key, value in sorted(summary["by_category"].items())))
        for outcome, count in sorted(summary["by_outcome"].items()):
            if outcome == "success" and count == summary["cases"]:
                line("warn", "outcomes", "every case expects 'success' — negative paths are probably missing")
    except Exception as exc:  # noqa: BLE001
        line("fail", "dataset", f"{type(exc).__name__}: {exc}")
        return 1
    for module in ("poc.agent_runtime", "poc.gateway", "poc.web_server", "poc.settings", "poc.integrations", "poc.responder", "poc.simulated_llm"):
        try:
            __import__(module)
            line("ok", "import", module)
        except Exception as exc:  # noqa: BLE001
            line("fail", "import", f"{module}: {type(exc).__name__}: {exc}")
    try:
        from poc.tool_contracts import get_registry

        names = get_registry().names()
        line("ok" if len(names) == 5 else "warn", "tool contract", f"{len(names)} tools: {', '.join(names)}")
    except Exception as exc:  # noqa: BLE001
        line("fail", "tool contract", f"{type(exc).__name__}: {exc}")
    line("ok" if _provider_configured() else "warn", "provider", "POC_LLM_* present → --mode live usable" if _provider_configured() else "no provider env — --mode live unavailable")
    thresholds = load_thresholds(args.thresholds, mode=args.mode)
    line("ok" if thresholds else "warn", "gates", f"{len(thresholds)} thresholds from {args.thresholds or DEFAULT_THRESHOLDS_PATH}" if thresholds else f"no thresholds file at {DEFAULT_THRESHOLDS_PATH}")
    try:
        from poc.decision.profiles import load_scenarios, load_split
        from poc.decision.router import DecisionConfig
        from poc.settings import get_settings

        scenarios = load_scenarios()
        split = load_split()
        config = DecisionConfig.from_settings(get_settings())
        line("ok", "decision scenarios", f"{len(scenarios.cases)} scripted cases · profiles {sorted(scenarios.profiles)}")
        line("ok", "decision split", f"{split.counts} (version {split.version})")
        line(
            "ok" if not config.enabled else "warn",
            "decision layer",
            f"provider={config.provider} mode={config.mode} key={'yes' if config.api_key else 'no'} (enabled only when explicitly configured)",
        )
    except Exception as exc:  # noqa: BLE001
        line("fail", "decision layer", f"{type(exc).__name__}: {exc}")
    started = time.perf_counter()
    try:
        options = RunOptions(mode=DETERMINISTIC, repeat_reads=1, repeat_writes=1, confirm_ttl_seconds=30)
        runner = Runner(dataset, dataset.cases[:6], options)
        result = runner.run()
        runner.cleanup()
        elapsed = (time.perf_counter() - started) * 1000
        failing = result["metrics"]["totals"]["cases_failed"]
        line("ok" if failing == 0 else "fail", "smoke run", f"6 cases in {elapsed:.0f}ms · {result['metrics']['totals']['executions']} executions · {failing} failing")
        line("ok" if elapsed < 4000 else "warn", "speed budget", f"{elapsed / 6:.0f}ms per case")
    except Exception as exc:  # noqa: BLE001
        line("fail", "smoke run", f"{type(exc).__name__}: {exc}")
    print(paint(f"\n{ok} checks ok · {warn} warnings · {fail} blockers\n", console_render.BOLD, console_render.GREEN if not fail else console_render.RED))
    return 1 if fail else 0


def explain(args: argparse.Namespace) -> int:
    if not args.target:
        print("[harness] explain needs a case id, e.g. python -m poc.harness explain TC-044", file=sys.stderr)
        return 2
    report = load_baseline(args.report if getattr(args, "report", None) else Path(args.out_dir))
    if not report:
        print("[harness] no report to explain — run the harness first (or pass --out)", file=sys.stderr)
        return 2
    matches = [case for case in report.get("cases") or [] if args.target.lower() in str(case.get("case_id", "")).lower() or args.target.lower() in str(case.get("input", "")).lower()]
    if not matches:
        print(f"[harness] {args.target!r} not present in this report")
        return 1
    for case in matches:
        print(f"\n{case['case_id']} · {case['category']} · user {case['user']} · {'PASS' if case.get('passed') else 'FAIL'}")
        print(f"  input: {case.get('input')}")
        for attempt in case.get("attempts") or []:
            print(f"  run {attempt.get('run')} attempt {attempt.get('attempt')}: tool {attempt.get('actual_tool')} (expected {attempt.get('expected_tool')}) · status {attempt.get('status')} · {attempt.get('latency_ms', {}).get('total')}ms")
            print(f"    args: {json.dumps(attempt.get('arguments') or {}, ensure_ascii=False)}")
            for check in attempt.get("checks") or []:
                mark = {"True": "✓", "False": "✗", "None": "–"}[str(check.get("passed"))]
                print(f"    {mark} {check.get('name')}: {check.get('detail')}")
            if attempt.get("text"):
                print("    response:")
                for text_line in str(attempt["text"]).splitlines():
                    print(f"      {text_line}")
        for failure in case.get("failures") or []:
            print(f"  ✗ {failure.get('check')}: {failure.get('detail')}")
    print()
    return 0


def list_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    selectors, limit = _selector_options(args)
    dataset = load_dataset(args.dataset)
    cases = select_tokens(dataset, selectors, limit=limit) if selectors else list(dataset.cases)
    from poc.harness.cases import CATEGORY_LABELS

    for case in cases:
        label, icon = CATEGORY_LABELS.get(case.category, (case.category, "•"))
        plan = " → ".join(f"{attempt.index}/{attempt.total}:{attempt.expect_outcome}{' +confirm' if attempt.auto_confirm else ''}" for attempt in case.attempts)
        print(f"{case.id}  {icon} {label:<16} {case.user:<18} {case.expected_tool or '—':<18} {plan}")
        print(f"      {case.input}")
    planned = sum(len(case.attempts) for case in cases)
    print(f"\n{len(cases)} cases · {planned} base executions (×repeats: reads×{args.repeat_reads}, writes×{args.repeat_writes})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "doctor":
            return doctor(args)
        if args.command == "explain":
            return explain(args)
        if args.command == "list":
            return list_command(args, parser)
        return run_command(args, parser)
    except KeyboardInterrupt:
        print("\n[harness] interrupted", file=sys.stderr)
        return 130


__all__ = ["build_parser", "main"]
