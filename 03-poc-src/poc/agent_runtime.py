"""Agent Runtime orchestration layer for the POC.

Translates Arabic natural-language requests into validated LLM tool calls,
constructs ToolGatewayRequest, and delegates ALL execution to the existing
ToolGateway. Performs no ERP operations, no database access, and no
authorization decisions.

Security invariant: LLM = Authority is never assumed. Model output is
untrusted input validated against the server-owned ToolRegistry before it
reaches the Gateway.

Harness responsibilities added on 2026-09-19 (behaviour, not authority):

- multi-turn memory (history is context only — never a policy input),
- stage events + timings for live progress and the eval harness,
- a structured :class:`~poc.answer.Answer` compiled from the gateway decision,
  so the cockpit shows an information card instead of a wall of prose,
- an optional, bounded schema-repair turn (off by default),
- proposal amendment: edit-then-confirm revalidates policy, schema, and hash.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import jsonschema

from poc.answer import Answer
from poc.responder import compose_answer
from poc.gateway import (
    ACCEPTED,
    CONFIRMATION_REQUIRED,
    CONFLICT,
    DENIED,
    GatewayResult,
    IN_PROGRESS,
    REPLAY,
    ToolGateway,
    ToolGatewayRequest,
)
from poc.llm_client import (
    LLMClientProtocol,
    LLMMessage,
    LLMProviderError,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)
from poc.prompts import build_composer_prompt, build_system_prompt
from poc.tool_contracts import ToolRegistry, get_registry
from poc.errors import translate_error, translate_odoo_exception, StructuredError
from poc.decision.policy import (
    ESCALATION_CLARIFY,
    ESCALATION_QUARANTINE,
    ESCALATION_STEP_UP,
    ESCALATION_WATCH,
    DecisionOutcome,
)

TOOL_CALL = "tool_call"
TEXT_ONLY = "text_only"
UNKNOWN_TOOL_REJECTED = "unknown_tool_rejected"
MALFORMED_TOOL_CALL = "malformed_tool_call"
INVALID_ARGUMENTS = "invalid_arguments"
MULTIPLE_TOOL_CALLS = "multiple_tool_calls"
CONFIRMED_EXECUTION = "confirmed_execution"
CONFIRMATION_DECLINED = "confirmation_declined"
CONFIRMATION_AMENDED = "confirmation_amended"

# Decision-intelligence outcomes. Both are *server-owned* outcomes produced by a
# monotonic escalation, never by a provider's opinion of what should happen:
#   DECISION_QUARANTINED   — an additive security signal exceeded the quarantine
#                            threshold, so nothing was routed or executed.
#   DECISION_CLARIFICATION — an ambiguous *write* intent was not turned into a
#                            proposal; the user is asked to clarify instead.
DECISION_QUARANTINED = "decision_quarantined"
DECISION_CLARIFICATION = "decision_clarification"

# The one sentence that must accompany every public decision display. Never
# imply the layer approved, authorized, executed, or verified anything.
DECISION_AUTHORITY = "signal_only"
DECISION_AUTHORITY_NOTICE = (
    "إشارة القرار استشارية فقط — القرار النهائي للخادم (السياسة والتفويض والتأكيد)."
)


def public_decision_view(decision: DecisionOutcome | None) -> dict[str, Any]:
    """Secret-free, authority-labelled view of a turn's decision signal.

    Always includes ``authority: signal_only``. Never includes user text, raw
    answers, API keys, or anything that could be read as an authorization.
    """
    if decision is None:
        return {
            "active": False,
            "authority": DECISION_AUTHORITY,
            "notice": DECISION_AUTHORITY_NOTICE,
        }
    route = getattr(decision, "route", None)
    escalation = getattr(decision, "escalation", None)
    result = getattr(decision, "result", None)
    choice = None
    if result is not None and getattr(result, "ok", False):
        answer_obj = result.choice("tool_route")
        if answer_obj is not None:
            choice = {
                "tool": answer_obj.choice,
                "confidence": round(float(answer_obj.confidence), 4),
                "margin": answer_obj.margin,
            }
    disagreement = getattr(decision, "disagreement", None)
    return {
        "active": True,
        "mode": getattr(decision, "mode", "") or "",
        "provider": getattr(decision, "provider", "") or "",
        "model": getattr(decision, "model", "") or "",
        "latency_ms": round(float(getattr(decision, "latency_ms", 0.0) or 0.0), 1),
        "route": choice,
        "narrowed": bool(getattr(route, "narrowed", False)),
        "strategy": getattr(route, "strategy", "") or "",
        "escalation_level": getattr(escalation, "level", "") or "",
        "fallback": bool(getattr(decision, "fallback", False)),
        "disagreement": disagreement.to_dict() if disagreement is not None else None,
        "authority": DECISION_AUTHORITY,
        "notice": DECISION_AUTHORITY_NOTICE,
    }

# Arabic response templates keyed by gateway result status. The Answer composer
# is the primary renderer; these remain the one-line fallback used when a
# payload shape predates the composer (and are asserted by the truthfulness
# tests: "تم" on accepted, "تأكيد" on pending, "مفيش صلاحية" on denied).
_RESPONSE_TEMPLATES = {
    ACCEPTED: "تمام يا فندم، تم تجهيز طلبك بنجاح:\n{detail}",
    CONFIRMATION_REQUIRED: "الطلب ده محتاج تأكيد من حضرتك قبل ما ننفذه. رقم الطلب: {detail}",
    CONFLICT: "عفواً، فيه تعارض: العملية دي اتنفذت أو لسه بتتنفيذ في طلب تاني.",
    IN_PROGRESS: "العملية لسه شغالة حالياً، ثواني وارجع لحضرتك.",
    REPLAY: "النتيجة دي من عملية سابقة مطابقة.",
    DENIED: "بعتذر لحضرتك، مفيش صلاحية لتنفيذ العملية دي:\n{detail}",
    CONFIRMED_EXECUTION: "تم تنفيذ العملية بنجاح بعد تأكيد حضرتك:\n{detail}",
    CONFIRMATION_DECLINED: "تمام، تم إلغاء العملية بناءً على طلبك.",
    DECISION_QUARANTINED: "الطلب ده اتحوّل لمراجعة أمنية ومحتاج تأكيد بشري قبل أي تنفيذ. مفيش أي عملية اتنفذت.",
    DECISION_CLARIFICATION: "محتاج توضيح بسيط قبل ما أنفذ أي حاجة: تقصد إيه بالظبط؟",
}

_READ_TOOLS = frozenset({"customer.search", "customer.get", "product.search", "sales.order.get"})

# Cheap deterministic operation-kind hints. They are used ONLY to decide whether
# the decision layer may narrow the LLM's candidate tool set — never as an
# authorization input. A conflict between a decision signal and one of these
# hints disables narrowing (see poc.decision.policy.compute_route_plan).
_WRITE_INTENT_TOKENS = (
    "اعمل", "انشئ", "أنشئ", "اضف", "أضف", "سجل", "اطلق", "نفذ", "create", "new order", "update", "عدل", "احذف", "delete",
)
_WRITE_INTENT_NOUNS = ("امر بيع", "أمر بيع", "اوردر", "طلب بيع", "order", "فاتوره", "فاتورة")

_INTERNAL_LEAK_MARKERS = (
    "Analyze User Input",
    "Check Guidelines",
    "Identify Tool",
    "Execute Tool Call",
    "Language:",
    "Translation/Meaning:",
    "Key entities:",
    "Thinking:",
    "Reasoning:",
)
_JSON_BLOCK_RE = re.compile(r"(\{(?:[^{}]|\{[^{}]*\})*\})", re.DOTALL)

StageSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class AgentResult:
    """Truthful result passed back to the caller."""

    outcome: str
    response_ar: str
    gateway_result: GatewayResult | None
    tool_call: LLMToolCall | None = None
    error: str | None = None
    structured_error_data: StructuredError | None = None
    answer: Answer | None = None
    stages: tuple[dict[str, Any], ...] = ()
    timings: Mapping[str, Any] = field(default_factory=dict)
    engine: str = "unknown"
    repaired: bool = False
    #: Decision-intelligence trace for this turn (None when the layer is off).
    #: Purely observational: it explains the route, it never authorizes one.
    decision: DecisionOutcome | None = None

    @property
    def structured_error(self):
        if self.structured_error_data is not None:
            return self.structured_error_data
        if self.gateway_result is not None:
            return self.gateway_result.structured_error
        if self.outcome == "llm_error":
            return translate_error("llm_error")
        if self.outcome == UNKNOWN_TOOL_REJECTED:
            return translate_error("UNKNOWN_TOOL")
        if self.outcome in {MALFORMED_TOOL_CALL, INVALID_ARGUMENTS}:
            return translate_error("INVALID_ARGUMENTS")
        return None


def _sanitize_leaked_reasoning(text: str) -> str:
    """Strip chain-of-thought headers a weak endpoint sometimes emits as prose."""
    if not text:
        return text
    if not any(marker in text for marker in _INTERNAL_LEAK_MARKERS):
        return text
    kept = [
        line
        for line in text.splitlines()
        if not any(marker in line for marker in _INTERNAL_LEAK_MARKERS)
    ]
    cleaned = "\n".join(kept).strip()
    return cleaned or "تمام يا فندم، أنا تحت أمرك. أقدر أساعدك في أي استفسار أو عملية في النظام."


def _read_result_summary(result: Mapping[str, Any]) -> str:
    """Compact Arabic summary for executed read results; '' otherwise.

    Kept as the template fallback for payloads the composer cannot shape.
    """
    if not isinstance(result, dict) or not result.get("success"):
        return ""
    for key, label in (("customers", "عميل"), ("products", "منتج")):
        rows = result.get(key)
        if isinstance(rows, list):
            if not rows:
                return f"مفيش {label}s مطابقة للبحث."
            head = [f"- {row.get('name', '?')}" for row in rows[:5] if isinstance(row, Mapping)]
            more = f"\nوفي {len(rows) - 5} صنف تانيين." if len(rows) > 5 else ""
            return f"لقيت {len(rows)} {label}:\n" + "\n".join(head) + more
    return ""


class AgentRuntimeError(RuntimeError):
    """Raised when Agent Runtime cannot produce a valid gateway request."""


class AgentRuntime:
    """Single orchestration boundary between the LLM and the ToolGateway."""

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        gateway: ToolGateway,
        registry: ToolRegistry | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        odoo_client_factory: Callable[[str, str], Any] | None = None,
        *,
        settings: Any = None,
        dialect: str = "ar-EG",
        response_style: str = "balanced",
        history_turns: int = 0,
        enable_narrative: bool = False,
        max_repair_turns: int = 0,
        decision_router: Any = None,
        evidence_store: Any = None,
    ) -> None:
        self.llm_client = llm_client
        self.gateway = gateway
        self.registry = registry or get_registry()
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.odoo_client_factory = odoo_client_factory
        self.settings = settings
        self.dialect = dialect
        self.response_style = response_style
        self.history_turns = int(history_turns or 0)
        self.enable_narrative = bool(enable_narrative)
        self.max_repair_turns = int(max_repair_turns or 0)
        # Additive decision intelligence (Jev). ``None`` or a router in ``off``
        # mode means MIZAN behaves exactly like the un-integrated baseline.
        self.decision_router = decision_router
        self.evidence_store = evidence_store

    # --- configuration plumbing (settings panel) --------------------------------
    @property
    def engine(self) -> str:
        return str(getattr(self.llm_client, "provider", "unknown")) or "unknown"

    def reconfigure(self, **changes: Any) -> None:
        """Hot-apply cockpit settings (never touches identity or the gateway)."""
        allowed = {"dialect", "response_style", "history_turns", "enable_narrative", "max_repair_turns"}
        for key, value in changes.items():
            if key == "llm_client" and value is not None:
                self.llm_client = value
                continue
            if key in allowed and value is not None:
                setattr(self, key, value)

    def set_llm_client(self, client: LLMClientProtocol) -> None:
        self.llm_client = client

    def set_decision_router(self, router: Any) -> None:
        """Hot-swap the decision layer. Closes the previous router if it differs.

        Identity, the gateway, and authorization are untouched. A ``None``
        router is the off path (identical to the un-integrated baseline).
        """
        previous = self.decision_router
        self.decision_router = router
        if previous is not None and previous is not router:
            closer = getattr(previous, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # a stale provider must not block reconfigure
                    pass

    # --- prompt / tools -------------------------------------------------------
    def _tool_definitions(self) -> list[LLMToolDefinition]:
        """Convert server-owned registry contracts into LLM tool format."""
        definitions: list[LLMToolDefinition] = []
        for name in self.registry.names():
            contract = self.registry.get(name)
            definitions.append(
                LLMToolDefinition(
                    name=name,
                    description=contract.get("description", ""),
                    input_schema=contract["inputSchema"],
                )
            )
        return definitions

    def _build_system_prompt(self) -> str:
        extra = ""
        style = (self.settings.get("agent.response_style", self.response_style) if self.settings else self.response_style)
        dialect = (self.settings.get("agent.dialect", self.dialect) if self.settings else self.dialect)
        return build_system_prompt(tool_names=self.registry.names(), dialect=str(dialect), response_style=str(style), extra=extra)

    # --- validation -----------------------------------------------------------
    def _validate_tool_call(self, call: LLMToolCall) -> tuple[str | None, str | None, str]:
        """Validate model output against the server-owned registry.

        Returns ``(version, error, reason)`` — ``reason`` is the jsonschema
        message used by the optional repair turn.
        """
        if not isinstance(call.name, str) or not call.name:
            return None, MALFORMED_TOOL_CALL, "the tool call has no name"
        try:
            contract = self.registry.get(call.name)
        except KeyError:
            return None, UNKNOWN_TOOL_REJECTED, f"'{call.name}' is not in the tool registry"
        version = contract["tool_version"]
        args = call.arguments
        if not isinstance(args, dict):
            return None, MALFORMED_TOOL_CALL, "arguments must be an object"
        schema = contract["inputSchema"]
        try:
            jsonschema.validate(instance=args, schema=schema)
        except jsonschema.ValidationError as error:
            return None, INVALID_ARGUMENTS, f"{error.json_path}: {error.message}"
        return version, None, ""

    # --- turn execution -------------------------------------------------------
    def process(
        self,
        user_input: str,
        *,
        history: list[dict[str, str]] | None = None,
        on_stage: StageSink | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> AgentResult:
        """Translate one Arabic NL request through LLM → validation → gateway."""
        started = time.perf_counter()
        stages: list[dict[str, Any]] = []

        def stage(name: str, **extra: Any) -> None:
            payload = {"stage": name, "t_ms": round((time.perf_counter() - started) * 1000, 1), **extra}
            stages.append(payload)
            if on_stage is not None:
                try:
                    on_stage(name, payload)
                except Exception:  # a dead SSE client cannot break the turn
                    pass

        stage("intake", chars=len(user_input or ""))
        system = self._build_system_prompt()
        tools = self._tool_definitions()

        # Decision screening runs BEFORE the language model and before any
        # gateway call. It can narrow, escalate, or refuse to route — it can
        # never authorize, execute, or verify anything (plan §10, §42).
        decision = self._screen_decision(user_input, tools, stage)
        blocked = self._decision_gate(user_input, decision, stage)
        if blocked is not None:
            timings = dict(blocked.timings or {})
            timings.setdefault("total_ms", round((time.perf_counter() - started) * 1000, 1))
            return AgentResult(
                outcome=blocked.outcome,
                response_ar=blocked.response_ar,
                gateway_result=None,
                tool_call=None,
                answer=blocked.answer,
                stages=tuple(stages),
                timings=timings,
                engine=self.engine,
                decision=decision,
            )
        tools = self._apply_route(decision, tools, stage)

        messages = self._messages(user_input, history)
        force_tool_choice = bool(self.settings.bool_of("model.force_tool_choice")) if (self.settings and self._looks_like_write(user_input)) else False
        stage("llm_call", tools=len(tools), history=len(messages) - 1, force_tool=force_tool_choice)
        llm_started = time.perf_counter()
        try:
            llm_response = self.llm_client.chat(messages=messages, system=system, tools=tools)
        except LLMProviderError as error:
            timings = self._timings(started, llm_started)
            stage("llm_error", error=str(error)[:160])
            return AgentResult(
                outcome="llm_error",
                response_ar="فيه مشكلة في الاتصال بمزود الذكاء الاصطناعي. حاول تاني.",
                gateway_result=None,
                error=str(error),
                answer=compose_answer(outcome="llm_error", timings={**timings, "engine": self.engine}),
                stages=tuple(stages),
                timings=timings,
                engine=self.engine,
                decision=decision,
            )
        llm_ms = (time.perf_counter() - llm_started) * 1000.0

        tool_calls = list(llm_response.tool_calls)
        if not tool_calls and llm_response.text:
            recovered = self._recover_tool_calls_from_text(llm_response.text, user_input)
            if recovered:
                tool_calls = recovered
                stage("json_recovery", recovered=len(recovered))
        if not tool_calls:
            cleaned = _sanitize_leaked_reasoning(llm_response.text or "")
            timings = {"llm_ms": round(llm_ms, 1), "total_ms": round((time.perf_counter() - started) * 1000, 1)}
            answer = compose_answer(
                outcome=TEXT_ONLY,
                model_text=cleaned,
                timings={**timings, "engine": self.engine},
            )
            stage("answer", kind="text")
            return AgentResult(
                outcome=TEXT_ONLY,
                response_ar=answer.to_text() or cleaned or "لم أفهم الطلب.",
                gateway_result=None,
                answer=answer,
                stages=tuple(stages),
                timings=timings,
                engine=self.engine,
                decision=decision,
            )
        if len(tool_calls) > 1:
            timings = {"llm_ms": round(llm_ms, 1)}
            return AgentResult(
                outcome=MULTIPLE_TOOL_CALLS,
                response_ar="وصل أكثر من طلب أداة في نفس الوقت. برجاء إعادة الصياغة.",
                gateway_result=None,
                answer=compose_answer(
                    outcome=MULTIPLE_TOOL_CALLS,
                    tool_name=tool_calls[0].name,
                    timings={**timings, "engine": self.engine},
                ),
                stages=tuple(stages),
                timings=timings,
                engine=self.engine,
                decision=decision,
            )

        call = tool_calls[0]
        decision = self._record_disagreement(decision, call.name, stage)
        stage("schema_validation", tool=call.name)
        version, error, reason = self._validate_tool_call(call)
        if error and self.max_repair_turns > 0:
            repaired = self._repair_tool_call(call, reason, history, on_stage, stage)
            if repaired is not None:
                call, version, error, reason = repaired
                if error is None:
                    stage("schema_validation", tool=call.name, repaired=True)
        if error:
            timings = {"llm_ms": round(llm_ms, 1)}
            return AgentResult(
                outcome=error,
                response_ar="الطلب مش صالح: الأداة أو الوسائط غير صحيحة.",
                gateway_result=None,
                tool_call=call,
                answer=compose_answer(
                    outcome=error,
                    tool_name=call.name,
                    tool_arguments=dict(call.arguments or {}),
                    timings={**timings, "engine": self.engine},
                ),
                stages=tuple(stages),
                timings=timings,
                engine=self.engine,
                decision=decision,
            )

        stage("authz")
        request_id = str(uuid.uuid4())
        gateway_request = ToolGatewayRequest(
            request_id=request_id,
            user_id=self.user_id or "",
            tenant_id=self.tenant_id or "",
            tool_name=call.name,
            tool_version=version,
            arguments=dict(call.arguments),
            idempotency_key=None,
        )
        gateway_result: GatewayResult | None = None
        gw_started = time.perf_counter()
        try:
            contract = self.registry.get(call.name)
            odoo_client = None
            if contract["readOnly"] and self.odoo_client_factory is not None:
                odoo_client = self.odoo_client_factory(self.user_id or "", self.tenant_id or "")
            if odoo_client is not None:
                gateway_result = self.gateway.handle_request(gateway_request, odoo_client=odoo_client)
            else:
                gateway_result = self.gateway.handle_request(gateway_request)
        except Exception as error:
            structured = translate_odoo_exception(error)
            stage("erp_error", detail=str(error)[:160])
            timings = {"llm_ms": round(llm_ms, 1), "gateway_ms": round((time.perf_counter() - gw_started) * 1000, 1)}
            answer = compose_answer(
                outcome="erp_error",
                structured_error=structured,
                tool_name=call.name,
                timings=timings,
            )
            return AgentResult(
                outcome="erp_error",
                response_ar=answer.to_text() or "حصلت مشكلة غير متوقعة أثناء معالجة الطلب.",
                gateway_result=gateway_result,
                tool_call=call,
                error=structured.message,
                structured_error_data=structured,
                answer=answer,
                stages=tuple(stages),
                timings=timings,
                engine=self.engine,
                decision=decision,
            )
        stage("execute", status=gateway_result.status, tool=gateway_result.tool_name)
        if gateway_result.proposal is not None:
            stage("await_signature", proposal_id=str(gateway_result.proposal.get("proposal_id")))
        elif gateway_result.status == ACCEPTED:
            stage("verification", verified=bool((gateway_result.result or {}).get("verified")))
        stage("audit", audit_id=gateway_result.audit_id)
        answer = compose_answer(
            outcome=TOOL_CALL,
            gateway_result=gateway_result,
            model_text=llm_response.text,
            tool_name=call.name,
            tool_arguments=dict(call.arguments or {}),
            timings={
                "llm_ms": round(llm_ms, 1),
                "gateway_ms": round((time.perf_counter() - gw_started) * 1000, 1),
                "engine": self.engine,
            },
        )
        if answer.status != "text_only" and llm_response.text:
            answer.with_analysis(str(llm_response.text).strip())
        result = AgentResult(
            outcome=TOOL_CALL,
            response_ar=self._response_text(gateway_result, answer),
            gateway_result=gateway_result,
            tool_call=call,
            answer=answer,
            stages=tuple(stages),
            timings={"llm_ms": round(llm_ms, 1), "gateway_ms": round((time.perf_counter() - gw_started) * 1000, 1)},
            engine=self.engine,
            decision=decision,
        )
        self._attach_decision_governance(answer, decision)
        if self.enable_narrative:
            result = self._attach_narrative(result, gateway_result, answer, stages, stage, started, user_input=user_input, on_delta=on_delta)
        stages_meta = dict(result.timings or {})
        stages_meta["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return AgentResult(
            outcome=result.outcome,
            response_ar=result.response_ar,
            gateway_result=result.gateway_result,
            tool_call=result.tool_call,
            error=result.error,
            structured_error_data=result.structured_error_data,
            answer=result.answer,
            stages=tuple(stages),
            timings=stages_meta,
            engine=result.engine,
            repaired=result.repaired,
            decision=decision,
        )

    # --- decision intelligence (additive signal only) -------------------------
    def decision_health(self) -> dict[str, Any]:
        """Provider-neutral decision-layer health for telemetry surfaces."""
        if self.decision_router is None:
            return {"enabled": False, "mode": "off", "provider": "off", "authority": "signal_only"}
        try:
            return dict(self.decision_router.health())
        except Exception as error:  # pragma: no cover - health must never raise
            return {"enabled": False, "error": type(error).__name__, "authority": "signal_only"}

    def _decision_on(self) -> bool:
        router = self.decision_router
        return bool(router is not None and getattr(router, "enabled", False))

    def _write_intent_hint(self, text: str) -> bool:
        """Cheap deterministic hint about the *kind* of operation requested.

        Used only to decide whether a decision signal may narrow the tool set.
        It never authorizes, and it is never a policy input.
        """
        probe = (text or "").lower()
        if "sales.order.create" in probe:
            return True
        verb = any(token in probe for token in _WRITE_INTENT_TOKENS)
        noun = any(token in probe for token in _WRITE_INTENT_NOUNS)
        if verb and noun:
            return True
        # «اعمل ... من المنتج 55 لعدد 2» carries the write verb and the product
        # noun but no explicit order word; the runtime already treats that shape
        # as a write, so the hint must agree or narrowing would hide the tool.
        return verb and any(token in probe for token in ("منتج", "صنف", "product")) and bool(
            re.search(r"\d+", probe)
        )

    def _screen_decision(self, user_input: str, tools: list[LLMToolDefinition], stage: Callable[..., None]) -> DecisionOutcome | None:
        if not self._decision_on():
            return None
        router = self.decision_router
        write_intent = self._write_intent_hint(user_input)
        stage("decision_screening", provider=getattr(router, "provider", ""), mode=getattr(router, "mode", ""), write_intent=write_intent)
        started = time.perf_counter()
        try:
            outcome = router.screen(
                user_input,
                all_tools=[definition.name for definition in tools],
                write_intent=write_intent,
                read_intent_hint=not write_intent,
                channel="web",
            )
        except Exception as error:  # a decision outage is never a MIZAN outage
            stage("decision_error", error=type(error).__name__)
            return None
        stage(
            "decision_done",
            provider=getattr(outcome, "provider", ""),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            strategy=getattr(getattr(outcome, "route", None), "strategy", ""),
            escalation=getattr(getattr(outcome, "escalation", None), "level", ""),
            fallback=bool(getattr(outcome, "fallback", False)),
        )
        return outcome

    def _decision_gate(self, user_input: str, decision: DecisionOutcome | None, stage: Callable[..., None]) -> AgentResult | None:
        """Apply the *only* two behaviours a decision signal may force.

        Both are monotonic (they add scrutiny; they never remove a control) and
        both are server-owned outcomes. Everything else the signal produces is a
        hint: a narrowed candidate set, an escalation notice, or a measurement.
        """
        if decision is None:
            return None
        level = getattr(getattr(decision, "escalation", None), "level", "")
        reasons = tuple(getattr(getattr(decision, "escalation", None), "reasons", ()) or ())

        if level == ESCALATION_QUARANTINE:
            stage("decision_quarantine", reasons=list(reasons))
            answer = compose_answer(outcome=DECISION_QUARANTINED, timings={**dict(self._decision_timings(decision)), "engine": self.engine})
            self._attach_decision_governance(answer, decision)
            return AgentResult(
                outcome=DECISION_QUARANTINED,
                response_ar=_RESPONSE_TEMPLATES[DECISION_QUARANTINED],
                gateway_result=None,
                answer=answer,
                timings=self._decision_timings(decision),
                engine=self.engine,
                decision=decision,
            )

        if level == ESCALATION_CLARIFY and self._write_intent_hint(user_input):
            # An ambiguous *write* is never turned into a proposal. A read stays
            # on the existing path (search → disambiguation), which is exactly
            # the runtime clarification logic the plan asks us to keep (plan §14).
            stage("decision_clarification", reasons=list(reasons))
            answer = compose_answer(outcome=DECISION_CLARIFICATION, timings={**dict(self._decision_timings(decision)), "engine": self.engine})
            self._attach_decision_governance(answer, decision)
            return AgentResult(
                outcome=DECISION_CLARIFICATION,
                response_ar=_RESPONSE_TEMPLATES[DECISION_CLARIFICATION],
                gateway_result=None,
                answer=answer,
                timings=self._decision_timings(decision),
                engine=self.engine,
                decision=decision,
            )
        return None

    def _apply_route(self, decision: DecisionOutcome | None, tools: list[LLMToolDefinition], stage: Callable[..., None]) -> list[LLMToolDefinition]:
        """Narrow the LLM's candidate set when — and only when — the signal is confident."""
        if decision is None:
            return tools
        route = getattr(decision, "route", None)
        if route is None or not getattr(route, "narrowed", False):
            return tools
        allowed = set(route.candidate_tools)
        narrowed = [definition for definition in tools if definition.name in allowed]
        if len(narrowed) < 2 or len(narrowed) >= len(tools):
            return tools
        stage("decision_route", strategy="narrowed", tools=[definition.name for definition in narrowed], reason=route.reason)
        return narrowed

    def _record_disagreement(self, decision: DecisionOutcome | None, llm_tool: str | None, stage: Callable[..., None]) -> DecisionOutcome | None:
        if decision is None:
            return None
        router = self.decision_router
        if router is None or not hasattr(router, "record_disagreement"):
            return decision
        try:
            updated = router.record_disagreement(decision, llm_tool)
        except Exception:  # pragma: no cover - measurement must not break a turn
            return decision
        if updated is not decision and getattr(updated, "disagreement", None) is not None:
            stage("decision_disagreement", **dict(updated.disagreement.to_dict()))
        return updated

    @staticmethod
    def _decision_timings(decision: DecisionOutcome | None) -> dict[str, Any]:
        if decision is None:
            return {}
        return {"decision_ms": round(float(getattr(decision, "latency_ms", 0.0) or 0.0), 1)}

    @staticmethod
    def _attach_decision_governance(answer: Answer | None, decision: DecisionOutcome | None) -> None:
        """Expose the route signal to the UI without ever implying authority."""
        if answer is None or decision is None:
            return
        try:
            answer.governance["decision"] = public_decision_view(decision)
        except Exception:  # pragma: no cover - display metadata must never break a turn
            return

    # --- helpers --------------------------------------------------------------
    def _messages(self, user_input: str, history: list[dict[str, str]] | None) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        limit = self.history_turns
        if self.settings is not None:
            limit = int(self.settings.int_of("agent.memory_turns") or 0)
        if history and limit > 0:
            allowed_roles = {"user", "assistant"}
            trimmed = [row for row in history if str(row.get("role")) in allowed_roles and str(row.get("content") or "").strip()]
            for row in trimmed[-limit:]:
                messages.append(LLMMessage(role=str(row["role"]), content=str(row["content"])[:4000]))
        messages.append(LLMMessage(role="user", content=user_input))
        return messages

    def _looks_like_write(self, text: str) -> bool:
        """Cheap pre-gate for force-tool-choice: writes must never hesitate."""
        if self.settings is None:
            return False
        mode = str(self.settings.get("model.force_tool_choice", "off") or "off").lower()
        if mode == "off":
            return False
        probe = (text or "").lower()
        return any(
            token in probe
            for token in ("اعمل", "انشئ", "أنشئ", "أضف", "اضف", "سجل", "create", "order", "أمر", "طلب بيع", "حذف", "عدل", "update")
        )

    def _recover_tool_calls_from_text(self, text: str, user_input: str) -> list[LLMToolCall]:
        """Salvage a tool call an endpoint printed as JSON text instead of emitting."""
        recovered: list[LLMToolCall] = []
        for raw_json in _JSON_BLOCK_RE.findall(text):
            try:
                parsed = json.loads(raw_json)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            if "customer_id" in parsed and "lines" in parsed:
                recovered.append(LLMToolCall(name="sales.order.create", arguments=parsed, call_id=str(uuid.uuid4())))
                break
            if "query" in parsed:
                is_product = "product" in text.lower() or "منتج" in user_input or "مخزون" in user_input
                recovered.append(
                    LLMToolCall(name="product.search" if is_product else "customer.search", arguments=parsed, call_id=str(uuid.uuid4()))
                )
                break
            if "order_id" in parsed:
                recovered.append(LLMToolCall(name="sales.order.get", arguments=parsed, call_id=str(uuid.uuid4())))
                break
            if "customer_id" in parsed and set(parsed) <= {"customer_id"}:
                recovered.append(LLMToolCall(name="customer.get", arguments=parsed, call_id=str(uuid.uuid4())))
                break
        return recovered

    def _repair_tool_call(
        self,
        call: LLMToolCall,
        reason: str,
        history: list[dict[str, str]] | None,
        on_stage: StageSink | None,
        stage: Callable[..., None],
    ) -> tuple[LLMToolCall, str | None, str | None, str] | None:
        """One bounded schema-repair turn; returns None to keep the failure."""
        if call.name not in self.registry.names():
            return None  # unknown tools are never "repaired" — that is fail-closed
        correction = (
            f"Your previous tool call was rejected by the gateway schema validator: {reason}. "
            f"Re-issue exactly one corrected call for the user's request using the '{call.name}' schema. "
            "Do not explain; only call the tool."
        )
        messages = self._messages(correction, history)
        stage("repair_attempt", tool=call.name, reason=reason[:120])
        try:
            response = self.llm_client.chat(messages=messages, system=self._build_system_prompt(), tools=self._tool_definitions())
        except Exception:
            return None
        if not response.tool_calls:
            return None
        candidate = response.tool_calls[0]
        version, error, _reason = self._validate_tool_call(candidate)
        if error:
            return None
        return candidate, version, None, ""

    def _timings(self, started: float, llm_started: float) -> dict[str, Any]:
        return {"llm_ms": round((time.perf_counter() - llm_started) * 1000, 1), "total_ms": round((time.perf_counter() - started) * 1000, 1)}

    def _attach_narrative(
        self,
        result: AgentResult,
        gateway_result: GatewayResult,
        answer: Answer,
        stages: list[dict[str, Any]],
        stage: Callable[..., None],
        started: float,
        *,
        user_input: str = "",
        on_delta: Callable[[str], None] | None = None,
    ) -> AgentResult:
        """Optional second pass: verified result → Arabic analysis (streamed)."""
        if gateway_result is None or gateway_result.status not in {ACCEPTED, CONFIRMATION_REQUIRED}:
            return result
        stream = getattr(self.llm_client, "stream_chat", None)
        payload = {
            "verified_result": dict(gateway_result.result or {}),
            "status": gateway_result.status,
            "tool": gateway_result.tool_name,
            "user_request": (user_input or "")[:400],
        }
        composer_prompt = build_composer_prompt(dialect=self.dialect, response_style=self.response_style)
        messages = [LLMMessage(role="user", content="verified_result = " + json.dumps(payload, ensure_ascii=False, default=str)[:6000])]
        stage("narrative_start", provider=self.engine)
        narrative_started = time.perf_counter()
        try:
            if on_delta is not None and callable(stream):
                response = stream(messages, lambda delta: on_delta(delta), system=composer_prompt, tools=None)
            else:
                response = self.llm_client.chat(messages=messages, system=composer_prompt, tools=None)
        except Exception as error:  # narrative is decoration: never fail the turn for it
            stage("narrative_skipped", error=type(error).__name__)
            return result
        text = (getattr(response, "text", "") or "").strip()
        if text:
            answer.with_analysis(text)
            stages.append({"stage": "narrative_done", "t_ms": round((time.perf_counter() - started) * 1000, 1), "chars": len(text)})
            answer.governance["narrative_engine"] = self.engine
            result_timings = dict(result.timings or {})
            result_timings["narrative_ms"] = round((time.perf_counter() - narrative_started) * 1000, 1)
            return AgentResult(
                outcome=result.outcome,
                response_ar=result.response_ar,
                gateway_result=result.gateway_result,
                tool_call=result.tool_call,
                error=result.error,
                structured_error_data=result.structured_error_data,
                answer=answer,
                stages=tuple(stages),
                timings=result_timings,
                engine=result.engine,
                repaired=result.repaired,
                decision=result.decision,
            )
        return result

    def _response_text(self, gateway_result: GatewayResult, answer: Answer) -> str:
        """Answer text: composer first, legacy template as the guarantee."""
        composed = (answer.to_text() or "").strip()
        template = _RESPONSE_TEMPLATES.get(gateway_result.status)
        # A read must never inherit the write phrasing ("تم تجهيز طلبك") just
        # because the composer's headline lacks the status keyword.
        if gateway_result.status == ACCEPTED and (gateway_result.tool_name or "") in _READ_TOOLS:
            template = "تم الفحص من Odoo:\n{detail}"
        if not template:
            return composed or f"حصلت حالة غير متوقعة: {gateway_result.status}"
        detail = ""
        if gateway_result.status == ACCEPTED:
            detail = _read_result_summary(dict(gateway_result.result or {})) or json.dumps(gateway_result.result, ensure_ascii=False, default=str)
        elif gateway_result.status == CONFIRMATION_REQUIRED:
            detail = gateway_result.reason or ""
        elif gateway_result.status == DENIED:
            detail = gateway_result.error_code or gateway_result.reason or ""
        else:
            detail = gateway_result.reason or ""
        guaranteed = template.format(detail=detail).strip()
        # The composer owns the phrasing; the template guarantees the truthfulness
        # keyword the product relies on per status (never claim more).
        required_token = {ACCEPTED: "تم", CONFIRMATION_REQUIRED: "تأكيد", DENIED: "مفيش صلاحية"}.get(gateway_result.status)
        if required_token and required_token not in composed:
            composed = f"{composed}\n{guaranteed.splitlines()[0]}".strip() if composed else guaranteed
        if (
            gateway_result.status == CONFIRMED_EXECUTION
            and gateway_result.structured_error is not None
        ):
            error = gateway_result.structured_error
            composed = f"العملية لم تكتمل بنجاح. رمز الخطأ: {error.code}. {error.message}"
        return composed or guaranteed

    # --- confirmation lifecycle ----------------------------------------------
    def confirm(self, proposal_id: str, *, on_stage: StageSink | None = None) -> AgentResult:
        """Pass an explicit user approval to the existing server-side boundary."""
        if self.odoo_client_factory is None:
            raise AgentRuntimeError("Odoo client factory is not configured")
        started = time.perf_counter()
        try:
            odoo_client = self.odoo_client_factory(self.user_id or "", self.tenant_id or "")
            gateway_result = self.gateway.confirm_and_execute(
                proposal_id=proposal_id,
                user_id=self.user_id or "",
                tenant_id=self.tenant_id or "",
                odoo_client=odoo_client,
            )
        except Exception as error:
            structured = translate_odoo_exception(error)
            return AgentResult(
                outcome="erp_error",
                response_ar="حصلت مشكلة أثناء تنفيذ العملية المؤكدة.",
                gateway_result=None,
                error=structured.message,
                structured_error_data=structured,
                answer=compose_answer(outcome="erp_error", structured_error=structured, timings={"total_ms": round((time.perf_counter() - started) * 1000, 1)}),
                timings={"total_ms": round((time.perf_counter() - started) * 1000, 1)},
                engine=self.engine,
            )
        answer = compose_answer(
            outcome=CONFIRMED_EXECUTION,
            gateway_result=gateway_result,
            timings={"total_ms": round((time.perf_counter() - started) * 1000, 1), "engine": self.engine},
        )
        if on_stage is not None:
            on_stage("execute", {"stage": "execute", "status": gateway_result.status})
            on_stage("answer", {"stage": "answer"})
        if self.enable_narrative:
            base = AgentResult(
                outcome=CONFIRMED_EXECUTION,
                response_ar=answer.to_text(),
                gateway_result=gateway_result,
                answer=answer,
                timings={"total_ms": round((time.perf_counter() - started) * 1000, 1)},
                engine=self.engine,
            )
            stages: list[dict[str, Any]] = []

            def stage(name: str, **extra: Any) -> None:
                stages.append({"stage": name, "t_ms": round((time.perf_counter() - started) * 1000, 1), **extra})
                if on_stage is not None:
                    on_stage(name, dict(extra))

            return self._attach_narrative(
                base, gateway_result, answer, stages, stage, started, on_delta=self._confirm_delta_sink(on_stage)
            )
        return AgentResult(
            outcome=CONFIRMED_EXECUTION,
            response_ar=self._response_text(gateway_result, answer) or answer.to_text(),
            gateway_result=gateway_result,
            answer=answer,
            timings={"total_ms": round((time.perf_counter() - started) * 1000, 1)},
            engine=self.engine,
        )

    @staticmethod
    def _confirm_delta_sink(on_stage: StageSink | None) -> Callable[[str], None] | None:
        if on_stage is None:
            return None

        def _emit(delta: str) -> None:
            on_stage("delta", {"text": delta})

        return _emit

    def decline(self, proposal_id: str) -> AgentResult:
        """Pass an explicit user denial to the existing server-side boundary."""
        started = time.perf_counter()
        try:
            gateway_result = self.gateway.record_confirmation_denial(
                proposal_id=proposal_id,
                user_id=self.user_id or "",
                tenant_id=self.tenant_id or "",
            )
        except Exception as error:
            structured = translate_odoo_exception(error)
            return AgentResult(
                outcome="erp_error",
                response_ar="حصلت مشكلة أثناء تسجيل الرفض.",
                gateway_result=None,
                error=structured.message,
                structured_error_data=structured,
                engine=self.engine,
                timings={"total_ms": round((time.perf_counter() - started) * 1000, 1)},
            )
        answer = compose_answer(outcome=CONFIRMATION_DECLINED, gateway_result=gateway_result, tool_name=getattr(gateway_result, "tool_name", ""))
        return AgentResult(
            outcome=CONFIRMATION_DECLINED,
            response_ar=answer.to_text() or _RESPONSE_TEMPLATES[CONFIRMATION_DECLINED],
            gateway_result=gateway_result,
            answer=answer,
            engine=self.engine,
            timings={"total_ms": round((time.perf_counter() - started) * 1000, 1)},
        )

    def amend_proposal(self, proposal_id: str, new_arguments: Mapping[str, Any]) -> AgentResult:
        """Re-issue a pending proposal with edited arguments, fully revalidated.

        Uses ``create_successor_proposal`` so a new proposal_version is issued,
        the predecessor is superseded, and any prior approval is invalidated
        (fresh operation_hash + new idempotency review). This way
        "خلّيني أغيّر الكمية 15 بدل 20" is a governance event, not a UI trick.
        """
        original = self.gateway.confirmation_store.get_proposal(proposal_id)
        if original is None:
            return AgentResult(
                outcome=CONFIRMATION_REQUIRED,
                response_ar="المقترح مش موجود أو انتهى — ابعت الطلب من جديد.",
                gateway_result=None,
                engine=self.engine,
            )
        if getattr(original, "user_id", None) != self.user_id or getattr(original, "tenant_id", None) != self.tenant_id:
            return AgentResult(
                outcome=DENIED,
                response_ar="مفيش صلاحية لتعديل مقترح مملوك لمستخدم تاني.",
                gateway_result=None,
                engine=self.engine,
            )
        # Validate new arguments against current schema BEFORE creating successor.
        call = LLMToolCall(name=original.tool_name, arguments=dict(new_arguments or {}), call_id=str(uuid.uuid4()))
        version, error, reason = self._validate_tool_call(call)
        if error:
            answer = compose_answer(outcome=error, tool_name=call.name, tool_arguments=dict(call.arguments), structured_error=None)
            return AgentResult(
                outcome=error,
                response_ar=f"التعديل مرفوض: {reason or 'العقد ما اتحققش'}. المقترح الأصلي فضل زي ما هو.",
                gateway_result=None,
                tool_call=call,
                answer=answer,
                engine=self.engine,
            )
        # Decline + release the old idempotency reservation before creating successor.
        declined = self.gateway.record_confirmation_denial(
            proposal_id=proposal_id,
            user_id=self.user_id or "",
            tenant_id=self.tenant_id or "",
        )
        successor = self.gateway.confirmation_store.create_successor_proposal(
            predecessor_id=proposal_id,
            new_arguments=dict(call.arguments),
            user_id=self.user_id or "",
            tenant_id=self.tenant_id or "",
        )
        if successor.proposal is None or successor.status != "approved":
            answer = compose_answer(outcome="error", structured_error=successor.structured_error, tool_name=call.name)
            return AgentResult(
                outcome="error",
                response_ar=answer.to_text() or "تعذر إنشاء النسخة المعدلة.",
                gateway_result=declined,
                answer=answer,
                engine=self.engine,
            )
        # Issue fresh gateway request to get idempotency reservation and audit row for the new version.
        request = ToolGatewayRequest(
            request_id=str(uuid.uuid4()),
            user_id=self.user_id or "",
            tenant_id=self.tenant_id or "",
            tool_name=call.name,
            tool_version=version or original.tool_version,
            arguments=dict(call.arguments),
            idempotency_key=None,
        )
        gateway_result = self.gateway.handle_request(request)
        answer = compose_answer(outcome=TOOL_CALL, gateway_result=gateway_result, tool_name=call.name)
        answer.notices.insert(
            0,
            f"المقترح اتعدّل للإصدار رقم {successor.proposal.proposal_version}: التوقيع القديم اتلغى، والتوقيع الجديد هو اللي نافّذ، والسجل فيه الاتنين.",
        )
        return AgentResult(
            outcome=CONFIRMATION_AMENDED,
            response_ar=answer.to_text() or "اتحدّث المقترح وجاهز للتوقيع.",
            gateway_result=gateway_result,
            tool_call=call,
            answer=answer,
            engine=self.engine,
        )


__all__ = [
    "ACCEPTED",
    "AgentResult",
    "AgentRuntime",
    "AgentRuntimeError",
    "CONFIRMATION_REQUIRED",
    "CONFIRMED_EXECUTION",
    "CONFIRMATION_DECLINED",
    "CONFIRMATION_AMENDED",
    "DECISION_AUTHORITY",
    "DECISION_AUTHORITY_NOTICE",
    "DENIED",
    "INVALID_ARGUMENTS",
    "MALFORMED_TOOL_CALL",
    "MULTIPLE_TOOL_CALLS",
    "TEXT_ONLY",
    "TOOL_CALL",
    "UNKNOWN_TOOL_REJECTED",
    "public_decision_view",
]
