"""Agent Runtime orchestration layer for the POC.

Translates Arabic natural-language requests into validated LLM tool calls,
constructs ToolGatewayRequest, and delegates ALL execution to the existing
ToolGateway. Performs no Odoo operations, no database access, and no
authorization decisions.

Security invariant: LLM = Authority is never assumed. Model output is
untrusted input validated against the server-owned ToolRegistry before it
reaches the Gateway.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import jsonschema

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
from poc.tool_contracts import ToolRegistry, get_registry
from poc.errors import translate_error, translate_odoo_exception, StructuredError

TOOL_CALL = "tool_call"
TEXT_ONLY = "text_only"
UNKNOWN_TOOL_REJECTED = "unknown_tool_rejected"
MALFORMED_TOOL_CALL = "malformed_tool_call"
INVALID_ARGUMENTS = "invalid_arguments"
MULTIPLE_TOOL_CALLS = "multiple_tool_calls"
CONFIRMED_EXECUTION = "confirmed_execution"
CONFIRMATION_DECLINED = "confirmation_declined"

# Arabic response templates keyed by gateway result status.
_RESPONSE_TEMPLATES = {
    ACCEPTED: "تمام يا فندم، تم تجهيز طلبك بنجاح:\n{detail}",
    CONFIRMATION_REQUIRED: "الطلب ده محتاج تأكيد من حضرتك قبل ما ننفذه. رقم الطلب: {detail}",
    CONFLICT: "عفواً، فيه تعارض: العملية دي اتنفذت أو لسه بتتنفيذ في طلب تاني.",
    IN_PROGRESS: "العملية لسه شغالة حالياً، ثواني وارجع لحضرتك.",
    REPLAY: "النتيجة دي من عملية سابقة مطابقة.",
    DENIED: "بعتذر لحضرتك، مفيش صلاحية لتنفيذ العملية دي:\n{detail}",
    CONFIRMED_EXECUTION: "تم تنفيذ العملية بنجاح بعد تأكيد حضرتك:\n{detail}",
    CONFIRMATION_DECLINED: "تمام، تم إلغاء العملية بناءً على طلبك.",
}


class AgentRuntimeError(RuntimeError):
    """Raised when Agent Runtime cannot produce a valid gateway request."""


def _read_result_summary(result: Mapping[str, Any]) -> str:
    """Human-readable Arabic summary for executed read results; '' otherwise."""
    if not isinstance(result, dict) or not result.get("success"):
        return ""
    customers = result.get("customers")
    if isinstance(customers, list):
        if not customers:
            return "مفيش عملاء مطابقين للبحث."
        summaries = [f"- {c.get('name', '?')} (تليفون: {c.get('phone', '-')}, إيميل: {c.get('email', '-')}, العنوان: {c.get('street', '-')}, حد ائتمان: {c.get('credit_limit', 0)})" for c in customers[:5]]
        more = f"\nوفي {len(customers) - 5} عميل تانيين." if len(customers) > 5 else ""
        return f"لقيت {len(customers)} عميل:\n" + "\n".join(summaries) + more
    products = result.get("products")
    if isinstance(products, list):
        if not products:
            return "مفيش منتجات مطابقة للبحث."
        summaries = [f"- {p.get('name', '?')} (كود: {p.get('default_code', '-')}, سعر: {p.get('list_price', 0)}, متاح: {p.get('qty_available', 0)})" for p in products[:5]]
        more = f"\nوفي {len(products) - 5} منتج تانيين." if len(products) > 5 else ""
        return f"لقيت {len(products)} منتج:\n" + "\n".join(summaries) + more
    customer = result.get("customer")
    if isinstance(customer, dict):
        details = "، ".join(f"{k}: {v}" for k, v in customer.items() if k != "name")
        return f"بيانات العميل {customer.get('name', '?')}:\n{details}"
    order = result.get("order")
    if isinstance(order, dict):
        state_map = {"draft": "مسودة", "sale": "مؤكد", "cancel": "ملغي", "done": "منتهي"}
        state_ar = state_map.get(order.get('state', ''), order.get('state', '?'))
        partner = order.get('partner_id', '?')
        if isinstance(partner, list) and len(partner) > 1:
            partner = partner[1]
        lines = order.get("order_line", [])
        lines_summary = f"وعدد المنتجات في الأوردر {len(lines)}" if lines else "بدون منتجات"
        return f"الأوردر {order.get('name', '?')} للعميل {partner} حالة {state_ar} بإجمالي {order.get('amount_total', '?')} جنيه\n{lines_summary}"
    return ""


@dataclass(frozen=True)
class AgentResult:
    """Truthful result passed back to the caller."""
    outcome: str
    response_ar: str
    gateway_result: GatewayResult | None
    tool_call: LLMToolCall | None = None
    error: str | None = None
    structured_error_data: StructuredError | None = None

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
    ) -> None:
        self.llm_client = llm_client
        self.gateway = gateway
        self.registry = registry or get_registry()
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.odoo_client_factory = odoo_client_factory

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
        return (
            "You are a professional Egyptian business consultant and ERP assistant (تتحدث بلهجة مصرية مهنية وودودة). "
            "Your goal is to help users manage their business effectively.\n\n"
            "AVAILABLE TOOLS: 'customer.search', 'customer.get', 'product.search', 'sales.order.create', 'sales.order.get'.\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. NEVER output your internal thinking, reasoning steps, or analysis (like 'Analyze User Input', 'Check Guidelines', 'Identify Tool', 'Execute Tool Call') to the user.\n"
            "2. When an ERP operation is requested, immediately call the single appropriate tool using the native tool calling mechanism.\n"
            "3. Answer general business/ERP questions conversationally when no tool is needed.\n"
            "4. Explain results in detail with context, analysis, and provide business insights.\n"
            "5. Include recommendations and next steps after every operation.\n"
            "6. When the user specifies names or search terms in Arabic, keep the query parameter in Arabic exactly as provided by the user. Never translate search queries or proper names to English.\n"
            "7. Your responses must be rich, conversational, and in Egyptian Arabic."
        )

    def _validate_tool_call(self, call: LLMToolCall) -> tuple[str | None, str | None]:
        """Validate model output against the server-owned registry. Returns (version, error)."""
        if not isinstance(call.name, str) or not call.name:
            return None, MALFORMED_TOOL_CALL
        try:
            contract = self.registry.get(call.name)
        except KeyError:
            return None, UNKNOWN_TOOL_REJECTED
        version = contract["tool_version"]
        args = call.arguments
        if not isinstance(args, dict):
            return None, MALFORMED_TOOL_CALL
        schema = contract["inputSchema"]
        try:
            jsonschema.validate(instance=args, schema=schema)
        except jsonschema.ValidationError:
            return None, INVALID_ARGUMENTS
        return version, None

    def process(self, user_input: str) -> AgentResult:
        """Translate one Arabic NL request through LLM → validation → gateway."""
        system = self._build_system_prompt()
        tools = self._tool_definitions()
        try:
            llm_response = self.llm_client.chat(
                messages=[LLMMessage(role="user", content=user_input)],
                system=system,
                tools=tools,
            )
        except LLMProviderError as error:
            return AgentResult(outcome="llm_error", response_ar="فيه مشكلة في الاتصال بمزود الذكاء الاصطناعي. حاول تاني.", gateway_result=None, error=str(error))

        tool_calls = list(llm_response.tool_calls)
        if not tool_calls and llm_response.text:
            # Fallback: Check if the model printed a tool call JSON in text
            text_content = llm_response.text
            # Look for JSON blocks or object structures containing tool arguments
            json_matches = re.findall(r"(\{(?:[^{}]|(?:\{[^{}]*\}))*\})", text_content, re.DOTALL)
            for raw_json in json_matches:
                try:
                    parsed = json.loads(raw_json)
                    if isinstance(parsed, dict):
                        if "customer_id" in parsed and "lines" in parsed:
                            tool_calls.append(LLMToolCall(name="sales.order.create", arguments=parsed, call_id=str(uuid.uuid4())))
                            break
                        elif "query" in parsed:
                            if "product" in text_content.lower() or "منتج" in user_input:
                                tool_calls.append(LLMToolCall(name="product.search", arguments=parsed, call_id=str(uuid.uuid4())))
                            else:
                                tool_calls.append(LLMToolCall(name="customer.search", arguments=parsed, call_id=str(uuid.uuid4())))
                            break
                        elif "order_id" in parsed:
                            tool_calls.append(LLMToolCall(name="sales.order.get", arguments=parsed, call_id=str(uuid.uuid4())))
                            break
                except Exception:
                    continue

        if not tool_calls:
            # Clean any internal analysis headers if model leaked them in TEXT_ONLY
            cleaned_text = llm_response.text or "لم أفهم الطلب."
            if "Analyze User Input" in cleaned_text or "Execute Tool Call" in cleaned_text:
                # Remove internal reasoning leak if present
                lines = [l for l in cleaned_text.splitlines() if not any(k in l for k in ["Analyze User Input", "Check Guidelines", "Identify Tool", "Execute Tool Call", "Language:", "Translation/Meaning:", "Key entities:"])]
                cleaned_text = "\n".join(lines).strip() or "تمام يا فندم، أنا تحت أمرك. أقدر أساعدك في أي استفسار أو عملية في النظام."
            return AgentResult(outcome=TEXT_ONLY, response_ar=cleaned_text, gateway_result=None)

        if len(tool_calls) > 1:
            return AgentResult(outcome=MULTIPLE_TOOL_CALLS, response_ar="وصلت أكثر من طلب أداة في نفس الوقت. برجاء إعادة الصياغة.", gateway_result=None)

        call = tool_calls[0]
        version, error = self._validate_tool_call(call)
        if error:
            return AgentResult(outcome=error, response_ar="الطلب مش صالح: الأداة أو الوسائط غير صحيحة.", gateway_result=None, tool_call=call)

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
            return AgentResult(
                outcome="erp_error",
                response_ar="حصلت مشكلة غير متوقعة أثناء معالجة الطلب. برجاء المحاولة مرة أخرى أو مراجعة المسؤول.",
                gateway_result=gateway_result,
                tool_call=call,
                error=structured.message,
                structured_error_data=structured,
            )
        response_ar = self._format_response(gateway_result)
        return AgentResult(outcome=TOOL_CALL, response_ar=response_ar, gateway_result=gateway_result, tool_call=call)

    def confirm(self, proposal_id: str) -> AgentResult:
        """Pass an explicit user approval to the existing server-side boundary."""
        if self.odoo_client_factory is None:
            raise AgentRuntimeError("Odoo client factory is not configured")
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
            )
        return AgentResult(
            outcome=CONFIRMED_EXECUTION,
            response_ar=self._format_response(gateway_result),
            gateway_result=gateway_result,
        )

    def decline(self, proposal_id: str) -> AgentResult:
        """Pass an explicit user denial to the existing server-side boundary."""
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
            )
        return AgentResult(
            outcome=CONFIRMATION_DECLINED,
            response_ar=self._format_response(gateway_result),
            gateway_result=gateway_result,
        )

    def _format_response(self, result: GatewayResult) -> str:
        """Return a truthful Arabic response that reflects the actual gateway result."""
        template = _RESPONSE_TEMPLATES.get(result.status, "حصلت حالة غير متوقعة: {detail}")
        detail = ""
        if result.status == ACCEPTED:
            if result.result:
                detail = _read_result_summary(result.result) or json.dumps(result.result, ensure_ascii=False, default=str)
            else:
                detail = "العملية تمت بنجاح، مفيش تفاصيل إضافية."
        elif result.status == CONFIRMATION_REQUIRED:
            detail = result.reason or ""
        elif result.status == DENIED:
            detail = result.error_code or result.reason or ""
        else:
            detail = result.reason or ""
        formatted = template.format(detail=detail)
        if (
            result.status == CONFIRMED_EXECUTION
            and result.structured_error is not None
        ):
            error = result.structured_error
            formatted = (
                "العملية لم تكتمل بنجاح. "
                f"رمز الخطأ: {error.code}. {error.message}"
            )
        return formatted

