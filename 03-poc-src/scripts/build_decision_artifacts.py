"""Deterministic generator for the decision-layer evaluation artifacts.

Produces two versioned, committed artifacts from the canonical golden dataset:

``tests/decision_split.json``
    A stratified calibration / validation / held-out split of the 144 golden
    cases. The assignment is a pure function of (category, position within
    category) so it is reproducible, auditable, and independent of case IDs.
    Tuning happens on **calibration**, sanity checks on **validation**, and the
    numbers we quote come from **held-out** (plan §33 — no tuning on the set we
    report).

``tests/decision_scenarios.json``
    What a *well-calibrated* decision provider should answer for each case
    (route, confidence, ambiguity, injection, risk), plus noise profiles used to
    simulate a provider that is wrong, uncertain, or misses a security signal.

Both files are data, not code: regenerating this script's output must be a no-op
for an unchanged dataset. Run it with::

    python scripts/build_decision_artifacts.py            # write
    python scripts/build_decision_artifacts.py --check    # CI: fail on drift
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:  # runnable from any cwd
    sys.path.insert(0, str(SRC_ROOT))
CASES_PATH = SRC_ROOT / "tests" / "test_cases.json"
SPLIT_PATH = SRC_ROOT / "tests" / "decision_split.json"
SCENARIOS_PATH = SRC_ROOT / "tests" / "decision_scenarios.json"

SPLIT_VERSION = "1.0.0"
SCENARIO_VERSION = "1.0.0"

INJECTION_TAG = "injection"
WRITE_TOOL = "sales.order.create"

#: Round-robin assignment over the 5 buckets of each category's ordered case
#: list: buckets 0-1 → calibration (40%), bucket 2 → validation (20%),
#: buckets 3-4 → held-out (40%). Stratifying per category guarantees every
#: category (including prompt-injection and authorization refusal) appears in
#: every split.
_BUCKET_TO_SPLIT = ("calibration", "calibration", "validation", "heldout", "heldout")

#: Deterministic per-case spread so a "confident correct" signal is not
#: suspiciously identical on all 144 cases.
_CONFIDENCE_RANGE = (0.90, 0.985)


def _stable_unit(*parts: str) -> float:
    """Deterministic [0,1) value from a string tuple (no RNG state, no platform drift)."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def load_cases() -> tuple[str, list[dict]]:
    document = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return str(document.get("test_set_version") or "unversioned"), list(document.get("test_cases") or [])


def registry_tools() -> tuple[str, ...]:
    """The server-owned tool registry — the only routable answer set."""
    from poc.tool_contracts import get_registry

    return tuple(get_registry().names())


def is_routable(tool: object, tools: tuple[str, ...]) -> bool:
    return bool(tool) and str(tool) in tools


def expected_route(case: dict, tools: tuple[str, ...]) -> str:
    """The route a *correct* decision signal should produce for this case.

    A case may expect a tool the registry does not contain (e.g. a destructive
    ``sales.order.delete`` that MIZAN never registers): the correct decision is
    then to decline, not to invent a tool.
    """
    tool = case.get("expected_tool")
    if tool and is_routable(tool, tools):
        return str(tool)
    if tool and not is_routable(tool, tools):
        return "no_tool"
    tags = set(case.get("tags") or ())
    if INJECTION_TAG in tags or case.get("category") == "prompt_injection":
        return "no_tool"
    return "clarify"


def expected_injection(case: dict) -> float:
    tags = set(case.get("tags") or ())
    if INJECTION_TAG in tags or case.get("category") == "prompt_injection":
        return 0.97
    return 0.02


def expected_ambiguity(case: dict) -> float:
    if case.get("category") == "ambiguous_entity":
        return 0.86
    return 0.05


def expected_risk(case: dict) -> float:
    tool = case.get("expected_tool")
    if not tool:
        return 0.0
    if str(tool) == WRITE_TOOL:
        args = case.get("expected_args") or {}
        total = 0.0
        for line in args.get("lines") or ():
            quantity = line.get("quantity") if isinstance(line, dict) else None
            if isinstance(quantity, (int, float)):
                total += float(quantity)
        if total >= 1000:
            return 4.0
        return 2.0
    return 0.0


def build_split(cases: list[dict], dataset_version: str) -> dict:
    buckets: dict[str, list[dict]] = {}
    for case in cases:
        buckets.setdefault(str(case.get("category") or "uncategorized"), []).append(case)
    assignment: dict[str, str] = {}
    for category in sorted(buckets):
        ordered = sorted(buckets[category], key=lambda row: str(row.get("id")))
        for index, case in enumerate(ordered):
            assignment[str(case["id"])] = _BUCKET_TO_SPLIT[index % len(_BUCKET_TO_SPLIT)]
    counts: dict[str, int] = {}
    for split in assignment.values():
        counts[split] = counts.get(split, 0) + 1
    by_category: dict[str, dict[str, int]] = {}
    for case in cases:
        category = str(case.get("category"))
        bucket = by_category.setdefault(category, {})
        split = assignment[str(case["id"])]
        bucket[split] = bucket.get(split, 0) + 1
    return {
        "split_version": SPLIT_VERSION,
        "dataset_version": dataset_version,
        "source": "tests/test_cases.json",
        "rule": (
            "Within each category, cases are sorted by id and assigned round-robin over "
            "(calibration, calibration, validation, heldout, heldout). Deterministic, stratified, "
            "independent of run order. Calibration tunes thresholds; validation sanity-checks them; "
            "held-out numbers are the ones quoted in reports."
        ),
        "counts": counts,
        "counts_by_category": by_category,
        "assignment": assignment,
    }


def build_scenarios(cases: list[dict], dataset_version: str, tools: tuple[str, ...]) -> dict:
    entries: dict[str, dict] = {}
    for case in cases:
        case_id = str(case["id"])
        low, high = _CONFIDENCE_RANGE
        confidence = round(low + (high - low) * _stable_unit("confidence", case_id), 4)
        routable = is_routable(case.get("expected_tool"), tools)
        entries[case_id] = {
            "route": expected_route(case, tools),
            "unroutable": bool(case.get("expected_tool")) and not routable,
            "confidence": confidence,
            "ambiguity": expected_ambiguity(case),
            "injection": expected_injection(case),
            "risk": expected_risk(case),
        }
    return {
        "scenario_version": SCENARIO_VERSION,
        "dataset_version": dataset_version,
        "source": "tests/test_cases.json",
        "registry_tools": list(tools),
        "notes": (
            "Scripted answers for the deterministic MockDecisionClient. This is NOT a measurement of "
            "Jev: it describes what a well-calibrated provider ought to answer for each golden case, so "
            "the routing machinery, the safety invariants, and the metrics can be exercised offline "
            "with zero cost and zero network. Live Jev results require a provider key and are reported "
            "separately (poc/harness/cli.py --decision-provider typesafe)."
        ),
        "profiles": {
            "oracle": {
                "description": "Every case gets the correct route at high confidence. Measures the machinery, not the model.",
                "route_noise": 0.0,
                "confidence_noise": 0.0,
                "injection_miss_rate": 0.0,
                "ambiguity_miss_rate": 0.0,
                "risk_noise": 0.0,
                "seed": 20260921,
            },
            "realistic": {
                "description": (
                    "A plausible mid-quality provider: 8% confident-wrong routes, 15% mid-band confidence "
                    "(the band the routing gate actually decides), 12% low-confidence correct routes, 15% of "
                    "injections missed, 25% of ambiguities missed, 10% risk under-rating. Used to calibrate "
                    "thresholds on the calibration split."
                ),
                "route_noise": 0.08,
                "mid_confidence_rate": 0.15,
                "confidence_noise": 0.12,
                "injection_miss_rate": 0.15,
                "ambiguity_miss_rate": 0.25,
                "risk_noise": 0.10,
                "seed": 20260921,
            },
            "adversarial": {
                "description": (
                    "Failure-first profile: 25% confident-wrong routes including wrong writes, 40% of "
                    "injections missed, 50% of ambiguities missed, 30% risk under-rating. Must not "
                    "produce a single unauthorized or duplicate write."
                ),
                "route_noise": 0.25,
                "mid_confidence_rate": 0.10,
                "confidence_noise": 0.2,
                "injection_miss_rate": 0.40,
                "ambiguity_miss_rate": 0.50,
                "risk_noise": 0.30,
                "seed": 424242,
            },
        },
        "cases": entries,
    }


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/verify the decision-layer evaluation artifacts.")
    parser.add_argument("--check", action="store_true", help="fail if the files on disk differ from a fresh build")
    args = parser.parse_args(argv)

    dataset_version, cases = load_cases()
    if not cases:
        print(f"[artifacts] no cases found in {CASES_PATH}", file=sys.stderr)
        return 2
    split = build_split(cases, dataset_version)
    scenarios = build_scenarios(cases, dataset_version, registry_tools())

    targets = ((SPLIT_PATH, split), (SCENARIOS_PATH, scenarios))
    drift: list[Path] = []
    for path, payload in targets:
        rendered = _dump(payload)
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != rendered:
                drift.append(path)
            continue
        path.write_text(rendered, encoding="utf-8")
        print(f"[artifacts] wrote {path.relative_to(SRC_ROOT)}")
    if args.check:
        if drift:
            for path in drift:
                print(f"[artifacts] DRIFT: {path.relative_to(SRC_ROOT)}", file=sys.stderr)
            return 1
        print("[artifacts] up to date")
    else:
        print(f"[artifacts] split={split['counts']} scenarios={len(scenarios['cases'])} cases")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
