"""Deterministic, offline decision provider used for tests and free development.

Not a happy-path stub (plan §29). It can produce, with zero network and zero
cost:

* a correct high-confidence tool route,
* a *wrong* high-confidence route (the dangerous case),
* a correct low-confidence route (must not narrow anything),
* an ambiguous intent,
* a prompt-injection signal,
* a risk escalation,
* a provider timeout, a rate limit, an outage, a malformed body, an invalid answer,
* a disagreement with the LLM,
* multi-question responses, including a Noul that carries an unexpected field.

Every successful answer is produced as a **provider-shaped payload** and passed
through the same :func:`~poc.decision.jev_client.normalize_systemone_response`
used by the real adapter, so the mock exercises production parsing instead of
routing around it.

Determinism: no randomness unless a seed is supplied. The same state + same
scenario always yields the same result, which is what makes the harness offline
runs reproducible.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from poc.decision.jev_client import normalize_systemone_response
from poc.decision.models import (
    DecisionError,
    DecisionErrorCode,
    DecisionMode,
    DecisionProviderKind,
    DecisionResult,
    compute_state_hash,
)
from poc.decision.questions import (
    ROUTE_CLARIFY,
    ROUTE_NO_TOOL,
    ROUTE_RESERVED_OPTIONS,
    QuestionId,
)
from poc.normalization import normalize_arabic

MOCK_PROVIDER = DecisionProviderKind.MOCK.value
MOCK_MODEL = "mock-system-one-1.13.0"

# ---------------------------------------------------------------------------
# Named fault / behaviour catalogue (plan §29)
# ---------------------------------------------------------------------------

FAULT_TIMEOUT = "timeout"
FAULT_RATE_LIMIT = "rate_limit"
FAULT_OUTAGE = "outage"
FAULT_MALFORMED = "malformed"
FAULT_INVALID_ANSWER = "invalid_answer"
FAULT_AUTH = "auth"
FAULT_CONNECTION = "connection"
FAULT_MISSING_ANSWER = "missing_answer"
FAULT_OVERSIZED = "oversized"

_FAULT_ERRORS: dict[str, DecisionError] = {
    FAULT_TIMEOUT: DecisionError(DecisionErrorCode.TIMEOUT, "mock provider timed out", provider=MOCK_PROVIDER),
    FAULT_RATE_LIMIT: DecisionError(
        DecisionErrorCode.RATE_LIMITED, "mock provider rate limited (HTTP 429)", status=429, provider=MOCK_PROVIDER, retry_after_s=0.05
    ),
    FAULT_OUTAGE: DecisionError(
        DecisionErrorCode.OVERLOADED, "mock provider overloaded (HTTP 529)", status=529, provider=MOCK_PROVIDER
    ),
    FAULT_AUTH: DecisionError(DecisionErrorCode.AUTH, "mock provider rejected the key (HTTP 401)", status=401, provider=MOCK_PROVIDER),
    FAULT_CONNECTION: DecisionError(DecisionErrorCode.CONNECTION, "mock provider unreachable", provider=MOCK_PROVIDER),
    FAULT_MALFORMED: DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, "mock provider returned a non-JSON body", provider=MOCK_PROVIDER),
    FAULT_INVALID_ANSWER: DecisionError(DecisionErrorCode.INVALID_ANSWER, "mock provider returned an invalid answer", provider=MOCK_PROVIDER),
    FAULT_MISSING_ANSWER: DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, "mock provider omitted a declared answer", provider=MOCK_PROVIDER),
    FAULT_OVERSIZED: DecisionError(DecisionErrorCode.OVERSIZED_STATE, "mock provider rejected the state size", status=413, provider=MOCK_PROVIDER),
}


@dataclass(frozen=True)
class MockScenario:
    """One scripted decision outcome (or fault)."""

    name: str
    route: str = ""                     # "" → derive from the rule engine
    route_confidence: float = 0.95
    probabilities: Mapping[str, float] | None = None
    ambiguity: float = 0.02
    injection: float = 0.01
    risk_score: float = 0.0
    fault: str = ""                     # one of the FAULT_* constants, "" = healthy
    fail_times: int = 0                 # with a fault: how many consecutive calls fail (0 = always)
    latency_ms: float = 12.0            # simulated provider latency recorded in the result
    include_unexpected_noul_confidence: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def is_healthy(self) -> bool:
        return not self.fault

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "route": self.route,
            "route_confidence": self.route_confidence,
            "probabilities": dict(self.probabilities) if self.probabilities else None,
            "ambiguity": self.ambiguity,
            "injection": self.injection,
            "risk_score": self.risk_score,
            "fault": self.fault,
            "fail_times": self.fail_times,
            "latency_ms": self.latency_ms,
        }


def scenario(
    name: str,
    *,
    route: str = "",
    confidence: float = 0.95,
    ambiguity: float = 0.02,
    injection: float = 0.01,
    risk: float = 0.0,
    fault: str = "",
    **extra: Any,
) -> MockScenario:
    """Terse constructor used by the scenario catalogue and by tests."""
    return MockScenario(
        name=name,
        route=route,
        route_confidence=confidence,
        ambiguity=ambiguity,
        injection=injection,
        risk_score=risk,
        fault=fault,
        **extra,
    )


SCENARIOS: dict[str, MockScenario] = {
    "confident_correct": scenario("confident_correct", confidence=0.97),
    "confident_wrong": scenario("confident_wrong", route="product.search", confidence=0.98, extra={"note": "deliberately wrong"}),
    "unconfident_correct": scenario("unconfident_correct", confidence=0.52),
    "ambiguous": scenario("ambiguous", route=ROUTE_CLARIFY, confidence=0.63, ambiguity=0.91),
    "ambiguous_unconfident": scenario("ambiguous_unconfident", route=ROUTE_CLARIFY, confidence=0.41, ambiguity=0.55),
    "prompt_injection": scenario("prompt_injection", route=ROUTE_NO_TOOL, confidence=0.88, injection=0.97, ambiguity=0.10),
    "risk_escalation": scenario("risk_escalation", confidence=0.93, risk=4.0),
    "timeout": scenario("timeout", fault=FAULT_TIMEOUT),
    "rate_limit": scenario("rate_limit", fault=FAULT_RATE_LIMIT),
    "outage": scenario("outage", fault=FAULT_OUTAGE),
    "malformed": scenario("malformed", fault=FAULT_MALFORMED),
    "invalid_answer": scenario("invalid_answer", fault=FAULT_INVALID_ANSWER),
    "auth_failure": scenario("auth_failure", fault=FAULT_AUTH),
    "no_tool": scenario("no_tool", route=ROUTE_NO_TOOL, confidence=0.93),
    "close_runner_up": scenario("close_runner_up", route="customer.search", confidence=0.71, probabilities=None),
    "noul_with_confidence": scenario("noul_with_confidence", include_unexpected_noul_confidence=True),
}

# --- rule engine (offline default when no scenario is scripted) -------------

_WRITE_HINTS = (
    "اعمل",
    "انشئ",
    "أنشئ",
    "اضف",
    "أضف",
    "سجل",
    "اطلق",
    "ابعت طلب",
    "اوردر جديد",
    "طلب بيع",
    "create order",
    "new order",
    "create",
)
_ORDER_HINTS = ("اوردر", "أمر", "امر", "طلب", "order", "فاتوره", "فاتورة", "شحنه", "شحنة")
_PRODUCT_HINTS = ("منتج", "صنف", "اصناف", "أصناف", "مخزون", "سعر", "اسعار", "كميه", "كمية", "product", "stock", "price")
_CUSTOMER_HINTS = ("عميل", "زبون", "شركه", "شركة", "متجر", "customer", "client", "partner")
_DETAIL_HINTS = ("تفاصيل", "بطاقه", "بطاقة", "بيانات", "حاله", "حالة", "رصيد", "customer.get")
_CHAT_HINTS = ("السلام عليكم", "اهلا", "أهلا", "صباح", "مساء", "شكرا", "شكرًا", "مين انت", "help", "مساعده", "مساعدة", "ازيك")
_QUANTITY_RE = re.compile(r"(?:عدد|كميه|كمية|qty|لعدد|بكميه)\s*[:=]?\s*(\d+(?:[.,]\d+)?)")
_ID_RE = re.compile(r"\b(\d{1,6})\b")

_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "تجاهل كل",
    "تجاهل التعليمات",
    "system:",
    "you are now",
    "انت الان",
    "أنت الآن",
    "admin mode",
    "وضع المسؤول",
    "override policy",
    "تجاوز السياس",
    "بدون تاكيد",
    "بدون تأكيد",
    "without confirmation",
    "drop table",
    "reveal",
    "اكشف",
    "مفاتيح",
    "api key",
    "api_key",
    "الرموز السريه",
    "الرموز السرية",
    "system prompt",
    "التعليمات المخفيه",
    "التعليمات المخفية",
    "hidden instruction",
)


def _fold(text: str) -> str:
    return normalize_arabic(str(text or "")).lower()


def rule_route(user_text: str, available: Sequence[str]) -> tuple[str, float]:
    """Deterministic keyword router used when no scenario is scripted.

    Deliberately simple and conservative: anything it cannot classify with a
    clear signal becomes ``clarify`` rather than a guess, mirroring the rule the
    runtime already applies to ambiguous entity names.
    """
    folded = _fold(user_text)
    options = set(available)

    def pick(name: str) -> tuple[str, float]:
        return (name, 0.9) if name in options else (ROUTE_CLARIFY, 0.45)

    if any(hint in folded for hint in _CHAT_HINTS) and not any(hint in folded for hint in _CUSTOMER_HINTS + _PRODUCT_HINTS + _ORDER_HINTS):
        return pick(ROUTE_NO_TOOL)

    wants_write = any(_fold(hint) in folded for hint in _WRITE_HINTS)
    order_word = any(_fold(hint) in folded for hint in _ORDER_HINTS)
    product_word = any(_fold(hint) in folded for hint in _PRODUCT_HINTS)
    customer_word = any(_fold(hint) in folded for hint in _CUSTOMER_HINTS)

    if wants_write and order_word and (product_word or _QUANTITY_RE.search(folded) or _ID_RE.search(folded)):
        return pick("sales.order.create")
    if order_word and _ID_RE.search(folded):
        return pick("sales.order.get")
    if product_word:
        return pick("product.search")
    if customer_word and any(_fold(hint) in folded for hint in _DETAIL_HINTS) and _ID_RE.search(folded):
        return pick("customer.get")
    if customer_word:
        return pick("customer.search")
    # «هاتلي أحمد» carries no noun at all: reuse the repository's existing
    # Arabic name extractor instead of inventing a second one.
    from poc.simulated_llm import extract_query_name

    if extract_query_name(user_text):
        return pick("product.search" if product_word else "customer.search")
    if _QUANTITY_RE.search(folded) or _ID_RE.search(folded):
        return (ROUTE_CLARIFY, 0.4)
    return (ROUTE_CLARIFY, 0.42)


def rule_injection(user_text: str) -> float:
    """Rule-based injection score. Deterministic; used only by the mock provider."""
    folded = _fold(user_text)
    hits = sum(1 for marker in _INJECTION_MARKERS if _fold(marker) in folded)
    if hits == 0:
        return 0.01
    return min(0.99, 0.62 + 0.12 * hits)


def rule_risk(user_text: str, route: str, argument_shape: Mapping[str, Any] | None = None) -> float:
    """Rule-based semantic risk score, in the units of SEMANTIC_RISK_LEVELS."""
    if route in {ROUTE_CLARIFY, ROUTE_NO_TOOL} or route.endswith(".search") or route.endswith(".get"):
        return 0.0
    if route.endswith(".delete") or route.endswith(".unlink"):
        return 4.0
    total = 0.0
    if isinstance(argument_shape, Mapping):
        value = argument_shape.get("total_quantity")
        if isinstance(value, (int, float)):
            total = float(value)
    if total >= 1000:
        return 4.0
    if total >= 100:
        return 3.2
    folded = _fold(user_text)
    if any(token in folded for token in ("كل العملاء", "تصدير", "export", "حذف", "delete", "مسح")):
        return 4.0
    return 2.0


class MockDecisionClient:
    """Deterministic offline provider implementing the DecisionClient protocol."""

    def __init__(
        self,
        *,
        scenarios: Mapping[str, MockScenario] | None = None,
        default: MockScenario | None = None,
        use_rules: bool = True,
        mode: str = DecisionMode.ADVISORY.value,
        threshold_version: str = "",
        seed: int | None = None,
        latency_ms: float = 12.0,
        provider: str = MOCK_PROVIDER,
        model: str = MOCK_MODEL,
    ) -> None:
        # Longest key first so overlapping substrings resolve deterministically.
        self._scenarios: list[tuple[str, MockScenario]] = sorted(
            (str(key), value) for key, value in (scenarios or {}).items()
        )
        self._scenarios.sort(key=lambda row: (-len(row[0]), row[0]))
        self.default_scenario = default
        self.use_rules = bool(use_rules)
        self.provider = provider
        self.model = model
        self.mode = str(mode)
        self.threshold_version = threshold_version
        self.latency_ms = float(latency_ms)
        self._rng = random.Random(seed) if seed is not None else None
        self._queue: list[MockScenario] = []
        self._fault_remaining: dict[str, int] = {}
        self.calls: list[dict[str, Any]] = []

    # --- scripting ---------------------------------------------------------
    def enqueue(self, item: MockScenario | str) -> "MockDecisionClient":
        """Queue the next answer (a scenario object or a catalogue name)."""
        self._queue.append(SCENARIOS[item] if isinstance(item, str) else item)
        return self

    def script(self, mapping: Mapping[str, MockScenario | str]) -> "MockDecisionClient":
        entries = [(str(key), SCENARIOS[value] if isinstance(value, str) else value) for key, value in mapping.items()]
        entries.sort(key=lambda row: (-len(row[0]), row[0]))
        self._scenarios = entries
        return self

    def reset(self) -> None:
        self._queue.clear()
        self._fault_remaining.clear()
        self.calls.clear()

    # --- scenario selection -------------------------------------------------
    def _select(self, state: Any) -> MockScenario | None:
        """Pick the scripted answer for this state.

        Matching is **exact-first** and deliberately conservative:

        1. an exact match on the raw utterance,
        2. an exact match on the folded (normalized, PII-masked, lower-cased)
           utterance, because that is what the state builder sends,
        3. optionally, a substring match for keys explicitly prefixed with ``~``.

        Free substring matching is *not* the default: a short key such as ``لا``
        ("no") is a substring of ``اعرض عملاء اسمهم أحمد``, so a fuzzy default
        would let an unrelated scenario steal the turn. A scripted evaluation
        must not have that failure mode.
        """
        if self._queue:
            return self._queue.pop(0)
        text = ""
        if isinstance(state, Mapping):
            text = str(state.get("user_text") or "")
        if text:
            for key, value in self._scenarios:
                if key and key == text:
                    return value
            folded = _fold(text)
            for key, value in self._scenarios:
                if key and _fold(key) == folded:
                    return value
            for key, value in self._scenarios:
                if key.startswith("~"):
                    needle = _fold(key[1:])
                    if needle and needle in folded:
                        return value
        if self.default_scenario is not None:
            return self.default_scenario
        return None

    def _fault_pending(self, item: MockScenario) -> bool:
        """Consume a bounded fault streak: ``fail_times`` then recover."""
        if not item.fault:
            return False
        if item.fail_times <= 0:
            return True
        remaining = self._fault_remaining.get(item.name, item.fail_times)
        if remaining > 0:
            self._fault_remaining[item.name] = remaining - 1
            return True
        return False

    # --- answering ----------------------------------------------------------
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
        item = self._select(state)
        self.calls.append(
            {
                "questions": sorted(questions),
                "state_keys": sorted(state) if isinstance(state, Mapping) else [],
                "scenario": item.name if item else "",
                "state_digest": state_digest[:12],
            }
        )

        def failure(error: DecisionError) -> DecisionResult:
            return DecisionResult(
                provider=self.provider,
                model=self.model,
                spec_version=spec_version,
                state_hash=state_digest,
                latency_ms=self.latency_ms,
                error=error,
                threshold_version=self.threshold_version,
                mode=self.mode,
            )

        if item is not None and item.fault and self._fault_pending(item):
            return failure(_FAULT_ERRORS.get(item.fault, _FAULT_ERRORS[FAULT_OUTAGE]))

        try:
            payload = self._build_payload(state, questions, item)
            if isinstance(payload, DecisionError):
                return failure(payload)
            return normalize_systemone_response(
                payload,
                questions=questions,
                question_specs=question_specs,
                provider=self.provider,
                model=self.model,
                spec_version=spec_version,
                state_hash=state_digest,
                latency_ms=self.latency_ms,
                threshold_version=self.threshold_version,
                mode=self.mode,
                request_id=f"mock-{len(self.calls):05d}",
            )
        except DecisionError as error:
            return failure(error)

    def _build_payload(
        self,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        item: MockScenario | None,
    ) -> dict[str, Any] | DecisionError:
        user_text = str(state.get("user_text") or "") if isinstance(state, Mapping) else ""
        argument_shape = state.get("argument_shape") if isinstance(state, Mapping) else None
        available: list[str] = []
        if isinstance(state, Mapping):
            for entry in state.get("candidate_tools") or ():
                name = str(entry).split(" ", 1)[0]
                if name:
                    available.append(name)
        # The routing Choice also declares the two reserved non-tool options;
        # the rule engine must be able to select them, so they are part of the
        # option set the mock answers over (never part of the tool registry).
        available.extend(name for name in ROUTE_RESERVED_OPTIONS if name not in available)

        scenario = item or SCENARIOS["confident_correct"]
        route = scenario.route
        confidence = scenario.route_confidence
        derived_from_rules = False
        if not route and self.use_rules:
            route, confidence = rule_route(user_text, available)
            derived_from_rules = True
        if not route:
            route = ROUTE_CLARIFY
            confidence = 0.4

        answers: dict[str, Any] = {}
        for question_id, question in questions.items():
            kind = str(question.get("type") or "")
            criteria = question.get("criteria")
            if question_id == QuestionId.TOOL_ROUTE.value and kind == "choice":
                options = tuple(str(key) for key in (criteria or {})) if isinstance(criteria, Mapping) else ()
                answers[question_id] = self._choice_answer(route, confidence, options, scenario)
            elif question_id == QuestionId.INTENT_AMBIGUOUS.value and kind == "noul":
                value = scenario.ambiguity
                if derived_from_rules:
                    value = 0.78 if route == ROUTE_CLARIFY else 0.05
                answers[question_id] = {"type": "noul", "noul": round(float(value), 6)}
            elif question_id == QuestionId.PROMPT_INJECTION.value and kind == "noul":
                value = scenario.injection
                if derived_from_rules:
                    value = rule_injection(user_text)
                answers[question_id] = {"type": "noul", "noul": round(float(value), 6)}
                if scenario.include_unexpected_noul_confidence:
                    # Deliberate: proves the parser tolerates (and ignores) a
                    # confidence field on a Noul rather than thresholding on it.
                    answers[question_id]["confidence"] = 0.88
            elif question_id == QuestionId.SEMANTIC_RISK.value and kind == "score":
                levels = list(criteria) if isinstance(criteria, Sequence) and not isinstance(criteria, (str, bytes)) else []
                value = scenario.risk_score
                if derived_from_rules:
                    value = rule_risk(user_text, route, argument_shape if isinstance(argument_shape, Mapping) else None)
                answers[question_id] = self._score_answer(float(value), levels, scenario)
            elif question_id == QuestionId.CLAIM_MATCHES_OUTCOME.value and kind == "noul":
                answers[question_id] = {"type": "noul", "noul": 0.02 if self._claim_matches(state, scenario) else 0.94}
            elif question_id == QuestionId.TRACE_ANOMALOUS.value and kind == "noul":
                answers[question_id] = {"type": "noul", "noul": 0.03}
            elif kind == "noul":
                answers[question_id] = {"type": "noul", "noul": 0.5}
            elif kind == "choice":
                options = tuple(str(key) for key in (criteria or {})) if isinstance(criteria, Mapping) else ()
                answers[question_id] = self._choice_answer(options[0] if options else "", confidence, options, scenario)
            elif kind == "score":
                levels = list(criteria) if isinstance(criteria, Sequence) and not isinstance(criteria, (str, bytes)) else []
                answers[question_id] = self._score_answer(0.0, levels, scenario)

        if scenario.fault == FAULT_MISSING_ANSWER and answers:
            answers.pop(sorted(answers)[0], None)
        if scenario.fault == FAULT_INVALID_ANSWER and QuestionId.TOOL_ROUTE.value in answers:
            answers[QuestionId.TOOL_ROUTE.value] = {
                "type": "choice",
                "choice": "not_a_declared_option",
                "probabilities": {name: 0.5 for name in ("not_a_declared_option", "something_else")},
                "confidence": 0.99,
            }
        if scenario.fault == FAULT_MALFORMED:
            return DecisionError(DecisionErrorCode.MALFORMED_RESPONSE, "mock provider returned a non-JSON body")

        payload: dict[str, Any] = {
            "model": self.model,
            "answers": answers,
            "usage": {"input_tokens": 220 + len(user_text) // 4, "output_tokens": 18},
        }
        return payload

    @staticmethod
    def _claim_matches(state: Any, scenario: MockScenario) -> bool:
        if isinstance(state, Mapping):
            if str(state.get("verification_status") or "") in {"failed", "unverified", "not_verified"}:
                return False
            if str(state.get("verified_outcome_code") or "") in {"", "VERIFICATION_FAILED"}:
                return False
        return not scenario.extra.get("claim_mismatch", False)

    def _choice_answer(self, route: str, confidence: float, options: tuple[str, ...], scenario: MockScenario) -> dict[str, Any]:
        """Build a valid distribution over the declared options.

        The runner-up share is derived from the confidence so that
        ``confidence`` and ``margin`` stay *consistent* — a mock that emits a
        flat distribution with confidence 0.99 would test nothing.
        """
        if not options:
            raise DecisionError(DecisionErrorCode.INVALID_REQUEST, "choice question has no options")
        chosen = route if route in options else options[0]
        confidence = max(0.0, min(1.0, float(confidence)))
        probabilities = dict(scenario.probabilities) if scenario.probabilities else None
        if probabilities is None:
            others = [name for name in options if name != chosen]
            probabilities = {chosen: confidence}
            if others:
                remaining = 1.0 - confidence
                weights = [1.0 / (index + 1) for index in range(len(others))]
                total_weight = sum(weights)
                for name, weight in zip(others, weights):
                    probabilities[name] = round(remaining * weight / total_weight, 6)
            else:
                probabilities[chosen] = 1.0
            # Re-normalize the rounding drift so the distribution sums to 1.
        probabilities = self._normalize(probabilities, options)
        return {
            "type": "choice",
            "choice": chosen,
            "probabilities": probabilities,
            "confidence": confidence,
        }

    @staticmethod
    def _normalize(probabilities: Mapping[str, float], options: tuple[str, ...]) -> dict[str, float]:
        ordered = {name: float(probabilities.get(name, 0.0)) for name in options}
        total = sum(ordered.values())
        if total <= 0:
            share = 1.0 / len(options)
            return {name: round(share, 6) for name in options}
        normalized = {name: value / total for name, value in ordered.items()}
        drift = 1.0 - sum(normalized.values())
        first = next(iter(normalized))
        normalized[first] = max(0.0, normalized[first] + drift)
        return {name: round(value, 6) for name, value in normalized.items()}

    def _score_answer(self, value: float, levels: Sequence[Any], scenario: MockScenario) -> dict[str, Any]:
        level_count = len(levels)
        if level_count < 2:
            raise DecisionError(DecisionErrorCode.INVALID_REQUEST, "score question has fewer than two levels")
        clamped = max(0.0, min(float(level_count - 1), float(value)))
        lower = int(clamped)
        upper = min(level_count - 1, lower + 1)
        fraction = clamped - lower
        probabilities = {str(index): 0.0 for index in range(level_count)}
        probabilities[str(lower)] = 1.0 - fraction
        probabilities[str(upper)] = probabilities.get(str(upper), 0.0) + fraction
        if scenario.extra.get("flat_risk"):
            probabilities = {str(index): 1.0 / level_count for index in range(level_count)}
        confidence = 0.35 if scenario.extra.get("flat_risk") else 0.9
        return {
            "type": "score",
            "score": round(clamped, 6),
            "legend": {str(index): str(level)[:120] for index, level in enumerate(levels)},
            "probabilities": probabilities,
            "confidence": confidence,
        }

    def health(self) -> Mapping[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "mode": self.mode,
            "calls": len(self.calls),
            "queued": len(self._queue),
            "scenario_keys": [key for key, _ in self._scenarios],
            "offline": True,
            "cost_usd": 0.0,
        }

    def close(self) -> None:  # nothing to release
        return None

__all__ = [
    "FAULT_AUTH",
    "FAULT_CONNECTION",
    "FAULT_INVALID_ANSWER",
    "FAULT_MALFORMED",
    "FAULT_MISSING_ANSWER",
    "FAULT_OUTAGE",
    "FAULT_OVERSIZED",
    "FAULT_RATE_LIMIT",
    "FAULT_TIMEOUT",
    "MOCK_MODEL",
    "MOCK_PROVIDER",
    "MockDecisionClient",
    "MockScenario",
    "SCENARIOS",
    "rule_injection",
    "rule_risk",
    "rule_route",
    "scenario",
]
