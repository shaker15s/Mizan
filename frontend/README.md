# MIZAN Frontend (Phase 10 — React/TypeScript)

مرحباً. ده واجهة MIZAN الجديدة مبنية بـ React 18 + TypeScript + Vite، بتحل محل
الواجهة القديمة HTML/JS الخام في `03-poc-src/poc/web/` على مراحل.

## مبادئ التصميم (ماشية مع plan.md)

1. **الخادم مصدر الحقيقة** — الواجهة لا تخترع أي نجاح أو ID أو حالة تنفيذ.
2. **سلوك fail-closed** — أي استثناء في الشبكة أو استجابة غير متوقعة بتعرض
   رسالة خطأ للمستخدم وما بتعتبرش العملية نجحت.
3. **RTL عربي أول** — `dir="rtl"` افتراضي + خطط خطوط عربية في `styles.css`.
4. **CSP صارم** — النص نفسه اللي بيرفعه السيرفر بايثون (مفيش سكربتات خارجية،
   مفيش `unsafe-inline` غير للـ styles عشان RTL).
5. **Same-origin فقط** — في التطوير Vite بيعمل proxy للـ `/api` على
   `http://127.0.0.1:8765` (السيرفر بايثون) عشان ما نحتاج CORS واسع.

## التشغيل (لأغراض التطوير)

السيرفر بايثون شغال ع البورت 8765:
```bash
cd 03-poc-src
POC_USER_ID=sales_user@test POC_TENANT_ID=poc_tenant_001 MIZAN_DEV=1 \
  .venv/bin/python -m poc.main
```

وبجوارو الواجهة:
```bash
cd frontend
npm install
npm run dev   # -> http://localhost:5173
```

## البناء للإنتاج

```bash
cd frontend
npm run build    # يطلع الملفات في frontend/dist/
```

مجلد `dist/` معمول عشان يتخدم من السيرفر بايثون مباشرة في مرحلة لاحقة (Phase 9c)
بدل مجلد `poc/web/` الحالي. لحد الآن السيرفر بايثون ما زال يخدم الواجهة
القديمة؛ بنية الـ React جاهزة ومبنية ونضيفة، والربط النهائي جاي في الخطوة
الجاية من Phase 10.

## هياكل البيانات

الأنواع المشتركة في `src/types.ts` معرّفة لتطابق تماماً إجابات الـ API
بايثون. `src/api.ts` طبقة رفيعة فوق `fetch` بضيف الـ headers المطلوبة
وهيطرح CSRF token لما نوصّله في Phase 9b.

## الاختبارات

```bash
npm test      # vitest (هيضاف ليها test cases في المرحلة القادمة)
npm run lint  # tsc --noEmit
```
