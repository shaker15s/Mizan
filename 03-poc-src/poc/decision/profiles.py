"""Synthetic decision-provider profiles for offline evaluation (plan §29, §33).

A **profile** turns the "what a well-calibrated provider should answer" script
into "what a provider of this quality actually answers", deterministically:

* ``oracle``      — always right. Measures the machinery, not the model.
* ``realistic``   — a plausible mid-quality provider (wrong routes, missed
                    injections, missed ambiguities, under-rated risk).
* ``adversarial`` — failure-first: wrong confident routes (including wrong
                    writes), missed security signals. Nothing here may produce an
                    unauthorized or duplicate write.

Determinism matters more than realism here: the noise for a given (profile, case)
pair is derived from a hash of the pair, so a case produces the same synthetic
answer regardless of run order, sharding, or which subset was selected. That is
what makes a calibration sweep reproducible and a threshold comparison honest.

Honesty rule: every report produced with a synthetic provider is labelled
``provider=mock`` and ``synthetic=true``. These numbers never stand in for
measurements of Jev itself.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from poc.decision.mock_client import MockDecisionClient, MockScenario
from poc.decision.questions import ROUTE_CLARIFY, ROUTE_NO_TOOL

SRC_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIOS_PATH = SRC_ROOT / "tests" / "decision_scenarios.json"
DEFAULT_SPLIT_PATH = SRC_ROOT / "tests" / "decision_split.json"

PROFILE_ORACLE = "oracle"
PROFILE_REALISTIC = "realistic"
PROFILE_ADVERSARIAL = "adversarial"

_DEFAULT_NOISE: dict[str, float] = {
    "route_noise": 0.0,
    "confidence_noise": 0.0,
    "mid_confidence_rate": 0.0,
    "injection_miss_rate": 0.0,
    "injection_false_positive_rate": 0.0,
    "ambiguity_miss_rate": 0.0,
    "ambiguity_false_positive_rate": 0.0,
    "risk_noise": 0.0,
    "seed": 0,
}

_LOW_CONFIDENCE_RANGE = (0.44, 0.72)
#: The band the routing gate actually decides in. Without mass here a threshold
#: sweep is degenerate: every candidate either accepts or rejects everything.
_MID_CONFIDENCE_RANGE = (0.70, 0.96)


class ProfileError(ValueError):
    """The scenario document or the requested profile is unusable."""


@dataclass(frozen=True)
class Split:
    """A versioned calibration / validation / held-out assignment."""

    version: str
    dataset_version: str
    assignment: Mapping[str, str]
    counts: Mapping[str, int]
    path: Path | None = None

    def cases_for(self, split: str) -> tuple[str, ...]:
        return tuple(sorted(case_id for case_id, name in self.assignment.items() if name == split))

    def split_of(self, case_id: str) -> str:
        return str(self.assignment.get(case_id, "unassigned"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "dataset_version": self.dataset_version,
            "counts": dict(self.counts),
            "cases": sorted(self.assignment),
        }


@dataclass(frozen=True)
class ScenarioDocument:
    """The generated script + the noise profiles, with provenance."""

    version: str
    dataset_version: str
    cases: Mapping[str, Mapping[str, Any]]
    profiles: Mapping[str, Mapping[str, Any]]
    notes: str = ""
    path: Path | None = None


def load_scenarios(path: Path | str = DEFAULT_SCENARIOS_PATH) -> ScenarioDocument:
    file_path = Path(path)
    if not file_path.exists():
        raise ProfileError(f"scenario document not found: {file_path}")
    try:
        document = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ProfileError(f"scenario document is not valid JSON: {error}") from error
    cases = document.get("cases")
    if not isinstance(cases, Mapping) or not cases:
        raise ProfileError("scenario document has no 'cases' map")
    return ScenarioDocument(
        version=str(document.get("scenario_version") or "unversioned"),
        dataset_version=str(document.get("dataset_version") or "unversioned"),
        cases=cases,
        profiles=document.get("profiles") or {},
        notes=str(document.get("notes") or ""),
        path=file_path,
    )


def load_split(path: Path | str = DEFAULT_SPLIT_PATH) -> Split:
    file_path = Path(path)
    if not file_path.exists():
        raise ProfileError(f"split document not found: {file_path}")
    document = json.loads(file_path.read_text(encoding="utf-8"))
    assignment = document.get("assignment")
    if not isinstance(assignment, Mapping) or not assignment:
        raise ProfileError("split document has no 'assignment' map")
    return Split(
        version=str(document.get("split_version") or "unversioned"),
        dataset_version=str(document.get("dataset_version") or "unversioned"),
        assignment={str(key): str(value) for key, value in assignment.items()},
        counts=document.get("counts") or {},
        path=file_path,
    )


def state_lookup_key(text: str) -> str:
    """The canonical key a scripted scenario is matched on.

    It is the *same transformation* the routing state applies to the utterance
    (strip → mask PII → cap → normalize Arabic → lower), so the harness cannot
    accidentally hand the mock provider a key it could never derive from the
    state it actually receives.
    """
    from poc.decision.redaction import mask_pii
    from poc.normalization import normalize_arabic

    return normalize_arabic(mask_pii(str(text or "").strip())[:4000]).lower()


def profile_parameters(document: ScenarioDocument, profile: str) -> dict[str, float]:
    raw = document.profiles.get(profile)
    if raw is None:
        raise ProfileError(f"unknown profile {profile!r}; available: {sorted(document.profiles)}")
    values = {**_DEFAULT_NOISE, **{key: float(value) for key, value in raw.items() if isinstance(value, (int, float))}}
    return values


def case_ids_in_split(split: Split, name: str) -> tuple[str, ...]:
    return split.cases_for(name)


def build_case_scenarios(
    document: ScenarioDocument,
    *,
    profile: str = PROFILE_ORACLE,
    case_ids: Iterable[str] | None = None,
    routes: Sequence[str] | None = None,
    keys: Mapping[str, str] | None = None,
) -> dict[str, MockScenario]:
    """Build per-case mock scenarios for one profile.

    ``case_ids`` restricts the build (the harness only needs the selected cases);
    ``routes`` is the declared routing option set, used to choose a *plausible*
    wrong answer rather than an invalid one.

    ``keys`` maps a case id to the text the client should match on (the golden
    case's own input). The mock provider sees only the *state*, never the case
    id, so the lookup key must come from the state — using the input text keeps
    the provider honest about what information it actually has.
    """
    parameters = profile_parameters(document, profile)
    seed = int(parameters.get("seed", 0))
    routes = tuple(routes or ())
    wanted = set(case_ids) if case_ids is not None else None
    out: dict[str, MockScenario] = {}
    for case_id, entry in document.cases.items():
        if wanted is not None and case_id not in wanted:
            continue
        rng = random.Random(f"{seed}:{profile}:{case_id}")
        correct_route = str(entry.get("route") or ROUTE_CLARIFY)
        confidence = float(entry.get("confidence") or 0.9)
        ambiguity = float(entry.get("ambiguity") or 0.05)
        injection = float(entry.get("injection") or 0.02)
        risk = float(entry.get("risk") or 0.0)
        faults: list[str] = []

        # --- routing noise --------------------------------------------------
        if rng.random() < parameters["route_noise"]:
            alternatives = [name for name in routes if name and name != correct_route]
            if alternatives:
                correct_route = rng.choice(alternatives)
                confidence = max(confidence, 0.90)  # the dangerous case: wrong *and* confident
                faults.append("route_noise")
        elif rng.random() < parameters["mid_confidence_rate"]:
            low, high = _MID_CONFIDENCE_RANGE
            confidence = round(low + (high - low) * rng.random(), 4)
            faults.append("mid_confidence")
        elif rng.random() < parameters["confidence_noise"]:
            low, high = _LOW_CONFIDENCE_RANGE
            confidence = round(low + (high - low) * rng.random(), 4)
            faults.append("low_confidence")

        # --- security signal noise ------------------------------------------
        if injection >= 0.5:
            if rng.random() < parameters["injection_miss_rate"]:
                injection = 0.08
                faults.append("injection_miss")
        else:
            if rng.random() < parameters["injection_false_positive_rate"]:
                injection = 0.93
                faults.append("injection_false_positive")
        if ambiguity >= 0.5:
            if rng.random() < parameters["ambiguity_miss_rate"]:
                ambiguity = 0.20
                faults.append("ambiguity_miss")
        else:
            if rng.random() < parameters["ambiguity_false_positive_rate"]:
                ambiguity = 0.88
                faults.append("ambiguity_false_positive")

        # --- semantic risk noise --------------------------------------------
        if risk > 0 and rng.random() < parameters["risk_noise"]:
            risk = max(0.0, risk - 1.0)
            faults.append("risk_underrated")

        lookup_key = state_lookup_key(str((keys or {}).get(case_id, case_id)))
        out[lookup_key] = MockScenario(
            name=f"{profile}:{case_id}",
            route=correct_route,
            route_confidence=round(confidence, 4),
            ambiguity=round(ambiguity, 4),
            injection=round(injection, 4),
            risk_score=round(risk, 4),
            latency_ms=float(rng.randint(60, 220)),
            extra={"profile": profile, "faults": faults},
        )
    return out


def build_mock_for_split(
    document: ScenarioDocument,
    split: Split,
    *,
    profile: str = PROFILE_ORACLE,
    split_name: str = "calibration",
    routes: Sequence[str] | None = None,
    mode: str = "advisory",
    threshold_version: str = "",
) -> MockDecisionClient:
    """Convenience: a mock client scripted for one split + profile."""
    case_ids = case_ids_in_split(split, split_name)
    scenarios = build_case_scenarios(document, profile=profile, case_ids=case_ids, routes=routes)
    return MockDecisionClient(
        scenarios=scenarios,
        use_rules=False,          # the script decides every answer: no rule drift
        mode=mode,
        threshold_version=threshold_version,
    )


__all__ = [
    "build_case_scenarios",
    "build_mock_for_split",
    "case_ids_in_split",
    "DEFAULT_SCENARIOS_PATH",
    "DEFAULT_SPLIT_PATH",
    "load_scenarios",
    "load_split",
    "PROFILE_ADVERSARIAL",
    "PROFILE_ORACLE",
    "PROFILE_REALISTIC",
    "ProfileError",
    "profile_parameters",
    "ScenarioDocument",
    "Split",
    "state_lookup_key",
]
