"""Conservative decision cache (plan §36).

What this cache is allowed to do:

* Cache **read-intent routing signals** — the classifier's answer for the same
  utterance under the same spec/model/threshold versions — for a short TTL.
* Serve a cached *decision signal*, which is advisory by definition. A cached
  signal can never authorize, execute, or verify anything.

What it is explicitly **not** allowed to do:

* Cache anything that could stand in for an authorization decision. Write-intent
  routing is only cacheable when the caller opts in *and* the classifier only
  produced a routing signal — and even then the cached value is still just a
  hint; the gateway re-validates and writes always require human confirmation.
* Survive a meaningful state change: the key includes a content hash of the
  state, so any change to the utterance or the tool registry produces a miss.
* Survive a version change: spec version, model id and threshold version are
  part of the key (plan §36). A threshold change invalidates every entry.

The cache is bounded, TTL-based, and disabled unless explicitly enabled.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from poc.decision.models import DecisionMode, DecisionResult
from poc.decision.questions import ROUTE_CLARIFY, ROUTE_NO_TOOL, QuestionId

#: Purposes that may be cached. Reads only; lifecycle keys are deliberately absent.
CACHEABLE_PURPOSES = frozenset({"routing_read_intent"})


@dataclass(frozen=True)
class CacheKey:
    """Everything that must match for a cached signal to be reusable."""

    state_hash: str
    spec_version: str
    model: str
    threshold_version: str
    question_ids: tuple[str, ...]
    purpose: str

    def as_tuple(self) -> tuple[str, ...]:
        return (
            self.state_hash,
            self.spec_version,
            self.model,
            self.threshold_version,
            ",".join(self.question_ids),
            self.purpose,
        )


@dataclass
class _Entry:
    result: DecisionResult
    expires_at: float


@dataclass
class DecisionCache:
    """Bounded TTL cache for advisory routing signals."""

    enabled: bool = False
    ttl_seconds: int = 300
    max_entries: int = 512
    _entries: dict[tuple[str, ...], _Entry] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    rejects: int = 0

    # --- policy ------------------------------------------------------------
    def is_cacheable(self, result: DecisionResult, *, purpose: str, read_intent: bool) -> tuple[bool, str]:
        """Decide whether a result may be stored. Default deny."""
        if not self.enabled:
            return False, "cache disabled"
        if purpose not in CACHEABLE_PURPOSES:
            return False, f"purpose {purpose!r} is not cacheable"
        if not read_intent:
            # A write-intent routing signal is *never* cached: the plan forbids
            # caching write authorization decisions in a way that outlives state.
            return False, "write intent is never cached"
        if result is None or not result.ok:
            return False, "only successful results are cached"
        choice = result.choice(QuestionId.TOOL_ROUTE.value)
        if choice is not None and choice.choice not in {ROUTE_CLARIFY, ROUTE_NO_TOOL} and not choice.choice.endswith((".search", ".get")):
            return False, f"route {choice.choice!r} is not a read tool"
        if result.mode in {DecisionMode.ENFORCING.value}:
            return False, "enforcing mode is never cached"
        return True, "cacheable read-intent routing signal"

    # --- storage -----------------------------------------------------------
    def get(self, key: CacheKey) -> DecisionResult | None:
        if not self.enabled:
            return None
        now = time.time()
        with self._lock:
            entry = self._entries.get(key.as_tuple())
            if entry is None:
                self.misses += 1
                return None
            if entry.expires_at <= now:
                self._entries.pop(key.as_tuple(), None)
                self.misses += 1
                return None
            self.hits += 1
            return entry.result

    def put(self, key: CacheKey, result: DecisionResult) -> None:
        if not self.enabled:
            return
        now = time.time()
        with self._lock:
            if len(self._entries) >= self.max_entries:
                # Evict the entry closest to expiry (cheap, deterministic).
                oldest = min(self._entries.items(), key=lambda row: row[1].expires_at)
                self._entries.pop(oldest[0], None)
                self.evictions += 1
            self._entries[key.as_tuple()] = _Entry(result=result, expires_at=now + max(1, int(self.ttl_seconds)))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> Mapping[str, Any]:
        with self._lock:
            size = len(self._entries)
        total = self.hits + self.misses
        return {
            "enabled": self.enabled,
            "ttl_seconds": self.ttl_seconds,
            "entries": size,
            "max_entries": self.max_entries,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "rejects": self.rejects,
            "hit_rate_pct": round(100.0 * self.hits / total, 2) if total else None,
        }


__all__ = ["CACHEABLE_PURPOSES", "CacheKey", "DecisionCache"]
