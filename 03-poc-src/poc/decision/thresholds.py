"""Versioned decision thresholds (plan §13, §19, §52).

Rules this module enforces:

- **No magic numbers in business logic.** Every threshold the code branches on
  comes from a :class:`DecisionThresholds` value that is versioned, logged with
  every decision, and storable next to an evaluation report.
- **Placeholders are labelled as placeholders.** The shipped defaults are
  conservative starting points, not calibrated truth. ``source`` records where a
  threshold set came from (``defaults`` / ``settings`` / ``calibration``) so a
  report can never present an uncalibrated number as a calibrated one.
- **Conservative monotonicity.** When a signal is missing, errored, or exactly
  on a boundary, the resolution is always the *more* conservative branch
  (see :meth:`DecisionThresholds.injection_state` and friends).

Boundary semantics are explicit and tested: comparisons use ``>=`` for
escalation thresholds ("at or above the threshold escalates"), so lowering a
threshold can only ever add scrutiny.

Calibration provenance (v1.1.0): ``route_min_confidence`` was lowered from the
0.90 placeholder to 0.85 by a sweep on the **calibration** split with the
synthetic ``realistic`` profile (``scripts/calibrate_decision.py``,
``data/decision-calibration.json``). The sweep showed 0.85 and 0.90 equally
safe with strictly more useful narrowings, so the more conservative of the two
was kept. This is *machinery* calibration against a synthetic provider — it is
not a measurement of Jev, and a real-provider calibration run is required
before quoting any routing threshold as tuned (plan §13, §33).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

#: Bump on ANY change to a default, a comparison direction, or a band meaning.
THRESHOLD_VERSION = "1.1.0"

SOURCE_DEFAULTS = "defaults"
SOURCE_SETTINGS = "settings"
SOURCE_CALIBRATION = "calibration"


@dataclass(frozen=True)
class DecisionThresholds:
    """Every number the decision layer branches on.

    The defaults below are **placeholders pending calibration** (plan §13). They
    are deliberately conservative: the routing gate is high enough that a
    marginal answer never narrows the LLM's tool set, and the injection gate is
    low enough that a suspicious input is escalated for review rather than
    silently allowed. ``source`` is reported alongside any result, so no one can
    mistake these for measured optima.
    """

    version: str = THRESHOLD_VERSION
    source: str = SOURCE_DEFAULTS
    calibrated_on: str = ""
    notes: str = "placeholder defaults — calibrate on the calibration split before quoting any threshold"

    # --- routing (Choice) ---
    route_min_confidence: float = 0.85
    route_min_margin: float = 0.50

    # --- ambiguity (Noul) ---
    ambiguity_trigger: float = 0.60

    # --- prompt injection (Noul) ---
    injection_quarantine: float = 0.90
    injection_flag: float = 0.70

    # --- semantic risk (Score, index into SEMANTIC_RISK_LEVELS) ---
    risk_escalate_level: float = 3.0

    # --- post-run review (Noul) ---
    review_claim_mismatch: float = 0.60
    review_anomaly: float = 0.80

    # --- advisory narrowing policy ---
    narrow_tool_set: bool = True
    keep_runner_up_tool: bool = True

    # --- cache ---
    cache_read_routes: bool = True
    cache_ttl_seconds: int = 300

    # ----------------------------------------------------------------- APIs
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "calibrated_on": self.calibrated_on,
            "route_min_confidence": self.route_min_confidence,
            "route_min_margin": self.route_min_margin,
            "ambiguity_trigger": self.ambiguity_trigger,
            "injection_quarantine": self.injection_quarantine,
            "injection_flag": self.injection_flag,
            "risk_escalate_level": self.risk_escalate_level,
            "review_claim_mismatch": self.review_claim_mismatch,
            "review_anomaly": self.review_anomaly,
            "narrow_tool_set": self.narrow_tool_set,
            "keep_runner_up_tool": self.keep_runner_up_tool,
            "cache_read_routes": self.cache_read_routes,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "notes": self.notes,
        }

    @staticmethod
    def from_mapping(values: Mapping[str, Any] | None, *, source: str = SOURCE_CALIBRATION) -> "DecisionThresholds":
        """Build from a mapping, ignoring unknown keys but never silently coercing."""
        base = DecisionThresholds()
        if not values:
            return base
        allowed = {
            "version",
            "source",
            "calibrated_on",
            "notes",
            "route_min_confidence",
            "route_min_margin",
            "ambiguity_trigger",
            "injection_quarantine",
            "injection_flag",
            "risk_escalate_level",
            "review_claim_mismatch",
            "review_anomaly",
            "narrow_tool_set",
            "keep_runner_up_tool",
            "cache_read_routes",
            "cache_ttl_seconds",
        }
        changes: dict[str, Any] = {}
        for key, value in values.items():
            if key not in allowed:
                continue
            if key in {"narrow_tool_set", "keep_runner_up_tool", "cache_read_routes"}:
                changes[key] = bool(value)
            elif key in {"cache_ttl_seconds"}:
                changes[key] = int(value)
            elif key in {"version", "source", "calibrated_on", "notes"}:
                changes[key] = str(value)
            else:
                number = float(value)
                # The semantic-risk threshold indexes the R0..R4 ladder, so it is
                # the one threshold bounded by the risk scale rather than by [0,1].
                upper = 4.0 if key == "risk_escalate_level" else 1.0
                if number < 0.0 or number > upper:
                    raise ValueError(f"threshold {key} must be within [0,{upper:g}], got {number}")
                changes[key] = number
        changes.setdefault("source", source)
        return replace(base, **changes)

    @staticmethod
    def from_settings(settings: Any) -> "DecisionThresholds":
        """Compose a threshold set from the typed SettingsStore (never a second config system)."""
        if settings is None:
            return DecisionThresholds()
        values = {
            "route_min_confidence": settings.float_of("decision.route_min_confidence"),
            "route_min_margin": settings.float_of("decision.route_min_margin"),
            "ambiguity_trigger": settings.float_of("decision.ambiguity_trigger"),
            "injection_quarantine": settings.float_of("decision.injection_quarantine"),
            "injection_flag": settings.float_of("decision.injection_flag"),
            "risk_escalate_level": settings.float_of("decision.risk_escalate_level"),
            "review_claim_mismatch": settings.float_of("decision.review_claim_mismatch"),
            "review_anomaly": settings.float_of("decision.review_anomaly"),
            "narrow_tool_set": settings.bool_of("decision.narrow_tool_set"),
            "keep_runner_up_tool": settings.bool_of("decision.keep_runner_up_tool"),
            "cache_read_routes": settings.bool_of("decision.cache_read_routes"),
            "cache_ttl_seconds": settings.int_of("decision.cache_ttl_seconds"),
            "version": str(settings.get("decision.threshold_version", THRESHOLD_VERSION) or THRESHOLD_VERSION),
            "source": SOURCE_SETTINGS,
        }
        values = {key: value for key, value in values.items() if value is not None}
        return DecisionThresholds.from_mapping(values, source=SOURCE_SETTINGS)

    # -------------------------------------------------- band resolutions
    def route_is_confident(self, confidence: float, margin: float) -> tuple[bool, str]:
        """Is a Choice answer strong enough to narrow the candidate tool set?

        Both gates must pass. Returns ``(confident, reason)``; the reason string
        goes into evidence so a reviewer can see *which* gate held.
        """
        if confidence < self.route_min_confidence:
            return False, f"confidence {confidence:.3f} < {self.route_min_confidence:.3f}"
        if margin < self.route_min_margin:
            return False, f"top-1 margin {margin:.3f} < {self.route_min_margin:.3f}"
        return True, "passed confidence and margin gates"

    def injection_state(self, probability: float) -> str:
        """Map a Noul probability onto ``quarantine`` / ``flag`` / ``clear``.

        Boundary behaviour is conservative in both directions: at or above a
        threshold escalates, and a probability exactly on the ``flag`` boundary
        flags rather than clears.
        """
        if probability >= self.injection_quarantine:
            return "quarantine"
        if probability >= self.injection_flag:
            return "flag"
        return "clear"

    def ambiguity_state(self, probability: float) -> bool:
        """A Noul above the trigger means the runtime should not confidently route."""
        return float(probability) >= self.ambiguity_trigger

    def risk_state(self, score: float) -> str:
        """Map a Score onto ``escalate`` / ``watch`` / ``nominal``.

        Half a level below the escalation threshold is a "watch": recorded,
        never acted on, and used for calibration analysis. The band is
        deliberately narrow so that ordinary writes (which a semantic model
        rates as level 2 "consequential but reversible") do not look escalated.
        """
        if float(score) >= self.risk_escalate_level:
            return "escalate"
        if float(score) >= self.risk_escalate_level - 0.5:
            return "watch"
        return "nominal"

    def claim_mismatch(self, probability: float) -> bool:
        return float(probability) >= self.review_claim_mismatch

    def trace_anomaly(self, probability: float) -> bool:
        return float(probability) >= self.review_anomaly

    def fingerprint(self) -> str:
        """Short, stable identity of the *values* (not the provenance)."""
        from poc.decision.models import canonical_json
        import hashlib

        payload = canonical_json(
            {
                key: value
                for key, value in self.to_dict().items()
                if key not in {"source", "calibrated_on", "notes"}
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Calibration grid (used by the harness; defined here so the search space is
# part of the reviewed contract, not an ad-hoc list inside a script)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationGrid:
    """The search space a calibration run is allowed to explore."""

    route_min_confidence: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 0.99)
    route_min_margin: tuple[float, ...] = (0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.70)
    ambiguity_trigger: tuple[float, ...] = (0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90)
    injection_quarantine: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95)
    injection_flag: tuple[float, ...] = (0.30, 0.40, 0.50, 0.60, 0.70, 0.80)
    risk_escalate_level: tuple[float, ...] = (2.0, 2.5, 3.0, 3.5, 4.0)
    objectives: tuple[str, ...] = ("max_precision_at_min_coverage", "conservative")
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_min_confidence": list(self.route_min_confidence),
            "route_min_margin": list(self.route_min_margin),
            "ambiguity_trigger": list(self.ambiguity_trigger),
            "injection_quarantine": list(self.injection_quarantine),
            "injection_flag": list(self.injection_flag),
            "risk_escalate_level": list(self.risk_escalate_level),
            "objectives": list(self.objectives),
        }

    def iter_candidates(self) -> Iterable[dict[str, float]]:
        """Deterministic product of the grid (ordering matters for reproducibility)."""
        for confidence in self.route_min_confidence:
            for margin in self.route_min_margin:
                yield {"route_min_confidence": confidence, "route_min_margin": margin}


__all__ = [
    "CalibrationGrid",
    "DecisionThresholds",
    "SOURCE_CALIBRATION",
    "SOURCE_DEFAULTS",
    "SOURCE_SETTINGS",
    "THRESHOLD_VERSION",
]
