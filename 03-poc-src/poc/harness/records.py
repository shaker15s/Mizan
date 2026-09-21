"""Result records produced by the harness runner and consumed by every reporter.

One flat, JSON-serializable shape so the console, JSON, Markdown, and HTML
renderers (and the baseline differ) all read the same data — no renderer
recomputes a verdict.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AttemptRecord:
    """One executed attempt of one case (a case may plan several attempts)."""

    case_id: str
    attempt: int = 1
    total_attempts: int = 1
    run: int = 1
    user: str = ""
    category: str = ""
    input: str = ""
    expected_tool: str | None = None
    actual_tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    error_code: str | None = None
    expected_outcome: str = "success"
    audited: bool = False
    erp_reads: int = 0
    erp_creates: int = 0
    latency_ms: dict[str, float] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    passed: bool = True
    text: str = ""
    text_chars: int = 0
    sections: int = 0
    kpis: int = 0
    error: str | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)
    #: Decision-layer trace for this attempt (None when the layer is off).
    #: Flattened, redacted, and never a source of truth for the ERP result.
    decision: dict[str, Any] | None = None

    @property
    def total_ms(self) -> float:
        return float(self.latency_ms.get("total", 0.0))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseRecord:
    """Aggregated verdict for one case across its attempts and repeats."""

    case_id: str
    category: str
    user: str
    input: str
    expected_tool: str | None = None
    expected_outcome: str = "success"
    attempts: list[AttemptRecord] = field(default_factory=list)
    repeats: int = 1
    passed: bool = True
    flaky: bool = False
    passes: int = 0
    runs: int = 0
    tags: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def latency_ms(self) -> float:
        values = [attempt.total_ms for attempt in self.attempts]
        return round(sum(values) / len(values), 1) if values else 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["latency_ms"] = self.latency_ms
        return payload


def summarize_attempts(attempts: list[AttemptRecord]) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """``(passed, tags, failure_rows)`` for a set of attempts."""
    tags: list[str] = []
    failures: list[dict[str, Any]] = []
    passed = True
    for attempt in attempts:
        if attempt.passed:
            continue
        passed = False
        for row in attempt.checks:
            if row.get("passed") is False:
                failures.append({"case_id": attempt.case_id, "attempt": attempt.attempt, "check": row.get("name"), "detail": row.get("detail")})
        for tag in attempt.tags:
            if tag not in tags:
                tags.append(tag)
    return passed, tags, failures


__all__ = ["AttemptRecord", "CaseRecord", "summarize_attempts"]
