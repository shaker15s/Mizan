"""Monotonic decision policy — the only place a decision signal changes behaviour.

Plan §42 is the whole design:

    Decision intelligence may increase conservatism.
    Decision intelligence may never reduce server-enforced safety.

Every function here is pure, total, and returns an explicit reason string. There
is no branch in this module that can:

* grant authorization,
* waive confirmation,
* lower a risk level,
* bypass idempotency,
* mark an execution successful,
* change ERP truth,
* select an identity, tenant, or credential.

Escalation levels (highest wins — they combine monotonically):

``none``        nothing to do.
``watch``       record it, refuse to narrow the tool set, add a review notice.
``step_up``     strong signal: same as ``watch`` plus an explicit ``escalated``
                marker that the runtime, harness and UI surface. The current
                runtime has no second approver, so this level deliberately does
                **not** invent an approval path — it raises visibility.
``clarify``     the runtime asks a clarifying question instead of routing.
``quarantine``  the runtime refuses to route and records a security escalation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from poc.decision.models import ChoiceAnswer, DecisionMode, DecisionResult
from poc.decision.questions import (
    ROUTE_CLARIFY,
    ROUTE_NO_TOOL,
    ROUTE_RESERVED_OPTIONS,
    QuestionId,
)
from poc.decision.thresholds import DecisionThresholds

# --- escalation levels, ordered by severity --------------------------------
ESCALATION_NONE = "none"
ESCALATION_WATCH = "watch"
ESCALATION_STEP_UP = "step_up"
ESCALATION_CLARIFY = "clarify"
ESCALATION_QUARANTINE = "quarantine"

ESCALATION_ORDER: tuple[str, ...] = (
    ESCALATION_NONE,
    ESCALATION_WATCH,
    ESCALATION_STEP_UP,
    ESCALATION_CLARIFY,
    ESCALATION_QUARANTINE,
)

#: Escalation levels that actually change what the runtime does, per mode.
#: Shadow mode records everything and changes nothing, by definition.
_MODE_PERMITS_CLARIFY = {DecisionMode.ADVISORY.value, DecisionMode.ENFORCING.value}
_MODE_PERMITS_QUARANTINE = {DecisionMode.ADVISORY.value, DecisionMode.ENFORCING.value}
_MODE_PERMITS_NARROWING = {DecisionMode.ADVISORY.value, DecisionMode.ENFORCING.value}

#: Minimum number of tools an advisory narrowing may leave available. Narrowing
#: to a single option removes the LLM's ability to recover from a bad decision
#: signal, which would turn a *hint* into an authority (plan §12).
MIN_NARROWED_TOOLS = 2


def escalation_rank(level: str) -> int:
    try:
        return ESCALATION_ORDER.index(level)
    except ValueError:
        return 0


def max_escalation(*levels: str) -> str:
    """Monotonic combination — the highest level always wins."""
    best = ESCALATION_NONE
    for level in levels:
        if escalation_rank(level) > escalation_rank(best):
            best = level
    return best


@dataclass(frozen=True)
class Escalation:
    """A monotonic scrutinity signal produced by the decision layer."""

    level: str = ESCALATION_NONE
    reasons: tuple[str, ...] = ()
    #: The level *proposed* by the signals, before mode restrictions applied.
    proposed_level: str = ESCALATION_NONE
    signals: Mapping[str, Any] = field(default_factory=dict)

    @property
    def changes_behaviour(self) -> bool:
        return escalation_rank(self.level) >= escalation_rank(ESCALATION_CLARIFY)

    @property
    def escalated(self) -> bool:
        return escalation_rank(self.level) >= escalation_rank(ESCALATION_WATCH)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "proposed_level": self.proposed_level,
            "reasons": list(self.reasons),
            "signals": dict(self.signals),
        }


@dataclass(frozen=True)
class RoutePlan:
    """What tool set the LLM is asked to choose from (never *which* tool runs)."""

    strategy: str = "unchanged"          # "unchanged" | "narrowed"
    candidate_tools: tuple[str, ...] = ()
    all_tools: tuple[str, ...] = ()
    decision_tool: str | None = None      # the raw signal (may be clarify/no_tool)
    runner_up_tool: str | None = None
    confidence: float | None = None
    margin: float | None = None
    reason: str = ""
    #: Explicit non-tool preference; the runtime may still answer in prose.
    prefer_clarification: bool = False

    @property
    def narrowed(self) -> bool:
        return self.strategy == "narrowed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "candidate_tools": list(self.candidate_tools),
            "decision_tool": self.decision_tool,
            "runner_up_tool": self.runner_up_tool,
            "confidence": self.confidence,
            "margin": self.margin,
            "prefer_clarification": self.prefer_clarification,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Disagreement:
    """A recorded difference between the decision signal and the LLM's choice.

    Never silently discarded (plan §12, §22, §68): recorded with both sides and
    the resolution that was applied.
    """

    decision_tool: str | None
    llm_tool: str | None
    resolution: str
    confidence: float | None = None
    margin: float | None = None
    escalated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_tool": self.decision_tool,
            "llm_tool": self.llm_tool,
            "resolution": self.resolution,
            "confidence": self.confidence,
            "margin": self.margin,
            "escalated": self.escalated,
        }


@dataclass(frozen=True)
class DecisionOutcome:
    """Everything the runtime needs, plus everything a reviewer needs."""

    mode: str
    provider: str = ""
    model: str = ""
    spec_version: str = ""
    state_hash: str = ""
    threshold_version: str = ""
    route: RoutePlan = field(default_factory=RoutePlan)
    escalation: Escalation = field(default_factory=Escalation)
    latency_ms: float = 0.0
    error_code: str = ""
    fallback: bool = False
    fallback_reason: str = ""
    result: DecisionResult | None = None
    disagreement: Disagreement | None = None
    cached: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.mode != DecisionMode.OFF.value

    @property
    def signal_present(self) -> bool:
        return self.result is not None and self.result.ok

    def to_dict(self, *, include_answers: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "spec_version": self.spec_version,
            "state_hash": self.state_hash,
            "threshold_version": self.threshold_version,
            "latency_ms": round(float(self.latency_ms), 3),
            "route": self.route.to_dict(),
            "escalation": self.escalation.to_dict(),
            "disagreement": self.disagreement.to_dict() if self.disagreement else None,
            "error_code": self.error_code,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "cached": self.cached,
            "reasons": list(self.reasons),
        }
        if self.result is not None:
            payload["result"] = self.result.to_dict(include_answers=include_answers)
        return payload


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------


def route_signal(result: DecisionResult) -> ChoiceAnswer | None:
    return result.choice(QuestionId.TOOL_ROUTE.value)


def ambiguity_signal(result: DecisionResult) -> float | None:
    answer = result.noul(QuestionId.INTENT_AMBIGUOUS.value)
    return None if answer is None else answer.noul


def injection_signal(result: DecisionResult) -> float | None:
    answer = result.noul(QuestionId.PROMPT_INJECTION.value)
    return None if answer is None else answer.noul


def risk_signal(result: DecisionResult) -> float | None:
    answer = result.score(QuestionId.SEMANTIC_RISK.value)
    return None if answer is None else answer.score


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


def compute_escalation(
    result: DecisionResult | None,
    thresholds: DecisionThresholds,
    *,
    mode: str,
    write_intent: bool = False,
) -> Escalation:
    """Combine signals into one monotonic escalation level.

    The *proposed* level is what the signals say; the returned ``level`` is what
    the configured mode is allowed to act on. Both are reported, so a shadow run
    can show what advisory mode *would* have done.
    """
    reasons: list[str] = []
    signals: dict[str, Any] = {}
    proposed = ESCALATION_NONE

    if result is not None and result.ok:
        injection = injection_signal(result)
        if injection is not None:
            signals["injection"] = round(injection, 6)
            state = thresholds.injection_state(injection)
            signals["injection_state"] = state
            if state == "quarantine":
                proposed = max_escalation(proposed, ESCALATION_QUARANTINE)
                reasons.append(f"prompt-injection signal {injection:.3f} ≥ quarantine threshold {thresholds.injection_quarantine:.2f}")
            elif state == "flag":
                proposed = max_escalation(proposed, ESCALATION_STEP_UP)
                reasons.append(f"prompt-injection signal {injection:.3f} ≥ flag threshold {thresholds.injection_flag:.2f}")

        ambiguity = ambiguity_signal(result)
        if ambiguity is not None:
            signals["ambiguity"] = round(ambiguity, 6)
            flagged = thresholds.ambiguity_state(ambiguity)
            signals["ambiguity_flagged"] = flagged
            if flagged:
                proposed = max_escalation(proposed, ESCALATION_CLARIFY)
                reasons.append(f"ambiguity signal {ambiguity:.3f} ≥ trigger {thresholds.ambiguity_trigger:.2f}")

        risk = risk_signal(result)
        if risk is not None:
            signals["semantic_risk"] = round(risk, 6)
            risk_state = thresholds.risk_state(risk)
            signals["semantic_risk_state"] = risk_state
            if risk_state == "escalate":
                # A semantic risk escalation never invents an approval step; it
                # blocks auto-narrowing and raises visibility (see module docstring).
                proposed = max_escalation(proposed, ESCALATION_STEP_UP)
                reasons.append(f"semantic risk score {risk:.2f} ≥ escalation level {thresholds.risk_escalate_level:.2f}")
            elif risk_state == "watch":
                proposed = max_escalation(proposed, ESCALATION_WATCH)
                reasons.append(f"semantic risk score {risk:.2f} in the watch band")

        route = route_signal(result)
        if route is not None and route.choice == ROUTE_CLARIFY:
            # Only meaningful when the decision layer is confident about the
            # ambiguity; a low-confidence "clarify" is just a shrug.
            if route.confidence >= thresholds.route_min_confidence:
                proposed = max_escalation(proposed, ESCALATION_CLARIFY)
                reasons.append(f"routing signal is 'clarify' at confidence {route.confidence:.3f}")
        elif route is not None and route.choice in ROUTE_RESERVED_OPTIONS:
            signals["route"] = route.choice

    level = proposed
    if level == ESCALATION_QUARANTINE and mode not in _MODE_PERMITS_QUARANTINE:
        level = ESCALATION_STEP_UP if mode == DecisionMode.SHADOW.value else ESCALATION_NONE
        if mode == DecisionMode.OFF.value:
            level = ESCALATION_NONE
        reasons.append(f"mode {mode!r} does not permit quarantine; recorded only")
    elif level == ESCALATION_CLARIFY and mode not in _MODE_PERMITS_CLARIFY:
        level = ESCALATION_WATCH if mode == DecisionMode.SHADOW.value else ESCALATION_NONE
        reasons.append(f"mode {mode!r} does not permit clarification escalation; recorded only")

    if write_intent and level == ESCALATION_NONE and proposed == ESCALATION_STEP_UP:
        # A write keeps the existing confirmation path (policy already requires
        # it); the semantic signal adds visibility only.
        reasons.append("write path already requires confirmation; signal recorded without adding a step")

    return Escalation(level=level, reasons=tuple(reasons), proposed_level=proposed, signals=signals)


# ---------------------------------------------------------------------------
# Tool-set routing
# ---------------------------------------------------------------------------


def compute_route_plan(
    result: DecisionResult | None,
    thresholds: DecisionThresholds,
    *,
    mode: str,
    all_tools: Sequence[str],
    write_intent: bool = False,
    read_intent_hint: bool = False,
) -> RoutePlan:
    """Decide whether the LLM's candidate tool set may be narrowed.

    Narrowing is **never** about which tool executes — the gateway still
    validates, authorises, and (for writes) requires human confirmation. It only
    reduces the option space handed to the language model, and it is skipped
    entirely whenever any deterministic signal disagrees with the route.
    """
    tools = tuple(all_tools)
    if result is None or not result.ok:
        return RoutePlan(candidate_tools=tools, all_tools=tools, reason="no usable decision signal; keeping the full tool set")

    choice = route_signal(result)
    if choice is None:
        return RoutePlan(candidate_tools=tools, all_tools=tools, reason="no routing answer in the decision result")

    # The *signal* is recorded whether or not this mode may act on it. Shadow
    # mode exists precisely to measure what advisory would have done, so dropping
    # the tool here would make the shadow report silently empty.
    signal = RoutePlan(
        candidate_tools=tools,
        all_tools=tools,
        decision_tool=choice.choice,
        runner_up_tool=(choice.top_two[1] if len(choice.top_two) > 1 else None),
        confidence=choice.confidence,
        margin=choice.margin,
        prefer_clarification=choice.choice == ROUTE_CLARIFY and choice.confidence >= thresholds.route_min_confidence,
        reason=f"recorded only; mode {mode!r} does not act on the signal",
    )

    if mode not in _MODE_PERMITS_NARROWING or not thresholds.narrow_tool_set:
        return replace(signal, reason=f"mode {mode!r} does not narrow the tool set")

    if choice.choice in ROUTE_RESERVED_OPTIONS:
        return replace(signal, reason=f"decision signal declined to select a tool ({choice.choice})")

    if choice.choice not in tools:
        # A signal naming a tool outside the server-owned registry is not a
        # permission and not a candidate: it is rejected outright.
        return replace(signal, reason=f"decision signal named {choice.choice!r}, which is not in the server-owned registry")

    confident, why = thresholds.route_is_confident(choice.confidence, choice.margin)
    if not confident:
        return replace(signal, reason=f"signal not confident enough to narrow ({why})")

    pinned_write = choice.choice.endswith(".create") or not choice.choice.endswith((".search", ".get"))
    if pinned_write and not write_intent and read_intent_hint:
        # Deterministic intent hints and the decision signal disagree about the
        # *kind* of operation. Narrowing here could hide the correct read tool.
        return replace(signal, reason="decision signal conflicts with the deterministic operation-kind hint; not narrowing")

    keep: list[str] = [choice.choice]
    runner_up = choice.top_two[1] if len(choice.top_two) > 1 else None
    if thresholds.keep_runner_up_tool and runner_up and runner_up in tools and runner_up not in keep:
        keep.append(runner_up)
    for fallback in tools:
        if len(keep) >= MIN_NARROWED_TOOLS:
            break
        if fallback not in keep:
            keep.append(fallback)

    ordered = tuple(name for name in tools if name in set(keep))
    if len(ordered) == len(tools):
        return replace(signal, reason="narrowing would not remove any candidate")
    return replace(
        signal,
        strategy="narrowed",
        candidate_tools=ordered,
        prefer_clarification=False,
        reason=f"signal confident ({why}); candidates limited to {len(ordered)}/{len(tools)} tools",
    )


# ---------------------------------------------------------------------------
# Disagreement
# ---------------------------------------------------------------------------


def compare_with_llm(
    decision_tool: str | None,
    llm_tool: str | None,
    *,
    confidence: float | None = None,
    margin: float | None = None,
    thresholds: DecisionThresholds | None = None,
    escalated: bool = False,
    narrowed: bool = False,
) -> Disagreement | None:
    """Classify the relationship between the decision signal and the LLM choice.

    Returns ``None`` when there is nothing to record (no signal, or agreement).
    Resolution strings are stable so the harness can count them.
    """
    if not decision_tool or decision_tool in ROUTE_RESERVED_OPTIONS:
        return None
    if llm_tool is None:
        return Disagreement(
            decision_tool=decision_tool,
            llm_tool=None,
            resolution="llm_declined_tool",
            confidence=confidence,
            margin=margin,
            escalated=escalated,
        )
    if decision_tool == llm_tool:
        return None
    confident = bool(
        thresholds is None
        or thresholds.route_is_confident(float(confidence or 0.0), float(margin if margin is not None else 1.0))[0]
    )
    if narrowed:
        resolution = "narrowed_set_pick_differs_from_decision"
    elif confident:
        resolution = "decision_confident_llm_differs_keep_server_path"
    else:
        resolution = "decision_uncertain_llm_authoritative"
    return Disagreement(
        decision_tool=decision_tool,
        llm_tool=llm_tool,
        resolution=resolution,
        confidence=confidence,
        margin=margin,
        escalated=escalated,
    )


# ---------------------------------------------------------------------------
# Risk combination (monotone, opt-in)
# ---------------------------------------------------------------------------


def effective_risk_level(
    deterministic_level: str,
    *,
    semantic_state: str,
    bump_enabled: bool,
) -> tuple[str, str]:
    """Combine the deterministic risk level with the semantic signal.

    Returns ``(level, reason)``. The returned level is never lower than the
    deterministic one — that is asserted by construction and by test. The bump
    is opt-in (``enforcing`` mode only, explicit setting), because raising the
    risk level changes authorization requirements and must not happen by default.
    """
    order = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}
    current = str(deterministic_level or "R0")
    if not bump_enabled or semantic_state != "escalate":
        return current, "deterministic level retained"
    target = "R3" if order.get(current, 0) < 3 else current
    if order.get(target, 0) <= order.get(current, 0):
        return current, "semantic signal cannot lower the deterministic level"
    return target, f"semantic risk escalated {current} → {target} (explicitly enabled)"


__all__ = [
    "compare_with_llm",
    "compute_escalation",
    "compute_route_plan",
    "DecisionOutcome",
    "Disagreement",
    "effective_risk_level",
    "Escalation",
    "ESCALATION_CLARIFY",
    "ESCALATION_NONE",
    "ESCALATION_ORDER",
    "ESCALATION_QUARANTINE",
    "ESCALATION_STEP_UP",
    "ESCALATION_WATCH",
    "escalation_rank",
    "max_escalation",
    "MIN_NARROWED_TOOLS",
    "RoutePlan",
    "route_signal",
]
