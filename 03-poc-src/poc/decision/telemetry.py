"""Decision-layer telemetry (plan §37).

Additive observability for the decision layer. Two rules:

* **Only safe fields.** No API keys, no raw state, no customer data, no prompts.
  Everything exposed here is a count, a duration, a mode name, a model id, or an
  error code from the canonical taxonomy.
* **Never blocks the request path.** Recording is O(1) and allocation-light; a
  telemetry bug must never fail a user turn.

Latency samples are kept in a bounded deque (percentiles do not need the entire
history), and every counter is exposed in a single ``snapshot()``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from poc.decision.models import DecisionMode
from poc.decision.policy import (
    ESCALATION_ORDER,
    escalation_rank,
)

MAX_LATENCY_SAMPLES = 512


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return round(float(ordered[index]), 1)


@dataclass
class DecisionTelemetry:
    """Thread-safe counters and bounded latency samples for one process."""

    enabled: bool = False
    mode: str = DecisionMode.OFF.value
    provider: str = ""
    model: str = ""
    spec_version: str = ""
    threshold_version: str = ""
    started_at: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _latencies: deque[float] = field(default_factory=lambda: deque(maxlen=MAX_LATENCY_SAMPLES), repr=False)
    counters: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)

    # --- recording ---------------------------------------------------------
    def bump(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + int(amount)

    def reason(self, key: str) -> None:
        """Count a coarse explanation string (never free-text user input)."""
        if not key:
            return
        cleaned = " ".join(str(key).split())[:120]
        with self._lock:
            self.reasons[cleaned] = self.reasons.get(cleaned, 0) + 1

    def record_latency(self, milliseconds: float) -> None:
        if milliseconds <= 0:
            return
        with self._lock:
            self._latencies.append(float(milliseconds))

    def record_outcome(self, outcome: Any) -> None:
        """Fold one :class:`~poc.decision.policy.DecisionOutcome` into the counters."""
        if outcome is None:
            return
        mode = str(getattr(outcome, "mode", "") or "")
        self.mode = mode or self.mode
        self.provider = str(getattr(outcome, "provider", "") or self.provider)
        self.model = str(getattr(outcome, "model", "") or self.model)
        self.bump("decisions")
        self.record_latency(float(getattr(outcome, "latency_ms", 0.0) or 0.0))
        if getattr(outcome, "cached", False):
            self.bump("cache_hits")
        error_code = str(getattr(outcome, "error_code", "") or "")
        if error_code:
            self.bump("provider_failures")
            self.bump(f"error:{error_code}")
        if getattr(outcome, "fallback", False):
            self.bump("fallbacks")
            self.reason(str(getattr(outcome, "fallback_reason", "")))
        route = getattr(outcome, "route", None)
        if route is not None and getattr(route, "narrowed", False):
            self.bump("narrowed")
        escalation = getattr(outcome, "escalation", None)
        if escalation is not None:
            level = str(getattr(escalation, "level", "") or "")
            proposed = str(getattr(escalation, "proposed_level", "") or "")
            if escalation_rank(proposed) > escalation_rank(ESCALATION_ORDER[0]):
                self.bump("escalations_proposed")
                self.bump(f"escalation_proposed:{proposed}")
            if escalation_rank(level) >= escalation_rank("watch"):
                self.bump("escalations")
                self.bump(f"escalation:{level}")
            for reason in getattr(escalation, "reasons", ()) or ():
                self.reason(str(reason))
        if getattr(outcome, "disagreement", None) is not None:
            self.bump("disagreements")
            self.reason(str(getattr(outcome.disagreement, "resolution", "")))

    # --- views -------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            samples = list(self._latencies)
            counters = dict(self.counters)
            reasons = dict(self.reasons)
        decisions = counters.get("decisions", 0)
        failures = counters.get("provider_failures", 0)
        return {
            "enabled": bool(self.enabled),
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "spec_version": self.spec_version,
            "threshold_version": self.threshold_version,
            "uptime_s": round(time.time() - self.started_at, 1),
            "counters": counters,
            "rates": {
                "fallback_rate_pct": round(100.0 * counters.get("fallbacks", 0) / decisions, 2) if decisions else None,
                "provider_failure_rate_pct": round(100.0 * failures / decisions, 2) if decisions else None,
                "disagreement_rate_pct": round(100.0 * counters.get("disagreements", 0) / decisions, 2) if decisions else None,
                "narrowing_rate_pct": round(100.0 * counters.get("narrowed", 0) / decisions, 2) if decisions else None,
                "escalation_rate_pct": round(100.0 * counters.get("escalations", 0) / decisions, 2) if decisions else None,
            },
            "latency_ms": {
                "count": len(samples),
                "p50": _percentile(samples, 50),
                "p95": _percentile(samples, 95),
                "max": round(max(samples), 1) if samples else None,
                "avg": round(sum(samples) / len(samples), 1) if samples else None,
            },
            "top_reasons": sorted(reasons.items(), key=lambda row: row[1], reverse=True)[:8],
        }

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.reasons.clear()
            self._latencies.clear()


__all__ = ["DecisionTelemetry", "MAX_LATENCY_SAMPLES"]
