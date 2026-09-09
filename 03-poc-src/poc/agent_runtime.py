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
    ACCEPTED: "تم تجهيز طلبك بنجاح: {detail}",
    CONFIRMATION_REQUIRED: "الطلب يحتاج تأكيد منك قبل التنفيذ. رقم الطلب: {detail}",
    CONFLICT: "فيه تعارض: العملية ده تم تنفيذها أو لسه بيتم تنفيذها بطلب تاني.",
    IN_PROGRESS: "العملية لسه شغالة حالياً. برجاء المحاولة بعد لحظات.",
    REPLAY: "تم إرجاع النتيجة من عملية سابقة مطابقة.",
    DENIED: "مسموحلكش تنفيذ العملية دي: {detail}",
    CONFIRMED_EXECUTION: "تم تنفيذ العملية بعد تأكيدك: {detail}",
    CONFIRMATION_DECLINED: "تم رفض العملية ولم يتم تنفيذها.",
}


class AgentRuntimeError(RuntimeError):
    """Raised when Agent Runtime cannot produce a valid gateway request."""


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
        tool_lines = []
        for name in self.registry.names():
            contract = self.registry.get(name)
            tool_lines.append(f"- {name} v{contract['tool_version']}: {contract.get('description', '')}")
        return (
            "أنت مساعد ERP. يجب عليك اختيار أداة واحدة فقط من القائمة أدناه. "
            "لا تختار أي أداة غير موجودة في القائمة. "
            "لا تنفذ أي عملية بنفسك — أرسل فقط اسم الأداة والوسائط المطلوبة.\n"
            "الأدوات المتاحة:\n" + "\n".join(tool_lines)
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

        if not llm_response.tool_calls:
            return AgentResult(outcome=TEXT_ONLY, response_ar=llm_response.text or "لم أفهم الطلب.", gateway_result=None)

        if len(llm_response.tool_calls) > 1:
            return AgentResult(outcome=MULTIPLE_TOOL_CALLS, response_ar="وصلت أكثر من طلب أداة في نفس الوقت. برجاء إعادة الصياغة.", gateway_result=None)

        call = llm_response.tool_calls[0]
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
        try:
            gateway_result = self.gateway.handle_request(gateway_request)
        except Exception as error:
            structured = translate_odoo_exception(error)
            return AgentResult(
                outcome="erp_error",
                response_ar="حصلت مشكلة غير متوقعة أثناء معالجة الطلب. برجاء المحاولة مرة أخرى أو مراجعة المسؤول.",
                gateway_result=None,
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
                detail = json.dumps(result.result, ensure_ascii=False, default=str)
            else:
                detail = "استعلام جاهز للتنفيذ"
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

