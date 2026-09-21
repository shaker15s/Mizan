"""Decision router — mode orchestration, wiring, and evidence (plan §9, §21, §41).

The router is the *only* component the agent runtime talks to. It owns:

* the provider client (mock / official / custom),
* the question specification and state construction,
* threshold resolution,
* the mode decision (off / shadow / advisory / enforcing),
* telemetry, the optional cache, and the evidence events.

It owns none of: policy, authorization, confirmation, idempotency, execution,
verification, or ERP truth. A reviewer reading a routed execution can answer
"what did the user ask / what did the LLM propose / what did the decision layer
say / what did policy say / why was this path chosen" entirely from the evidence
graph plus the existing audit trail.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from poc.decision.cache import CacheKey, DecisionCache
from poc.decision.jev_client import DecisionCircuitBreaker, JevConfig, JevDecisionClient
from poc.decision.mock_client import MockDecisionClient
from poc.decision.models import (
    DecisionError,
    DecisionErrorCode,
    DecisionMode,
    DecisionProviderKind,
    DecisionResult,
    compute_state_hash,
)
from poc.decision.policy import (
    ESCALATION_NONE,
    DecisionOutcome,
    Escalation,
    RoutePlan,
    compare_with_llm,
    compute_escalation,
    compute_route_plan,
    risk_signal,
)
from poc.decision.questions import (
    DECISION_SPEC_VERSION,
    QuestionId,
    QuestionSet,
    build_post_run_question_set,
    build_routing_question_set,
    build_routing_state,
    summarize_intent,
)
from poc.decision.telemetry import DecisionTelemetry
from poc.decision.thresholds import DecisionThresholds, THRESHOLD_VERSION

#: Purposes the cache understands (see cache.CACHEABLE_PURPOSES).
PURPOSE_ROUTING = "routing"


@dataclass(frozen=True)
class DecisionConfig:
    """Explicit, typed configuration for the decision layer.

    Nothing here activates implicitly. ``provider=off`` (the default) means the
    decision layer never runs, no matter what keys exist in the environment —
    plan §45: "Never silently switch to real Jev because a key happens to be
    present."
    """

    provider: str = DecisionProviderKind.OFF.value
    mode: str = DecisionMode.OFF.value
    model: str = ""
    base_url: str = ""
    path: str = ""
    api_key: str = ""
    timeout_s: float = 10.0
    total_budget_s: float = 20.0
    retries: int = 1
    allow_external_provider: bool = False
    unofficial: bool = False
    synthetic_only: bool = True
    free_dev_profile: bool = False
    enable_post_run_review: bool = False
    spec_version: str = DECISION_SPEC_VERSION
    threshold_version: str = THRESHOLD_VERSION
    note: str = ""

    @property
    def enabled(self) -> bool:
        """Both axes must opt in.

        ``provider`` selects *which* client to call; ``mode`` selects whether the
        layer may change behaviour. ``mode=off`` is the regression-safe default
        and silences the layer even when a provider has been configured, so a
        stray ``decision.provider=mock`` can never turn screening on by itself.
        """
        return self.provider not in {"", DecisionProviderKind.OFF.value} and self.mode not in {"", DecisionMode.OFF.value}

    @property
    def calls_network(self) -> bool:
        return self.provider in {DecisionProviderKind.TYPESAFE.value, DecisionProviderKind.CUSTOM.value}

    def display(self) -> dict[str, Any]:
        """Secret-free description for telemetry, health and the settings panel."""
        return {
            "provider": self.provider,
            "mode": self.mode,
            "model": self.model or None,
            "endpoint_host": _host(self.base_url),
            "timeout_s": self.timeout_s,
            "retries": self.retries,
            "allow_external_provider": self.allow_external_provider,
            "unofficial": self.unofficial,
            "synthetic_only": self.synthetic_only,
            "free_dev_profile": self.free_dev_profile,
            "post_run_review": self.enable_post_run_review,
            "spec_version": self.spec_version,
            "threshold_version": self.threshold_version,
            "key_configured": bool(self.api_key),
            "note": self.note,
        }

    @staticmethod
    def from_settings(settings: Any) -> "DecisionConfig":
        """Read the typed SettingsStore (never a second configuration system)."""
        if settings is None:
            return DecisionConfig()
        mode = str(settings.get("decision.mode", DecisionMode.OFF.value) or DecisionMode.OFF.value).strip().lower()
        provider = str(settings.get("decision.provider", DecisionProviderKind.OFF.value) or DecisionProviderKind.OFF.value).strip().lower()
        base_url = str(settings.get("decision.base_url", "") or "").strip()
        api_key = settings.secret("decision.api_key") if hasattr(settings, "secret") else ""
        if not api_key:
            # Fall back to the documented environment conventions *only* when the
            # provider was explicitly selected in the typed settings.
            if provider == DecisionProviderKind.TYPESAFE.value:
                api_key = os.environ.get("TYPESAFE_API_KEY", "")
                base_url = base_url or os.environ.get("TYPESAFE_BASE_URL", "")
            elif provider == DecisionProviderKind.CUSTOM.value:
                api_key = os.environ.get("DECISION_API_KEY", "")
                base_url = base_url or os.environ.get("DECISION_BASE_URL", "")
        return DecisionConfig(
            provider=provider,
            mode=mode,
            model=str(settings.get("decision.model", "") or ""),
            base_url=base_url,
            path=str(settings.get("decision.path", "") or ""),
            api_key=api_key,
            timeout_s=settings.float_of("decision.timeout_seconds") or 10.0,
            retries=settings.int_of("decision.retries"),
            allow_external_provider=settings.bool_of("decision.allow_external_provider"),
            unofficial=bool(settings.bool_of("decision.unofficial_provider")),
            synthetic_only=settings.bool_of("decision.synthetic_only"),
            free_dev_profile=settings.bool_of("decision.free_dev_profile"),
            enable_post_run_review=settings.bool_of("decision.post_run_review"),
            note=str(settings.get("decision.note", "") or ""),
        )


def _host(url: str) -> str:
    if not url:
        return ""
    try:
        import httpx

        return httpx.URL(url).host or ""
    except Exception:  # pragma: no cover - a malformed URL is reported, not raised
        return "invalid"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def build_decision_client(
    config: DecisionConfig,
    *,
    registry: Any = None,
    telemetry: DecisionTelemetry | None = None,
) -> tuple[Any, str]:
    """Construct the configured provider client. Returns ``(client, refusal_reason)``.

    Refusal is explicit and visible: a misconfigured provider degrades to the
    baseline path with a reason a human can read, instead of throwing inside a
    user turn.
    """
    if not config.enabled:
        return None, "decision provider is off"
    if config.provider == DecisionProviderKind.MOCK.value:
        return (
            MockDecisionClient(mode=config.mode, threshold_version=config.threshold_version, provider="mock"),
            "",
        )
    if config.provider in {DecisionProviderKind.TYPESAFE.value, DecisionProviderKind.CUSTOM.value}:
        if config.provider == DecisionProviderKind.CUSTOM.value and not config.allow_external_provider:
            return None, "custom/unofficial decision endpoints require decision.allow_external_provider=true"
        if not config.base_url:
            return None, "decision provider selected but no base_url configured"
        jev = JevConfig(
            provider=config.provider,
            base_url=config.base_url,
            path=config.path or "/v1/systemone",
            model=config.model or "jev-latest",
            api_key=config.api_key,
            timeout_s=float(config.timeout_s),
            total_budget_s=float(config.total_budget_s),
            retries=max(0, int(config.retries)),
            unofficial=config.unofficial,
            synthetic_only=config.synthetic_only,
        )
        return (
            JevDecisionClient(
                jev,
                breaker=DecisionCircuitBreaker(),
                mode=config.mode,
                threshold_version=config.threshold_version,
            ),
            "" if config.api_key else "no decision API key configured (calls will fail closed to the baseline path)",
        )
    return None, f"unknown decision provider {config.provider!r}"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


@dataclass
class DecisionRouter:
    """Orchestrates one decision screening per user turn."""

    client: Any = None
    thresholds: DecisionThresholds = field(default_factory=DecisionThresholds)
    config: DecisionConfig = field(default_factory=DecisionConfig)
    telemetry: DecisionTelemetry = field(default_factory=DecisionTelemetry)
    cache: DecisionCache = field(default_factory=DecisionCache)
    evidence_store: Any = None
    registry: Any = None
    refusal_reason: str = ""
    clock: Callable[[], float] = time.perf_counter
    _routing_questions: QuestionSet | None = field(default=None, repr=False)
    _post_run_questions: QuestionSet | None = field(default=None, repr=False)

    # --- helpers -----------------------------------------------------------
    @property
    def mode(self) -> str:
        return str(self.config.mode)

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.client is not None

    @property
    def provider(self) -> str:
        return str(getattr(self.client, "provider", "") or self.config.provider)

    @property
    def model(self) -> str:
        return str(getattr(self.client, "model", "") or self.config.model)

    def tool_contracts(self) -> list[dict[str, Any]]:
        registry = self.registry
        if registry is None:
            from poc.tool_contracts import get_registry

            registry = get_registry()
        contracts: list[dict[str, Any]] = []
        for name in registry.names():
            contract = dict(registry.get(name))
            contract["name"] = name
            contracts.append(contract)
        return contracts

    def routing_questions(self, contracts: Sequence[Mapping[str, Any]] | None = None) -> QuestionSet:
        contracts = list(contracts) if contracts is not None else self.tool_contracts()
        if self._routing_questions is None:
            self._routing_questions = build_routing_question_set(contracts)
        return self._routing_questions

    def post_run_questions(self) -> QuestionSet:
        if self._post_run_questions is None:
            self._post_run_questions = build_post_run_question_set()
        return self._post_run_questions

    def invalidate_question_cache(self) -> None:
        """Called when the tool registry or spec changes under a running process."""
        self._routing_questions = None

    # --- evidence ----------------------------------------------------------
    def _evidence(self, event_type: str, payload: Mapping[str, Any], *, execution_id: str | None, trace_id: str | None) -> None:
        store = self.evidence_store
        if store is None:
            return
        try:
            from poc.evidence import EvidenceType

            member = EvidenceType(event_type)
        except Exception:  # unknown event type must never break a turn
            return
        try:
            store.append(
                event_type=member,
                payload=dict(payload),
                execution_id=execution_id,
                trace_id=trace_id,
                policy_version=self.config.threshold_version,
            )
        except Exception:
            # Evidence is required for governance, but a store failure must not
            # convert a decision signal into a runtime crash: the telemetry
            # counter still records that something went wrong.
            self.telemetry.bump("evidence_failures")

    # --- screening ---------------------------------------------------------
    def screen(
        self,
        user_input: str,
        *,
        all_tools: Sequence[str] | None = None,
        tool_contracts: Sequence[Mapping[str, Any]] | None = None,
        write_intent: bool = False,
        read_intent_hint: bool = False,
        channel: str = "web",
        language_hint: str = "ar",
        execution_id: str | None = None,
        trace_id: str | None = None,
    ) -> DecisionOutcome:
        """Run one screening pass. Never raises; never blocks an ERP action."""
        started = self.clock()
        contracts = list(tool_contracts) if tool_contracts is not None else self.tool_contracts()
        tool_names = tuple(all_tools) if all_tools is not None else tuple(str(contract.get("name")) for contract in contracts)

        if not self.enabled:
            reason = self.refusal_reason or "decision layer disabled"
            outcome = DecisionOutcome(
                mode=DecisionMode.OFF.value,
                provider=self.config.provider,
                model=self.config.model,
                spec_version=self.config.spec_version,
                threshold_version=self.thresholds.version,
                route=RoutePlan(candidate_tools=tool_names, all_tools=tool_names, reason=reason),
                escalation=Escalation(level=ESCALATION_NONE, reasons=(reason,)),
                fallback=True,
                fallback_reason=reason,
                reasons=(reason,),
            )
            self.telemetry.record_outcome(outcome)
            return outcome

        if not tool_names:
            reason = "no tools registered; nothing to route"
            outcome = DecisionOutcome(
                mode=self.mode,
                provider=self.provider,
                model=self.model,
                spec_version=self.config.spec_version,
                threshold_version=self.thresholds.version,
                route=RoutePlan(candidate_tools=(), all_tools=(), reason=reason),
                escalation=Escalation(level=ESCALATION_NONE, reasons=(reason,)),
                fallback=True,
                fallback_reason=reason,
                reasons=(reason,),
            )
            self.telemetry.record_outcome(outcome)
            return outcome

        # --- build questions + state (allowlist-first, default-deny) -------
        questions = self.routing_questions(contracts)
        request = questions.to_request()
        try:
            state = build_routing_state(
                user_input,
                contracts,
                language_hint=language_hint,
                channel=channel,
                operation_hint="write" if write_intent else ("read" if read_intent_hint else ""),
            )
            state_hash = compute_state_hash(state, spec_version=questions.version, model=self.model)
        except DecisionError as error:
            return self._degraded(started, error.code.value, str(error), tool_names, execution_id, trace_id)

        # --- cache (read-intent routing only) ------------------------------
        cache_key = CacheKey(
            state_hash=state_hash,
            spec_version=questions.version,
            model=self.model,
            threshold_version=self.thresholds.fingerprint(),
            question_ids=questions.ids(),
            purpose=PURPOSE_ROUTING,
        )
        cached = self.cache.get(cache_key)

        self._evidence(
            "decision_request",
            {
                "purpose": PURPOSE_ROUTING,
                "provider": self.provider,
                "model": self.model,
                "spec_version": questions.version,
                "spec_hash": questions.content_hash(),
                "state_hash": state_hash,
                "questions": list(questions.ids()),
                "state_keys": sorted(state),
                "mode": self.mode,
                "threshold_version": self.thresholds.version,
                "cached": cached is not None,
            },
            execution_id=execution_id,
            trace_id=trace_id,
        )

        result: DecisionResult | None = cached
        if result is None:
            try:
                result = self.client.decide(
                    state,
                    request,
                    spec_version=questions.version,
                    state_hash=state_hash,
                    timeout_s=self.config.timeout_s,
                )
            except DecisionError as error:
                return self._degraded(started, error.code.value, str(error), tool_names, execution_id, trace_id)
            except Exception as error:  # a provider adapter must never explode into a turn
                return self._degraded(
                    started,
                    DecisionErrorCode.INTERNAL.value,
                    f"{type(error).__name__}: {error}",
                    tool_names,
                    execution_id,
                    trace_id,
                )
            if result.ok:
                cacheable, why = self.cache.is_cacheable(result, purpose=PURPOSE_ROUTING, read_intent=not write_intent)
                if cacheable:
                    self.cache.put(cache_key, replace(result, cached=True))
                else:
                    self.cache.rejects += 1

        latency_ms = (self.clock() - started) * 1000.0
        escalation = compute_escalation(result, self.thresholds, mode=self.mode, write_intent=write_intent)
        route = compute_route_plan(
            result,
            self.thresholds,
            mode=self.mode,
            all_tools=tool_names,
            write_intent=write_intent,
            read_intent_hint=read_intent_hint,
        )
        # An escalation above "watch" always wins over narrowing: scrutinity and
        # suggestion must never pull in opposite directions (plan §42).
        if escalation.changes_behaviour and route.narrowed:
            route = RoutePlan(
                candidate_tools=route.all_tools,
                all_tools=route.all_tools,
                decision_tool=route.decision_tool,
                runner_up_tool=route.runner_up_tool,
                confidence=route.confidence,
                margin=route.margin,
                prefer_clarification=route.prefer_clarification,
                reason="escalation active; the tool set was not narrowed",
            )

        outcome = DecisionOutcome(
            mode=self.mode,
            provider=str(result.provider if result is not None else self.config.provider),
            model=str(result.model if result is not None else self.config.model),
            spec_version=questions.version,
            state_hash=state_hash,
            threshold_version=self.thresholds.version,
            route=route,
            escalation=escalation,
            latency_ms=latency_ms,
            error_code=(result.error.code.value if result is not None and result.error is not None else ""),
            fallback=bool(result is None or not result.ok),
            fallback_reason=(result.error.message if result is not None and result.error is not None else ""),
            result=result,
            cached=bool(result is not None and result.cached),
            reasons=tuple(escalation.reasons) + ((route.reason,) if route.reason else ()),
        )
        self.telemetry.record_outcome(outcome)

        if result is not None and result.ok:
            self._evidence(
                "decision_response",
                {
                    "provider": result.provider,
                    "model": result.model,
                    "request_id": result.request_id,
                    "spec_version": result.spec_version,
                    "state_hash": result.state_hash,
                    "threshold_version": result.threshold_version,
                    "latency_ms": round(result.latency_ms, 3),
                    "usage": result.usage.to_dict(),
                    "answers": _summarize_answers(result),
                    "unknown_answers": list(result.unknown_answers),
                    "cached": result.cached,
                    "mode": self.mode,
                },
                execution_id=execution_id,
                trace_id=trace_id,
            )
            self._evidence(
                "decision_routing",
                {
                    "strategy": route.strategy,
                    "candidate_tools": list(route.candidate_tools),
                    "decision_tool": route.decision_tool,
                    "runner_up_tool": route.runner_up_tool,
                    "confidence": route.confidence,
                    "margin": route.margin,
                    "prefer_clarification": route.prefer_clarification,
                    "escalation_level": escalation.level,
                    "proposed_level": escalation.proposed_level,
                    "reason": route.reason,
                },
                execution_id=execution_id,
                trace_id=trace_id,
            )
        elif result is not None and result.error is not None:
            self._evidence(
                "decision_response",
                {
                    "provider": result.provider,
                    "model": result.model,
                    "error": result.error.to_dict(),
                    "state_hash": result.state_hash,
                    "mode": self.mode,
                },
                execution_id=execution_id,
                trace_id=trace_id,
            )
        if escalation.escalated:
            self._evidence(
                "decision_escalation",
                {
                    "level": escalation.level,
                    "proposed_level": escalation.proposed_level,
                    "reasons": list(escalation.reasons),
                    "signals": dict(escalation.signals),
                },
                execution_id=execution_id,
                trace_id=trace_id,
            )
        return outcome

    def _degraded(
        self,
        started: float,
        error_code: str,
        message: str,
        tool_names: Sequence[str],
        execution_id: str | None,
        trace_id: str | None,
    ) -> DecisionOutcome:
        latency_ms = (self.clock() - started) * 1000.0
        outcome = DecisionOutcome(
            mode=self.mode,
            provider=self.provider,
            model=self.model,
            spec_version=self.config.spec_version,
            threshold_version=self.thresholds.version,
            route=RoutePlan(candidate_tools=tuple(tool_names), all_tools=tuple(tool_names), reason="decision signal unavailable"),
            escalation=Escalation(level=ESCALATION_NONE, reasons=(f"decision call failed: {error_code}",)),
            latency_ms=latency_ms,
            error_code=error_code,
            fallback=True,
            fallback_reason=message[:200],
            reasons=(f"decision call failed: {error_code}",),
        )
        self.telemetry.record_outcome(outcome)
        self._evidence(
            "decision_response",
            {
                "provider": self.config.provider,
                "model": self.config.model,
                "error": {"code": error_code, "message": message[:200]},
                "mode": self.mode,
            },
            execution_id=execution_id,
            trace_id=trace_id,
        )
        return outcome

    # --- disagreement ------------------------------------------------------
    def record_disagreement(self, outcome: DecisionOutcome, llm_tool: str | None, *, execution_id: str | None = None, trace_id: str | None = None) -> DecisionOutcome:
        """Compare the signal with the LLM's chosen tool and record the difference.

        Never discards a disagreement silently (plan §12, §68). Resolution always
        prefers the existing server-owned path: a disagreement never executes,
        never rejects on its own, and never changes authorization.
        """
        if outcome.result is None or not outcome.result.ok:
            return outcome
        choice = outcome.result.choice(QuestionId.TOOL_ROUTE.value)
        if choice is None:
            return outcome
        disagreement = compare_with_llm(
            choice.choice,
            llm_tool,
            confidence=choice.confidence,
            margin=choice.margin,
            thresholds=self.thresholds,
            escalated=outcome.escalation.escalated,
            narrowed=outcome.route.narrowed,
        )
        if disagreement is None:
            return outcome
        updated = replace(outcome, disagreement=disagreement)
        self.telemetry.bump("disagreements")
        self.telemetry.reason(disagreement.resolution)
        self._evidence(
            "decision_disagreement",
            disagreement.to_dict(),
            execution_id=execution_id,
            trace_id=trace_id,
        )
        return updated

    # --- optional post-run review -----------------------------------------
    def post_run_review(
        self,
        *,
        user_input: str,
        tool_name: str,
        operation_type: str,
        verified: bool,
        verified_outcome_code: str,
        response_class: str,
        execution_id: str | None = None,
        trace_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        """Optional semantic review of a completed execution (plan §17).

        Returns ``None`` when disabled. The result is *review metadata only*:
        it can add a "needs review" notice and an anomaly counter, and it can
        never alter the verified ERP truth or the user-visible claim.
        """
        if not self.enabled or not self.config.enable_post_run_review:
            return None
        questions = self.post_run_questions()
        state = {
            "intent_summary": summarize_intent(user_input),
            "tool_name": tool_name,
            "operation_type": operation_type,
            "verified_outcome_code": verified_outcome_code,
            "verification_status": "verified" if verified else "unverified",
            "response_class": response_class,
        }
        try:
            result = self.client.decide(
                state,
                questions.to_request(),
                spec_version=questions.version,
                timeout_s=self.config.timeout_s,
            )
        except Exception as error:  # pragma: no cover - defensive
            self.telemetry.bump("post_run_failures")
            # ``authority`` is repeated on every review payload, including the
            # failure paths, so a caller can never mistake review metadata for a
            # statement about the ERP truth (plan §17).
            return {"authority": "signal_only", "ok": False, "error": f"{type(error).__name__}"}
        if not result.ok:
            self.telemetry.bump("post_run_failures")
            return {"authority": "signal_only", "ok": False, "error": result.error.code.value if result.error else ""}
        mismatch = result.noul(QuestionId.CLAIM_MATCHES_OUTCOME.value)
        anomaly = result.noul(QuestionId.TRACE_ANOMALOUS.value)
        review = {
            "authority": "signal_only",
            "ok": True,
            "provider": result.provider,
            "model": result.model,
            "state_hash": result.state_hash,
            "claim_mismatch_probability": mismatch.noul if mismatch else None,
            "claim_mismatch": bool(mismatch and self.thresholds.claim_mismatch(mismatch.noul)),
            "anomaly_probability": anomaly.noul if anomaly else None,
            "anomaly": bool(anomaly and self.thresholds.trace_anomaly(anomaly.noul)),
            "requires_review": bool(
                (mismatch and self.thresholds.claim_mismatch(mismatch.noul)) or (anomaly and self.thresholds.trace_anomaly(anomaly.noul))
            ),
        }
        if review["requires_review"]:
            self.telemetry.bump("post_run_reviews")
        self._evidence(
            "decision_response",
            {"purpose": "post_run_review", **review},
            execution_id=execution_id,
            trace_id=trace_id,
        )
        return review

    # --- observability -----------------------------------------------------
    def health(self) -> dict[str, Any]:
        client_health: Mapping[str, Any] = {}
        try:
            if self.client is not None:
                client_health = dict(self.client.health())
        except Exception:  # pragma: no cover - health must never raise
            client_health = {"error": "health_unavailable"}
        return {
            "enabled": self.enabled,
            "config": self.config.display(),
            "refusal_reason": self.refusal_reason,
            "spec_version": self.config.spec_version,
            "thresholds": self.thresholds.to_dict(),
            "cache": dict(self.cache.stats()),
            "client": client_health,
            "telemetry": self.telemetry.snapshot(),
            "authority": "signal_only",
            "authority_notice": "The decision layer cannot authorize, execute, approve, or verify. Server policy remains authoritative.",
        }

    def close(self) -> None:
        try:
            if self.client is not None:
                self.client.close()
        except Exception:  # pragma: no cover
            pass


def _summarize_answers(result: DecisionResult) -> dict[str, Any]:
    """Compact, secret-free answer summary for evidence payloads."""
    summary: dict[str, Any] = {}
    for question_id, answer in result.answers.items():
        if hasattr(answer, "choice"):
            summary[question_id] = {
                "choice": answer.choice,
                "confidence": round(float(answer.confidence), 6),
                "margin": answer.margin,
            }
        elif hasattr(answer, "score"):
            summary[question_id] = {"score": round(float(answer.score), 6), "confidence": round(float(answer.confidence), 6)}
        elif hasattr(answer, "noul"):
            summary[question_id] = {"noul": round(float(answer.noul), 6)}
    return summary


def build_router(
    *,
    settings: Any = None,
    registry: Any = None,
    evidence_store: Any = None,
    client: Any = None,
    config: DecisionConfig | None = None,
    thresholds: DecisionThresholds | None = None,
    telemetry: DecisionTelemetry | None = None,
    cache: DecisionCache | None = None,
) -> DecisionRouter:
    """Composition helper: settings → config → client → router."""
    resolved_config = config or DecisionConfig.from_settings(settings)
    resolved_thresholds = thresholds or (DecisionThresholds.from_settings(settings) if settings is not None else DecisionThresholds())
    resolved_telemetry = telemetry or DecisionTelemetry(
        enabled=resolved_config.enabled,
        mode=resolved_config.mode,
        provider=resolved_config.provider,
        model=resolved_config.model,
        spec_version=resolved_config.spec_version,
        threshold_version=resolved_thresholds.version,
    )
    resolved_cache = cache or DecisionCache(
        enabled=bool(resolved_config.enabled and getattr(settings, "bool_of", lambda _k: False)("decision.cache_read_routes")),
        ttl_seconds=int(getattr(settings, "int_of", lambda _k: 0)("decision.cache_ttl_seconds") or 300)
        if settings is not None
        else 300,
    )
    refusal = ""
    if client is None and resolved_config.enabled:
        client, refusal = build_decision_client(resolved_config, registry=registry, telemetry=resolved_telemetry)
    elif client is not None:
        resolved_config = replace(resolved_config, provider=getattr(client, "provider", resolved_config.provider))
    return DecisionRouter(
        client=client,
        thresholds=resolved_thresholds,
        config=resolved_config,
        telemetry=resolved_telemetry,
        cache=resolved_cache,
        evidence_store=evidence_store,
        registry=registry,
        refusal_reason=refusal,
    )


__all__ = [
    "DecisionConfig",
    "DecisionRouter",
    "build_decision_client",
    "build_router",
    "PURPOSE_ROUTING",
    "risk_signal",
]
