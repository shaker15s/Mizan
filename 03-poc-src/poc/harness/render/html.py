"""HTML renderer — a single self-contained RTL dashboard.

One file, no CDN, no build step: it must survive as a CI artifact, sit in a
`data/reports/` folder, and still open correctly two years from now. Arabic
first (logical properties, `dir="auto"` isolates for identifiers and user
text), dark-ink-on-paper palette, and the whole report is readable without
JavaScript — JS only adds filtering and collapsing.
"""

from __future__ import annotations

import html
import json
from typing import Any, Mapping, Sequence

_CSS = """
:root{
  --ink:#101725; --ink-soft:#4a5568; --paper:#fbfaf7; --card:#ffffff;
  --line:#e6e1d8; --accent:#0f766e; --accent-soft:#d7f0ec;
  --ok:#15803d; --warn:#b45309; --bad:#b91c1c; --mono:ui-monospace,"SFMono-Regular","JetBrains Mono",Menlo,monospace;
  --sans:"IBM Plex Sans Arabic","Noto Kufi Arabic","Cairo","Segoe UI",Tahoma,system-ui,-apple-system,sans-serif;
  --radius:14px; --shadow:0 1px 2px rgba(16,23,37,.06),0 10px 30px -18px rgba(16,23,37,.35);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.78;letter-spacing:0;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:28px 22px 80px}
header.hero{background:linear-gradient(135deg,#0b3f3a 0%,#0f766e 55%,#12836f 100%);color:#eafaf6;border-radius:20px;padding:26px 28px;box-shadow:var(--shadow)}
.hero-top{display:flex;flex-wrap:wrap;align-items:center;gap:14px;justify-content:space-between}
.hero h1{margin:0;font-size:23px;font-weight:700;letter-spacing:-.2px}
.hero .sub{margin:6px 0 0;font-size:14px;opacity:.82}
.verdict{font-size:15px;font-weight:700;padding:8px 16px;border-radius:999px;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.28)}
.verdict.pass{background:#137f4b;border-color:#1aa76a}
.verdict.gate{background:#a16207;border-color:#c88a12}
.verdict.fail{background:#a3231e;border-color:#cf3a34}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.chip{font-size:12.5px;padding:5px 11px;border-radius:999px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);white-space:nowrap}
.chip code{font-family:var(--mono);font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin:18px 0 4px}
.kpi{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.18);border-radius:var(--radius);padding:13px 15px}
.kpi .label{font-size:12.5px;opacity:.8}
.kpi .value{font-size:26px;font-weight:700;line-height:1.25;margin-top:2px}
.kpi .value small{font-size:13px;font-weight:600;opacity:.75}
section{margin-top:26px}
h2{font-size:16px;margin:0 0 12px;font-weight:700;display:flex;align-items:center;gap:9px}
h2 .en{font-size:12.5px;color:var(--ink-soft);font-weight:600;font-family:var(--mono)}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.rows{display:grid;gap:1px;background:var(--line)}
.row{display:grid;grid-template-columns:minmax(120px,1fr) auto auto;gap:12px;align-items:center;background:var(--card);padding:11px 15px}
.row .k{font-size:14.5px}
.row .v{font-family:var(--mono);font-size:14px;text-align:end;min-width:88px}
.state{font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap}
.state.ok{background:#e7f6ec;color:var(--ok)}
.state.bad{background:#fdeaea;color:var(--bad)}
.state.na{background:#eef0f3;color:var(--ink-soft)}
.state.warn{background:#fdf3e2;color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:10px 13px;text-align:start;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12.5px;color:var(--ink-soft);font-weight:700;background:#f6f4ef;position:sticky;top:0}
tbody tr:hover{background:#faf9f5}
td.num,th.num{font-family:var(--mono);text-align:end;white-space:nowrap}
.meter{position:relative;height:7px;border-radius:999px;background:#eceae3;min-width:96px;overflow:hidden}
.meter i{position:absolute;inset-inline-start:0;top:0;bottom:0;border-radius:999px;background:var(--accent)}
.meter.low i{background:var(--bad)}
.meter.mid i{background:var(--warn)}
.meter-wrap{display:flex;align-items:center;gap:9px}
.meter-wrap span{font-family:var(--mono);font-size:12.5px;color:var(--ink-soft);min-width:52px}
.note{padding:12px 15px;font-size:13.5px;color:var(--ink-soft);background:#f7f6f1;border-inline-start:3px solid var(--accent)}
code,pre{font-family:var(--mono);font-size:12.8px}
pre{margin:0;padding:12px 14px;background:#f7f6f1;border:1px solid var(--line);border-radius:10px;overflow:auto;white-space:pre-wrap;line-height:1.6}
.tag{display:inline-block;font-family:var(--mono);font-size:11.5px;padding:2px 8px;border-radius:6px;background:#f1efe9;border:1px solid var(--line);color:var(--ink-soft);margin-inline-end:4px}
.tag.hot{background:#fdeaea;border-color:#f3c8c6;color:var(--bad)}
[dir="auto"]{unicode-bidi:isolate}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;align-items:center}
.filters button{font:inherit;font-size:13px;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--ink-soft);cursor:pointer}
.filters button[aria-pressed="true"]{background:var(--ink);color:#fff;border-color:var(--ink)}
.filters input{font:inherit;font-size:13.5px;padding:7px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);min-width:210px}
details.case{border-bottom:1px solid var(--line)}
details.case>summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:auto minmax(88px,auto) minmax(140px,1fr) auto auto auto;gap:12px;align-items:center;padding:11px 15px}
details.case>summary::-webkit-details-marker{display:none}
details.case[open]{background:#faf9f5}
details.case .body{padding:0 15px 15px;display:grid;gap:10px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);justify-self:start}
.dot.bad{background:var(--bad)}.dot.flaky{background:var(--warn)}
.cid{font-family:var(--mono);font-size:12.5px}
.who{font-size:12.5px;color:var(--ink-soft)}
.cat{font-size:12.5px;color:var(--ink-soft)}
.ms{font-family:var(--mono);font-size:12.5px;color:var(--ink-soft)}
.attempt{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--card);display:grid;gap:6px}
.attempt h4{margin:0;font-size:13px;font-weight:700;color:var(--ink-soft)}
.checkline{font-size:13px;display:flex;gap:8px;align-items:baseline}
.checkline b{font-family:var(--mono);font-size:12px;font-weight:600}
svg.spark{display:block;width:100%;height:44px;overflow:visible}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);font-size:12.5px;color:var(--ink-soft);display:flex;flex-wrap:wrap;gap:10px;justify-content:space-between}
@media (prefers-color-scheme:dark){
  :root{--paper:#0d1117;--card:#151b23;--ink:#e8edf4;--ink-soft:#96a3b4;--line:#242c37;--accent:#2dd4bf;--accent-soft:#12332f;--shadow:none;background:#0d1117}
  th{background:#111820} tbody tr:hover{background:#181f29} .note,pre{background:#111820}
  .row{background:var(--card)} .filters input{color:var(--ink)} details.case[open]{background:#181f29}
  body{color:var(--ink)} .meter{background:#20272f}
}
@media (max-width:720px){details.case>summary{grid-template-columns:auto auto 1fr}.who,.cat{display:none}}
@media print{.filters,details.case .body{display:none!important}details.case{break-inside:avoid}header.hero{background:#0f766e!important;-webkit-print-color-adjust:exact}}
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def iso(value: Any) -> str:
    """Bidirectional-isolated text: safe next to Latin identifiers in RTL flow."""
    return f'<span dir="auto">{esc(value)}</span>'


def num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "نعم" if value else "لا"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}{suffix}" if isinstance(value, float) else f"{value:,}{suffix}"
    return esc(value)


def state_chip(ok: Any, *, na: str = "غير مقيس") -> str:
    if ok is None:
        return '<span class="state na">لا ينطبق</span>'
    if ok is True:
        return '<span class="state ok">مطابق</span>'
    if ok is False:
        return '<span class="state bad">مخالف</span>'
    return esc(ok)


def meter(value: Any, *, scale: float = 100.0) -> str:
    if value is None:
        return '<div class="meter-wrap"><div class="meter"></div><span>—</span></div>'
    ratio = max(0.0, min(1.0, float(value) / scale))
    klass = "low" if ratio < 0.8 else "mid" if ratio < 0.999 else ""
    return (
        f'<div class="meter-wrap"><div class="meter {klass}"><i style="width:{ratio * 100:.1f}%"></i></div>'
        f"<span>{ratio * 100:.1f}%</span></div>"
    )


def _sparkline(values: Sequence[float], *, width: int = 260, height: int = 44) -> str:
    clean = [float(value) for value in values if value and value > 0]
    if len(clean) < 2:
        return ""
    high = max(clean)
    step = width / (len(clean) - 1)
    points = [(index * step, height - (value / high) * (height - 6) - 3) for index, value in enumerate(clean)]
    path = " ".join(f"{'M' if index == 0 else 'L'}{x:.1f},{y:.1f}" for index, (x, y) in enumerate(points))
    area = f"{path} L{width},{height} L0,{height} Z"
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" aria-hidden="true">'
        f'<path d="{area}" fill="var(--accent)" opacity=".12"/>'
        f'<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>'
        f'<circle cx="{points[-1][0]:.1f}" cy="{points[-1][1]:.1f}" r="2.6" fill="var(--accent)"/>'
        f"</svg>"
    )


def _governance_rows(governance: Mapping[str, Any]) -> str:
    rows = [
        ("كتابات بلا صلاحية", "unauthorized writes", num(governance.get("unauthorized_writes")), not governance.get("unauthorized_writes")),
        ("أوردرات مكررة من إعادة الإرسال", "duplicate orders", num(governance.get("duplicate_orders")), not governance.get("duplicate_orders")),
        ("تغطية التدقيق", "audit coverage", num(governance.get("audit_coverage"), "%"), (governance.get("audit_coverage") or 0) >= 99.99),
        ("سلسلة hashes للتدقيق", "audit chain", "صحيحة" if governance.get("audit_chain_valid") else "مكسورة", bool(governance.get("audit_chain_valid"))),
        ("كشف تعارض مفتاح التكرار", "idempotency conflict", "مكتشف" if governance.get("idempotency_conflict_detected") else "مفقود", bool(governance.get("idempotency_conflict_detected"))),
        ("إنفاذ السياسة", "policy enforcement", num(governance.get("policy_enforcement_rate"), "%"), (governance.get("policy_enforcement_rate") or 0) >= 99.99),
        ("نجاح مزوّد النموذج", "provider success", num(governance.get("provider_success_rate"), "%"), governance.get("provider_success_rate") in (None, 100.0)),
    ]
    return '<div class="rows">' + "".join(
        f'<div class="row"><div class="k">{iso(k)} <span class="en" style="opacity:.5">· {en}</span></div><div class="v">{v}</div>{state_chip(ok)}</div>'
        for k, en, v, ok in rows
    ) + "</div>"


def _case_details(case: Mapping[str, Any]) -> str:
    blocks: list[str] = []
    for attempt in case.get("attempts") or []:
        checks = "".join(
            f'<div class="checkline">{state_chip(row.get("passed"))}<b>{esc(row.get("name"))}</b>'
            f'<span dir="auto">{esc(row.get("detail"))}</span></div>'
            for row in attempt.get("checks") or []
        )
        text = (attempt.get("text") or "").strip()
        args = json.dumps(attempt.get("arguments") or {}, ensure_ascii=False)
        blocks.append(
            '<div class="attempt">'
            f"<h4>محاولة {attempt.get('attempt')} من {attempt.get('total_attempts')} · تشغيل {attempt.get('run')} · {num(attempt.get('latency_ms', {}).get('total'), 'ms')}</h4>"
            f'<div class="checkline"><span>الأداة:</span><code dir="auto">{esc(attempt.get("actual_tool"))}</code>'
            f'<span>المتوقعة:</span><code dir="auto">{esc(attempt.get("expected_tool"))}</code>'
            f'<span>الحالة:</span><code>{esc(attempt.get("status"))}</code></div>'
            f'<pre dir="auto">{iso(args[:600])}</pre>'
            f'{checks}'
            + (f'<pre dir="auto">{iso(text[:1400])}</pre>' if text else "")
            + "</div>"
        )
    return "".join(blocks)


def render(report: Mapping[str, Any], *, diff: Mapping[str, Any] | None = None, title: str = "تقرير تقييم Mizan") -> str:
    meta = report.get("meta") or {}
    metrics = report.get("metrics") or {}
    totals = metrics.get("totals") or {}
    governance = metrics.get("governance") or {}
    model = metrics.get("model") or {}
    answer = metrics.get("answer_quality") or {}
    latency = metrics.get("latency_ms") or {}
    runtime = metrics.get("runtime") or {}
    reliability = metrics.get("reliability") or {}
    slices = (metrics.get("slices") or {}).get("by_category") or {}
    users = (metrics.get("slices") or {}).get("by_user") or {}
    tags = metrics.get("failure_tags") or {}
    verdict = report.get("verdict") or {}
    label = str(verdict.get("label", "…"))
    verdict_class = "pass" if label == "PASS" else "gate" if label.startswith("GATE") else "fail"
    environment = meta.get("environment") or {}

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="ar" dir="rtl">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{iso(title)} · {esc(meta.get('mode'))}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<div class="wrap">',
    ]

    # hero ------------------------------------------------------------------
    parts.append('<header class="hero">')
    parts.append('<div class="hero-top"><div>')
    parts.append(f"<h1>⚖︎ {iso(title)}</h1>")
    parts.append(
        f'<p class="sub">{totals.get("cases", 0)} حالة اختبار · {totals.get("executions", 0)} تنفيذ زمنها الكلي '
        f"{num(runtime.get('wall_ms'), 'ms')} · وضع <code>{esc(meta.get('mode'))}</code> · "
        f"بيئة ERP <code>{esc(meta.get('erp'))}</code></p>"
    )
    parts.append("</div>")
    parts.append(f'<div class="verdict {verdict_class}">{iso(label)}</div></div>')
    parts.append('<div class="kpis">')
    kpis = [
        ("نسبة النجاح", num(metrics.get("pass_rate"), "%")),
        ("حالات فاشلة", num(totals.get("cases_failed", 0))),
        ("حالات متذبذبة", num(totals.get("flaky_cases", 0))),
        ("كتابات بلا صلاحية", num(governance.get("unauthorized_writes", 0))),
        ("زمن قراءة p95", num((latency.get("read") or {}).get("p95"), "ms")),
        ("زمن كتابة p95", num((latency.get("write") or {}).get("p95"), "ms")),
    ]
    for label_, value in kpis:
        parts.append(f'<div class="kpi"><div class="label">{iso(label_)}</div><div class="value">{value}</div></div>')
    parts.append("</div>")
    chips = [
        f'النموذج <code>{esc(meta.get("model"))}</code>',
        f'إصدار الـ prompt <code>{esc(meta.get("prompt_version"))}</code>',
        f'الحزمة <code>v{esc((meta.get("dataset") or {}).get("version"))}</code>',
        f'commit <code>{esc(environment.get("commit"))}</code>' + (" (dirty)" if environment.get("dirty") else ""),
        f'Python <code>{esc(environment.get("python"))}</code>',
        f'{esc(meta.get("generated_at"))}',
    ]
    parts.append('<div class="chips">' + "".join(f'<div class="chip">{chip}</div>' for chip in chips) + "</div>")
    parts.append("</header>")

    # governance ------------------------------------------------------------
    parts.append('<div class="grid2">')
    parts.append("<section><h2>الحوكمة <span class='en'>governance · always measured</span></h2>" + _governance_rows(governance) + "</section>")

    # model intelligence ----------------------------------------------------
    parts.append("<section><h2>ذكاء النموذج <span class='en'>model intelligence</span></h2><div class='card rows'>")
    if model.get("tool_selection_accuracy") is None:
        parts.append(
            "<div class='note'>لم يُقس ذكاء النموذج في هذا الوضع: الـ LLM كان scripti بالقيم المتوقعة، "
            "وأي نسبة هنا تكون دائرية. شغّل <code>--mode live</code> لقياس حقيقي. المقاييس الإدارية "
            "(الحوكمة/الصيغة/الثبات) كلها مقاسة فعليًا.</div>"
        )
    else:
        for label_, value in (("اختيار الأداة من عربي", model.get("tool_selection_accuracy")), ("دقة الوسائط", model.get("parameter_accuracy")), ("دقة النتيجة", model.get("outcome_accuracy"))):
            parts.append(f'<div class="row"><div class="k">{iso(label_)}</div><div style="min-width:170px">{meter(value)}</div><span></span></div>')
    parts.append("</div></section></div>")

    # answer quality --------------------------------------------------------
    parts.append("<section><h2>جودة الرد <span class='en'>answer quality</span></h2><div class='card rows'>")
    for label_, value, extra in (
        ("منظّم (عنوان + أقسام + جدول + خطوات)", answer.get("structured_rate"), ""),
        ("أرقام موثوقة من نتائج Odoo", answer.get("grounded_rate"), ""),
        ("رد غير فارغ", answer.get("non_empty_rate"), ""),
    ):
        parts.append(f'<div class="row"><div class="k">{iso(label_)}{extra}</div><div style="min-width:170px">{meter(value)}</div><span></span></div>')
    leaks = answer.get("reasoning_leaks") or 0
    parts.append(f'<div class="row"><div class="k">تسريب تفكير أو سياسة داخل الرد</div><div class="v">{num(leaks)}</div>{state_chip(not leaks)}</div>')
    parts.append("</div></section>")

    # latency ---------------------------------------------------------------
    parts.append("<section><h2>الزمن <span class='en'>latency · per execution</span></h2><div class='card'>")
    lat_rows = []
    for key, name in (("read", "قراءة"), ("write", "كتابة + تأكيد"), ("llm", "استدعاء النموذج"), ("all", "الكل")):
        block = latency.get(key) or {}
        lat_rows.append(
            f"<tr><td>{iso(name)}</td><td class='num'>{num(block.get('count'))}</td><td class='num'>{num(block.get('p50'), 'ms')}</td>"
            f"<td class='num'>{num(block.get('p95'), 'ms')}</td><td class='num'>{num(block.get('p99'), 'ms')}</td><td class='num'>{num(block.get('max'), 'ms')}</td></tr>"
        )
    parts.append(
        "<table><thead><tr><th>القناة</th><th class='num'>عدد</th><th class='num'>p50</th><th class='num'>p95</th><th class='num'>p99</th><th class='num'>أقصى</th></tr></thead>"
        f"<tbody>{''.join(lat_rows)}</tbody></table>"
    )
    series = [float(((attempt.get("latency_ms") or {}).get("total") or 0)) for case in report.get("cases") or [] for attempt in case.get("attempts") or []]
    spark = _sparkline(series)
    if spark:
        parts.append(f'<div style="padding:10px 15px 14px"><div class="label" style="font-size:12.5px;color:var(--ink-soft)">توزيع زمن التنفيذ لكل محاولة ({len(series)})</div>{spark}</div>')
    parts.append("</div></section>")

    # slices ----------------------------------------------------------------
    parts.append("<section><h2>التقطيعة حسب الفئة <span class='en'>slices</span></h2><div class='card'><table><thead><tr>"
                 "<th>الفئة</th><th>الحالات</th><th>التنفيذات</th><th style='min-width:150px'>نسبة النجاح</th><th>وسوم الفشل</th></tr></thead><tbody>")
    for key, bucket in sorted(slices.items()):
        tag_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in sorted(bucket.get("tags") or [])) or '<span class="tag">نظيف</span>'
        parts.append(
            f"<tr><td dir='auto'>{esc(key)}</td><td class='num'>{bucket.get('cases')}</td><td class='num'>{bucket.get('executions')}</td>"
            f"<td>{meter(bucket.get('pass_rate'))}</td><td>{tag_html}</td></tr>"
        )
    parts.append("</tbody></table></div></section>")

    if users:
        parts.append("<section><h2>حسب المستخدم <span class='en'>by user</span></h2><div class='grid2'>")
        for key, bucket in sorted(users.items()):
            bad = bucket.get("unauthorized_writes", 0)
            parts.append(
                "<div class='card rows'>"
                f"<div class='row'><div class='k'>{iso(key)}</div><div class='v'>{num(bucket.get('pass_rate'), '%')}</div>{state_chip(not bad)}</div>"
                f"<div class='row'><div class='k' style='color:var(--ink-soft)'>{bucket.get('cases')} حالة · كتابات بلا صلاحية {bad}</div><div></div><span></span></div>"
                "</div>"
            )
        parts.append("</div></section>")

    # reliability -----------------------------------------------------------
    if reliability.get("repeat_cases"):
        parts.append(
            "<section><h2>الثبات <span class='en'>pass^k</span></h2><div class='card'>"
            f"<div class='note'>{reliability.get('pass_all_repeats')} من {reliability.get('repeat_cases')} حالة نجحت في كل "
            f"التكرارات ({num(reliability.get('pass_k_rate'), '%')}). التكرار يقيس ثبات النظام لا متوسط حظه.</div></div></section>"
        )

    # gates -----------------------------------------------------------------
    gates = ((report.get("thresholds") or {}).get("evaluated") or [])
    if gates:
        parts.append("<section><h2>بوابات الإصدار <span class='en'>release gates</span></h2><div class='card'><table><thead><tr><th>المعيار</th><th class='num'>القيمة</th><th>المطلوب</th><th>الحالة</th></tr></thead><tbody>")
        for row in gates:
            ok = {"pass": True, "fail": False}.get(row.get("state"))
            parts.append(f"<tr><td><code>{esc(row.get('criterion'))}</code></td><td class='num'>{num(row.get('value'))}</td><td><code>{esc(row.get('required'))}</code></td><td>{state_chip(ok)}</td></tr>")
        parts.append("</tbody></table></div></section>")

    # failure tags ----------------------------------------------------------
    if tags:
        parts.append("<section><h2>وسوم الفشل <span class='en'>failure tags</span></h2><div class='card'><div style='padding:14px 15px'>" + "".join(
            f'<span class="tag hot">{esc(tag)} · {count}</span>' for tag, count in sorted(tags.items(), key=lambda item: -item[1])
        ) + "</div></section>")

    # baseline diff ---------------------------------------------------------
    if diff and diff.get("available"):
        base, cur = diff.get("baseline") or {}, diff.get("current") or {}
        parts.append("<section><h2>مقارنة بالأساس <span class='en'>baseline diff</span></h2><div class='card rows'>")
        parts.append(
            f"<div class='row'><div class='k'>الأساس</div><div class='v'>{iso(base.get('mode'))} · {iso(base.get('model'))}</div><span></span></div>"
            f"<div class='row'><div class='k'>الحالي</div><div class='v'>{iso(cur.get('mode'))} · {iso(cur.get('model'))}</div><span></span></div>"
            + ("" if diff.get("comparable") else "<div class='note'>⚠️ الوضع أو إصدار مجموعة الاختبارات مختلف — الفروقات للإطلاع فقط.</div>")
            + f"<div class='row'><div class='k'>فرق نسبة النجاح</div><div class='v'>{num(diff.get('pass_rate_delta'), 'pp')}</div>{state_chip((diff.get('pass_rate_delta') or 0) >= 0)}</div>"
        )
        for row in diff.get("regressions") or []:
            parts.append(f"<div class='row'><div class='k'>{iso(row['criterion'])}</div><div class='v'>{num(row['before'])} ← {num(row['after'])}</div>{state_chip(False)}</div>")
        for row in diff.get("improvements") or []:
            parts.append(f"<div class='row'><div class='k'>{iso(row['criterion'])}</div><div class='v'>{num(row['before'])} → {num(row['after'])}</div>{state_chip(True)}</div>")
        if diff.get("newly_failing"):
            parts.append("<div class='row'><div class='k'>حالات بدأت تفشل</div><div class='v'>" + "".join(f'<code>{esc(i)}</code> ' for i in diff["newly_failing"]) + "</div><span></span></div>")
        if diff.get("newly_passing"):
            parts.append("<div class='row'><div class='k'>حالات بدأت تنجح</div><div class='v'>" + "".join(f'<code>{esc(i)}</code> ' for i in diff["newly_passing"]) + "</div><span></span></div>")
        parts.append("</div></section>")

    # cases -----------------------------------------------------------------
    cases = report.get("cases") or []
    categories = sorted({str(case.get("category")) for case in cases})
    parts.append("<section><h2>الحالات <span class='en'>cases</span></h2><div class='filters'>")
    parts.append('<button data-filter="all" aria-pressed="true">الكل</button>')
    parts.append('<button data-filter="failed" aria-pressed="false">الفاشلة فقط</button>')
    for category in categories:
        parts.append(f'<button data-filter="cat:{esc(category)}" aria-pressed="false">{esc(category)}</button>')
    parts.append("<input id='q' type='search' placeholder='ابحث في الحالة أو نصها…' aria-label='بحث'>")
    parts.append("</div><div class='card' id='cases'>")
    for case in cases:
        klass = "bad" if not case.get("passed") else "flaky" if case.get("flaky") else ""
        repeats = f" ×{case.get('runs')}" if (case.get("runs") or 1) > 1 else ""
        tags_html = "".join(f'<span class="tag hot">{esc(t)}</span>' for t in case.get("tags") or [])
        parts.append(
            f'<details class="case" data-cat="{esc(case.get("category"))}" data-status="{"pass" if case.get("passed") else "fail"}">'
            f"<summary><span class='dot {klass}'></span><span class='cid'>{esc(case.get('case_id'))}{repeats}</span>"
            f"<span dir='auto'>{iso(case.get('input'))}</span><span class='who'>{iso(case.get('user'))}</span>"
            f"<span class='cat'>{esc(case.get('category'))}</span><span class='ms'>{num(case.get('latency_ms'), 'ms')}</span></summary>"
            f'<div class="body">{("<div>" + tags_html + "</div>") if tags_html else ""}{_case_details(case)}</div></details>'
        )
    parts.append("</div></section>")

    artifacts: Sequence[str] = report.get("artifacts") or []
    parts.append("<footer><div>مولّد بواسطة <code>poc.harness</code> · كل رقم هنا مقاس من سلوك فعلي (gateway + Odoo double + audit chain) لا من رد النموذج وحده.</div>")
    parts.append("<div>" + " · ".join(f"<code>{esc(path)}</code>" for path in artifacts) + "</div></footer>")
    parts.append("</div>")
    parts.append(
        "<script>const b=document.querySelectorAll('.filters button'),q=document.getElementById('q'),c=document.querySelectorAll('#cases details');"
        "let f='all';function ap(){c.forEach(x=>{const ok=(f==='all')||(f==='failed'?x.dataset.status==='fail':x.dataset.cat===f.slice(4));"
        "const t=(q?.value||'').trim().toLowerCase();x.style.display=(ok&&(!t||x.textContent.toLowerCase().includes(t)))?'':'none';});}"
        "b.forEach(x=>x.addEventListener('click',()=>{f=x.dataset.filter;b.forEach(y=>y.setAttribute('aria-pressed',String(y===x)));ap();}));"
        "q?.addEventListener('input',ap);</script>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


__all__ = ["render"]
