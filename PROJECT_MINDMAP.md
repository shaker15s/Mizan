# 🗺️ الخريطة الذهنية الكاملة — Agent-Native ERP (MIZAN / ميزان)

**آخر تحديث:** 16 سبتمبر 2026 — بناءً على قراءة كاملة لكل ملف في المشروع + تشغيل حقيقي للتستات
**الحالة الإجمالية:** POC مكتمل ومُختبَر — Architecture Proven، verdict حي: **GO WITH CONDITIONS**

---

## 1) الفكرة في سطر واحد 🎯

> **نظام تشغيل أعمال (Business Operating System) يكتب فيه صاحب البيزنس بالعربي الطبيعي، والـ AI ينفذ عمليات الـ ERP الحقيقية عبر أدوات deterministic محمية بصلاحيات وتأكيد وتدقيق — مش chatbot، مش browser automation.**

- **الكودنيم:** MIZAN / ميزان
- **السوق:** SMBs عربية — تبدأ من مصر → GCC → MENA
- **الجملة التأسيسية:** *"قول له تعمل إيه في شغلك، مش تدور على الزرار"*

---

## 2) الفلسفة الاستراتيجية (من الـ PRD — 185 قسم) 📜

### القرار التنفيذي الأساسي
- ❌ **مش** هنبني full ERP من البداية
- ❌ **مش** هنبني chatbot عام
- ❌ **مش** هنستخدم browser automation كآلية تنفيذ رئيسية
- ✅ هنبني **سلسلة عمودية واحدة** ونثبتها: عربي → LLM → Router → Tool Contract → Odoo → عملية حقيقية → JSON + Audit

### الفجوة التنافسية (لماذا إحنا مش نسخة Odoo)
Odoo 19 و Dynamics 365 و Oracle Fusion و SAP كلهم ضافوا AI agents — لكن كلهم مربوطين بنظامهم الخاص. الفرصة إحنا:
1. **Arabic-first execution** — اللهجة المصرية أول درجة
2. **SMB-first simplicity**
3. **Cross-ERP compatibility** (Odoo / ERPNext / custom)
4. **Model-agnostic** — أي موديل في العالم
5. **Agent-native core** — AI مش إضافة، الـ action layer هو المنتج
6. **Approvals + Audit** لكل عملية consequential
7. **Trust layer** — الـ moat الحقيقي

### الـ Moat (حسب الـ PRD §145)
> الموديل **مش** الـ moat. الـ chat **مش** الـ moat. **المجموعة** هي: Business ontology + Canonical actions + Integration mappings + Policy engine + Trust/audit + Arabic business intelligence + Eval data + Workflow network

### المحطات الزمنية (من الـ PRD)
- **POC (أسبوع 1-4):** سلسلة واحدة مُثبتة — تم ✅
- **60 يوم:** دومين أغنى + ERPNext adapter + policy UI + pilot deployment
- **90 يوم:** connector framework + pilots خارجيين + billing
- **6-12 شهر:** Full Business OS (sales/inventory/purchasing/accounting/CRM/HR/automation/voice/WhatsApp/marketplace)

---

## 3) خريطة المستودع (Repo Map) 📁

```
agent-native-erp/
├── 00-research/                    # 📚 حزمة الأبحاث (7 سبتمبر) — المرجع الأعلى
│   ├── research-report.md          #    التقرير الرئيسي: الفرضية + المخاطر + أصغر POC
│   ├── source-index.md             #    فهرس المصادر
│   ├── technical-risks.md          #    المخاطر التقنية
│   ├── architecture-challenges.md  #    تحديات المعمارية
│   └── competitive-landscape.md    #    المشهد التنافسي
│
├── 01-spec/                        # 📋 الـ PRD (4,709 سطر، 185 قسم)
│   └── agent_native_erp_prd.md     #    Master Blueprint: رؤية + تقنية + أعمال
│
├── 02-poc/                         # 🏗️ دوكس التصميم والتدقيق (10 مستندات)
│   ├── TECHNICAL_DESIGN.md         #    المعمارية التنفيذية (42.6K)
│   ├── SECURITY_MODEL.md           #    نموذج الأمان + threat model (21.4K)
│   ├── TEST_PLAN.md                #    خطة الـ 50 تست + معايير GO/NO-GO
│   ├── TOOL_CONTRACTS.md           #    عقود الأدوات الخمسة
│   ├── DECISIONS.md                #    18 ADR + amendments + ADR-21
│   ├── FINAL_AUDIT_REPORT.md       #    تدقيق مستقل: F-01..F-21
│   ├── HARDENING_PLAN.md           #    خطة التحصين
│   ├── POST_AUDIT_HARDENING_REPORT.md
│   ├── LIVE_MODEL_EVALUATION_REPORT.md  # 🏆 التقييم الحي (90 تنفيذ حي)
│   └── TRACEABILITY_MATRIX.md      #    مصفوفة التتبع
│
├── 03-poc-src/                     # 💻 كود الـ POC الفعلي (Python 3.12+)
│   ├── poc/                        #    14 مودول إنتاجي (~2,400 سطر)
│   │   ├── gateway.py              #    ⭐ حاجز الثقة (trust boundary)
│   │   ├── agent_runtime.py        #    طبقة تنسيق الـ LLM
│   │   ├── llm_client.py           #    عملاء LLM (أي مزود بالعالم)
│   │   ├── tool_contracts.py       #    سجل الأدوات الخمسة
│   │   ├── idempotency.py          #    متجر عدم التكرار (SQLite)
│   │   ├── confirmation.py         #    آلة حالات التأكيد (9 نقاط)
│   │   ├── audit_store.py          #    سجل التدقيق (hash chain)
│   │   ├── authz.py                #    محرك الصلاحيات (YAML policy)
│   │   ├── errors.py               #    تصنيف الأخطاء الكانوني
│   │   ├── verification.py         #    التحقق بعد الكتابة (read-back)
│   │   ├── odoo_client.py          #    عميل Odoo JSON-2 + Circuit Breaker
│   │   ├── bootstrap.py            #    جذر التركيب (composition root)
│   │   ├── main.py                 #    الـ CLI (عربي تفاعلي)
│   │   ├── web_server.py           #    خادم الـ Web Cockpit (zero-dep)
│   │   ├── db/init.py              #    مهيئ مخزن SQLite (WAL)
│   │   └── web/                    #    واجهة Daylight (HTML/JS/CSS)
│   ├── tests/                      #    341 test — 336 passed ✅ / 5 skipped
│   ├── poc/tests/                  #    run_eval.py + verify_audit.py
│   ├── tools/                      #    Odoo smoke test + نتائجه
│   ├── data/                       #    poc_gateway.db + تقييمات + tmp files
│   ├── docker-compose.yml          #    Odoo 19 + PostgreSQL 15
│   ├── odoo-config/odoo.conf
│   ├── users.yaml                  #    سياسة الصلاحيات (server-owned)
│   ├── requirements.txt            #    6 dependencies فقط — pinned
│   └── .env.example
│
├── PROJECT_MINDMAP.md              # 🗺️ هذا الملف
└── patch.txt / .tmp.patch / .tmp_py.txt  # 🗑️ ملفات مؤقتة (نظافة)
```

---

## 4) البنية المعمارية والتدفق (Architecture Flow) 🏛️

```
المستخدم (عربي مصري)
    ↓
Web Cockpit (index.html/app.js)  أو  CLI (main.py)
    ↓  POST /api/chat
AgentRuntime (orchestration)
    ├── system prompt (عربي + قواعد صارمة)
    ├── LLM call (أي مزود — Anthropic/OpenAI-compatible)
    ├── تحقق من tool call ضد السجل المملوك للخادم
    └── ToolGatewayRequest (envelope صارم)
    ↓
ToolGateway ⭐ (حاجز الثقة — المالك الوحيد للأمان)
    ├── 1. تحقق الـ envelope (identity fields صارمة)
    ├── 2. جلب العقد من ToolRegistry (server-owned)
    ├── 3. فحص tool_version
    ├── 4. تحقق الوسائط ضد JSON Schema
    ├── 5. تقييم الصلاحيات (PolicyEngine — fails closed)
    ├── 6. لو write → idempotency reserve (BEGIN IMMEDIATE)
    ├── 7. لو write + requiresConfirmation → PROPOSAL (9 نقاط)
    ├── 8. التنفيذ عبر OdooJSON2Client (read أو confirmed write)
    ├── 9. Read-back verification بعد كل كتابة
    └── 10. Audit record (hash chain SHA-256) — 100% تغطية
    ↓
Odoo 19 Community (Docker, localhost:8069)
    └── res.partner / product.product / sale.order (JSON-2 API)
```

### قواعد الحدود الصارمة
- `agent_runtime.py` **لا يستورد** `odoo_client` ولا المفاتيح — اختبارات import-scanner تفرضه
- `gateway.py` هو **الوحيد** اللي ينفذ Odoo ويكتب audit ويقيّم صلاحيات
- الـ LLM **لا يحمل** credentials، **لا يقرر** authorization، **لا يتخطى** confirmation، **لا يختار** tenant
- ⚠️ **تحفظ موثق:** عملية واحدة = module isolation **مش** security boundary حقيقية — الإنتاج يتطلب فصل processes/containers

---

## 5) الأدوات الخمسة (Tool Contracts) 🔧

| الأداة | النوع | الخطر | تأكيد | الـ Odoo mapping |
|---|---|---|---|---|
| `customer.search` | read | R0 | لا | `res.partner` search_read (limit 20) |
| `customer.get` | read | R0 | لا | `res.partner` read بالـ ID |
| `product.search` | read | R0 | لا | `product.product` search_read (limit 20) |
| `sales.order.create` | write | R2 | **نعم** | `sale.order` create + `client_order_ref` = idempotency key |
| `sales.order.get` | read | R0 | لا | `sale.order` read بالـ ID (يُستخدم داخلياً للتحقق) |

- كل عقد: `tool_version: 1.0.0` + inputSchema/outputSchema + `additionalProperties: false`
- **حدود معروفة وموثقة:** `ilike` مش بيطبّع الألف/التاء المربوطة — ✅ **اتحلت (17 سبتمبر):** fallback ladder في `_execute_read` (raw → normalized → hamza-variant، max 3 calls عادة 1) — "احمد" بلاقي "أحمد حسن" والعكس؛ البحث بيمس كل الـ partners مش العملاء بس
- 📦 `poc/normalization.py` — الـ normalizer المشترك (نقل من run_eval.py — إزالة ازدواجية)

---

## 6) نموذج الأمان بالتفصيل (Security Model) 🛡️

### الحماية بثلاث طبقات
1. **Gateway policy check** (deterministic, server-side) — من `users.yaml`:
   - `sales_user@test` → 5 أدوات (read + write)
   - `readonly_user@test` → 4 قراءة فقط (write → POLICY_DENIED)
   - `no_access_user@test` → لا شيء (PERMISSION_DENIED)
2. **Odoo access rights** — صلاحيات Odoo نفسها طبقة أخيرة
3. **9-point re-authorization checklist** عند كل تأكيد: هوية المستخدم → tenant → وجود الأداة → الإصدار → الصلاحية → الحالة → الانتهاء → operation_hash → ربط الـ proposal

### عدم التكرار (Idempotency)
- **المفتاح:** content-derived `SHA256(tenant‖user‖tool‖canonical_args)[:32]` — الـ retry الطبيعي بيضرب نفس المفتاح
- **الفصل المقصود:** `execution_id` (UUID, تتبع) ≠ `idempotency_key` (dedup)
- **آلة الحالات:** pending → completed / unknown، مع `BEGIN IMMEDIATE` + `INSERT OR IGNORE` (test-and-set)
- **Provenance:** `sale.order.client_order_ref` يحمل المفتاح — الـ reconciliation يبحث فيه قبل أي إعادة تنفيذ → **صفر تكرارات**
- 🆕 (غير مسجل): التجديد التلقائي للحجز المنتهي (>300 ثانية) بدل deadlock أبدي

### سجل التدقيق (Audit)
- SQLite WAL `data/poc_gateway.db` — 3 جداول: `audit_log`, `idempotency_keys`, `proposals`
- **Hash chain:** كل صف يحمل `previous_hash + own_hash` (SHA-256) — سلسلة tamper-evident
- **Append-only** بـ code discipline (لا UPDATE/DELETE — grep-enforced test)
- **Sanitization allowlist:** `query/customer_id/order_id/limit/lines[product_id,quantity]` فقط — لا PII ولا secrets ولا prompts
- Clock discipline: كل الطوابع UTC ISO-8601 من التطبيق

### الأخطاء الكانونية (18 كود)
`SCHEMA_INVALID` · `TOOL_NOT_FOUND` · `TOOL_VERSION_MISMATCH` · `PERMISSION_DENIED` · `ENTITY_NOT_FOUND` · `ERP_CONNECTION_ERROR` · `ERP_VALIDATION_ERROR` · `VERIFICATION_FAILED` · `CONFIRMATION_EXPIRED` · `CONFIRMATION_HASH_MISMATCH` · `CONFIRMATION_REPLAY` · `IDEMPOTENCY_CONFLICT` · `AMBIGUOUS_OUTCOME` · `BUSINESS_RULE_VIOLATED` · `MAX_TOOL_CALLS_EXCEEDED` · `CONFIRMATION_DECLINED` · `LLM_ERROR` · `AUDIT_WRITE_FAILED` (داخلي)

- كل خطأ: `retryable` + `requires_user_action` + `model_visible` + `category`
- دالة `translate_odoo_exception` تمشي على الـ MRO — بدون استيراد الـ adapter

---

## 7) طبقة الـ LLM (Model-Agnostic) 🤖

- **البروتوكول:** `LLMClientProtocol` ضيق — الـ runtime غير مربوط بأي SDK
- **مزودان:**
  - `AnthropicLLMClient` — SDK رسمي، بيدعم `ANTHROPIC_BASE_URL` (z.ai GLM وغيره)
  - `OpenAICompatibleLLMClient` — **أي endpoint متوافق** (OpenAI, NVIDIA NIM, OpenRouter, Together, DeepInfra, vLLM, Ollama) — عشان المشروع هيتساب والمشتري هيستخدم موديله
  - المصنع: `LLM_PROVIDER=anthropic|openai_compatible`
- `FakeLLMClient` — test double deterministic
- **الـ system prompt:** مستشار أعمال مصري محترف + قواعد صارمة (لا تسريب تفكير داخلي، لا ترجمة أسماء عربية، native tool calling)
- 🆕 **Fallback ذكي (غير مسجل):** لو الموديل طبع tool call كـ JSON في النص بدل native calls — الـ runtime يلتقطه ويرسّمه للأداة الصحيحة + تنظيف تسريب الرأس التحليلي
- ✅ **tool_choice opt-in (17 سبتمبر):** `FORCE_TOOL_CHOICE` env (default off) — anthropic → `{"type":"any"}` / `{"type":"tool","name":X}`، openai_compatible → `"required"` — يمنع تردد text_only على العمليات التجارية (deployment-level opt-in، حسب فلسفة المشروع)

---

## 8) الـ Web Cockpit (واجهة النهارية) 🖥️

- **خادم:** `ThreadingHTTPServer` zero-dependency (مكتبة stdlib فقط) — port 8080
- **الواجهة:** Tailwind CDN + Cairo/IBM Plex Arabic + JetBrains Mono — RTL كامل
- **الـ APIs:**
  - `GET /api/health` · `/api/telemetry` (Odoo online, circuit breaker, model, security) · `/api/tools` · `/api/audit` (chain validity)
  - `POST /api/chat` · `/api/confirm` · `/api/decline` · `/api/test/replay` (محاكاة هجوم تكرار)
- **المكونات:**
  - دردشة مع بطاقات نتائج (جداول عملاء / كروت منتجات بمؤشرات مخزون / تفاصيل أوردر بشريط مراحل)
  - **بطاقة تأكيد تفاعلية:** كاونتن داون 60 ثانية + تعديل كمية + اعتماد/رفض
  - **مسار الأمان 8 مراحل** (stepper animation حي)
  - درج سجل التدقيق التشفيري + مودال عقود الأدوات
  - **تصدير CSV** + نسخ الملخص (تحسينات Consortium)
- 🆕 (غير مسجل): `[CHAT_IN]/[CHAT_OUT]` logging + إصلاح بطاقة التأكيد لتقرأ `lines` مش `order_lines` بس + fix لـ adjustQty
- ✅ (17 سبتمبر): **صوت حقيقي** — Web Speech API (`ar-EG`, native feature) بدل الـ placeholder + fallback رشيق لمتصفحات مش بتدعم · **خطوط premium** — Alexandria (sans/UI) + Readex Pro (data) بدل Cairo/IBM Plex (Cairo بقى generic) · JetBrains Mono للـ hashes

---

## 9) الاختبارات والتقييم (Tests & Live Evaluation) 🧪

### الحالة الحقيقية (اتحققت منها بنفسي — آخر تشغيل 17 سبتمبر)
- **pytest:** ✅ **351 passed, 5 skipped** (الـ 5 integration محتاجة Odoo حي) — 23.8 ثانية (+15 test جديد: normalization ladder + tool_choice)
- **ملفات الاختبار:** ~20 ملف: gateway, idempotency, confirmation, authz, audit_store, tool_contracts, odoo_client, llm, errors, verification, normalization_gateway (جديد), tool_choice (جديد), scenarios (golden path, security, errors), phase regressions
- **استيراد scanner + env-scan + secret-leak scan:** اختبارات من الطبقة الأولى

### التقييم الحي (10 سبتمبر — AUTHORITATIVE)
- **90 تنفيذ حي** على 50 حالة عربية مصرية — Claude Haiku 4 عبر FCC proxy — Odoo 19 Docker حي
- **Verdict: GO WITH CONDITIONS** (86.67% داخل نطاق 75–89%)

| المقياس | الهدف | المقيس | الحالة |
|---|---|---|---|
| Tool selection | ≥90% | **86.67%** (78/90) | GO WITH CONDITIONS |
| Parameter accuracy (semantic) | ≥95% | 74.44% | CONDITIONAL (orthographic) |
| Schema validity | ≥99% | **100%** | PROVEN |
| Unauthorized writes | 0 | **0** | PROVEN ✅ |
| Duplicate orders | 0 | **0** | PROVEN ✅ |
| Audit hash chain | valid | **True** | PROVEN ✅ |
| Confirmation replay prevention | 100% | **100%** | PROVEN ✅ |
| Prompt injection resistance | 1/1 | **1/1** | PROVEN ✅ |
| Compound write success | ≥90% | 50% | CONDITIONAL (text_only hesitation) |
| Read repeatability (pass^3) | ≥90% | 65% | CONDITIONAL (variance) |
| P95 latency read | <5s | 29.1s | MEASURED (proxy latency) |

### التوصيات الثلاث للإنتاج (من التقييم)
1. الموديل **لازم** ما يبقاش ليه وصول مباشر للـ ERP — الـ dual-plane أثبت نفسه (الـ gateway وقف 100% من الانتهاكات)
2. **Normalization عربي deterministic إجباري** لحل اللهجات
3. `tool_choice=any` للعمليات التجارية يمنع تردد text_only

---

## 10) تاريخ التطوير (Git History) 📊

```
fccebb2  baseline: research + POC design + step-1 environment
8291ada  checkpoint: full POC implementation (steps 1-13) + eval report
741da72  phase 0: reproducibility & hygiene
aaf86cc  phase 1: correctness fixes
ae57cc8  phase 2: execute read-only tools in the gateway (B1)
6944a8b  phase 3: eval harness + audit verifier
393a7ff  docs: STEP15 report addendum
90b4ce4  post-audit hardening: reconciliation pass (F-01..F-21)
634e983  llm: provider-agnostic clients — any model in the world
729013a  feat(eval): 50-case live Arabic evaluation (Haiku via FCC proxy)
72a4f95  feat(web): enterprise daylight web cockpit + zero-dep server
105a18d  feat: comprehensive improvement - LLM quality, UX, security hardening
29aa1b4  feat: Consortium Plan Phase 1 - Circuit Breaker, SQLite WAL concurrency, CSV export & sparklines  ← HEAD
```

### ✅ اتلزمت (17 سبتمبر — 6 commits)
```
c2ea803  fix: text-JSON tool-call fallback, expired-reservation renewal, confirmation card lines fix, chat in/out logging
f0909eb  chore: remove temp patch files and tmp test databases
55ddea7  docs: full project mind map
da95a37  feat: Arabic normalization fallback ladder in gateway + tool_choice opt-in (FORCE_TOOL_CHOICE)
fcc7909  feat(web): premium Arabic fonts (Alexandria + Readex Pro) and real voice input via Web Speech API (ar-EG)
6280cdb  docs+ci: add project README and GitHub Actions CI workflow
```

---

## 11) فحص الواقع (Reality Check) — بأسلوب [WORKING]/[FAKE] 🚦

| المكوّن | الحالة | الدليل |
|---|---|---|
| Tool Gateway (trust boundary) | 🟢 **[WORKING]** | 336 tests + تدقيق مستقل: الـ LLM لا يستطيع تجاوز أي شيء |
| Idempotency (صفر تكرار) | 🟢 **[WORKING]** | تقييم حي: 0 تكرارات + اختبارات concurrency |
| Confirmation lifecycle (9 نقاط) | 🟢 **[WORKING]** | تقييم حي: 100% replay prevention |
| Audit hash chain | 🟢 **[WORKING]** | `verify_chain() == True` حي + UI يعرضه |
| Read tools (تنفيذ حقيقي) | 🟢 **[WORKING]** | `_execute_read` ينفذ عبر Odoo client فعلياً (أُصلح بعد F-03) |
| Write tools (تنفيذ حقيقي) | 🟢 **[WORKING]** | تقييم حي: أوامر بيع فعلية في Odoo draft + read-back |
| Web Cockpit | 🟢 **[WORKING]** | خادم zero-dep + APIs كاملة؛ يحتاج Odoo شغال للميزات الكاملة |
| الـ LLM عبر proxy | 🟢 **[WORKING]** | 90 تنفيذ حي على Haiku |
| Circuit Breaker | 🟢 **[WORKING]** | في `odoo_client.py` + UI badge — ponytail: global singleton، per-tenant لو throughput |
| الأداء P95 <5s | 🟡 **[PARTIALLY]** | 29.1s مقاس — معظمه latency الـ proxy/الموديل، مش الـ pipeline |
| Multi-tenant isolation | 🔴 **[MISSING]** | tenant واحد فقط — deferred للـ pre-pilot (موثق) |
| ERPNext adapter | 🔴 **[MISSING]** | تصميم فقط في الـ PRD §35 |
| Separate trust domains (production) | 🔴 **[MISSING]** | module isolation فقط — production requirement موثق |
| MCP adapter | 🔴 **[MISSING]** | ADR-16: طبقة اختيارية لاحقاً |

---

## 12) القرارات المعمارية (18 ADR — من DECISIONS.md) ⚖️

| ADR | القرار |
|---|---|
| 01 | عملية Python واحدة (module isolation، مش security boundary) |
| 02 | Python 3.12+ (لا frameworks — لا LangChain/FastAPI) |
| 03 | SQLite WAL للثلاث جداول (انحراف موثق عن توصية PostgreSQL) |
| 04 | مفاتيح Odoo API لكل مستخدم (مش bot account) |
| 05 | Agent loop مخصص مصغر (بدون framework) |
| 06 | Odoo JSON-2 adapter نحيف (لا CLI-Anything في الـ runtime) |
| 07 | Read-back verification بعد كل كتابة |
| 08 | Idempotency بمفاتيح content-addressable + reconciliation بالـ provenance |
| 09 | Hash chain SHA-256 لسجل التدقيق |
| 10 | YAML policy للصلاحيات (لا OPA) |
| 11 | واجهة CLI فقط (⚠️ الأصل — الـ Web Cockpit أُضيف لاحقاً) |
| 12 | Anthropic Claude Sonnet 4.5 كمزود واحد (اتوسع لاحقاً لأي مزود) |
| 13 | عربي مصري (مش MSA) للـ test set |
| 14 | 50 حالة (مش 200) |
| 15 | انتهاء التأكيد 5 دقائق |
| 16 | MCP كطبقة توافق اختيارية |
| 17 | تأجيل التطبيع الإملائي العربي |
| 18 | لا `limit` parameter للـ LLM — hard cap عند الـ adapter |
| 21 | Reconciliation addendum (تقسيم pre/post-create failure + صدق التقييم) |

---

## 13) إيه اللي ناقص / الخطوة الجاية (Roadmap) 🚀

### فوري — ✅ تم (17 سبتمبر)
1. ~~التزم الـ 4 ملفات المعدلة~~ — تم (c2ea803)
2. ~~النظافة~~ — تم (f0909eb)
3. ~~README + CI~~ — تم (6280cdb) — `README.md` + `.github/workflows/ci.yml` (CI على push/PR، الـ 5 integration auto-skip)
4. ✅ **Accuracy fix** — normalization fallback ladder في الـ gateway (da95a37) — "احمد" بلاقي "أحمد حسن" والعكس (max 3 calls، عادة 1)
5. ✅ **tool_choice opt-in** — `FORCE_TOOL_CHOICE` env (da95a37) — default off، يمنع تردد text_only
6. ✅ **خطوط premium** — Alexandria + Readex Pro (fcc7909)
7. ✅ **صوت حقيقي** — Web Speech API ar-EG بدل الـ placeholder (fcc7909)

### قصير المدى (60 يوم — حسب الـ PRD §141)
- دومين أغنى (Inventory + Warehouses → Purchasing + Suppliers)
- **ERPNext adapter** (أول cross-ERP)
- Policy engine UI + Approvals Center + AI Activity Center
- ~~tool_choice للعمليات التجارية~~ ✅ تم · ~~تحسين accuracy بـ normalization~~ ✅ تم (الـ ladder) — الباقي: aliases + scorecard view + eval as regression gate (skill: `wshobson/agents@llm-evaluation`, 11.3K installs)

### متوسط (90 يوم)
- Production-grade connector framework + أول pilots خارجيين (5-10 شركات، founder-led)
- Billing + observability (trace_id dashboards) + connector certification
- **فصل trust domains:** agent runtime ≠ gateway process/containers + secret manager

### لاحقاً (6-12 شهر)
- Native ERP core (Phase A→G: sales → inventory → purchasing → receivables → cash → HR → automation)
- Workflow engine + Autonomous mode L0→L5 (opt-in صريح)
- MCP adapter + Marketplace (verticals: Distribution أول مرشح)
- WhatsApp / Voice channels فوق نفس الـ action layer

### Connectors — الحالة النهاردة
- ✅ **شغالين:** GitHub, Context7 (دوكس), HuggingFace, Firecrawl/exa (بحث), Apify, Zapier, Claude Browser (preview مدمج)
- ❌ **فشل اتصال (timeout — بيرجعوا بـ restart):** playwright, chrome-devtools, desktop-commander, browser-use, ui5
- 🔐 **محتاجة OAuth (وثّقها من claude.ai settings أو `claude mcp` تفاعلي):** Stripe, QuickBooks, PayPal, Square, Notion, Slack, Linear, Figma, Datadog, HubSpot — مفيدة لاحقاً للـ ERP (payments/accounting/integrations)

---

## 14) الحكم النهائي 🏁

> **الـ POC أثبت الفرضية الأساسية:** عربي مصري → LLM → أداة صحيحة → policy يتحقق → Odoo ينفذ عملية حقيقية → نتيجة structured → audit مشفر → رد صادق.
>
> **الـ control plane حقيقي ومُختبَر:** صفر كتابات غير مصرح بها، صفر تكرارات، 100% schema validity، hash chain سليم — كلها مُثبتة حياً وبتستات.
>
> **الفرصة:** طبقة تشغيل الأعمال الـ agent-native للسوق العربي — الموديل قابل للاستبدال، والـ action layer هو المنتج.
>
> **اللي فاضل مش architecture — ده scale:** أدوات أكتر، ERP تاني، tenants، pilots، billing، فصل trust domains.
