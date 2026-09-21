"""Typed decision models — provider-neutral, strictly validated.

Design rules (mirrors the rest of MIZAN's error-handling posture):

- **The provider shape never leaks.** HTTP/JSON shapes live in
  :mod:`poc.decision.jev_client`; everything downstream consumes only these
  types, so swapping Jev for another decision model does not touch the runtime,
  the gateway, the harness, or the UI.
- **Strict parsing, explicit failures.** A malformed answer is a typed error,
  never a silent default. An unknown selected option is *rejected*, not coerced.
- **Noul is not a Choice.** ``Noul`` carries a single probability and has no
  ``confidence``. Applying Choice-style confidence thresholds to a Noul is a
  category error, so the types make it impossible (plan §5, §27).
- **No provider-specific fields** are kept except an opaque, redacted
  ``raw_reference`` mapping used for debugging and completely excluded from
  evidence payloads.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

DECISION_LAYER_VERSION = "1.0.0"

# Sum tolerance for provider probability distributions (0..1 floats rounded by
# the provider). Deliberately tight: a distribution that does not sum to ~1 is
# not a calibrated distribution and must not be trusted.
PROBABILITY_SUM_TOLERANCE = 0.02


class QuestionType(str, Enum):
    """The three System One primitives. No fourth type is assumed to exist."""

    CHOICE = "choice"
    SCORE = "score"
    NOUL = "noul"


class DecisionProviderKind(str, Enum):
    """Which adapter answers the questions.

    ``mock`` and ``typesafe``/``custom`` share one protocol, so the runtime
    cannot tell them apart — that is the point (plan §7, §9).
    """

    OFF = "off"
    MOCK = "mock"
    TYPESAFE = "typesafe"
    CUSTOM = "custom"


class DecisionMode(str, Enum):
    """How much influence a decision signal is allowed to have.

    ``OFF``      — no calls, MIZAN behaves exactly like the un-integrated baseline.
    ``SHADOW``   — calls happen, results are recorded, behaviour is unchanged.
    ``ADVISORY`` — signals may narrow candidates / escalate / ask for clarification.
    ``ENFORCING``— reserved; requires the release gates in plan §57 and is only
                   reachable through explicit configuration.
    """

    OFF = "off"
    SHADOW = "shadow"
    ADVISORY = "advisory"
    ENFORCING = "enforcing"


class DecisionErrorCode(str, Enum):
    """Canonical decision-layer failure taxonomy.

    Stable strings: telemetry, evidence, harness reports and tests depend on
    them. ``retryable`` is a property of the *decision call only* — retrying a
    decision call must never retry an ERP execution (plan §25, §26).
    """

    DISABLED = "decision_disabled"
    TIMEOUT = "decision_timeout"
    CONNECTION = "decision_connection_error"
    RATE_LIMITED = "decision_rate_limited"
    OVERLOADED = "decision_overloaded"
    AUTH = "decision_auth_error"
    INVALID_REQUEST = "decision_invalid_request"
    PROVIDER_ERROR = "decision_provider_error"
    MALFORMED_RESPONSE = "decision_malformed_response"
    INVALID_ANSWER = "decision_invalid_answer"
    OVERSIZED_STATE = "decision_oversized_state"
    REDACTION_REJECTED = "decision_redaction_rejected"
    CIRCUIT_OPEN = "decision_circuit_open"
    UNSUPPORTED = "decision_unsupported"
    INTERNAL = "decision_internal_error"


_RETRYABLE = frozenset(
    {
        DecisionErrorCode.TIMEOUT,
        DecisionErrorCode.CONNECTION,
        DecisionErrorCode.RATE_LIMITED,
        DecisionErrorCode.OVERLOADED,
        DecisionErrorCode.PROVIDER_ERROR,
    }
)

_USER_ACTION = frozenset(
    {
        DecisionErrorCode.AUTH,
        DecisionErrorCode.INVALID_REQUEST,
        DecisionErrorCode.OVERSIZED_STATE,
        DecisionErrorCode.REDACTION_REJECTED,
        DecisionErrorCode.UNSUPPORTED,
    }
)


class DecisionError(RuntimeError):
    """A typed, mapped failure of a decision call.

    Never raised into the gateway path: the router converts it into a
    :class:`DecisionResult` with ``error`` set, so a Jev outage degrades to the
    baseline path instead of becoming a MIZAN outage (plan §43).
    """

    def __init__(
        self,
        code: DecisionErrorCode | str,
        message: str,
        *,
        attempts: int = 1,
        status: int | None = None,
        provider: str = "",
        retry_after_s: float | None = None,
    ) -> None:
        self.code = DecisionErrorCode(code) if not isinstance(code, DecisionErrorCode) else code
        super().__init__(message)
        self.message = message
        self.attempts = int(attempts)
        self.status = status
        self.provider = provider
        self.retry_after_s = retry_after_s

    @property
    def retryable(self) -> bool:
        """Transient *decision-call* failure — safe to retry the decision only."""
        return self.code in _RETRYABLE

    @property
    def requires_user_action(self) -> bool:
        """A human (config/credential/payload owner) must fix something."""
        return self.code in _USER_ACTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "requires_user_action": self.requires_user_action,
            "attempts": self.attempts,
            "status": self.status,
            "provider": self.provider,
        }


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChoiceAnswer:
    """One option out of a declared set, plus the full distribution."""

    choice: str
    probabilities: Mapping[str, float]
    confidence: float
    unexpected_fields: tuple[str, ...] = ()

    @property
    def margin(self) -> float:
        """Top-1 minus top-2 probability — a second uncertainty axis.

        ``confidence`` collapses the whole distribution shape; the margin is
        what actually matters for "is there a runner-up close behind?".
        A deliberate design choice from plan §13.
        """
        if len(self.probabilities) < 2:
            return round(float(self.probabilities.get(self.choice, 1.0)), 6)
        ordered = sorted(self.probabilities.values(), reverse=True)
        return round(float(ordered[0] - ordered[1]), 6)

    @property
    def top_two(self) -> tuple[str, ...]:
        ordered = sorted(self.probabilities.items(), key=lambda row: row[1], reverse=True)
        return tuple(name for name, _ in ordered[:2])

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": QuestionType.CHOICE.value,
            "choice": self.choice,
            "probabilities": {key: round(float(value), 6) for key, value in self.probabilities.items()},
            "confidence": round(float(self.confidence), 6),
            "margin": self.margin,
        }


@dataclass(frozen=True)
class ScoreAnswer:
    """A position along an ordered rubric; may fall between two levels."""

    score: float
    legend: Mapping[str, str]
    probabilities: Mapping[str, float]
    confidence: float
    unexpected_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": QuestionType.SCORE.value,
            "score": round(float(self.score), 6),
            "legend": dict(self.legend),
            "probabilities": {key: round(float(value), 6) for key, value in self.probabilities.items()},
            "confidence": round(float(self.confidence), 6),
        }


@dataclass(frozen=True)
class NoulAnswer:
    """P(yes) in [0, 1].

    **No ``confidence`` field exists.** Near 0.5 means "the model splits evenly"
    — it is an uncertainty signal, not a medium-strength "yes". Thresholds for a
    Noul are escalation thresholds, never Choice-confidence thresholds.
    """

    noul: float
    unexpected_fields: tuple[str, ...] = ()

    @property
    def undecided(self) -> bool:
        """True when the model is effectively a coin flip."""
        return abs(self.noul - 0.5) < 0.05

    def to_dict(self) -> dict[str, Any]:
        return {"type": QuestionType.NOUL.value, "noul": round(float(self.noul), 6)}


DecisionAnswer = ChoiceAnswer | ScoreAnswer | NoulAnswer


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": int(self.input_tokens), "output_tokens": int(self.output_tokens)}

    @property
    def total_tokens(self) -> int:
        return int(self.input_tokens) + int(self.output_tokens)


@dataclass(frozen=True)
class DecisionResult:
    """The normalized outcome of one decision call (success *or* failure).

    A failure is a first-class result: it carries provider/model/latency so the
    evidence graph can still explain *why* the system fell back.
    """

    provider: str
    model: str
    spec_version: str
    state_hash: str
    answers: Mapping[str, DecisionAnswer] = field(default_factory=dict)
    latency_ms: float = 0.0
    usage: DecisionUsage = field(default_factory=DecisionUsage)
    request_id: str | None = None
    error: DecisionError | None = None
    cached: bool = False
    unknown_answers: tuple[str, ...] = ()
    threshold_version: str = ""
    mode: str = DecisionMode.OFF.value

    # --- accessors (typed: a Choice question can never be read as a Noul) ----
    def choice(self, question_id: str) -> ChoiceAnswer | None:
        answer = self.answers.get(question_id)
        return answer if isinstance(answer, ChoiceAnswer) else None

    def score(self, question_id: str) -> ScoreAnswer | None:
        answer = self.answers.get(question_id)
        return answer if isinstance(answer, ScoreAnswer) else None

    def noul(self, question_id: str) -> NoulAnswer | None:
        answer = self.answers.get(question_id)
        return answer if isinstance(answer, NoulAnswer) else None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self, *, include_answers: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "spec_version": self.spec_version,
            "state_hash": self.state_hash,
            "threshold_version": self.threshold_version,
            "mode": self.mode,
            "latency_ms": round(float(self.latency_ms), 3),
            "usage": self.usage.to_dict(),
            "request_id": self.request_id,
            "cached": self.cached,
            "ok": self.ok,
            "error": self.error.to_dict() if self.error is not None else None,
        }
        if include_answers:
            payload["answers"] = {key: value.to_dict() for key, value in self.answers.items()}
        return payload


# ---------------------------------------------------------------------------
# State hashing (plan §19, §20)
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Deterministic JSON used for hashing. No float formatting surprises."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_state_hash(state: Any, *, spec_version: str, model: str = "") -> str:
    """Stable hash over the *canonicalized decision input*.

    Includes the question-spec version and the model id: a confidence number
    without knowing the question wording and the model that answered it is not
    a reproducible artifact (plan §19).
    """
    payload = canonical_json(
        {
            "spec_version": spec_version,
            "model": model,
            "state": state,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Parsing helpers shared by every provider adapter
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _validate_distribution(probabilities: Any, *, expected: set[str], where: str) -> dict[str, float]:
    if not isinstance(probabilities, Mapping) or not probabilities:
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: probabilities missing")
    cleaned: dict[str, float] = {}
    for key, value in probabilities.items():
        if not isinstance(key, str):
            raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: non-string option key")
        if not _is_number(value):
            raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: probability for {key!r} is not a number")
        number = float(value)
        if number < 0.0 or number > 1.0:
            raise DecisionError(DecisionErrorCode.INVALID_ANSWER, f"{where}: probability for {key!r} outside [0,1]")
        cleaned[key] = number
    if expected and set(cleaned) != expected:
        missing = sorted(expected - set(cleaned))
        extra = sorted(set(cleaned) - expected)
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unexpected {extra}")
        raise DecisionError(DecisionErrorCode.INVALID_ANSWER, f"{where}: option set mismatch ({', '.join(detail)})")
    total = sum(cleaned.values())
    if abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
        raise DecisionError(DecisionErrorCode.INVALID_ANSWER, f"{where}: probabilities sum to {total:.4f}, expected ~1")
    return cleaned


def parse_choice_answer(raw: Any, *, declared_options: tuple[str, ...], where: str) -> ChoiceAnswer:
    if not isinstance(raw, Mapping):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: answer is not an object")
    choice = raw.get("choice")
    if not isinstance(choice, str) or not choice:
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: 'choice' missing or not a string")
    probabilities = _validate_distribution(raw.get("probabilities"), expected=set(declared_options), where=where)
    if choice not in probabilities:
        raise DecisionError(
            DecisionErrorCode.INVALID_ANSWER,
            f"{where}: selected option {choice!r} is not one of the declared options",
        )
    confidence = raw.get("confidence")
    if not _is_number(confidence):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: 'confidence' missing on a Choice answer")
    confidence_value = float(confidence)
    if confidence_value < 0.0 or confidence_value > 1.0:
        raise DecisionError(DecisionErrorCode.INVALID_ANSWER, f"{where}: confidence outside [0,1]")
    unexpected = tuple(sorted(set(raw) - {"type", "choice", "probabilities", "confidence"}))
    return ChoiceAnswer(
        choice=choice,
        probabilities=probabilities,
        confidence=confidence_value,
        unexpected_fields=unexpected,
    )


def parse_score_answer(raw: Any, *, level_indices: tuple[str, ...], where: str) -> ScoreAnswer:
    if not isinstance(raw, Mapping):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: answer is not an object")
    score = raw.get("score")
    if not _is_number(score):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: 'score' missing or not a number")
    probabilities = _validate_distribution(raw.get("probabilities"), expected=set(level_indices), where=where)
    confidence = raw.get("confidence")
    if not _is_number(confidence):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: 'confidence' missing on a Score answer")
    legend_raw = raw.get("legend")
    if legend_raw is not None and not isinstance(legend_raw, Mapping):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: 'legend' is not an object")
    legend = {str(key): str(value) for key, value in (legend_raw or {}).items()}
    score_value = float(score)
    # A Score may land between two levels; it may not leave the declared range.
    if score_value < -0.001 or score_value > len(level_indices) - 1 + 0.001:
        raise DecisionError(DecisionErrorCode.INVALID_ANSWER, f"{where}: score {score_value} outside the declared levels")
    unexpected = tuple(sorted(set(raw) - {"type", "score", "legend", "probabilities", "confidence"}))
    return ScoreAnswer(
        score=score_value,
        legend=legend,
        probabilities=probabilities,
        confidence=float(confidence),
        unexpected_fields=unexpected,
    )


def parse_noul_answer(raw: Any, *, where: str) -> NoulAnswer:
    """Parse a Noul answer.

    A provider that returns an extra ``confidence`` field on a Noul is *not*
    rejected — but the field is recorded as unexpected and never used as a
    threshold input, because a Noul has no separate confidence semantics
    (plan §5, §28).
    """
    if not isinstance(raw, Mapping):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: answer is not an object")
    value = raw.get("noul")
    if not _is_number(value):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, f"{where}: 'noul' missing or not a number")
    number = float(value)
    if number < 0.0 or number > 1.0:
        raise DecisionError(DecisionErrorCode.INVALID_ANSWER, f"{where}: noul {number} outside [0,1]")
    unexpected = tuple(sorted(set(raw) - {"type", "noul"}))
    return NoulAnswer(noul=number, unexpected_fields=unexpected)


__all__ = [
    "ChoiceAnswer",
    "DECISION_LAYER_VERSION",
    "DecisionAnswer",
    "DecisionError",
    "DecisionErrorCode",
    "DecisionMode",
    "DecisionProviderKind",
    "DecisionResult",
    "DecisionUsage",
    "NoulAnswer",
    "PROBABILITY_SUM_TOLERANCE",
    "QuestionType",
    "ScoreAnswer",
    "canonical_json",
    "compute_state_hash",
    "parse_choice_answer",
    "parse_noul_answer",
    "parse_score_answer",
]
