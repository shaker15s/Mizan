# Scale Policy — Mizan Phase 14

الخطة بتمنع التوسع قبل ما يكون فيه دليل عملي يدعمه. هذه الوثيقة بتحدد الشروط
اللي عندها نسمح بإدخال Postgres / Redis / الخدمات الإضافية.

## Non-negotiable invariants (ما نتساهل فيها)

1. **LLM untrusted** — أي استجابة موديل بتمر بسياسة الـ policy/tool-gateway،
   وما فيش مسار مباشر للكتابة في الـ ERP.
2. **Server is authority** — الـ state machine والـ proposal IDs والـ lease
   والـ audit كلها مملوكة للخادم.
3. **No fabrication** — الخادم ما يخترعش أرقام أوامر أو نجاحات؛ النجاح ييجي بعد
   تحقق عكسي من الـ ERP.
4. **Fail closed** — أي غموض في السياسة أو أداة مش معروفة أو توقيع ناقص → رفض.
5. **Arabic first** — الرسائل الأساسية بالعربي، والـ RTL مدعوم من أول يوم.

## Conditions for PostgreSQL

ننتقل من SQLite إلى Postgres فقط لو:

- البيئة pilot بتخدم أكثر من **10 مستخدمين متزامنين** و p95 latency للكتابة
  تجاوز 900ms باستمرار، أو
- الحجم التخزيني لسجل audit تجاوز **10GB** وأصبحت عمليات الاستعلام والتحقق
  من السلسلة بطيئة جدًا (> 1s للـ verify_chain)، أو
- صار فيه requirement حقيقي لمشاركة الحالة بين عدة عمليات خادم (horizontal
  scale) مع ضمانات transactional قوية.

وعندها فقط نضيف تنفيذ `Storage` جديد من `poc/storage.py` بدل ما نكتب الـ SQL
من جديد في كل مكان.

## Conditions for Redis

Redis مسموح به فقط للـ cache/ephemeral state:

- rate-limit counters (بدل الذاكرة الحالية لو صار فيه load عالي).
- short-lived lease locks عبر TTL (بدل التأجير الحالي في نفس العملية).
- caching نتائج read من Odoo لو تكررت نفس الاستعلامات بكثرة وضمن صلاحيات
  المستخدم.

ممنوع استخدام Redis كسجل audit أو كمصدر للحقائق.

## Conditions for multi-process / multi-node

نسمح بتشغيل أكثر من عملية/خادم فقط لما:

- `poc/deploy.py` فيه readiness/liveness endpoints شغالة على كل العقد.
- الـ idempotency store متعدد العقد (Postgres أو Redis مع معاملات مناسبة).
- الـ audit chain ما زال سلسلة واحدة متصلة عبر العقد (hash chain على مزود
  مشترك).
- تم اختبار failover في بيئة staging قبل الإنتاج.

## Forbidden until justified (ممنوع بدون مبرر قوي)

- Multi-agent swarm
- Kubernetes في المراحل الأولى
- Kafka/event bus في كل مسار
- Browser automation واسع النطاق
- تنفيذ مالي مستقل بدون مراجعة بشرية واضحة
- توسيع كتالوج الأدوات بشكل غير منضبط

## Evidence gate

كل خطوة توسع تتطلب:

1. تقرير harness (144 case + حالات الانحدار الجديدة) PASS.
2. تقرير اختبارات حمل (load test) يثبت أن التحسين بيحل المشكلة فعلاً.
3. تحديث الـ `EVIDENCE_MANIFEST.json` مع commit SHA والنتائج.
4. Review من الـ policy على أن الـ invariants ما اتكسرتش.

