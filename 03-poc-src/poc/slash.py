"""Slash commands — instant, model-free paths through the same gateway.

``/search customer أحمد`` resolves to exactly the same
:class:`~poc.gateway.ToolGatewayRequest` the agent would build, so validation,
authorization, confirmation, idempotency, and audit stay identical. The only
difference is that no LLM is consulted: the operator supplied the intent
explicitly, so a model call would only add latency, cost, and variance.

Why this belongs in the product: sales reps repeat the same five queries all
day. Making those zero-latency and zero-token is the cheapest speed-up in the
whole stack — and it gives the eval harness a deterministic UI-path probe.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from poc.answer import Answer, TONE_INFO
from poc.responder import compose_answer

CMD_HELP = "/help"
CMD_WHOAMI = "/whoami"
CMD_TOOLS = "/tools"
CMD_AUDIT = "/audit"
CMD_SEARCH = "/search"
CMD_CUSTOMER = "/customer"
CMD_ORDER = "/order"
CMD_CREATE = "/create"
CMD_SETTINGS = "/settings"
CMD_INTEGRATIONS = "/integrations"
CMD_DEMO = "/demo"

SLASH_COMMANDS: tuple[dict[str, str], ...] = (
    {"command": "/search customer <نص>", "group": "استعلام", "help": "بحث عملاء مباشر بدون استدعاء نموذج."},
    {"command": "/search product <نص>", "group": "استعلام", "help": "بحث منتجات/مخزون مباشر."},
    {"command": "/customer <id>", "group": "استعلام", "help": "بطاقة عميل بالـ ID."},
    {"command": "/order <id>", "group": "استعلام", "help": "تفاصيل أمر بيع بالـ ID."},
    {"command": "/create <customer_id> <product_id> <qty>", "group": "تنفيذ", "help": "تجهيز أمر بيع (يمرّ ببوابة التأكيد نفسها)."},
    {"command": "/audit [n]", "group": "حوكمة", "help": "آخر n كتلة من سلسلة التدقيق + حالة السلسلة."},
    {"command": "/tools", "group": "حوكمة", "help": "عقود الأدوات المتاحة لك."},
    {"command": "/whoami", "group": "حوكمة", "help": "هويتك ودورك والصلاحيات الفعلية."},
    {"command": "/help", "group": "مساعدة", "help": "كل الأوامر مع أمثلة."},
    {"command": "/settings", "group": "واجهة", "help": "فتح لوحة الإعدادات (على جهازك، مش على الخادم)."},
    {"command": "/integrations", "group": "واجهة", "help": "فتح لوحة التكاملات."},
    {"command": "/demo replay", "group": "تجريبي", "help": "محاكاة هجوم تكرار لإظهار منع التكرار."},
)


@dataclass
class SlashResult:
    """Outcome of a slash command: a tool decision, an info answer, or a UI action."""

    handled: bool = False
    answer: Answer | None = None
    action: str | None = None  # "settings" | "integrations" | "replay" | "help"
    gateway_result: Any = None
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    text: str = ""


def is_slash(text: str) -> bool:
    return (text or "").lstrip().startswith("/")


def catalog() -> list[dict[str, str]]:
    return [dict(row) for row in SLASH_COMMANDS]


def _tokens(text: str) -> list[str]:
    try:
        return shlex.split(text.strip())
    except ValueError:
        return text.strip().split()


def _info(title: str, headline: str, *, items: Sequence[str] = (), fields: Sequence[Mapping[str, Any]] = ()) -> SlashResult:
    from poc.answer import fields_section, list_section

    sections = []
    if fields:
        sections.append(fields_section("التفاصيل", [dict(row) for row in fields], icon="ℹ️"))
    if items:
        sections.append(list_section("المتاح", list(items), icon="🧾"))
    return SlashResult(handled=True, answer=Answer(status="info", tone=TONE_INFO, title=title, headline=headline, sections=sections))


def execute(text: str, runtime: Any) -> SlashResult:
    """Run one slash command against a runtime. Never raises; degrades to an answer."""
    if not is_slash(text):
        return SlashResult(handled=False)
    tokens = _tokens(text)
    verb = (tokens[0] if tokens else "").lower()
    rest = tokens[1:]

    if verb == "/help":
        lines = [f"{row['command']} — {row['help']}" for row in SLASH_COMMANDS]
        return _info("الأوامر", "دي كل الأوامر المباشرة — بتعدّي على نفس البوابة ومفيش استدعاء نموذج.", items=lines)

    if verb == "/settings":
        return SlashResult(handled=True, action="settings", text="فتح لوحة الإعدادات")
    if verb == "/integrations":
        return SlashResult(handled=True, action="integrations", text="فتح لوحة التكاملات")
    if verb == "/demo" and rest and rest[0].lower() == "replay":
        return SlashResult(handled=True, action="replay", text="محاكاة هجوم التكرار")

    if verb == "/tools":
        registry = runtime.registry
        rows = []
        for name in registry.names():
            contract = registry.get(name)
            rows.append(
                {
                    "label": f"{name} · v{contract['tool_version']}",
                    "value": ("قراءة" if contract["readOnly"] else "كتابة") + (" + تأكيد" if contract.get("requiresConfirmation") else ""),
                    "tone": "info" if contract["readOnly"] else "pending",
                }
            )
        return _info("عقود الأدوات", f"{len(rows)} أداة معرّفة على الخادم — مفيش أي أداة ديناميكية.", fields=rows)

    if verb == "/whoami":
        from poc.authz import ALLOWED
        from poc.tool_contracts import get_registry

        engine = runtime.gateway.policy_engine
        allowed: list[str] = []
        denied: list[str] = []
        for name in get_registry().names():
            decision = engine.evaluate(
                {
                    "user_id": runtime.user_id,
                    "tenant_id": runtime.tenant_id,
                    "tool_name": name,
                    "tool_version": runtime.registry.get(name)["tool_version"],
                }
            )
            (allowed if decision.decision == ALLOWED else denied).append(name)
        return _info(
            "هويتك وصلاحياتك",
            f"أنت {runtime.user_id} داخل الـ tenant {runtime.tenant_id} — الصلاحيات من سياسة الخادم مش من كلام النموذج.",
            items=[
                f"مسموح: {', '.join(allowed) if allowed else 'ولا أداة (الدور بلا صلاحيات)'}",
                f"مرفوض: {', '.join(denied) if denied else 'مفيش'}",
            ],
        )

    if verb == "/audit":
        limit = 10
        if rest and rest[0].isdigit():
            limit = max(1, min(200, int(rest[0])))
        store = runtime.gateway.audit_store
        rows = store.list(limit=limit)
        chain = store.verify_chain()
        from poc.answer import Answer as _Answer, Kpi, TONE_SUCCESS, TONE_DENIED, table_section

        table_rows = [
            {
                "audit_id": row.get("audit_id"),
                "tool": row.get("tool_name"),
                "status": row.get("result_status"),
                "policy": row.get("policy_decision"),
                "hash": str(row.get("own_hash") or "")[:12] + "…",
                "tone": TONE_SUCCESS if row.get("result_status") == "success" else TONE_DENIED,
            }
            for row in reversed(rows)
        ]
        answer = _Answer(
            status="audit",
            tone=TONE_SUCCESS if chain.get("valid") else TONE_DENIED,
            title="سجل التدقيق",
            headline=(f"آخر {len(rows)} عملية — سلسلة SHA-256 سليمة." if chain.get("valid") else "تحذير: سلسلة التدقيق غير سليمة."),
            kpis=[
                Kpi("عدد الكتل", str(len(rows)), icon="🧱"),
                Kpi("حالة السلسلة", "VALID" if chain.get("valid") else "BROKEN", tone=TONE_SUCCESS if chain.get("valid") else TONE_DENIED, icon="🔗"),
            ],
            sections=[
                table_section(
                    "الكتل الأخيرة",
                    (
                        {"key": "audit_id", "label": "#", "align": "center", "kind": "mono"},
                        {"key": "tool", "label": "الأداة", "align": "start"},
                        {"key": "status", "label": "النتيجة", "align": "center", "kind": "badge"},
                        {"key": "policy", "label": "القرار", "align": "center"},
                        {"key": "hash", "label": "هاش الكتلة", "align": "start", "kind": "mono"},
                    ),
                    table_rows,
                    icon="🛡️",
                )
            ],
            data={"records": [dict(row) for row in rows], "chain": dict(chain)},
        )
        return SlashResult(handled=True, answer=answer)

    if verb == "/search":
        kind = (rest[0].lower() if rest else "customer")
        if kind in {"product", "products", "منتج", "مخزون"}:
            tool = "product.search"
        elif kind in {"customer", "customers", "عميل"}:
            tool = "customer.search"
        else:  # /search محمد أحمد
            tool, rest = "customer.search", rest
        query = " ".join(rest).strip()
        if not query:
            return _info("بحث", "اكتب حاجة تدوّر عليها: /search customer محمد أحمد")
        return _tool_call(runtime, tool, {"query": query})

    if verb == "/customer":
        if not rest or not rest[0].lstrip("#").isdigit():
            return _info("عميل", "المطلوب رقم: /customer 42")
        return _tool_call(runtime, "customer.get", {"customer_id": int(rest[0].lstrip("#"))})

    if verb == "/order":
        if not rest or not rest[0].lstrip("#").isdigit():
            return _info("أمر بيع", "المطلوب رقم الأمر: /order 100")
        return _tool_call(runtime, "sales.order.get", {"order_id": int(rest[0].lstrip("#"))})

    if verb == "/create":
        if len(rest) < 3 or not all(token.isdigit() for token in rest[:3]):
            return _info("إنشاء أمر", "الصيغة: /create <رقم العميل> <رقم المنتج> <الكمية> — مثال /create 42 55 15")
        customer_id, product_id, quantity = (int(value) for value in rest[:3])
        return _tool_call(runtime, "sales.order.create", {"customer_id": customer_id, "lines": [{"product_id": product_id, "quantity": max(1, quantity)}]})

    return _info("أمر غير معروف", f"مفيش أمر اسمه {verb}. اكتب /help تشوف القائمة.")


def _tool_call(runtime: Any, tool_name: str, arguments: dict[str, Any]) -> SlashResult:
    """Route a slash command through the gateway exactly like the agent would."""
    import uuid

    from poc.gateway import ToolGatewayRequest

    try:
        contract = runtime.registry.get(tool_name)
    except KeyError:
        return _info("أداة غير معروفة", f"الأداة {tool_name} مش في سجل العقود.")
    request = ToolGatewayRequest(
        request_id=str(uuid.uuid4()),
        user_id=runtime.user_id or "",
        tenant_id=runtime.tenant_id or "",
        tool_name=tool_name,
        tool_version=contract["tool_version"],
        arguments=dict(arguments),
        idempotency_key=None,
    )
    odoo_client = None
    if contract["readOnly"] and getattr(runtime, "odoo_client_factory", None) is not None:
        try:
            odoo_client = runtime.odoo_client_factory(runtime.user_id or "", runtime.tenant_id or "")
        except Exception:
            odoo_client = None
    try:
        if odoo_client is not None:
            gw = runtime.gateway.handle_request(request, odoo_client=odoo_client)
        else:
            gw = runtime.gateway.handle_request(request)
    except Exception as error:  # never let a control-plane error escape the chat
        return _info("خطأ في البوابة", f"البوابة رفضت الطلب: {type(error).__name__}.")
    answer = compose_answer(outcome="tool_call", gateway_result=gw, tool_name=tool_name, tool_arguments=arguments)
    return SlashResult(handled=True, answer=answer, gateway_result=gw, tool_name=tool_name, arguments=arguments)


__all__ = ["SLASH_COMMANDS", "SlashResult", "catalog", "execute", "is_slash"]
