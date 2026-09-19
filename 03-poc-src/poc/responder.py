"""Deterministic answer composer — the voice of the cockpit.

Turns a gateway decision into a structured :class:`~poc.answer.Answer` with
real information design: a headline that answers the question first, KPIs that
matter financially, typed sections (table / fields / steps), warnings the
operator must see, and one-click next steps.

Deliberately **not** an LLM call: the facts a card shows must be identical to
what the gateway executed, byte for byte, on every run. The model's prose is
only ever attached as ``analysis`` on top of these facts — it can decorate, it
can never fabricate a row.

Truthfulness rules (enforced by tests in ``tests/test_responder.py``):
- ``accepted`` with no executed payload ⇒ "ready", never "done".
- ``confirmation_required`` ⇒ contains "تأكيد" and never claims creation.
- ``denied`` ⇒ names the missing permission, never a generic failure.
- no status ever leaks internal reasoning or raw ERP errors.
"""

from __future__ import annotations

from typing import Any, Mapping

from poc.answer import (
    Action,
    Answer,
    Kpi,
    TONE_DENIED,
    TONE_EMPTY,
    TONE_ERROR,
    TONE_INFO,
    TONE_PENDING,
    TONE_SUCCESS,
    bars_section,
    fields_section,
    format_date_ar,
    format_egp,
    format_number,
    list_section,
    steps_section,
    table_section,
)
from poc.gateway import (
    ACCEPTED,
    CONFIRMATION_REQUIRED,
    CONFLICT,
    DENIED,
    GatewayResult,
    IN_PROGRESS,
    REPLAY,
)

ORDER_STATE_AR = {
    "draft": ("مسودة", "pending"),
    "sent": ("مرسل للعميل", "info"),
    "sale": ("مؤكد", "success"),
    "done": ("منتهي", "success"),
    "cancel": ("ملغي", "denied"),
}

_ORDER_COLUMNS = (
    {"key": "name", "label": "العميل/الصنف", "align": "start"},
    {"key": "qty", "label": "الكمية", "align": "center", "kind": "number"},
    {"key": "price", "label": "سعر الوحدة", "align": "end", "kind": "money"},
    {"key": "line_total", "label": "الإجمالي", "align": "end", "kind": "money"},
)

# `sales.order.create` is answered by Odoo with identifiers only ({"order_id": …}),
# so the price columns must appear only when the ERP actually returned prices.
# Fabricating "0 ج.م" would turn a real record into a false number.
_ORDER_COLUMNS_MIN = (
    {"key": "name", "label": "الصنف", "align": "start"},
    {"key": "qty", "label": "الكمية", "align": "center", "kind": "number"},
)


def _clean(value: Any, fallback: str = "—") -> str:
    if value is None or value == "" or value == []:
        return fallback
    return str(value)


def _proposal_window(proposal: Mapping[str, Any]) -> str:
    """'5 دقايق' / '90 ثانية' — read off the proposal, so the copy never promises a
    window the operator has since reconfigured through settings."""
    from datetime import datetime

    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        seconds = int((datetime.strptime(str(proposal.get("expires_at")), fmt) - datetime.strptime(str(proposal.get("created_at")), fmt)).total_seconds())
    except (TypeError, ValueError):
        return "وقت محدود"
    if seconds >= 60:
        return f"{max(1, round(seconds / 60))} دقايق"
    return f"{max(1, seconds)} ثانية"


def _proposal_ttl(proposal: Mapping[str, Any]) -> str:
    """Human window for a pending proposal, derived from its own timestamps."""
    from datetime import datetime, timezone

    created, expires = proposal.get("created_at"), proposal.get("expires_at")
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        seconds = int((datetime.strptime(str(expires), fmt) - datetime.strptime(str(created), fmt)).total_seconds())
        minutes = max(1, round(seconds / 60))
        hour = datetime.strptime(str(expires), fmt).strftime("%H:%M")
        return f"{minutes} دقايق (بتنتهي {hour} UTC)"
    except (TypeError, ValueError):
        return _clean(expires)


def _short(value: Any, size: int = 12) -> str:
    text = str(value or "")
    return text[:size] + ("…" if len(text) > size else "")


def _partner_name(raw: Any) -> str:
    if isinstance(raw, (list, tuple)) and len(raw) > 1:
        return str(raw[1])
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("id") or "—")
    return str(raw) if raw is not None else "—"


def _first_present(records: list[dict[str, Any]], preferred: list[str]) -> list[str]:
    present = [key for key in preferred if any(isinstance(rec, dict) and rec.get(key) not in (None, "") for rec in records)]
    return present or [key for key in preferred[:2]]


def _elapsed(extra: Mapping[str, Any] | None) -> str:
    if not extra:
        return ""
    ms = extra.get("elapsed_ms")
    return f"{format_number(ms, 0)} مللي ثانية" if isinstance(ms, (int, float)) else ""


# ---------------------------------------------------------------- read results


def _customers_answer(result: Mapping[str, Any], gw: GatewayResult | None, *, query: str = "") -> Answer:
    customers = [dict(row) for row in (result.get("customers") or []) if isinstance(row, Mapping)]
    total = int(result.get("count", len(customers)) or len(customers))
    query = str(query or result.get("query") or "").strip()
    if not customers:
        first_token = query.split()[0] if query else "أحمد"
        kpis = [Kpi("عملاء مطابقين", "0", tone=TONE_EMPTY, hint="البحث اتنفذ على الاسم والهاتف والبريد", icon="🔎")]
        if query:
            kpis.append(Kpi("كلمة البحث", query[:28], tone=TONE_INFO, icon="🏷️"))
        return Answer(
            status=ACCEPTED,
            tone=TONE_EMPTY,
            title="استعلام عملاء",
            headline="تم الفحص من Odoo — مفيش عميل مطابق لبحثك.",
            tool=gw.tool_name if gw else "customer.search",
            kpis=kpis,
            sections=[
                fields_section(
                    "تفاصيل البحث",
                    [
                        {"label": "البحث عن", "value": query or "—"},
                        {"label": "عدد النتائج", "value": "0 من 0 سجل"},
                        {"label": "مصدر البيانات", "value": "Odoo · res.partner", "kind": "mono"},
                    ],
                    icon="🧭",
                )
            ],
            notices=["جرّب جزء من الاسم أو تهجية مختلفة (أ/ا، ة/ه) — البحث بيطابق بعد التطبيع."],
            next_steps=[
                Action(f"جرّب بالاسم الأول ({first_token})", f"ابحث عن عميل اسمه {first_token}", icon="🔎"),
                Action("اعرف الأدوات والصلاحيات المتاحة", "/tools", kind="link", icon="📜"),
            ],
        )
    columns = [{"key": "id", "label": "ID", "align": "center", "kind": "mono"}]
    for key, label, align in (
        ("name", "العميل", "start"),
        ("phone", "الهاتف", "start"),
        ("email", "البريد", "start"),
        ("street", "العنوان", "start"),
        ("city", "المدينة", "start"),
    ):
        if any(rec.get(key) not in (None, "") for rec in customers):
            columns.append({"key": key, "label": label, "align": align})
    if any(rec.get("credit_limit") not in (None,) for rec in customers):
        columns.append({"key": "credit", "label": "الحد الائتماني", "align": "end", "kind": "money"})
    rows = []
    for rec in customers:
        row: dict[str, Any] = {"id": rec.get("id"), "name": _clean(rec.get("name")), "phone": _clean(rec.get("phone")), "email": _clean(rec.get("email")), "street": _clean(rec.get("street")), "city": _clean(rec.get("city"))}
        if rec.get("credit_limit") is not None:
            row["credit"] = format_egp(rec.get("credit_limit") or 0)
        rows.append(row)
    from poc.normalization import normalize_arabic

    exact = sum(1 for rec in customers if normalize_arabic(str(rec.get("name", ""))) == normalize_arabic(query)) if query else 0
    kpis = [
        Kpi("عميل مطابق", format_number(total), tone=TONE_SUCCESS, icon="👥"),
        Kpi("مطابقة تامة", format_number(exact), hint="مطابقة بالاسم الحرفي" if exact else "كل النتائج مطابقة جزئية", tone=TONE_INFO if exact else TONE_EMPTY, icon="🎯"),
    ]
    sections = [
        table_section(
            "نتائج البحث",
            columns,
            rows,
            icon="👥",
            caption=f"أول {len(rows)} من {total} نتيجة" if total > len(rows) else "",
            searchable=len(rows) > 6,
        )
    ]
    steps = [
        Action(f"بطاقة {customers[0].get('name', 'العميل')}", f"هات تفاصيل العميل {customers[0].get('id')}", icon="🪪")
    ]
    if total > 1:
        steps.append(Action("اطلب أمر بيع للعميل ده", f"اعمل أمر بيع للعميل {customers[0].get('id')}", icon="🧾"))
    return Answer(
        status=ACCEPTED,
        tone=TONE_SUCCESS,
        title="استعلام عملاء",
        headline=f"تم الفحص من Odoo — لقيت {total} عميل" + (f" مطابقين لبحثك." if total != 1 else " مطابق لبحثك."),
        tool=gw.tool_name if gw else "customer.search",
        kpis=kpis,
        sections=sections,
        next_steps=steps,
        data={"customers": customers, "count": total, "query": result.get("query")},
        warnings=["أكتر من نتيجة — لو تقصد عميل بالتحديد قولي الرقم أو الاسم كامل."] if total > 1 else [],
    )


def _customer_answer(result: Mapping[str, Any], gw: GatewayResult | None) -> Answer:
    customer = dict(result.get("customer") or {})
    if not customer:
        return Answer(
            status=ACCEPTED,
            tone=TONE_EMPTY,
            title="بطاقة عميل",
            headline="تم الفحص من Odoo — مفيش بيانات للعميل ده.",
            tool=gw.tool_name if gw else "customer.get",
            kpis=[Kpi("سجلات مقابلة", "0", tone=TONE_EMPTY, icon="🔎")],
            sections=[
                fields_section(
                    "إيه اللي حصل",
                    [
                        {"label": "النتيجة", "value": "مفيش سجل بالرقم ده في Odoo"},
                        {"label": "الخطوة التالية", "value": "اتأكد من رقم العميل أو ابحث بالاسم"},
                    ],
                    icon="🧭",
                )
            ],
            next_steps=[Action("ابحث بالاسم بدل الرقم", "ابحث عن عميل اسمه ", icon="🔎")],
        )
    fields = [
        {"label": "الرقم في Odoo", "value": f"#{customer.get('id', '—')}", "kind": "mono"},
        {"label": "الهاتف", "value": _clean(customer.get("phone")), "dir": "ltr"},
        {"label": "البريد الإلكتروني", "value": _clean(customer.get("email"))},
        {"label": "العنوان", "value": _clean("، ".join(str(v) for v in (customer.get("street"), customer.get("city"), customer.get("country")) if v))},
        {"label": "الحد الائتماني", "value": format_egp(customer.get("credit_limit") or 0), "kind": "money", "tone": TONE_SUCCESS if (customer.get("credit_limit") or 0) > 0 else TONE_INFO},
        {"label": "الضريبة", "value": _clean(customer.get("vat"))},
    ]
    used = sum(len(str(row["value"])) for row in fields)
    return Answer(
        status=ACCEPTED,
        tone=TONE_SUCCESS,
        title="بطاقة عميل",
        headline=f"تم الجلب — دي بطاقة {customer.get('name', 'العميل')} من Odoo 19.",
        tool=gw.tool_name if gw else "customer.get",
        kpis=[Kpi("الحد الائتماني", format_egp(customer.get("credit_limit") or 0), tone=TONE_SUCCESS, icon="💳")],
        sections=[fields_section("بيانات العميل", [row for row in fields if row["value"] != "—" or row["label"] == "العنوان"], icon="🪪")],
        next_steps=[
            Action("اعمل له أمر بيع", f"اعمل أمر بيع للعميل {customer.get('id')}", icon="🧾"),
            Action("انسخ رقم الهاتف", str(customer.get("phone") or ""), kind="copy", icon="📋"),
        ],
        data={"customer": customer},
    )


def _products_answer(result: Mapping[str, Any], gw: GatewayResult | None, *, query: str = "") -> Answer:
    products = [dict(row) for row in (result.get("products") or []) if isinstance(row, Mapping)]
    total = int(result.get("count", len(products)) or len(products))
    if not products:
        query = str(query or "").strip()
        first_token = query.split()[0] if query else "مياه"
        kpis = [Kpi("منتجات مطابقة", "0", tone=TONE_EMPTY, hint="البحث بيطابق الاسم وكود الصنف", icon="📦")]
        if query:
            kpis.append(Kpi("كلمة البحث", query[:28], tone=TONE_INFO, icon="🏷️"))
        return Answer(
            status=ACCEPTED,
            tone=TONE_EMPTY,
            title="استعلام مخزون",
            headline="تم الفحص من Odoo — مفيش منتج مطابق لبحثك.",
            tool=gw.tool_name if gw else "product.search",
            kpis=kpis,
            sections=[
                fields_section(
                    "تفاصيل البحث",
                    [
                        {"label": "البحث عن", "value": query or "—"},
                        {"label": "عدد النتائج", "value": "0 من 0 صنف"},
                        {"label": "مصدر البيانات", "value": "Odoo · product.product", "kind": "mono"},
                    ],
                    icon="🧭",
                )
            ],
            notices=["ابحث بكود الصنف أو جزء من الاسم — مثلاً «مياه» بدل «مياه معدنية 1.5 لتر»."],
            next_steps=[
                Action(f"جرّب كلمة أقصر ({first_token})", f"ابحث في المنتجات عن {first_token}", icon="🔎"),
                Action("اعرض الأصناف المتاحة", "ابحث في المنتجات عن ", kind="link", icon="📦"),
            ],
        )
    rows = []
    bars = []
    stock_value = 0.0
    out_of_stock = 0
    for rec in products:
        price = float(rec.get("list_price") or 0)
        qty = float(rec.get("qty_available") or 0)
        stock_value += price * qty
        if qty <= 0:
            out_of_stock += 1
        rows.append(
            {
                "id": rec.get("id"),
                "name": _clean(rec.get("name")),
                "code": _clean(rec.get("default_code")),
                "price": format_egp(price),
                "qty": format_number(qty),
                "status": "رايق" if qty > 25 else ("منخفض" if qty > 0 else "خلص"),
                "tone": TONE_SUCCESS if qty > 25 else (TONE_PENDING if qty > 0 else TONE_DENIED),
            }
        )
        bars.append({"label": str(rec.get("name") or rec.get("id")), "value": qty, "caption": f"{format_egp(price * qty)} قيمة متاحة"})
    columns = [
        {"key": "id", "label": "ID", "align": "center", "kind": "mono"},
        {"key": "name", "label": "الصنف", "align": "start"},
        {"key": "code", "label": "الكود", "align": "center", "kind": "mono"},
        {"key": "price", "label": "سعر القائمة", "align": "end", "kind": "money"},
        {"key": "qty", "label": "المتاح", "align": "center", "kind": "number"},
        {"key": "status", "label": "الحالة", "align": "center", "kind": "badge"},
    ]
    kpis = [
        Kpi("أصناف", format_number(total), icon="📦", tone=TONE_SUCCESS),
        Kpi("قيمة المخزون (بسعر القائمة)", format_egp(stock_value), hint="سعر قائمة × الكمية المتاحة", icon="💰"),
        Kpi("أصناف خلصت", format_number(out_of_stock), tone=TONE_DENIED if out_of_stock else TONE_SUCCESS, icon="🚫"),
    ]
    sections = [
        table_section("الأصناف", columns, rows, icon="📦", caption="متاح = qty_available المقروء من Odoo.", searchable=len(rows) > 6),
        bars_section("توزيع الكميات", bars, icon="📊"),
    ]
    return Answer(
        status=ACCEPTED,
        tone=TONE_SUCCESS,
        title="استعلام مخزون",
        headline=f"تم الفحص من Odoo — لقيت {total} منتج" + (" مطابق لبحثك." if total == 1 else " مطابقين لبحثك."),
        tool=gw.tool_name if gw else "product.search",
        kpis=kpis,
        sections=sections,
        next_steps=[
            Action(f"اعمل طلب بـ {_short(products[0].get('name', 'المنتج'), 24)}", f"اعمل أمر بيع للعميل 42 لعدد 10 من المنتج {products[0].get('id')}", icon="🧾"),
            Action("ابحث عن صنف تاني", "/search product ", kind="link", icon="🔎"),
        ],
        warnings=[f"{out_of_stock} صنف رصيده صفر — محتاج إعادة طلبية."] if out_of_stock else [],
        data={"products": products, "count": total, "stock_value": round(stock_value, 2)},
    )


def _order_answer(result: Mapping[str, Any], gw: GatewayResult | None) -> Answer:
    order = dict(result.get("order") or {})
    if not order:
        return Answer(
            status=ACCEPTED,
            tone=TONE_EMPTY,
            title="أمر بيع",
            headline="تم الفحص من Odoo — مفيش أمر بيع بالرقم ده.",
            tool=gw.tool_name if gw else "sales.order.get",
            kpis=[Kpi("أوردرات مقابلة", "0", tone=TONE_EMPTY, icon="🧾")],
            sections=[
                fields_section(
                    "إيه اللي حصل",
                    [
                        {"label": "النتيجة", "value": "مفيش أمر بالرقم ده في Odoo"},
                        {"label": "الخطوة التالية", "value": "اسحب آخر الأوردرات واختار منه"},
                    ],
                    icon="🧭",
                )
            ],
            next_steps=[Action("جرّب رقم أمر تاني", "/order ", kind="link", icon="🧾")],
        )
    state = str(order.get("state") or "")
    state_ar, state_tone = ORDER_STATE_AR.get(state, (state or "—", TONE_INFO))
    lines = [dict(line) for line in (order.get("order_line") or order.get("order_lines") or []) if isinstance(line, Mapping)]
    rows = []
    computed = 0.0
    for line in lines:
        qty = float(line.get("product_uom_qty") or line.get("qty") or 0)
        price = float(line.get("price_unit") or 0)
        computed += qty * price
        rows.append(
            {
                "name": _clean(line.get("product_name") or line.get("name") or f"منتج #{line.get('product_id')}"),
                "qty": format_number(qty),
                "price": format_egp(price),
                "line_total": format_egp(qty * price),
            }
        )
    amount_total = float(order.get("amount_total") or 0)
    amount_untaxed = float(order.get("amount_untaxed") or 0)
    tax = round(max(0.0, amount_total - amount_untaxed), 2)
    fields = [
        {"label": "مرجع Odoo", "value": _clean(order.get("name")), "kind": "mono"},
        {"label": "العميل", "value": _clean(order.get("partner_name") or _partner_name(order.get("partner_id")))},
        {"label": "التاريخ", "value": format_date_ar(order.get("date_order"))},
        {"label": "الحالة", "value": state_ar, "tone": state_tone, "kind": "badge"},
    ]
    if order.get("client_order_ref"):
        fields.append({"label": "مفتاح منع التكرار", "value": _short(order.get("client_order_ref")), "kind": "mono"})
    kpis = [
        Kpi("الإجمالي", format_egp(amount_total), tone=TONE_SUCCESS, icon="🧾"),
        Kpi("قبل الضريبة", format_egp(amount_untaxed) if amount_untaxed else "—", hint="price subtotal", icon="➖"),
        Kpi("الضريبة", format_egp(tax) if tax else "—", hint="فرق الإجمالي عن ما قبل الضريبة", icon="％"),
        Kpi("الأصناف", format_number(len(lines)) if lines else "—", icon="📦"),
    ]
    sections = [
        fields_section("رأس الأمر", fields, icon="📄"),
        table_section("بنود الأمر", _ORDER_COLUMNS, rows, icon="🧾", foot={"label": "الإجمالي", "line_total": format_egp(amount_total or computed)} if rows else None),
        steps_section("دورة الحياة", [f"مسودة", "تأكيد البيع", "التسليم", "الفاتورة"], icon="🔄", caption=f"الأمر حاليًا في حالة: {state_ar}"),
    ]
    next_steps = []
    if state == "draft":
        next_steps.append(Action("اعرض بنود الأمر", f"هات تفاصيل الأوردر {order.get('id')}", icon="👀"))
    next_steps.append(Action("راجع سجل التدقيق", "/audit", kind="link", icon="🛡️"))
    return Answer(
        status=ACCEPTED,
        tone=TONE_SUCCESS if state in {"sale", "done"} else TONE_INFO,
        title="أمر بيع",
        headline=f"تم الجلب — الأمر {_clean(order.get('name'))} للعميل {_clean(order.get('partner_name') or _partner_name(order.get('partner_id')))}: {state_ar} بإجمالي {format_egp(amount_total)}.",
        tool=gw.tool_name if gw else "sales.order.get",
        kpis=kpis,
        sections=sections,
        next_steps=next_steps,
        warnings=[f"الإجمالي المحسوب من البنود {format_egp(computed)} يختلف عن المسجل في Odoo — راجع الخصومات."] if lines and abs(computed - amount_total) > 0.5 else [],
        data={"order": order, "lines": lines},
    )


def _created_answer(result: Mapping[str, Any], gw: GatewayResult | None, *, verified: bool) -> Answer:
    order_id = result.get("order_id")
    name = str(result.get("order_name") or "").strip()
    amount = result.get("amount_total")
    state = str(result.get("state") or "draft")
    state_ar, state_tone = ORDER_STATE_AR.get(state, (state, TONE_INFO))
    lines = result.get("lines") or []
    kpis = [
        Kpi("مرجع الأمر", name or (f"#{order_id}" if order_id is not None else "—"), tone=TONE_SUCCESS, icon="🧾"),
        Kpi("الإجمالي", format_egp(amount) if amount is not None else "—", hint="محسوب في Odoo", icon="💰"),
        Kpi("الحالة", state_ar, tone=state_tone, icon="🚦"),
        Kpi("التحقق العكسي", "نجح" if verified else "غير متاح", hint="قراءة راجعة من Odoo" if verified else "", tone=TONE_SUCCESS if verified else TONE_PENDING, icon="🔁"),
    ]
    fields = [
        {"label": "معرّف الأمر", "value": f"#{order_id if order_id is not None else '—'}", "kind": "mono"},
        {"label": "العميل", "value": _clean(result.get("customer_id")), "kind": "mono"},
        {"label": "عدد البنود", "value": format_number(len(lines)) if lines else "—"},
    ]
    if result.get("idempotency_key"):
        fields.append({"label": "مفتاح منع التكرار", "value": _short(result.get("idempotency_key")), "kind": "mono"})
    sections = [fields_section("الإيصال", fields, icon="🧾")]
    notices: list[str] = []
    if isinstance(lines, (list, tuple)) and lines:
        priced = any(
            isinstance(line, Mapping) and (line.get("price_unit") is not None or line.get("price_subtotal") is not None)
            for line in lines
        )
        rows = []
        for line in lines:
            if isinstance(line, Mapping):
                qty = float(line.get("quantity") or line.get("product_uom_qty") or 0)
                row: dict[str, Any] = {"name": _clean(line.get("product_name") or f"منتج #{line.get('product_id')}"), "qty": format_number(qty)}
                if priced:
                    price = float(line.get("price_unit") or 0)
                    row["price"] = format_egp(price)
                    row["line_total"] = format_egp(line.get("price_subtotal") or qty * price)
                rows.append(row)
        if rows:
            sections.append(table_section("البنود المسجلة", _ORDER_COLUMNS if priced else _ORDER_COLUMNS_MIN, rows, icon="📦"))
        if not priced and amount is None:
            notices.append("Odoo رجّع معرّفات البنود والكميات بس — السعر والإجمالي بيتقروا من أمر البيع نفسه (الأمر ده)، وميزان مش بيخمّن أرقام.")
    return Answer(
        status=gw.status if gw else "confirmed_execution",
        tone=TONE_SUCCESS,
        title="إنشاء أمر بيع",
        headline=(f"تم إنشاء أمر البيع {name or ('#' + str(order_id))} في Odoo بعد توقيعك، بإجمالي {format_egp(amount)}.")
        if amount is not None
        else f"تم إنشاء أمر البيع {name or ('#' + str(order_id))} في Odoo بعد توقيعك — الإجمالي بيتحسب في ERP.",
        tool=gw.tool_name if gw else "sales.order.create",
        kpis=kpis,
        sections=sections,
        next_steps=[
            Action(f"تتبع الأمر {order_id}", f"هات تفاصيل الأوردر {order_id}", icon="👀"),
            Action("افتح سجل التدقيق", "/audit", kind="link", icon="🛡️"),
        ],
        notices=[*notices, "الأمر في حالة مسودة: الفوترة والتأكيد النهائي بيتعملوا من شاشة Odoo مش من هنا."],
        data={"created": dict(result), "verified": verified, "order_id": order_id, "order_name": name},
    )


# -------------------------------------------------------------- status answers


def _confirmation_answer(gw: GatewayResult, proposal: Mapping[str, Any] | None) -> Answer:
    proposal = dict(proposal or {})
    args = dict(proposal.get("arguments") or {})
    lines = [dict(line) for line in (args.get("lines") or []) if isinstance(line, Mapping)]
    rows = []
    estimated = 0.0
    for line in lines:
        qty = float(line.get("quantity") or 0)
        price = float(line.get("price_unit") or 0)
        estimated += qty * price
        rows.append({"name": _clean(line.get("product_name") or f"منتج #{line.get('product_id')}"), "qty": format_number(qty), "price": format_egp(price) if price else "—", "line_total": format_egp(qty * price) if price else "—"})
    fields = [
        {"label": "الأداة", "value": f"{proposal.get('tool_name', gw.tool_name)} · v{proposal.get('tool_version', gw.tool_version)}", "kind": "mono"},
        {"label": "العميل", "value": f"#{args.get('customer_id', '—')}", "kind": "mono"},
        {"label": "بند التوقيع", "value": _short(proposal.get("operation_hash"), 16), "kind": "mono"},
        {"label": "صلاحية العرض", "value": _proposal_ttl(proposal), "kind": "mono"},
    ]
    sections = []
    if rows:
        priced = any(row["price"] != "—" for row in rows)
        columns = tuple(col for col in _ORDER_COLUMNS if priced or col["key"] in ("name", "qty"))
        foot = {"label": "تقديري", "line_total": format_egp(estimated)} if estimated and priced else None
        sections.append(table_section("ما سيتم تنفيذه حرفيًا", columns, rows, icon="🧾", foot=foot))
    sections.append(fields_section("تفاصيل المقترح", fields, icon="🔐"))
    return Answer(
        status=CONFIRMATION_REQUIRED,
        tone=TONE_PENDING,
        title="محتاج توقيعك",
        headline=f"محتاج تأكيد منك قبل مانفذ — العرض محجوز {_proposal_window(proposal)} ومش متنفذش أي حاجة لحد دلوقتي.",
        tool=gw.tool_name,
        kpis=[
            Kpi("حالة البوابة", "في انتظار التوقيع", tone=TONE_PENDING, icon="⏳"),
            Kpi("عمليات منفذة", "0", hint="مفيش أي كتابة في Odoo لحد الآن", tone=TONE_INFO, icon="✍️"),
        ],
        sections=sections,
        notices=["مفتاح منع التكرار اتحجز: حتى لو دوسي تأكيد مرتين، أمر واحد بس هيتعمل."],
        next_steps=[Action("شوف العقد", "/tools", kind="link", icon="📜")],
        data={"proposal": proposal, "estimated_total": estimated},
    )


def _denied_answer(gw: GatewayResult) -> Answer:
    code = gw.error_code or "POLICY_DENIED"
    return Answer(
        status=DENIED,
        tone=TONE_DENIED,
        title="مرفوض بالسياسة",
        headline=f"مفيش صلاحية لتنفيذ {gw.tool_name} — البوابة رفضت قبل ما توصل لـ Odoo.",
        tool=gw.tool_name,
        kpis=[Kpi("رمز الرفض", code, tone=TONE_DENIED, icon="⛔"), Kpi("عمليات في ERP", "0", hint="الرفض حدث قبل التنفيذ", tone=TONE_INFO, icon="🛑")],
        sections=[
            list_section(
                "ليه الرفض ده؟",
                [
                    "الصلاحية بتتحسب من سياسة الخادم (users.yaml)، مش من كلام النموذج.",
                    "لو دورك فيه حق قراءة بس، إنشاء الأوامر بيتعمل من حساب المبيعات.",
                    "مفيش أي أثر للطلب ده في قاعدة بيانات ERP — الرفض آمن ومغلق.",
                ],
                icon="🧯",
            )
        ],
        next_steps=[Action("اعرض صلاحياتي", "/whoami", icon="🪪"), Action("اعرف الأداة اللي ليّ حق فيها", "/tools", icon="📜")],
        warnings=[f"العملية محجوبة بالكامل عن المستخدم الحالي ({gw.user_id})."],
    )


def _conflict_answer(gw: GatewayResult) -> Answer:
    replay = gw.status == REPLAY
    stored = dict(gw.result or {})
    rows = []
    stored_lines = [line for line in (stored.get("lines") or []) if isinstance(line, Mapping)]
    priced = any(line.get("price_unit") is not None or line.get("price_subtotal") is not None for line in stored_lines)
    for line in stored_lines:
        qty = float(line.get("quantity") or line.get("product_uom_qty") or 0)
        row: dict[str, Any] = {"name": _clean(line.get("product_name") or f"منتج #{line.get('product_id')}"), "qty": format_number(qty)}
        if priced:
            price = float(line.get("price_unit") or 0)
            row["price"] = format_egp(line.get("price_subtotal") is None and price or line.get("price_unit") or 0)
            row["line_total"] = format_egp(line.get("price_subtotal") or qty * price)
        rows.append(row)
    sections = [fields_section("النتيجة الأصلية", [
        {"label": "مرجع الأمر", "value": _clean(stored.get("order_name")), "kind": "mono"},
        {"label": "الإجمالي", "value": format_egp(stored.get("amount_total")) if stored.get("amount_total") is not None else "—"},
        {"label": "الحالة", "value": _clean(stored.get("state"))},
    ], icon="♻️")]
    if rows:
        sections.insert(0, table_section("بنود العملية الأصلية", _ORDER_COLUMNS if priced else _ORDER_COLUMNS_MIN, rows, icon="📦"))
    return Answer(
        status=gw.status,
        tone=TONE_INFO,
        title="عملية مكررة محجوبة",
        headline=("الطلب ده اتعمل قبل كده بنفس المعاملات — رجّعتلك النتيجة الأصلية ومنعت تكرار القيد."
                  if replay else
                  "فيه عملية بنفس المعاملات شغالة حاليًا أو اتقفلت بنتيجة غير أكيدة — البوابة رافضة تكرها لحد ما الحالة تتحسم."),
        tool=gw.tool_name,
        kpis=[
            Kpi("مفتاح منع التكرار", _short(gw.idempotency_key, 14) or "—", hint="SHA-256 مشتق من المعاملات", tone=TONE_INFO, icon="🔑"),
            Kpi("قيد مكرر اتعمل", "0", tone=TONE_SUCCESS, hint="الحماية اشتغلت", icon="🛡️"),
        ],
        sections=sections,
        warnings=[f"رمز الحالة: {gw.error_code or gw.status}"] if gw.error_code else [],
        notices=[gw.reason] if gw.reason else [],
        next_steps=[Action("شوف عقود الأدوات", "/tools", kind="link", icon="📜")] if not replay else [Action("افتح سجل التدقيق", "/audit", kind="link", icon="🛡️")],
        data={"stored_result": stored, "replay": replay},
    )


def _in_progress_answer(gw: GatewayResult) -> Answer:
    return Answer(
        status=IN_PROGRESS,
        tone=TONE_PENDING,
        title="العملية لسه شغالة",
        headline="في طلب بنفس المعاملات لسه قيد التنفيذ — استنى ثواني وابعت تاني.",
        tool=gw.tool_name,
        notices=["البوابة مش بتعيد تنفيذ عملية نصف منتهية: الحالة دي بتحمي من قيد مكرر أو مفقود."],
        next_steps=[Action("أعد المحاولة", "إعادة المحاولة", icon="🔁")],
    )


def _error_answer(*, title: str, headline: str, code: str, retryable: bool, guidance: list[str], tool: str = "", status: str = "error") -> Answer:
    sections = [list_section("إيه اللي يتعمل دلوقتي؟", guidance, icon="🧭")] if guidance else []
    return Answer(
        status=status,
        tone=TONE_ERROR,
        title=title,
        headline=headline,
        tool=tool,
        kpis=[
            Kpi("رمز الخطأ", code or "—", tone=TONE_ERROR, icon="🧾"),
            Kpi("إعادة المحاولة ممكنة", "أيوه" if retryable else "لأ", hint="حسب تصنيف الخطأ في البوابة", tone=TONE_PENDING if retryable else TONE_DENIED, icon="🔁"),
        ],
        sections=sections,
        next_steps=[Action("حاول تاني", "إعادة المحاولة", icon="🔁")] if retryable else [],
    )


# ------------------------------------------------------------------- entry point


def compose_answer(
    *,
    outcome: str,
    gateway_result: GatewayResult | None = None,
    model_text: str | None = None,
    tool_name: str | None = None,
    tool_arguments: Mapping[str, Any] | None = None,
    structured_error: Any = None,
    timings: Mapping[str, Any] | None = None,
) -> Answer:
    """Compile one turn into a renderable answer. Never raises."""
    gw = gateway_result
    arguments = dict(tool_arguments or {})
    if gw is not None:
        payload = dict(gw.result or {})
        if gw.status == ACCEPTED:
            query = str(arguments.get("query") or "")
            if "customers" in payload:
                answer = _customers_answer(payload, gw, query=query)
            elif "products" in payload:
                answer = _products_answer(payload, gw, query=query)
            elif "customer" in payload:
                answer = _customer_answer(payload, gw)
            elif "order" in payload:
                answer = _order_answer(payload, gw)
            elif payload:
                answer = _created_answer(payload, gw, verified=bool(payload.get("verified")))
            else:
                answer = Answer(
                    status=ACCEPTED,
                    tone=TONE_PENDING,
                    title="جاهز للتنفيذ",
                    headline="تم فحص الطلب والمصادقة عليه في البوابة — التنفيذ على Odoo لسه ما تمّش (وضع التحكم فقط).",
                    tool=gw.tool_name,
                    sections=[fields_section("قرار البوابة", [
                        {"label": "الأداة", "value": f"{gw.tool_name} · v{gw.tool_version}", "kind": "mono"},
                        {"label": "السياسة", "value": _clean(gw.policy_decision)},
                        {"label": "السبب", "value": _clean(gw.reason or "read_allowed")},
                    ], icon="🛡️")],
                )
        elif gw.status == CONFIRMATION_REQUIRED:
            answer = _confirmation_answer(gw, gw.proposal)
        elif gw.status == DENIED:
            answer = _denied_answer(gw)
        elif gw.status in {CONFLICT, REPLAY}:
            answer = _conflict_answer(gw)
        elif gw.status == IN_PROGRESS:
            answer = _in_progress_answer(gw)
        else:
            error = gw.structured_error if gw.structured_error is not None else structured_error
            code = getattr(error, "code", None) or gw.error_code or gw.status
            answer = _error_answer(
                title="العملية اكتملت بنتيجة غير ناجحة" if gw.status == "confirmed_execution" else "فشل تنفيذ في ERP",
                headline=f"العملية لم تكتمل: {getattr(error, 'message', '') or gw.reason or 'راجع البوابة'}.",
                code=str(code),
                retryable=bool(getattr(error, "retryable", False)),
                guidance=[
                    "البوابة سجلت المحاولة في سلسلة التدقيق — مفيش نصف عملية اتساب.",
                    "لو الخطأ اتصال، استنى قاطع الدائرة يترجع ثم أعد المحاولة.",
                    "لو الخطأ تحقق/verification، راجع الرقم المرجعي في Odoo قبل ما تعيد.",
                ],
                tool=gw.tool_name,
                status=str(gw.status),
            )
    else:
        # No gateway decision: text-only chat or a rejected/hallucinated call.
        if outcome == "text_only":
            answer = Answer(
                status="text_only",
                tone=TONE_INFO,
                title="رد مباشر",
                headline=(model_text or "").strip() or "أنا معاك — قولي عايز تعمل إيه في Odoo.",
                tool="",
                next_steps=[
                    Action("دوّر على عميل", "/search customer ", icon="🔎"),
                    Action("افحص المخزون", "فحص قائمة المنتجات والأسعار والمخزون المتاح", icon="📦"),
                ],
            )
        else:
            code_map = {
                "unknown_tool_rejected": ("أداة غير معروفة", "النموذج طلب أداة مش موجودة في سجل العقود، والبوابة حذفتها قبل ما توصل لأي تنفيذ.", "UNKNOWN_TOOL", False, ["الأدوات المتاحة: customer.search / customer.get / product.search / sales.order.create / sales.order.get.", "اتطلب بصيغة عملية واضحة، أو استخدم /tools لعرض العقود.", "أي أداة خارج السجل مرفوضة (fail closed) — مفيش تنفيذ ديناميكي."]),
                "malformed_tool_call": ("نداء غير صالح", "الاستدعاء وصل بشكل مش مطابق للعقد، فرفضناه قبل البوابة.", "INVALID_REQUEST", True, ["صيغة المعاملات لازم تطابق الـ JSON Schema للعقد.", "جرّب تصيغ الطلب من جديد."]),
                "invalid_arguments": ("معاملات مرفوضة", "القيم اللي في الاستدعاء مش مطابقة للعقد (نوع أو حد أدنى غلط).", "INVALID_ARGUMENTS", True, ["راجع النوع: IDs أرقام، والكمية ≥ 1.", "لو مش متأكد من الـ ID، استخدم /search."]),
                "multiple_tool_calls": ("أكتر من طلب في نفس الوقت", "وصل أكتر من نداء أداة في الرد واحد — بننفذ واحد في الرسالة عشان التتقيق يفضل واضح.", "MULTIPLE_TOOL_CALLS", True, ["ابعت كل عملية في رسالة لوحدها."]),
                "llm_error": ("مشكلة في الاتصال بالنموذج", "مزوّد الذكاء الاصطناعي ماردش — الطلب ما وصلش للبوابة أصلًا.", "LLM_ERROR", True, ["اتشيك على المفتاح/الـ endpoint من الإعدادات.", "مفيش أي عملية اتنفذت في ERP."]),
                "erp_error": ("مشكلة في الاتصال بـ ERP", "حصل خطأ في طبقة ERP — البوابة سجلته في سلسلة التدقيق.", "ERP_ERROR", True, ["اتشيك على حالة قاطع الدائرة في تليمتري الخادم.", "لو الاستعلام بسيط، جرّب `/search`."]),
                "conflict": (
                    "قيد مكرر اتحجب",
                    "فيه عملية بنفس المعاملات اتسجلت قبل كده — البوابة منعت الكتابة التانية في Odoo ورّجعتك النتيجة الأصلية.",
                    "IDEMPOTENCY_CONFLICT",
                    False,
                    [
                        "مفتاح منع التكرار SHA-256 مشتق من المعاملات، فأي إعادة لنفس الطلب بتقابل نفس القفل.",
                        "النتيجة الأصلية رجعت زي ما هي — مفيش قيد اتعمل مرتين.",
                        "لو الطلب ده المفروض يبقى جديد، غيّر بند واحد (صنف أو كمية) وهيمشي بمفتاح جديد.",
                    ],
                ),
                "in_progress": (
                    "العملية لسه شغالة",
                    "في طلب بنفس المعاملات قيد التنفيذ حاليًا — البوابة مستنية نتيجته عشان ما تعملش قيد مكرر أو ضايل.",
                    "OPERATION_IN_PROGRESS",
                    True,
                    ["استنى ثواني وابعت تاني؛ لو اتقفلت بنتيجة غير أكيدة البوابة هتطلب مصالحة (reconciliation)."],
                ),
            }
            title, headline, code, retryable, guidance = code_map.get(
                outcome,
                ("مش قادر أكمل", "مفيش رد مفهوم من النموذج — جرّب تصيغ الطلب بشكل أقصر.", "UNKNOWN", True, ["اكتب اسم العميل أو رقم الأمر بصراحة."]),
            )
            error = structured_error
            answer = _error_answer(
                title=title,
                headline=headline,
                code=str(getattr(error, "code", None) or code),
                retryable=bool(getattr(error, "retryable", retryable)) if error is not None else retryable,
                guidance=list(guidance),
                tool=tool_name or "",
            )
    if model_text and answer.analysis == "" and gw is not None and gw.status in {ACCEPTED, CONFIRMATION_REQUIRED}:
        answer.with_analysis(model_text)
    if timings:
        answer.governance = {
            **answer.governance,
            **{key: value for key, value in timings.items() if value not in (None, "")},
        }
    if gw is not None:
        answer.governance = {
            **answer.governance,
            "tool": gw.tool_name,
            "tool_version": gw.tool_version,
            "policy": gw.policy_decision,
            "audit_id": gw.audit_id,
            "idempotency_key": gw.idempotency_key,
            "request_id": gw.request_id,
        }
    return answer


__all__ = ["compose_answer", "ORDER_STATE_AR"]
