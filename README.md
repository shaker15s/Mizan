# MIZAN / ميزان — Agent-Native ERP

> **قول له تعمل إيه في شغلك، مش تدور على الزرار.**
>
> نظام تشغيل أعمال يكتب فيه صاحب البيزنس بالعربي الطبيعي، والـ AI ينفذ عمليات الـ ERP الحقيقية عبر أدوات deterministic محمية بصلاحيات وتأكيد وتدقيق — مش chatbot، مش browser automation.

## المعمارية

```
المستخدم (عربي مصري)
    ↓
Web Cockpit (Tailwind + vanilla JS)  |  CLI (python -m poc.main)
    ↓
AgentRuntime — tool calling عبر أي مزود LLM (Anthropic / OpenAI-compatible)
    ↓
ToolGateway ⭐ حاجز الثقة — schema validation · RBAC · idempotency
    · تأكيد 9 نقاط · audit hash chain (SHA-256)
    ↓
Odoo 19 Community (Docker, JSON-2 API)
```

## الحالة الحقيقية

| المكوّن | الحالة |
|---|---|
| Tool Gateway (trust boundary) | 🟢 [WORKING] |
| Idempotency (صفر تكرارات) | 🟢 [WORKING] |
| Confirmation lifecycle (9 نقاط) | 🟢 [WORKING] |
| Audit hash chain (SHA-256) | 🟢 [WORKING] |
| Read + Write tools (تنفيذ حقيقي) | 🟢 [WORKING] |
| Web Cockpit + CLI | 🟢 [WORKING] |
| LLM (أي مزود) | 🟢 [WORKING] |
| Arabic normalization (fallback ladder) | 🟢 [WORKING] |
| Multi-tenant isolation | 🔴 [MISSING] — tenant واحد (POC) |
| ERPNext adapter | 🔴 [MISSING] — roadmap |
| Production trust separation | 🔴 [MISSING] — process/container فصل مطلوب للإنتاج |

## التقييم الحي (90 تنفيذ — Claude Haiku)

**Verdict: GO WITH CONDITIONS**

| المقياس | المقيس |
|---|---|
| Tool selection | 86.67% |
| Schema validity | 100% |
| Unauthorized writes | **0** |
| Duplicate orders | **0** |
| Audit hash chain | Valid |
| Prompt injection resistance | 1/1 |

## التشغيل السريع

```bash
cd 03-poc-src
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
copy .env.example .env    # واملأ المفاتيح
python -m poc.db.init
python -m poc.web_server --port 8080    # أو: python -m poc.main --interactive
```

التستات:

```bash
cd 03-poc-src && .venv/Scripts/python.exe -m pytest tests/ -q
```

## نموذج الأمان

- **الـ Gateway هو المالك الوحيد للأمان:** الـ LLM لا يحمل credentials، لا يقرر authorization، لا يتخطى التأكيد، لا يختار tenant
- **صلاحيات deterministic** من `users.yaml` — fail closed على أي التباس
- **تأكيد بشري 9 نقاط** لكل كتابة: هوية → tenant → أداة → إصدار → صلاحية → حالة → انتهاء → operation_hash → ربط الـ proposal
- **عدم التكرار:** مفاتيح content-addressable + provenance في `sale.order.client_order_ref` — الـ retry الطبيعي آمن
- **تدقيق tamper-evident:** سلسلة hash SHA-256، append-only، sanitization allowlist — لا PII ولا secrets

## الدوكيومنتيشن

- [الـ PRD الكامل](01-spec/agent_native_erp_prd.md) — الرؤية والاستراتيجية (185 قسم)
- [التصميم التقني](02-poc/TECHNICAL_DESIGN.md) — المعمارية التنفيذية
- [نموذج الأمان](02-poc/SECURITY_MODEL.md) — threat model + lifecycle
- [التقييم الحي](02-poc/LIVE_MODEL_EVALUATION_REPORT.md) — 90 تنفيذ حي
- [الخريطة الذهنية](PROJECT_MINDMAP.md) — كل التفاصيل في مكان واحد
