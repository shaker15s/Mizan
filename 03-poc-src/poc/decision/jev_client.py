"""Provider-neutral HTTP adapter for a System One / Jev decision endpoint.

Why a hand-written client instead of the official SDK (plan §8, ADR-004):

* ``typesafe-sdk`` 0.7.0 pulls in ``httpx2`` **in addition to** MIZAN's existing
  ``httpx``, plus ``pydantic``, ``pydantic-core`` and ``tenacity``. That is four
  new dependencies — including a second HTTP stack — for one POST endpoint.
* The decision layer must stay provider-neutral. MIZAN already talks to a
  TypeSafe-shaped endpoint over ``httpx`` elsewhere in the repo, and a ~300-line
  adapter keeps the wire contract reviewable and testable offline.
* The SDK's *retry semantics* are still honoured: retryable statuses
  (408/429/5xx, including 529 "overloaded"), bounded exponential backoff with
  jitter, full-support for ``Retry-After`` / ``retry-after-ms``, per-call total
  timeout budget, and a request-id header — all mirrored from the documented
  behaviour and the SDK source inspected at research time.

Provider neutrality: official TypeSafe, a documented gateway (OpenRouter
``/api/alpha/decisions``), or a customer's own System One-shaped endpoint are
the *same code path* with a different ``base_url``, ``path`` and ``model``.
Nothing outside this module knows which one is in use.

Safety properties enforced here:

* **No silent activation.** The adapter is only constructed when configuration
  explicitly selects a non-mock provider; a stray API key changes nothing.
* **Never sends secrets.** The redaction layer runs before this module sees the
  state; this module additionally refuses to log anything but a redacted digest.
* **A decision failure is a value, never an exception into the caller.** The
  caller degrades to baseline behaviour instead of failing the request (plan §43).
* **Retrying a decision never retries an ERP action.** Retries are bounded and
  complete before any execution authority is involved (plan §25, §26).
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx

from poc.decision.models import (
    DecisionAnswer,
    DecisionError,
    DecisionErrorCode,
    DecisionMode,
    DecisionProviderKind,
    DecisionResult,
    DecisionUsage,
    QuestionType,
    compute_state_hash,
    parse_choice_answer,
    parse_noul_answer,
    parse_score_answer,
)

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_PATH = "/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_TOTAL_BUDGET_S = 20.0
DEFAULT_RETRIES = 1
BACKOFF_BASE_S = 0.35
BACKOFF_CAP_S = 4.0
JITTER_RATIO = 0.25
REQUEST_ID_HEADER = "x-typesafe-request-id"

#: Statuses worth retrying. 401/403/404/422 are *not* transient — retrying them
#: only hides a configuration or payload bug (mirrors the SDK's default set,
#: which is 408 + 429 + all 5xx, including 529 "overloaded").
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 529})

_STATUS_ERROR_CODES: dict[int, DecisionErrorCode] = {
    400: DecisionErrorCode.INVALID_REQUEST,
    401: DecisionErrorCode.AUTH,
    403: DecisionErrorCode.AUTH,
    404: DecisionErrorCode.INVALID_REQUEST,
    408: DecisionErrorCode.TIMEOUT,
    413: DecisionErrorCode.OVERSIZED_STATE,
    422: DecisionErrorCode.INVALID_REQUEST,
    429: DecisionErrorCode.RATE_LIMITED,
    500: DecisionErrorCode.PROVIDER_ERROR,
    502: DecisionErrorCode.PROVIDER_ERROR,
    503: DecisionErrorCode.PROVIDER_ERROR,
    529: DecisionErrorCode.OVERLOADED,
}


class DecisionCircuitBreaker:
    """Small circuit breaker using the repository's existing conventions.

    Deliberately a local class rather than an import from ``poc.odoo_client``:
    the decision layer must not depend on the ERP client module (plan §10).
    Same vocabulary as the Odoo breaker (CLOSED / OPEN / HALF_OPEN) so operators
    read both the same way.
    """

    def __init__(self, failure_threshold: int = 4, recovery_timeout: float = 30.0) -> None:
        self.failure_threshold = int(failure_threshold)
        self.recovery_timeout = float(recovery_timeout)
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"
        self.short_circuits = 0

    def can_execute(self, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if (moment - self.last_failure_time) >= self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            self.short_circuits += 1
            return False
        return True

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self, *, now: float | None = None) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time() if now is None else now
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "threshold": self.failure_threshold,
            "short_circuits": self.short_circuits,
            "healthy": self.state == "CLOSED",
        }


@dataclass(frozen=True)
class JevConfig:
    """Connection + resilience configuration for one decision provider."""

    provider: str = DecisionProviderKind.TYPESAFE.value
    base_url: str = DEFAULT_BASE_URL
    path: str = DEFAULT_PATH
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout_s: float = DEFAULT_TIMEOUT_S
    total_budget_s: float = DEFAULT_TOTAL_BUDGET_S
    retries: int = DEFAULT_RETRIES
    backoff_base_s: float = BACKOFF_BASE_S
    backoff_cap_s: float = BACKOFF_CAP_S
    jitter_ratio: float = JITTER_RATIO
    respect_retry_after: bool = True
    breaker_failure_threshold: int = 4
    breaker_recovery_seconds: float = 30.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    #: Marks a third-party / unofficial endpoint so every record can say so.
    unofficial: bool = False
    synthetic_only: bool = True

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.path}"

    def display(self) -> dict[str, Any]:
        """Redacted description — safe for telemetry, logs and the UI."""
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint_host": httpx.URL(self.endpoint).host if self.base_url else "",
            "path": self.path,
            "timeout_s": self.timeout_s,
            "retries": self.retries,
            "unofficial": self.unofficial,
            "synthetic_only": self.synthetic_only,
            "key_configured": bool(self.api_key),
        }


# ---------------------------------------------------------------------------
# Wire helpers (pure, so they can be unit-tested without a network)
# ---------------------------------------------------------------------------


def validate_questions(questions: Mapping[str, Mapping[str, Any]]) -> None:
    """Fail loudly on a malformed question set *before* spending a request."""
    if not questions:
        raise DecisionError(DecisionErrorCode.INVALID_REQUEST, "at least one question is required")
    for question_id, question in questions.items():
        if not isinstance(question, Mapping):
            raise DecisionError(DecisionErrorCode.INVALID_REQUEST, f"question {question_id!r} is not an object")
        kind = str(question.get("type") or "")
        if kind not in {member.value for member in QuestionType}:
            raise DecisionError(DecisionErrorCode.INVALID_REQUEST, f"question {question_id!r} has unknown type {kind!r}")
        if not question.get("instructions"):
            raise DecisionError(DecisionErrorCode.INVALID_REQUEST, f"question {question_id!r} has no instructions")
        criteria = question.get("criteria")
        if kind == QuestionType.CHOICE.value:
            if not isinstance(criteria, Mapping) or not criteria:
                raise DecisionError(DecisionErrorCode.INVALID_REQUEST, f"choice {question_id!r} needs an options map")
            if len(criteria) > 255:
                raise DecisionError(DecisionErrorCode.INVALID_REQUEST, f"choice {question_id!r} exceeds the 255 option limit")
        if kind == QuestionType.SCORE.value:
            if not isinstance(criteria, Sequence) or isinstance(criteria, (str, bytes)) or len(criteria) < 2:
                raise DecisionError(DecisionErrorCode.INVALID_REQUEST, f"score {question_id!r} needs at least two levels")
            if len(criteria) > 10:
                raise DecisionError(DecisionErrorCode.INVALID_REQUEST, f"score {question_id!r} exceeds the 10 level limit")


def build_request_body(state: Any, questions: Mapping[str, Mapping[str, Any]], model: str) -> dict[str, Any]:
    return {"state": state, "model": model, "questions": {key: dict(value) for key, value in questions.items()}}


def normalize_systemone_response(
    payload: Any,
    *,
    questions: Mapping[str, Mapping[str, Any]],
    question_specs: Mapping[str, Any] | None = None,
    provider: str,
    model: str,
    spec_version: str,
    state_hash: str,
    latency_ms: float = 0.0,
    threshold_version: str = "",
    mode: str = DecisionMode.OFF.value,
    request_id: str | None = None,
) -> DecisionResult:
    """Normalize a System One response into :class:`DecisionResult`.

    Shared by the real adapter *and* the mock, so the mock exercises the exact
    production parsing path (a mock that bypasses parsing proves nothing, plan §29).

    Strictness rules:

    * a response without ``answers`` is ``malformed_response``;
    * a declared question with no answer is ``malformed_response`` (missing answer);
    * an answer for an undeclared question is recorded in ``unknown_answers`` and dropped;
    * an answer whose ``type`` disagrees with the declared question is ``invalid_answer``;
    * a Choice whose selected option is not one of the declared options is ``invalid_answer``;
    * a Noul carrying an extra ``confidence`` field is accepted, and the field is
      recorded as unexpected — never silently used as a threshold input.
    """
    if not isinstance(payload, Mapping):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, "provider response is not a JSON object")

    raw_answers = payload.get("answers")
    if raw_answers is None:
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, "provider response has no 'answers' object")
    if not isinstance(raw_answers, Mapping):
        raise DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, "'answers' is not an object")

    usage_raw = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    usage = DecisionUsage(
        input_tokens=int(usage_raw.get("input_tokens") or 0),
        output_tokens=int(usage_raw.get("output_tokens") or 0),
    )
    reported_model = str(payload.get("model") or model or "")

    answers: dict[str, DecisionAnswer] = {}
    unknown: list[str] = []
    for question_id, raw in raw_answers.items():
        if question_id not in questions:
            unknown.append(str(question_id))
            continue
        question = questions[question_id]
        declared_kind = str(question.get("type") or "")
        where = f"answer {question_id!r}"
        if isinstance(raw, Mapping):
            reported_kind = str(raw.get("type") or declared_kind)
            if reported_kind and reported_kind != declared_kind:
                raise DecisionError(
                    DecisionErrorCode.INVALID_ANSWER,
                    f"{where}: provider returned type {reported_kind!r} for a {declared_kind!r} question",
                )
        if declared_kind == QuestionType.CHOICE.value:
            criteria = question.get("criteria")
            options = tuple(str(key) for key in (criteria or {})) if isinstance(criteria, Mapping) else ()
            answers[question_id] = parse_choice_answer(raw, declared_options=options, where=where)
        elif declared_kind == QuestionType.SCORE.value:
            criteria = question.get("criteria") or []
            levels = tuple(str(index) for index in range(len(criteria)))
            answers[question_id] = parse_score_answer(raw, level_indices=levels, where=where)
        elif declared_kind == QuestionType.NOUL.value:
            answers[question_id] = parse_noul_answer(raw, where=where)
        else:  # pragma: no cover - validate_questions already rejected this
            raise DecisionError(DecisionErrorCode.INVALID_REQUEST, f"{where}: unknown question type {declared_kind!r}")

    missing = sorted(set(questions) - set(answers))
    if missing:
        raise DecisionError(
            DecisionErrorCode.MALFORMED_RESPONSE,
            f"provider response is missing answers for: {', '.join(missing)}",
        )

    return DecisionResult(
        provider=provider,
        model=reported_model,
        spec_version=spec_version,
        state_hash=state_hash,
        answers=answers,
        latency_ms=latency_ms,
        usage=usage,
        request_id=request_id,
        unknown_answers=tuple(sorted(unknown)),
        threshold_version=threshold_version,
        mode=mode,
    )


def parse_retry_after(headers: Mapping[str, str], *, now: float | None = None) -> float | None:
    """Seconds to wait, from ``retry-after-ms`` or ``Retry-After``."""
    raw_ms = headers.get("retry-after-ms") if hasattr(headers, "get") else None
    if raw_ms:
        try:
            value = float(str(raw_ms).strip())
            if value >= 0:
                return value / 1000.0
        except ValueError:
            pass
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if not raw:
        return None
    try:
        value = float(str(raw).strip())
        return value if value >= 0 else None
    except ValueError:
        return None


def _error_for_status(status: int, body: Any, provider: str, *, attempts: int, retry_after: float | None) -> DecisionError:
    code = _STATUS_ERROR_CODES.get(status)
    if code is None:
        code = DecisionErrorCode.PROVIDER_ERROR if status >= 500 else DecisionErrorCode.INVALID_REQUEST
    message = _extract_provider_message(body) or f"provider returned HTTP {status}"
    return DecisionError(
        code,
        f"{message} (HTTP {status})",
        attempts=attempts,
        status=status,
        provider=provider,
        retry_after_s=retry_after,
    )


def _extract_provider_message(body: Any, *, limit: int = 400) -> str:
    """Best-effort, bounded, redaction-safe extraction of an error message."""
    text = ""
    if isinstance(body, str):
        text = body
    elif isinstance(body, Mapping):
        for key in ("error", "message", "detail"):
            value = body.get(key)
            if isinstance(value, str):
                text = value
                break
            if isinstance(value, Mapping) and isinstance(value.get("message"), str):
                text = value["message"]
                break
    if not text:
        return ""
    text = " ".join(str(text).split())
    return text[:limit]


def backoff_delay(attempt: int, *, base: float, cap: float, jitter_ratio: float, rng: random.Random | None = None) -> float:
    """Bounded exponential backoff with downward jitter (same shape as the SDK)."""
    if base <= 0 or cap <= 0:
        return 0.0
    exponential = min(cap, base * (2 ** max(0, attempt - 1)))
    source = rng or random
    delay = exponential * (1 - source.random() * max(0.0, min(1.0, jitter_ratio)))
    return round(min(exponential, max(0.0, delay)), 3)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class JevDecisionClient:
    """HTTP client implementing :class:`~poc.decision.protocol.DecisionClient`."""

    def __init__(
        self,
        config: JevConfig,
        *,
        http_client: httpx.Client | None = None,
        breaker: DecisionCircuitBreaker | None = None,
        mode: str = DecisionMode.SHADOW.value,
        threshold_version: str = "",
        clock: Any = None,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.provider = str(config.provider or DecisionProviderKind.TYPESAFE.value)
        self.model = str(config.model or DEFAULT_MODEL)
        self.mode = str(mode)
        self.threshold_version = threshold_version
        self._client = http_client
        self._owns_client = http_client is None
        self.breaker = breaker or DecisionCircuitBreaker(
            failure_threshold=config.breaker_failure_threshold,
            recovery_timeout=config.breaker_recovery_seconds,
        )
        self._clock = clock or time.perf_counter
        self._rng = rng
        self.calls = 0
        self.failures = 0
        self.retries = 0
        self.last_latency_ms = 0.0
        self.last_request_id: str | None = None

    # --- transport ---------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(self.config.timeout_s))
        return self._client

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "mizan-decision-layer/1.0",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        for key, value in (self.config.extra_headers or {}).items():
            headers[str(key)] = str(value)
        return headers

    # --- public API --------------------------------------------------------
    def decide(
        self,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        *,
        spec_version: str,
        state_hash: str | None = None,
        timeout_s: float | None = None,
        question_specs: Mapping[str, Any] | None = None,
    ) -> DecisionResult:
        state_digest = state_hash or compute_state_hash(state, spec_version=spec_version, model=self.model)
        started = self._clock()

        def failed(error: DecisionError) -> DecisionResult:
            self.failures += 1
            self.last_latency_ms = (self._clock() - started) * 1000.0
            return DecisionResult(
                provider=self.provider,
                model=self.model,
                spec_version=spec_version,
                state_hash=state_digest,
                latency_ms=self.last_latency_ms,
                error=error,
                threshold_version=self.threshold_version,
                mode=self.mode,
            )

        # 0. Preconditions that must not consume a request.
        if not self.config.api_key:
            return failed(
                DecisionError(
                    DecisionErrorCode.AUTH,
                    "no API key configured for the decision provider",
                    provider=self.provider,
                )
            )
        try:
            validate_questions(questions)
        except DecisionError as error:
            return failed(error)

        if not self.breaker.can_execute():
            return failed(
                DecisionError(
                    DecisionErrorCode.CIRCUIT_OPEN,
                    f"decision provider circuit is {self.breaker.state}",
                    provider=self.provider,
                )
            )

        body = build_request_body(state, questions, self.model)
        budget = float(timeout_s or self.config.total_budget_s)
        deadline = started + budget
        attempt = 0
        last_error: DecisionError | None = None

        while True:
            attempt += 1
            self.calls += 1
            request_started = self._clock()
            try:
                response = self._http().post(
                    self.config.endpoint,
                    json=body,
                    headers=self._headers(),
                    timeout=httpx.Timeout(float(timeout_s or self.config.timeout_s)),
                )
            except httpx.TimeoutException as error:
                last_error = DecisionError(
                    DecisionErrorCode.TIMEOUT,
                    f"decision provider timed out: {type(error).__name__}",
                    attempts=attempt,
                    provider=self.provider,
                )
            except httpx.HTTPError as error:
                last_error = DecisionError(
                    DecisionErrorCode.CONNECTION,
                    f"decision provider unreachable: {type(error).__name__}",
                    attempts=attempt,
                    provider=self.provider,
                )
            else:
                self.last_request_id = response.headers.get(REQUEST_ID_HEADER)
                if response.status_code >= 400:
                    retry_after = parse_retry_after(response.headers)
                    try:
                        payload: Any = response.json()
                    except (json.JSONDecodeError, ValueError):
                        payload = response.text[:400]
                    last_error = _error_for_status(
                        response.status_code,
                        payload,
                        self.provider,
                        attempts=attempt,
                        retry_after=retry_after,
                    )
                    if retry_after is not None and retry_after > self.config.backoff_cap_s:
                        # A long Retry-After must not stall a user request.
                        last_error = DecisionError(
                            DecisionErrorCode.RATE_LIMITED,
                            f"{last_error.message}; retry-after {retry_after:.1f}s exceeds the local cap",
                            attempts=attempt,
                            status=response.status_code,
                            provider=self.provider,
                            retry_after_s=retry_after,
                        )
                        self.breaker.record_failure()
                        return failed(last_error)
                else:
                    try:
                        payload = response.json()
                    except (json.JSONDecodeError, ValueError):
                        self.breaker.record_failure()
                        return failed(
                            DecisionError(
                                DecisionErrorCode.MALFORMED_RESPONSE,
                                "provider returned a non-JSON success body",
                                attempts=attempt,
                                status=response.status_code,
                                provider=self.provider,
                            )
                        )
                    latency_ms = (self._clock() - request_started) * 1000.0
                    try:
                        result = normalize_systemone_response(
                            payload,
                            questions=questions,
                            question_specs=question_specs,
                            provider=self.provider,
                            model=self.model,
                            spec_version=spec_version,
                            state_hash=state_digest,
                            latency_ms=latency_ms,
                            threshold_version=self.threshold_version,
                            mode=self.mode,
                            request_id=self.last_request_id,
                        )
                    except DecisionError as error:
                        self.breaker.record_failure()
                        return failed(error)
                    self.breaker.record_success()
                    self.last_latency_ms = latency_ms
                    return result

            # --- retry decision ---
            self.retries += 1
            self.breaker.record_failure()
            retryable = bool(last_error and last_error.retryable)
            exhausted = attempt > self.config.retries
            if not retryable or exhausted:
                return failed(last_error or DecisionError(DecisionErrorCode.INTERNAL, "decision call failed"))
            delay = 0.0
            if self.config.respect_retry_after and last_error and last_error.retry_after_s:
                delay = min(self.config.backoff_cap_s, last_error.retry_after_s)
            else:
                delay = backoff_delay(
                    attempt,
                    base=self.config.backoff_base_s,
                    cap=self.config.backoff_cap_s,
                    jitter_ratio=self.config.jitter_ratio,
                    rng=self._rng,
                )
            if self._clock() + delay > deadline:
                # Retrying would blow the per-request budget: honour the budget.
                return failed(
                    DecisionError(
                        (last_error.code if last_error else DecisionErrorCode.TIMEOUT),
                        f"{last_error.message if last_error else 'decision call failed'}; retry budget exhausted",
                        attempts=attempt,
                        provider=self.provider,
                    )
                )
            if delay > 0:
                time.sleep(delay)

    def health(self) -> Mapping[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "mode": self.mode,
            "endpoint": self.config.display(),
            "breaker": self.breaker.status(),
            "calls": self.calls,
            "failures": self.failures,
            "retries": self.retries,
            "last_latency_ms": round(self.last_latency_ms, 3),
            "key_configured": bool(self.config.api_key),
            "unofficial": self.config.unofficial,
        }

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - closing must never raise
                pass
        self._client = None


__all__ = [
    "BACKOFF_BASE_S",
    "BACKOFF_CAP_S",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_PATH",
    "DecisionCircuitBreaker",
    "JevConfig",
    "JevDecisionClient",
    "REQUEST_ID_HEADER",
    "RETRYABLE_STATUSES",
    "backoff_delay",
    "build_request_body",
    "normalize_systemone_response",
    "parse_retry_after",
    "validate_questions",
]
