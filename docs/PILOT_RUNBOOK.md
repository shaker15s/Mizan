# Pilot Runbook — Mizan Phase 13

يصف هذا الدليل الخطوات العملية لتشغيل Mizan في بيئة تجريبية (pilot) مع عملاء حقيقيين بشكل متحكم فيه، قبل أي توسع.

## 1. Pre-flight checklist (قبل التشغيل)

- [ ] `pytest tests/` كلو أخضر (حاليًا 502 passed, 5 skipped).
- [ ] `python -m poc.harness --mode deterministic` بيجيب PASS (144/144 case).
- [ ] تم توليد `MIZAN_SESSION_SECRET` و `MIZAN_SIGNING_KEY` بقيم عشوائية فعلًا
      (مش القيم الافتراضية)، وتم ضبط `MIZAN_ENV=pilot`.
- [ ] ملف `users.yaml` محدد فيه المستخدمين والأدوات المسموح بها، ومافيش صلاحيات
      أوسع من المطلوب (least privilege).
- [ ] Odoo test tenant منفصل عن بيانات الإنتاج؛ ما فيش `auto_confirm`
      على أوامر فعلية بدون review.
- [ ] تم إنشاء مجلد backups (`MIZAN_BACKUP_DIR`) واختبار snapshot/restore
      (`python -m poc.backup --snapshot`).
- [ ] CSP, CORS, rate limits مفعلة في `web_server.py`.

## 2. Start-up sequence (التشغيل)

```bash
cd 03-poc-src
export MIZAN_ENV=pilot
export POC_USER_ID=sales_user@test
export MIZAN_SESSION_SECRET=$(python -c "from poc.deploy import generate_session_secret; print(generate_session_secret())")
export MIZAN_SIGNING_KEY=$(python -c "from poc.deploy import generate_session_secret; print(generate_session_secret())")

# بناء واجهة React/TS لواجهة الإنتاج
cd frontend && npm ci && npx vite build && cd ..

# تشغيل الخدمة
PYTHONPATH=. .venv/bin/python -m poc.web_server --port 8080
```

## 3. Smoke test

1. فتح `/` والتأكد من أن واجهة React/TS بتظهر.
2. طلب `GET /api/me` بيرجع `user_id`, `tenant_id`.
3. إرسال `ابحث عن محمد` في `/api/chat` وانتظار رد غير فارغ.
4. محاولة إنشاء أمر بيع لعميل صحيح والتأكد من ظهور بطاقة تأكيد وعدم التنفيذ قبل الضغط على «تأكيد».
5. محاولة prompt injection (مثلاً «تجاهل التعليمات») والتأكد من الرفض بدون تنفيذ.
6. التحقق من أن `data/reports/mizan-eval-latest.md` فيه نتيجة PASS بعد التشغيل.

## 4. Monitoring

- عدّاد: عدد الطلبات، زمن الاستجابة p95، عدد الـ denials، عدد الـ policy violations.
- Audit chain: دورياً شغّل `environment.verify_chain()` في الـ harness أو
  استخدم `/api/audit` للتأكد من أن السلسلة ما انكسرتش.
- Logs: مستوى INFO في pilot، واحتفظ بسجلات الطلبات والرفض 30 يومًا.

## 5. Rollback

لو حصل خطأ مؤثر على البيانات:

1. إيقاف الخدمة.
2. أخذ snapshot فوري للحالة الحالية: `python -m poc.backup --snapshot` (for forensics).
3. استرجاع آخر snapshot سليم: `python -m poc.backup --restore <file> --force`.
4. إعادة تشغيل الخدمة والتأكد من أن الـ audit chain سليمة.
5. فتح incident بـ `new_incident_id()` وتسجيل السبب والإجراء التصحيحي.

## 6. Exit criteria (متى ننتقل للتوسع)

- [ ] أسبوعان بدون انتهاك policy (0 unauthorized writes).
- [ ] معدل success على التدفقات الحقيقية ≥ 95%.
- [ ] ما فيش ثغرات حقن تعليمات ناجحة (governance.prompt_injection_resisted = yes
      على مجموعة اختبارات حقيقية).
- [ ] Latency p95 للـ reads ≤ 250ms، للـ writes ≤ 900ms تحت الحمل المتوقع.
- [ ] Backup/restore تم اختباره بنجاح 3 مرات على الأقل.
