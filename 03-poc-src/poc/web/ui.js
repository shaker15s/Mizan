/* Mizan cockpit — component library.
 *
 * Plain DOM builders, no framework and no build step: the cockpit is served
 * straight off the POC's stdlib HTTP server, so the frontend has to be readable,
 * cacheable files. Every component here takes data + callbacks and returns a
 * node; state lives in app.js. Two rules are non-negotiable:
 *   1. user/ERP/model text goes through textContent (never innerHTML), and
 *   2. layout uses logical properties only, so RTL is the default, not a patch.
 */

import { renderMarkdown, textToNodes } from "./markdown.js";

/* ------------------------------------------------------------------- primitives */

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") node.setAttribute("style", value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "aria" || key.startsWith("data-")) {
      if (key === "aria") for (const [name, inner] of Object.entries(value)) node.setAttribute(`aria-${name}`, inner);
      else node.setAttribute(key, value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  for (const child of children.flat(4)) {
    if (child == null || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
  return node;
}

const ICON_PATHS = {
  send: "M4 12h13m0 0-5-5m5 5-5 5M20 4v16",
  check: "M4.5 12.5l4.5 4.5 10-10",
  close: "M6 6l12 12M18 6L6 18",
  alert: "M12 9v4m0 3.5h.01M10.3 4.2 2.6 17.5A1.6 1.6 0 0 0 4 20h16a1.6 1.6 0 0 0 1.4-2.5L13.7 4.2a1.6 1.6 0 0 0-2.8 0Z",
  shield: "M12 3.5 5 6.2v5.3c0 4.2 2.9 7.6 7 8.9 4.1-1.3 7-4.7 7-8.9V6.2L12 3.5Zm0 4.3v4.4m0 2.6h.01",
  clock: "M12 7.5V12l3.2 2M20 12a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
  copy: "M9 9h9.2A1.8 1.8 0 0 1 20 10.8v9.4A1.8 1.8 0 0 1 18.2 22H9.8A1.8 1.8 0 0 1 8 20.2V10.8A1.8 1.8 0 0 1 9.8 9Zm0 0H4.8A1.8 1.8 0 0 1 3 7.2V3.8A1.8 1.8 0 0 1 4.8 2h4.4A1.8 1.8 0 0 1 11 3.8V9",
  download: "M12 3.5v11m0 0 4-4m-4 4-4-4M4 17.5v1.7A1.8 1.8 0 0 0 5.8 21h12.4a1.8 1.8 0 0 0 1.8-1.8v-1.7",
  tools: "M14.5 6.5a3.5 3.5 0 0 0 4.7 3.3l-8 8a2.3 2.3 0 1 1-3.3-3.2l8-8a3.5 3.5 0 0 0-3.2-4.7M6.5 17.5h.01",
  audit: "M8 4h6l4 4v12H8zM14 4v4h4M11 12h6M11 16h6",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7.6 7.6 0 0 0-2-1.2l-.4-2.5H9.8l-.4 2.5c-.7.3-1.4.7-2 1.2l-2.3-1-2 3.4 2 1.5a7.6 7.6 0 0 0 0 2.4l-2 1.5 2 3.4 2.3-1c.6.5 1.3.9 2 1.2l.4 2.5h4.4l.4-2.5c.7-.3 1.4-.7 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2Z",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Zm4.7-1.8L20 20",
  chevron: "M9 6l6 6-6 6",
  refresh: "M20 12a8 8 0 1 1-2.6-5.9M20 4v4.5h-4.5",
  bolt: "M13.5 3 5 13.5h5L9.5 21 18 10.5h-5L13.5 3Z",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8.5a7 7 0 0 1 14 0",
  cart: "M4 5.5h1.6a1.5 1.5 0 0 1 1.5 1.3L8.5 15a1.5 1.5 0 0 0 1.5 1.3h7.2a1.5 1.5 0 0 0 1.5-1.2l1.3-6.1H7M10 20.5h.01M17 20.5h.01",
  box: "M12 3.2 4.5 6.8v10.4L12 20.8l7.5-3.6V6.8L12 3.2Zm0 0v17.6M4.5 6.8 12 10.4l7.5-3.6",
  plug: "M9 3v5m6-5v5M6.5 8h11v2.5a5.5 5.5 0 0 1-11 0V8ZM12 16v5",
  keyboard: "M3.5 7h17a.5.5 0 0 1 .5.5v9a.5.5 0 0 1-.5.5h-17a.5.5 0 0 1-.5-.5v-9a.5.5 0 0 1 .5-.5Zm3 3.5h.01M9.5 10.5h.01M13 10.5h.01M16.5 10.5h.01M7 14h10",
  sparkle: "M12 4l1.4 3.6L17 9l-3.6 1.4L12 14l-1.4-3.6L7 9l3.6-1.4L12 4Zm6 8.5.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2Z",
  scale: "M12 4v16m-4 0h8M12 6.5 7 9m5-2.5L17 9M4.5 15.5 7 9l2.5 6.5a2.8 2.8 0 0 1-5 0Zm10 0L17 9l2.5 6.5a2.8 2.8 0 0 1-5 0Z",
  play: "M8 5.5v13l10-6.5-10-6.5Z",
  edit: "M4 20h4l11-11a2.1 2.1 0 0 0-3-3L5 17v3Zm12.5-14.5 3 3",
  plus: "M12 5.5v13m-6.5-6.5h13",
  minus: "M5.5 12h13",
  eye: "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Zm9.5 2.6a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2Z",
  link: "M10 14a4 4 0 0 0 5.7 0l2.6-2.6A4 4 0 0 0 12.6 5.7L11 7.3m3 3.7a4 4 0 0 0-5.7 0L5.7 13.6A4 4 0 0 0 11.4 20.3L13 18.7",
};

export function icon(name, size = 18, extra = "") {
  const path = ICON_PATHS[name] || ICON_PATHS.sparkle;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.className.baseVal = `icon ${extra}`.trim();
  const node = document.createElementNS("http://www.w3.org/2000/svg", "path");
  node.setAttribute("d", path);
  svg.append(node);
  return svg;
}

/* ------------------------------------------------------------------- formatters */

const ARABIC_DIGITS = false; // Western numerals are the product's choice for tables/IDs.

export function num(value, digits = 0) {
  if (value == null || value === "") return "—";
  const number = typeof value === "number" ? value : Number(String(value).replace(/,/g, ""));
  if (!Number.isFinite(number)) return String(value);
  const text = number.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return ARABIC_DIGITS ? text.replace(/[0-9]/g, (d) => "٠١٢٣٤٥٦٧٨٩"[d]) : text;
}

export function money(value, currency = "ج.م") {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${num(number, number % 1 ? 2 : 0)} ${currency}`;
}

export function ms(value) {
  if (value == null) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (number < 1000) return `${num(number, number < 10 ? 1 : 0)} ms`;
  return `${(number / 1000).toFixed(2)} ث`;
}

export function relTime(input) {
  if (!input) return "—";
  const stamp = typeof input === "number" ? input : Date.parse(String(input).replace(" ", "T"));
  if (!Number.isFinite(stamp)) return String(input);
  const delta = (Date.now() - stamp) / 1000;
  if (delta < 45) return "دلوقتي";
  if (delta < 3600) return `من ${Math.round(delta / 60)} دقيقة`;
  if (delta < 86400) return `من ${Math.round(delta / 3600)} ساعة`;
  return `من ${Math.round(delta / 86400)} يوم`;
}

export function utcTime(input) {
  if (!input) return "—";
  const stamp = typeof input === "number" ? input * 1000 : Date.parse(String(input).replace(" ", "T"));
  if (!Number.isFinite(stamp)) return String(input);
  return new Date(stamp).toISOString().slice(11, 19);
}

export function shorten(value, max = 18) {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/* ---------------------------------------------------------------------- badges */

const STATUS_TONES = {
  accepted: "success",
  success: "success",
  executed: "success",
  verified: "success",
  replay: "info",
  confirmation_required: "warn",
  pending: "warn",
  conflict: "danger",
  denied: "danger",
  permission_denied: "danger",
  error: "danger",
  erp_error: "danger",
  validation_error: "danger",
  text_only: "neutral",
  unknown_tool_rejected: "danger",
  invalid_arguments: "danger",
};

export function statusBadge(status, label) {
  const tone = STATUS_TONES[String(status)] || "neutral";
  return el("span", { class: `badge badge-${tone}`, dataset: { tone } }, label || String(status || "—").replace(/_/g, " "));
}

export function chip(text, props = {}) {
  return el("span", { class: "chip", ...props }, text);
}

export function keyValue(label, value, props = {}) {
  return el(
    "div",
    { class: "kv", ...props },
    el("span", { class: "kv-label" }, label),
    value instanceof Node ? value : el("span", { class: "kv-value", dir: "auto" }, String(value ?? "—")),
  );
}

export function spinner(label = "شغال…") {
  return el("span", { class: "spinner", role: "status", "aria-live": "polite" }, el("i", { class: "spinner-ring" }), el("span", { class: "spinner-label" }, label));
}

export function emptyState(title, hint, action) {
  return el("div", { class: "empty-state" }, el("div", { class: "empty-glyph" }, icon("scale", 26)), el("p", { class: "empty-title" }, title), hint ? el("p", { class: "empty-hint" }, hint) : null, action || null);
}

/* -------------------------------------------------------------------- answer card */

function tableSection(section, onRowClick) {
  const columns = section.columns || [];
  const rows = section.rows || [];
  const table = el("table", { class: "data-table" });
  const head = el("tr");
  for (const column of columns) head.append(el("th", { scope: "col", class: `align-${column.align || "start"}` }, column.label || column.key));
  table.append(el("thead", {}, head));
  const body = el("tbody");
  rows.forEach((row, position) => {
    const tr = el("tr", { dataset: { row: position } });
    columns.forEach((column, index) => {
      const cell = el(index === 0 ? "th" : "td", {
        class: [`align-${column.align || "start"}`, column.kind === "mono" ? "mono" : "", column.tone && row[column.key + "_tone"] ? `tone-${row[column.key + "_tone"]}` : ""].filter(Boolean).join(" "),
        dir: column.dir || "auto",
      });
      const raw = row[column.key];
      if (column.kind === "money") cell.textContent = money(raw);
      else if (column.kind === "badge" || column.kind === "status") cell.append(statusBadge(raw, row[`${column.key}_label`] || raw));
      else cell.textContent = raw == null || raw === "" ? "—" : String(raw);
      tr.append(cell);
    });
    if (onRowClick) {
      tr.classList.add("clickable");
      tr.tabIndex = 0;
      tr.addEventListener("click", () => onRowClick(row, section));
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onRowClick(row, section);
        }
      });
    }
    body.append(tr);
  });
  table.append(body);
  const foot = section.foot && Object.keys(section.foot).length ? el("tfoot", {}, el("tr", {}, el("td", { colSpan: columns.length || 1, class: "table-foot", dir: "auto" }, formatFoot(section.foot)))) : null;
  if (foot) table.append(foot);

  const wrap = el("div", { class: "table-wrap" });
  const scroller = el("div", { class: "table-scroll" }, table);
  const tools = el("div", { class: "table-tools" });
  if (section.searchable && rows.length > 4) {
    const input = el("input", { class: "table-filter", type: "search", placeholder: "فلتر النتائج…", "aria-label": "فلتر نتائج الجدول" });
    input.addEventListener("input", () => {
      const needle = input.value.trim().toLowerCase();
      body.querySelectorAll("tr").forEach((tr) => {
        const hit = !needle || tr.textContent.toLowerCase().includes(needle);
        tr.classList.toggle("is-hidden", !hit);
      });
    });
    tools.append(input);
  }
  tools.append(
    el(
      "button",
      {
        class: "ghost-btn",
        type: "button",
        title: "نسخ الجدول CSV",
        onclick: () => copyText(sectionToCsv(section), "اتنسخ الجدول كـ CSV"),
      },
      icon("copy", 15, "icon-flip"),
      el("span", {}, "CSV"),
    ),
  );
  wrap.append(el("div", { class: "table-head" }, tools), scroller);
  return wrap;
}

function formatFoot(foot) {
  return Object.entries(foot)
    .map(([key, value]) => `${key}: ${typeof value === "number" ? num(value, value % 1 ? 2 : 0) : value}`)
    .join("  ·  ");
}

export function sectionToCsv(section) {
  const columns = section.columns || [];
  const header = columns.map((column) => csvCell(column.label || column.key)).join(",");
  const lines = (section.rows || []).map((row) => columns.map((column) => csvCell(row[column.key])).join(","));
  return [header, ...lines].join("\n");
}

function csvCell(value) {
  const text = value == null ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function fieldsSection(section) {
  const grid = el("dl", { class: "fields-grid" });
  for (const field of section.fields || []) {
    const row = el("div", { class: `field field-${field.tone || "default"}` });
    row.append(el("dt", {}, field.label || ""));
    const value = el("dd", { dir: field.dir || "auto", class: field.kind === "mono" ? "mono" : "" }, String(field.value ?? "—"));
    if (field.copy) {
      value.append(
        el("button", { class: "inline-copy", type: "button", title: "نسخ", "aria-label": `نسخ ${field.label || ""}`, onclick: () => copyText(String(field.value ?? ""), "اتنسخ") }, icon("copy", 13)),
      );
    }
    row.append(value);
    if (field.hint) row.append(el("span", { class: "field-hint" }, field.hint));
    grid.append(row);
  }
  return grid;
}

function barsSection(section) {
  const wrap = el("div", { class: "bars" });
  const items = section.fields || [];
  const max = Math.max(1, ...items.map((item) => Number(item.value || 0)));
  for (const item of items) {
    const value = Number(item.value || 0);
    const ratio = Math.max(0, Math.min(1, value / (max || 1)));
    wrap.append(
      el(
        "div",
        { class: "bar-row" },
        el("span", { class: "bar-label", dir: "auto" }, item.label || ""),
        el("div", { class: "bar-track" }, el("i", { class: `bar-fill tone-${item.tone || "accent"}`, style: `--ratio: ${(ratio * 100).toFixed(1)}%` })),
        el("span", { class: "bar-value mono", dir: "auto" }, item.display || String(item.value ?? "—")),
        item.caption ? el("span", { class: "bar-caption", dir: "auto" }, item.caption) : null,
      ),
    );
  }
  return wrap;
}

function listSection(section, ordered) {
  const list = el(ordered ? "ol" : "ul", { class: "section-list" });
  for (const item of section.items || []) list.append(el("li", { dir: "auto" }, item));
  return list;
}

export function sectionNode(section, opts = {}) {
  const body =
    section.kind === "table"
      ? tableSection(section, opts.onRowClick)
      : section.kind === "fields"
        ? fieldsSection(section)
        : section.kind === "bars"
          ? barsSection(section)
          : section.kind === "steps"
            ? listSection(section, true)
            : listSection(section, false);
  const node = el("section", { class: `answer-section section-${section.kind}` });
  if (section.title) node.append(el("h4", { class: "section-title" }, el("span", { class: "section-emoji" }, section.icon || ""), el("span", {}, section.title)));
  node.append(body);
  if (section.caption) node.append(el("p", { class: "section-caption", dir: "auto" }, section.caption));
  return node;
}

export function kpiGrid(kpis) {
  const grid = el("div", { class: "kpi-grid" });
  for (const kpi of kpis || []) {
    grid.append(
      el(
        "div",
        { class: `kpi tone-${kpi.tone || "neutral"}` },
        el("div", { class: "kpi-label" }, kpi.icon ? el("span", { class: "kpi-emoji" }, kpi.icon) : null, kpi.label || ""),
        el("div", { class: "kpi-value mono", dir: "auto" }, String(kpi.value ?? "—")),
        kpi.hint ? el("div", { class: "kpi-hint", dir: "auto" }, kpi.hint) : null,
      ),
    );
  }
  return grid;
}

export function actionChips(actions, handlers = {}) {
  const wrap = el("div", { class: "actions" });
  for (const action of actions || []) {
    const kind = action.kind || "prompt";
    const props = { class: `action action-${kind}`, type: "button", title: action.prompt || action.label };
    if (kind === "copy") props.onclick = () => copyText(action.prompt || "", "اتنسخ");
    else if (kind === "link") props.onclick = () => (handlers.onLink || noop)(action);
    else props.onclick = () => (handlers.onRun || noop)(action.prompt || action.label, action);
    wrap.append(el("button", props, el("span", { class: "action-emoji" }, action.icon || "→"), el("span", { dir: "auto" }, action.label || "")));
  }
  return wrap;
}

const GOVERNANCE_LABELS = {
  engine: "المحرك",
  tool: "الأداة",
  tool_version: "الإصدار",
  llm_ms: "النموذج",
  gateway_ms: "البوابة",
  total_ms: "الإجمالي",
  audit_id: "التدقيق",
  request_id: "request",
  execution_id: "execution",
  idempotency_key: "مفتاح التكرار",
  policy: "السياسة",
  user: "المستخدم",
  tenant: "المستأجر",
  stages: "المراحل",
  risk: "المخاطرة",
};

export function governanceFooter(governance, handlers = {}) {
  if (!governance || !Object.keys(governance).length) return null;
  const row = el("div", { class: "governance" });
  const entries = Object.entries(governance).filter(([, value]) => value !== null && value !== undefined && value !== "" && !Array.isArray(value));
  for (const [key, value] of entries) {
    const label = GOVERNANCE_LABELS[key] || key.replace(/_/g, " ");
    let display = value;
    if (key.endsWith("_ms")) display = ms(value);
    else if (key === "audit_id") display = `#${value}`;
    else if (typeof value === "boolean") display = value ? "أيوه" : "لأ";
    else if (typeof value === "number") display = num(value, Number.isInteger(value) ? 0 : 1);
    const cell = el("span", { class: `gov-cell gov-${key}` }, el("b", {}, label), el("span", { class: "mono", dir: "auto" }, String(display)));
    if (key === "audit_id" && handlers.onOpenAudit) {
      const button = el("button", { class: "gov-link", type: "button", title: "افتح سجل التدقيق", onclick: () => handlers.onOpenAudit(value) }, "#" + String(value), icon("search", 12));
      cell.replaceChildren(el("b", {}, label), button);
    }
    if (key === "idempotency_key" || key === "request_id") {
      cell.classList.add("copyable");
      cell.title = "دوس للنسخ";
      cell.addEventListener("click", () => copyText(String(value), `${label} اتنسخ`));
    }
    row.append(cell);
  }
  return row;
}

function noop() {}

/** The information card — the whole point of the answer pipeline. */
export function answerCard(answer, opts = {}) {
  const card = el("article", { class: `answer-card tone-${answer.tone || "neutral"}`, dataset: { status: answer.status || "", tool: answer.tool || "" } });
  const header = el("header", { class: "answer-head" });
  header.append(
    el("div", { class: "answer-headline", dir: "auto" }, answer.headline || answer.title || "رد"),
    el("div", { class: "answer-meta" }, statusBadge(answer.status), answer.tool ? el("span", { class: "mono meta-tool", dir: "ltr" }, answer.tool) : null, answer.tool_version ? el("span", { class: "mono meta-dim" }, `v${answer.tool_version}`) : null, opts.engine ? chip(`${opts.engine}`, { class: "chip chip-engine" }) : null),
  );
  card.append(header);

  if (answer.kpis && answer.kpis.length) card.append(kpiGrid(answer.kpis));
  for (const section of answer.sections || []) card.append(sectionNode(section, { onRowClick: opts.onRowClick }));
  if (answer.analysis) {
    const analysis = el("section", { class: "answer-section section-analysis" }, el("h4", { class: "section-title" }, el("span", { class: "section-emoji" }, "💡"), el("span", {}, "قراءة تحليلية")));
    const body = el("div", { class: "analysis-body" });
    renderMarkdown(body, answer.analysis);
    analysis.append(body);
    card.append(analysis);
  }
  for (const notice of answer.notices || []) card.append(el("p", { class: "notice", dir: "auto" }, notice));
  for (const warning of answer.warnings || []) card.append(el("p", { class: "notice notice-warn", dir: "auto" }, el("span", { class: "notice-emoji" }, "⚠️"), warning));
  if (answer.next_steps && answer.next_steps.length) card.append(actionChips(answer.next_steps, { onRun: opts.onRun, onLink: opts.onLink }));
  const footer = governanceFooter(answer.governance, { onOpenAudit: opts.onOpenAudit });
  if (footer) card.append(footer);
  const utilities = el("div", { class: "card-utils" });
  utilities.append(
    el("button", { class: "util-btn", type: "button", title: "نسخ نص الرد", onclick: () => copyText(opts.plain || answer.headline || "", "اتنسخ الرد") }, icon("copy", 14), el("span", {}, "نسخ")),
    el("button", { class: "util-btn", type: "button", title: "نسخ Markdown", onclick: () => copyText(opts.markdown || "", "اتنسخ الـ Markdown") }, icon("copy", 14), el("span", {}, "Markdown")),
  );
  if (opts.onRegenerate) utilities.append(el("button", { class: "util-btn", type: "button", title: "إعادة توليد الرد", onclick: () => opts.onRegenerate() }, icon("refresh", 14), el("span", {}, "إعادة")));
  if (opts.onExport) utilities.append(el("button", { class: "util-btn", type: "button", title: "تنزيل CSV", onclick: () => opts.onExport() }, icon("download", 14), el("span", {}, "CSV")));
  card.append(utilities);
  return card;
}

/** Prose fallback (text-only turns, errors without a structured card). */
export function proseCard(text, meta = {}) {
  const card = el("article", { class: `prose-card tone-${meta.tone || "neutral"}` });
  const body = el("div", { class: "prose-body" });
  if (meta.markdown) renderMarkdown(body, meta.markdown);
  else body.append(textToNodes(text || ""));
  card.append(body);
  if (meta.footer) card.append(meta.footer);
  return card;
}

/* ---------------------------------------------------------------- proposal card */

/** The server's proposal record. Field names mirror poc/confirmation.py exactly —
 * nothing here is invented: amounts are only shown when the ERP actually
 * returned them, because a signature card must never promise a number the
 * gateway has not verified. */
export function proposalCard(proposal, handlers = {}) {
  const data = proposal || {};
  const args = data.arguments || {};
  const lines = Array.isArray(args.lines) ? args.lines : [];
  const card = el("article", { class: "proposal-card", dataset: { proposal: data.proposal_id || "" } });
  const parsed = parseInstant(data.expires_at);
  card.append(
    el(
      "header",
      { class: "proposal-head" },
      el(
        "div",
        { class: "proposal-title" },
        el("h4", {}, el("span", { class: "proposal-emoji" }, "🖋️"), el("span", {}, "محتاج توقيعك قبل ما ينفّذ")),
        el("p", { class: "proposal-sub", dir: "auto" }, "البوابة جهّزت الطلب وموقّعته بالهاش — مفيش أي كتابة اتعملت في ERP لحد ما توقّع."),
        el("div", { class: "row" }, statusBadge("confirmation_required", "في انتظار التوقيع"), data.tool_name ? el("span", { class: "mono meta-tool", dir: "ltr" }, data.tool_name) : null, el("button", { class: "util-btn", type: "button", title: "نسخ معرّف المقترح", onclick: () => copyText(data.proposal_id || "", "اتنسخ معرّف المقترح") }, icon("copy", 13), el("span", {}, "proposal id"))),
      ),
      countdown(parsed.expiresAt, parseInstant(data.createdAt).expiresAt, () => handlers.onExpire?.(data)),
    ),
  );

  const summary = el("div", { class: "proposal-summary" });
  if (lines.length) {
    const table = el("table", { class: "data-table proposal-lines" });
    const priced = lines.some((line) => line.price_unit != null);
    table.append(
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", { scope: "col" }, "الصنف"),
          el("th", { scope: "col", class: "align-end" }, "الكمية"),
          priced ? el("th", { scope: "col", class: "align-end" }, "سعر الوحدة") : null,
        ),
      ),
    );
    const body = el("tbody");
    lines.forEach((line, index) => {
      body.append(
        el(
          "tr",
          { dataset: { line: String(index) } },
          el("th", { scope: "row", dir: "auto" }, line.product_name || line.display_name || `منتج #${line.product_id ?? "—"}`),
          el("td", { class: "align-end mono" }, num(line.quantity, Number(line.quantity) % 1 ? 2 : 0)),
          priced ? el("td", { class: "align-end mono" }, money(line.price_unit)) : null,
        ),
      );
    });
    table.append(body);
    summary.append(
      el("div", { class: "table-scroll" }, table),
      el("p", { class: "section-caption" }, "الإجماليات والأسعار بترجع من ERP بعد التنفيذ — البوابة مش بتخمّن أرقام."),
    );
  }
  const meta = el("div", { class: "proposal-meta" });
  for (const [label, value, opts = {}] of [
    ["الأداة", `${data.tool_name || "—"} · v${data.tool_version || "1.0.0"}`, { dir: "ltr" }],
    ["العميل", data.customer_name || (args.customer_id != null ? `#${args.customer_id}` : "—"), {}],
    ["بند التوقيع", data.operation_hash || "—", { dir: "ltr", copy: true, hint: "SHA-256 لمعاملات الطلب — أي تغيير بيبطّل التوقيع" }],
    ["المستخدم", `${data.user_id || "—"} @ ${data.tenant_id || "—"}`, { dir: "ltr" }],
    ["عرض الطلب", parsed.human, {}],
  ]) {
    const cell = keyValue(
      label,
      opts.copy
        ? el(
            "button",
            { class: "gov-link mono", type: "button", title: "دوس للنسخ", onclick: () => copyText(String(value), `${label} اتنسخ`) },
            shorten(value, 18),
          )
        : el("span", { class: "mono", dir: opts.dir || "auto" }, String(value)),
    );
    if (opts.hint) cell.append(el("span", { class: "field-hint" }, opts.hint));
    meta.append(cell);
  }
  summary.append(meta);
  card.append(summary);

  const actions = el("div", { class: "proposal-actions" });
  actions.append(
    el(
      "div",
      { class: "proposal-actions-main" },
      el("button", { class: "primary-btn", type: "button", onclick: () => handlers.onConfirm?.(data) }, icon("check", 16), el("span", {}, "نفّذ بعد التوقيع")),
      el("button", { class: "danger-ghost-btn", type: "button", onclick: () => handlers.onDecline?.(data) }, icon("close", 16), el("span", {}, "ارفض")),
    ),
    lines.length ? el("div", { class: "proposal-amend" }, quantityStepper(lines, args, (next) => handlers.onAmend?.(data, next))) : null,
  );
  card.append(actions);
  return card;
}

/** ISO-8601 or epoch seconds in, absolute seconds + a human TTL out. */
export function parseInstant(value) {
  if (value == null || value === "") return {};
  const asNumber = Number(value);
  const stamp = Number.isFinite(asNumber) && asNumber > 1_000_000 ? asNumber : Date.parse(String(value));
  if (!Number.isFinite(stamp)) return {};
  const seconds = stamp > 1e12 ? stamp / 1000 : stamp;
  const remaining = Math.round(seconds - Date.now() / 1000);
  const minutes = Math.floor(Math.abs(remaining) / 60);
  const rest = Math.abs(remaining) % 60;
  const phrase = `${minutes}:${String(rest).padStart(2, "0")}`;
  return {
    expiresAt: seconds,
    remaining,
    human: remaining > 0 ? `فاضل ${phrase} دقيقة` : remaining === 0 ? "خلص دلوقتي" : "اتقادم",
  };
}

function quantityStepper(lines, args, onCommit) {
  const wrap = el("div", { class: "amend" });
  const next = lines.map((line) => Number(line.quantity ?? 1));
  const valueNodes = [];
  wrap.append(el("b", { class: "amend-title" }, "عدّل الكمية قبل التوقيع"));
  lines.forEach((line, index) => {
    const value = el("b", { class: "mono" }, String(next[index]));
    valueNodes.push(value);
    wrap.append(
      el(
        "div",
        { class: "amend-row" },
        el("span", { class: "amend-label", dir: "auto" }, line.product_name || `#${line.product_id ?? index + 1}`),
        el(
          "div",
          { class: "stepper" },
          el("button", { class: "step-btn", type: "button", "aria-label": "نقص", onclick: () => bump(index, -1) }, icon("minus", 14)),
          value,
          el("button", { class: "step-btn", type: "button", "aria-label": "زود", onclick: () => bump(index, 1) }, icon("plus", 14)),
        ),
      ),
    );
  });
  const apply = el("button", { class: "ghost-btn", type: "button", disabled: true, onclick: () => onCommit(produce()) }, el("span", {}, "طبّق التعديل على المقترح"));
  wrap.append(apply);

  function bump(index, delta) {
    next[index] = Math.max(1, next[index] + delta);
    render();
  }
  function produce() {
    /* Full replacement arguments, as /api/amend expects: everything the operator
       can see stays byte-identical except the quantities in the stepper. */
    return { ...(args || {}), lines: lines.map((line, index) => ({ product_id: line.product_id, quantity: next[index] })) };
  }
  function render() {
    const dirty = lines.some((line, index) => next[index] !== Number(line.quantity ?? 1));
    lines.forEach((line, index) => {
      valueNodes[index].textContent = String(next[index]);
      valueNodes[index].classList.toggle("is-dirty", next[index] !== Number(line.quantity ?? 1));
    });
    apply.disabled = !dirty;
    apply.classList.toggle("is-ready", dirty);
  }
  render();
  return wrap;
}

function countdown(expiresAt, createdAt, onExpire) {
  const ring = el(
    "div",
    { class: "countdown", role: "timer" },
    el(
      "svg",
      { viewBox: "0 0 36 36", class: "countdown-svg" },
      el("circle", { class: "countdown-track", cx: "18", cy: "18", r: "15.5" }),
      el("circle", { class: "countdown-arc", cx: "18", cy: "18", r: "15.5", "stroke-dasharray": "97.4", "stroke-dashoffset": "0" }),
      el("text", { x: "18", y: "19.5", class: "countdown-text", "text-anchor": "middle" }, "—"),
    ),
    el("span", { class: "countdown-label" }, "انتهاء العرض"),
  );
  const arc = ring.querySelector(".countdown-arc");
  const label = ring.querySelector(".countdown-text");
  if (!Number.isFinite(Number(expiresAt))) return ring;
  const total = Math.max(1, Number(expiresAt) - (Number(createdAt) || Number(expiresAt) - 300));
  let timer = null;
  let fired = false;
  const tick = () => {
    const remaining = Math.max(0, Number(expiresAt) - Date.now() / 1000);
    const ratio = remaining / total;
    arc.setAttribute("stroke-dashoffset", String((1 - ratio) * 97.4));
    ring.classList.toggle("is-urgent", ratio < 0.25);
    ring.classList.toggle("is-expired", remaining <= 0);
    const minutes = Math.floor(remaining / 60);
    const seconds = Math.floor(remaining % 60);
    label.textContent = remaining <= 0 ? "خلص" : `${minutes}:${String(seconds).padStart(2, "0")}`;
    if (remaining <= 0 && !fired) {
      fired = true;
      clearInterval(timer);
      onExpire?.();
    }
  };
  tick();
  timer = setInterval(tick, 1000);
  return ring;
}

/* ----------------------------------------------------------------------- stepper */

const STAGE_LABELS = {
  intake: ["استلمت الطلب", "inbox"],
  llm_call: ["بيفهم طلبك", "sparkle"],
  schema_validation: ["بيتحقق من العقد", "shield"],
  authz: ["بيشوف الصلاحيات", "user"],
  await_signature: ["محتاج توقيعك", "edit"],
  execute: ["بينفذ على ERP", "bolt"],
  verification: ["بيراجع النتيجة", "check"],
  audit: ["بيسجّل في السلسلة", "audit"],
  answer: ["بيجهز الرد", "scale"],
  narrative_start: ["بيكتب التحليل", "sparkle"],
  narrative_done: ["التحليل خلص", "check"],
  narrative_skipped: ["التحليل اتخطّى", "minus"],
  delta: ["بيكتب", "sparkle"],
  repair_attempt: ["بيصلح الطلب", "refresh"],
  json_recovery: ["بيصلح JSON", "refresh"],
  llm_error: ["مشكلة في النموذج", "alert"],
  erp_error: ["رجع ERP", "alert"],
  slash_command: ["أمر مباشر", "keyboard"],
};

export function pipelineStepper(container, stages, activeStage) {
  if (!container) return;
  clear(container);
  const list = el("ol", { class: "stepper-list" });
  const seen = new Set();
  for (const stage of stages || []) {
    const [label, glyph] = STAGE_LABELS[stage.stage] || [String(stage.stage || "").replace(/_/g, " "), "sparkle"];
    seen.add(stage.stage);
    list.append(
      el(
        "li",
        { class: `step is-done${stage.t_ms != null ? "" : ""}` },
        el("span", { class: "step-glyph" }, icon(glyph, 13)),
        el("span", { class: "step-label" }, label),
        stage.t_ms != null ? el("span", { class: "step-ms mono" }, ms(stage.t_ms)) : null,
      ),
    );
  }
  if (activeStage) {
    const [label, glyph] = STAGE_LABELS[activeStage] || [String(activeStage).replace(/_/g, " "), "sparkle"];
    list.append(el("li", { class: "step is-active" }, el("span", { class: "step-glyph" }, icon(glyph, 13)), el("span", { class: "step-label" }, label), el("span", { class: "step-dots" }, el("i"), el("i"), el("i"))));
  }
  container.append(list);
  container.hidden = !stages?.length && !activeStage;
}

/* ---------------------------------------------------------------------- overlays */

const toastRoot = () => document.getElementById("toasts");

export function toast({ tone = "info", title = "", message = "", timeout = 4200, action = null } = {}) {
  const root = toastRoot();
  if (!root) return null;
  const node = el(
    "div",
    { class: `toast toast-${tone}`, role: tone === "danger" ? "alert" : "status", "aria-live": tone === "danger" ? "assertive" : "polite" },
    el("span", { class: "toast-glyph" }, icon(tone === "success" ? "check" : tone === "danger" ? "alert" : tone === "warn" ? "alert" : "sparkle", 16)),
    el("div", { class: "toast-body" }, title ? el("b", { dir: "auto" }, title) : null, message ? el("p", { dir: "auto" }, message) : null),
    action ? el("button", { class: "toast-action", type: "button", onclick: () => { action.run(); dismiss(); } }, action.label) : null,
    el("button", { class: "toast-close", type: "button", "aria-label": "اقفل", onclick: () => dismiss() }, icon("close", 14)),
  );
  function dismiss() {
    node.classList.add("is-leaving");
    setTimeout(() => node.remove(), 260);
  }
  root.append(node);
  if (timeout) setTimeout(dismiss, timeout);
  node.addEventListener("click", (event) => {
    if (event.target.closest("button")) return;
    dismiss();
  });
  return dismiss;
}

let modalStack = [];

export function modal({ title = "", subtitle = "", body = null, footer = null, size = "md", onClose = null, closeLabel = "اقفل" } = {}) {
  const root = document.getElementById("modal-root");
  if (!root) return null;
  root.hidden = false;
  document.documentElement.classList.add("modal-open");
  const panel = el(
    "div",
    { class: `modal modal-${size}`, role: "dialog", "aria-modal": "true", "aria-label": title },
    el(
      "header",
      { class: "modal-head" },
      el("div", {}, el("h3", {}, title), subtitle ? el("p", { class: "modal-sub" }, subtitle) : null),
      el("button", { class: "icon-btn", type: "button", "aria-label": closeLabel }, icon("close", 18)),
    ),
    el("div", { class: "modal-body" }, body),
    footer ? el("footer", { class: "modal-foot" }, footer) : null,
  );
  const scrim = el("div", { class: "modal-scrim" });
  const wrap = el("div", { class: "modal-wrap" }, scrim, panel);
  root.append(wrap);
  const close = () => {
    wrap.classList.add("is-leaving");
    modalStack = modalStack.filter((row) => row.wrap !== wrap);
    setTimeout(() => {
      wrap.remove();
      if (!modalStack.length) {
        root.hidden = true;
        document.documentElement.classList.remove("modal-open");
      }
    }, 200);
    onClose?.();
    lastFocus?.focus?.();
  };
  const lastFocus = document.activeElement;
  panel.querySelector(".modal-head .icon-btn").addEventListener("click", close);
  scrim.addEventListener("click", close);
  wrap.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  });
  requestAnimationFrame(() => {
    wrap.classList.add("is-open");
    panel.querySelector("input, button, [tabindex]")?.focus?.();
  });
  modalStack.push({ wrap, close });
  return { panel, close };
}

export function closeTopModal() {
  const top = modalStack[modalStack.length - 1];
  if (top) top.close();
  return Boolean(top);
}

export function modalOpen() {
  return modalStack.length > 0;
}

/* ----------------------------------------------------------------------- helpers */

export async function copyText(text, successMessage = "اتنسخ") {
  const value = String(text ?? "");
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
    else {
      const area = el("textarea", { class: "visually-hidden" });
      area.value = value;
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    toast({ tone: "success", message: successMessage, timeout: 2000 });
  } catch (error) {
    toast({ tone: "warn", title: "النسخ اتعملش", message: "المتصفح مانع الوصول للحافظة — حدد النص ونسخه يدوي." });
  }
}

export function downloadCsv(content, filename = "mizan-export.csv") {
  const blob = new Blob([`\uFEFF${content}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = el("a", { href: url, download: filename });
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

export function sparkline(values, width = 120, height = 28) {
  const clean = (values || []).map(Number).filter((value) => Number.isFinite(value) && value > 0);
  if (clean.length < 2) return el("span", { class: "mono meta-dim" }, "—");
  const max = Math.max(...clean);
  const step = width / (clean.length - 1);
  const points = clean.map((value, index) => [index * step, height - (value / max) * (height - 4) - 2]);
  const path = points.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "spark");
  svg.setAttribute("aria-hidden", "true");
  const area = document.createElementNS("http://www.w3.org/2000/svg", "path");
  area.setAttribute("d", `${path} L${width},${height} L0,${height} Z`);
  area.setAttribute("class", "spark-area");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
  line.setAttribute("d", path);
  line.setAttribute("class", "spark-line");
  svg.append(area, line);
  return svg;
}

export function mount(container, node) {
  const host = typeof container === "string" ? document.querySelector(container) : container;
  if (!host) return null;
  clear(host);
  if (node) host.append(node);
  return host;
}

export const ui = {
  el,
  icon,
  num,
  money,
  ms,
  relTime,
  utcTime,
  shorten,
  statusBadge,
  chip,
  keyValue,
  spinner,
  emptyState,
  answerCard,
  proseCard,
  proposalCard,
  pipelineStepper,
  sectionNode,
  kpiGrid,
  actionChips,
  governanceFooter,
  toast,
  modal,
  closeTopModal,
  modalOpen,
  copyText,
  downloadCsv,
  sectionToCsv,
  sparkline,
  mount,
  clear,
};

export default ui;
