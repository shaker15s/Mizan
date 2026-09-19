"""Outbound integrations — delivery hooks that never touch the transaction.

An integration is an *observer* of governance events (a write executed and
verified, a write declined, a policy denial). It can decorate and notify, it can
never authorize, retry, or alter what the gateway already decided. Every
dispatch is best-effort and isolated: a dead Slack webhook must not turn a
successful ERP write into a failure.

Design choices that keep this honest:

- **Preview-first.** An integration without credentials returns a rendered
  preview of the exact payload it *would* send, so the cockpit demo shows the
  integration working without inventing a delivery.
- **No secrets in payloads or logs.** Auth material is read at send time only.
- **Bounded and explicit.** Fixed endpoints/verbs per integration, a timeout,
  and a size cap — no general-purpose HTTP surface.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

INTEGRATION_TIMEOUT_SECONDS = 8.0
MAX_PAYLOAD_BYTES = 12_000

STATUS_SENT = "sent"
STATUS_PREVIEW = "preview"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"

EVENT_ORDER_EXECUTED = "order.executed"
EVENT_ORDER_DECLINED = "order.declined"
EVENT_WRITE_DENIED = "write.denied"


@dataclass(frozen=True)
class IntegrationField:
    key: str
    label_ar: str
    placeholder: str = ""
    secret: bool = False
    required: bool = False
    help_ar: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label_ar,
            "placeholder": self.placeholder,
            "secret": self.secret,
            "required": self.required,
            "help": self.help_ar,
        }


@dataclass(frozen=True)
class IntegrationSpec:
    id: str
    name_ar: str
    description_ar: str
    icon: str
    kind: str  # http_json | http_form | smtp | probe
    fields: tuple[IntegrationField, ...]
    events: tuple[str, ...]
    docs_url: str = ""

    def to_dict(self, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        config = dict(config or {})
        return {
            "id": self.id,
            "name": self.name_ar,
            "description": self.description_ar,
            "icon": self.icon,
            "kind": self.kind,
            "docs_url": self.docs_url,
            "events": list(self.events),
            "fields": [item.to_dict() for item in self.fields],
            "enabled": bool(config.get("enabled")),
            "configured": all(
                bool(str(config.get(item.key) or "").strip()) for item in self.fields if item.required
            ),
            "values": {
                item.key: ("••••" if item.secret and config.get(item.key) else config.get(item.key, ""))
                for item in self.fields
            },
        }


@dataclass
class DeliveryResult:
    integration: str
    status: str
    detail: str = ""
    preview: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    event: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "integration": self.integration,
            "status": self.status,
            "detail": self.detail,
            "preview": dict(self.preview),
            "latency_ms": round(self.latency_ms, 1),
            "event": self.event,
        }


INTEGRATIONS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        id="webhook",
        name_ar="Webhook موقّع (HMAC)",
        description_ar="أي نظام عندك (Zapier / n8n / ERP خارجي) يستقبل حدث التنفيذ مع توقيع HMAC-SHA256.",
        icon="🪝",
        kind="http_json",
        docs_url="https://docs.github.com/en/webhooks/webhook-events-and-payloads",
        fields=(
            IntegrationField("url", "عنوان الـ URL", "https://hooks.acme.test/mizan", required=True, help_ar="POST JSON"),
            IntegrationField("secret", "سر التوقيع", "whsec_…", secret=True, help_ar="يُرسَل في ترويسة X-Mizan-Signature"),
        ),
        events=(EVENT_ORDER_EXECUTED, EVENT_ORDER_DECLINED, EVENT_WRITE_DENIED),
    ),
    IntegrationSpec(
        id="slack",
        name_ar="سلاك — قناة المبيعات",
        description_ar="إبلاغ فوري في قناة المبيعات بأي أمر اتعمل أو اترفض، بنفس صيغة الكارت.",
        icon="💬",
        kind="http_json",
        docs_url="https://api.slack.com/messaging/webhooks",
        fields=(
            IntegrationField("url", "Incoming Webhook URL", "https://hooks.slack.com/services/…", required=True),
            IntegrationField("channel", "القناة (اختياري)", "#sales"),
        ),
        events=(EVENT_ORDER_EXECUTED, EVENT_ORDER_DECLINED, EVENT_WRITE_DENIED),
    ),
    IntegrationSpec(
        id="whatsapp",
        name_ar="واتس‌اب بيزنس API",
        description_ar="رسالة تأكيد للمندوب أو العميل على واتس‌اب بعد تنفيذ الأمر (Cloud API).",
        icon="📱",
        kind="http_json",
        docs_url="https://developers.facebook.com/docs/whatsapp/cloud-api",
        fields=(
            IntegrationField("token", "Access Token", "EAAG…", secret=True, required=True),
            IntegrationField("phone_number_id", "Phone Number ID", "1234567890", required=True),
            IntegrationField("to", "رقم المستلم", "+201001234567", required=True),
        ),
        events=(EVENT_ORDER_EXECUTED,),
    ),
    IntegrationSpec(
        id="email",
        name_ar="بريق يومي / فوري (SMTP)",
        description_ar="ملخص العملية على هيئة إيميل منسق (JSON→HTML) لمدير الحسابات.",
        icon="✉️",
        kind="smtp",
        docs_url="https://datatracker.ietf.org/doc/html/rfc5321",
        fields=(
            IntegrationField("host", "سيرفر SMTP", "smtp.acme.test", required=True),
            IntegrationField("port", "المنفذ", "587"),
            IntegrationField("username", "المستخدم", "erp@acme.test"),
            IntegrationField("password", "كلمة المرور", "••••", secret=True),
            IntegrationField("from", "المُرسل", "mizan@acme.test", required=True),
            IntegrationField("to", "المستلم", "sales-manager@acme.test", required=True),
        ),
        events=(EVENT_ORDER_EXECUTED, EVENT_ORDER_DECLINED),
    ),
)

SPEC_BY_ID = {spec.id: spec for spec in INTEGRATIONS}


def catalog(config_map: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    config_map = config_map or {}
    return [spec.to_dict(config_map.get(spec.id)) for spec in INTEGRATIONS]


def render_message(event: Mapping[str, Any]) -> str:
    """Compact Arabic notification text for one governance event."""
    kind = str(event.get("event", ""))
    tool = event.get("tool") or "—"
    if kind == EVENT_ORDER_EXECUTED:
        bits = [
            f"✅ تم تنفيذ {tool}",
            f"الأمر: {event.get('order_name') or event.get('order_id') or '—'}",
            f"العميل: {event.get('customer') or event.get('customer_id') or '—'}",
            f"الإجمالي: {event.get('amount_total') if event.get('amount_total') is not None else '—'} ج.م",
            f"تدقيق: #{event.get('audit_id') or '—'} • {event.get('user') or '—'}",
        ]
    elif kind == EVENT_ORDER_DECLINED:
        bits = [f"⛔ إلغاء مقترح {tool} بواسطة {event.get('user') or '—'}", "مفيش أي تعديل في ERP."]
    elif kind == EVENT_WRITE_DENIED:
        bits = [f"⛔ رفض سياسة: {tool}", f"المستخدم: {event.get('user') or '—'}", f"الرمز: {event.get('error_code') or 'POLICY_DENIED'}"]
    else:
        bits = [f"حدث {kind or 'غير معروف'} من ميزان", json.dumps(event, ensure_ascii=False, default=str)[:400]]
    return "\n".join(str(row) for row in bits)


def _validate_http_url(url: str) -> str | None:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "عنوان URL غير صالح — لازم يبدأ بـ http(s)://"
    return None


def _truncate(payload: Any) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES:
        return payload if isinstance(payload, dict) else {"payload": payload}
    return {"truncated": True, "preview": text[:MAX_PAYLOAD_BYTES]}


def deliver(
    spec_id: str,
    event: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    *,
    live: bool = True,
    secret_store: Mapping[str, str] | None = None,
) -> DeliveryResult:
    """Send (or preview) one event to one integration. Never raises."""
    spec = SPEC_BY_ID.get(spec_id)
    if spec is None:
        return DeliveryResult(integration=spec_id, status=STATUS_ERROR, detail="تكامل غير معروف")
    config = dict(config or {})
    secrets = dict(secret_store or {})
    for item in spec.fields:
        if item.secret and not config.get(item.key):
            value = secrets.get(f"integrations.{spec_id}.{item.key}") or secrets.get(item.key)
            if value:
                config[item.key] = value
    if spec.events and event.get("event") and str(event["event"]) not in spec.events:
        return DeliveryResult(integration=spec_id, status=STATUS_SKIPPED, detail="الحدث مش مشترك في هذا التكامل", event=str(event.get("event", "")))
    text = render_message(event)
    payload = {"text": text, "event": dict(event)}

    missing = [item.key for item in spec.fields if item.required and not str(config.get(item.key) or "").strip()]
    if missing or not config.get("enabled") or not live:
        return DeliveryResult(
            integration=spec_id,
            status=STATUS_PREVIEW,
            detail=(
                "عايز إرسال فعلي؟ فعّل التكامل وكمّل " + ", ".join(missing)
                if missing
                else "التكامل مطفّي — دي معاينة لما هيوصل."
            ),
            preview={"body": text, "payload_keys": sorted(payload)},
            event=str(event.get("event", "")),
        )

    started = time.perf_counter()
    try:
        if spec.kind == "smtp":
            detail = _send_email(spec, config, text)
        else:
            detail = _send_http(spec, config, payload, text)
        return DeliveryResult(integration=spec_id, status=STATUS_SENT, detail=detail, latency_ms=(time.perf_counter() - started) * 1000, event=str(event.get("event", "")))
    except Exception as error:  # noqa: BLE001 - integrations are always best-effort
        return DeliveryResult(
            integration=spec_id,
            status=STATUS_ERROR,
            detail=f"{type(error).__name__}: {str(error)[:180]}",
            latency_ms=(time.perf_counter() - started) * 1000,
            event=str(event.get("event", "")),
        )


def _send_http(spec: IntegrationSpec, config: Mapping[str, Any], payload: Mapping[str, Any], text: str) -> str:
    url = str(config.get("url") or "").strip()
    error = _validate_http_url(url)
    if error:
        raise ValueError(error)
    headers = {"Content-Type": "application/json", "User-Agent": "Mizan-ERP/1.0"}
    body: dict[str, Any] = dict(payload)
    if spec.id == "slack":
        body = {"text": text}
        if config.get("channel"):
            body["channel"] = str(config["channel"])
    elif spec.id == "whatsapp":
        body = {
            "messaging_product": "whatsapp",
            "to": str(config.get("to") or "").lstrip("+"),
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        url = f"https://graph.facebook.com/v21.0/{config.get('phone_number_id')}/messages"
        headers["Authorization"] = f"Bearer {config.get('token')}"
    secret = str(config.get("secret") or "")
    if secret:
        digest = hmac.new(secret.encode("utf-8"), json.dumps(body, ensure_ascii=False).encode("utf-8"), hashlib.sha256).hexdigest()
        headers["X-Mizan-Signature"] = f"sha256={digest}"
    response = httpx.post(url, json=_truncate(body), headers=headers, timeout=INTEGRATION_TIMEOUT_SECONDS)
    response.raise_for_status()
    return f"HTTP {response.status_code}"


def _send_email(spec: IntegrationSpec, config: Mapping[str, Any], text: str) -> str:
    message = EmailMessage()
    message["Subject"] = "ميزان — إشعار تنفيذ ERP"
    message["From"] = str(config.get("from") or "mizan@localhost")
    message["To"] = str(config.get("to") or "")
    message.set_content(text)
    host = str(config.get("host") or "")
    port = int(str(config.get("port") or 587))
    if not message["To"]:
        raise ValueError("مستلم واحد على الأقل مطلوب")
    with smtplib.SMTP(host, port, timeout=INTEGRATION_TIMEOUT_SECONDS) as client:
        client.ehlo()
        if port == 587:
            client.starttls()
        if config.get("username"):
            client.login(str(config["username"]), str(config.get("password") or ""))
        client.send_message(message)
    return f"accepted by {host}:{port}"


def dispatch_all(
    event: Mapping[str, Any],
    configs: Mapping[str, Mapping[str, Any]],
    *,
    only_enabled: bool = True,
    secret_store: Mapping[str, str] | None = None,
) -> list[DeliveryResult]:
    """Fan one event out to every (enabled) integration; isolated failures."""
    results: list[DeliveryResult] = []
    for spec in INTEGRATIONS:
        config = dict(configs.get(spec.id) or {})
        if only_enabled and not config.get("enabled"):
            continue
        results.append(deliver(spec.id, event, config, secret_store=secret_store))
    return results


__all__ = [
    "DeliveryResult",
    "EVENT_ORDER_DECLINED",
    "EVENT_ORDER_EXECUTED",
    "EVENT_WRITE_DENIED",
    "INTEGRATIONS",
    "IntegrationSpec",
    "SPEC_BY_ID",
    "STATUS_ERROR",
    "STATUS_PREVIEW",
    "STATUS_SENT",
    "STATUS_SKIPPED",
    "catalog",
    "deliver",
    "dispatch_all",
    "render_message",
]
