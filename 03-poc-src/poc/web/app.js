/* =============================================================================
   Mizan cockpit — application shell
   -----------------------------------------------------------------------------
   Everything here is vanilla ESM on purpose: the POC server is stdlib-only, so a
   build step would be a lie about the stack. The contract the UI renders against
   is the server's `answer` object (a lossless projection of what actually
   executed) — never a re-parsed copy of model prose. Model text is only used for
   narrative blocks, and it always goes through textContent/`markdown.js`.
   ============================================================================= */

import {
  el,
  icon,
  clear,
  append,
  num,
  ms,
  relTime,
  utcTime,
  shorten,
  spinner,
  statusBadge,
  emptyState,
  answerCard,
  proseCard,
  proposalCard,
  pipelineStepper,
  toast,
  modal,
  closeTopModal,
  modalOpen,
  copyText,
  downloadCsv,
  sectionToCsv,
  sparkline,
  chip,
  keyValue,
} from "./ui.js";
import { renderMarkdown } from "./markdown.js";

/* ---------------------------------------------------------------------- storage */

const NS = "mizan.";
const store = {
  get(key, fallback = null) {
    try {
      const raw = localStorage.getItem(NS + key);
      return raw == null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(NS + key, JSON.stringify(value));
    } catch {
      /* private mode: the cockpit still works, it just forgets */
    }
  },
};

const state = {
  sessionId: store.get("session", ""),
  busy: false,
  controller: null,
  lastIntent: "",
  streaming: store.get("streaming", true),
  turns: store.get("turns", []),
  commands: [],
  tools: [],
  telemetry: null,
  latencySeries: [],
  settings: null,
  settingsDraft: {},
  integrations: null,
  audit: { records: [], chain_valid: true, count: 0 },
  proposals: new Map(),
  railTab: store.get("railTab", "context"),
  quickPrompts: store.get("quickPrompts", null),
  voiceOn: false,
};

/* ---------------------------------------------------------------------- helpers */

const $ = (id) => document.getElementById(id);
const dom = {
  thread: () => $("chat-messages"),
  form: () => $("chat-form"),
  input: () => $("chat-input"),
  send: () => $("btn-send"),
  stepper: () => $("pipeline-stepper"),
  suggestions: () => $("suggest-row"),
  palette: () => $("slash-palette"),
  statusbar: () => $("statusbar"),
  rail: () => $("rail"),
  railPanels: () => $("rail-panels"),
  modalRoot: () => $("modal-root"),
};

class ApiError extends Error {
  constructor(status, code, message, payload) {
    super(message || code || `HTTP ${status}`);
    this.status = status;
    this.code = code || "HTTP_ERROR";
    this.payload = payload || {};
  }
}

async function api(path, { method = "GET", body, signal, timeout = 45000, allow = [] } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), timeout);
  const onAbort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", onAbort);
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await response.text();
    let payload = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = { raw: text };
    }
    if (!response.ok && !allow.includes(response.status)) {
      throw new ApiError(response.status, payload?.error?.code, payload?.error?.message || payload?.response_ar, payload);
    }
    return payload;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error?.name === "AbortError" && signal?.aborted) throw error;
    if (error?.name === "AbortError") throw new ApiError(0, "TIMEOUT", "الخادم ما ردّش في المعقول — جرب تاني.");
    throw new ApiError(0, "NETWORK", "الاتصال بالـ cockpit انقطع. اتأكد إن السيرفر شغال.");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

const errorText = (error) =>
  error?.code === "MESSAGE_TOO_LONG"
    ? error.message
    : error?.message || "حصل خطأ غير متوقع.";

/* ------------------------------------------------------------------- status bar */

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  store.set("theme", theme);
  const button = $("btn-theme");
  if (button) {
    button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    button.title = theme === "dark" ? "الوضع الفاتح" : "الوضع الداكن";
  }
}

function initTheme() {
  const saved = store.get("theme", null);
  const dark = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  setTheme(saved || (dark?.matches ? "dark" : "light"));
  dark?.addEventListener?.("change", (event) => {
    if (!store.get("theme", null)) setTheme(event.matches ? "dark" : "light");
  });
  $("btn-theme")?.addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
}

function stat(label, value, tone = "", glyph = null) {
  return el("span", { class: `stat${tone ? ` stat-${tone}` : ""}` }, glyph || el("span", { class: "dot" }), el("span", {}, label), el("b", { dir: "auto" }, String(value)));
}

async function fetchTelemetry() {
  try {
    const data = await api("/api/telemetry", { timeout: 8000 });
    state.telemetry = data;
    const latencies = state.latencySeries.slice(-24);
    renderStatusbar(data);
    renderContextPanel();
    if ($("rail-tab-tools")?.getAttribute("aria-selected") === "true") renderToolsPanel();
    return data;
  } catch (error) {
    dom.statusbar()?.replaceChildren(stat("الاتصال", "مقطوع", "bad", el("span", { class: "dot dot-off" })));
    return null;
  }
}

function renderStatusbar(data) {
  const bar = dom.statusbar();
  if (!bar || !data) return;
  const odoo = data.odoo || {};
  const breaker = data.circuit_breaker || {};
  const model = data.model || {};
  const agent = data.agent || {};
  const sessions = data.sessions || {};
  const latency = state.latencySeries;
  bar.replaceChildren(
    stat("Odoo", odoo.online ? `متصل · ${num(odoo.latency_ms ?? 0, 1)} ms` : "مغلق", odoo.online ? "ok" : "bad", el("span", { class: `dot ${odoo.online ? "dot-on" : "dot-off"}` })),
    stat("المحرك", model.engine === "simulated" ? "offline rules" : model.provider || "live", model.engine === "simulated" ? "warn" : "", icon("sparkle", 13)),
    stat("النموذج", shorten(model.name || "—", 22), "", icon("bolt", 13)),
    stat("Circuit", breaker.state || "closed", breaker.state === "open" ? "bad" : breaker.state === "half_open" ? "warn" : "ok", icon("shield", 13)),
    stat("الجلسة", `${sessions.turns ?? 0} لفة`, "", icon("user", 13)),
    stat("Uptime", shorten(data.uptime_human || "—", 18), "", icon("clock", 13)),
    latency.length > 1
      ? el("span", { class: "stat" }, el("span", { class: "mono" }, `p50 ${ms(median(latency))}`), sparkline(latency.slice(-20)))
      : null,
    el("span", { class: "stat", title: `prompt ${model.prompt_version || "—"} · settings v${data.settings?.version ?? "—"}` }, icon("audit", 13), el("b", {}, `v${data.api_version}`)),
  );
  $("header-odoo-status")?.replaceChildren(el("span", { class: `dot ${odoo.online ? "dot-on" : "dot-off"}` }), el("span", {}, odoo.database || "—"));
  $("odoo-db-label") && ($("odoo-db-label").textContent = odoo.database || "—");
  $("model-label") && ($("model-label").textContent = `${model.name || "—"} · ${model.engine || "—"}`);
  $("uptime-label") && ($("uptime-label").textContent = data.uptime_human || "—");
  $("telemetry-live-badge")?.classList.toggle("is-live", !document.hidden);
}

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
};

/* ------------------------------------------------------------------------ turns */

function turnShell(role) {
  return el("article", { class: `turn turn-${role}` });
}

function addUserTurn(text) {
  const li = turnShell("user");
  li.append(
    el("div", { class: "turn-meta" }, el("span", {}, new Date().toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" })), el("span", { class: "avatar" }, "أنت")),
    el("div", { class: "bubble", dir: "auto" }, text),
  );
  return li;
}

function beginLiveTurn(intent) {
  const li = turnShell("assistant");
  li.dataset.live = "1";
  const meta = el("div", { class: "turn-meta" }, el("span", { class: "avatar" }, "⚖️"), el("b", {}, "Mizan"), el("span", { class: "meta-dim" }, "بيفكر…"));
  const body = el("div", { class: "answer-card live-card" }, el("div", { class: "thinking" }, spinner("بيشتغل على طلبك"), el("span", { class: "thinking-text", dir: "auto" }), ));
  body.append(el("div", { class: "skeleton-block" }, el("i", { class: "skeleton-line" }), el("i", { class: "skeleton-line" }), el("i", { class: "skeleton-line" })));
  li.append(meta, body);
  dom.thread().querySelector(".thread-inner")?.append(li);
  const node = {
    li,
    meta,
    card: body,
    stages: [],
    text: "",
    intent,
    finish: (payload) => {
      li.dataset.live = "";
      li.replaceChildren();
      li.append(meta, renderAnswerNode(payload, { intent }));
      requestAnimationFrame(() => li.scrollIntoView({ block: "end", behavior: "smooth" }));
    },
    fail: (message, code) => {
      li.dataset.live = "";
      li.replaceChildren();
      li.append(
        meta,
        el(
          "article",
          { class: "answer-card tone-danger" },
          el("header", { class: "answer-head" }, el("div", { class: "answer-headline" }, "الطلب وقف"), el("div", { class: "answer-meta" }, statusBadge("error", code || "error"))),
          el("p", { class: "notice", dir: "auto" }, el("span", { class: "notice-emoji" }, "⛔"), message),
          el(
            "div",
            { class: "actions" },
            el("button", { class: "action", type: "button", onclick: () => sendTurn(intent) }, icon("refresh", 15), el("span", {}, "إعادة المحاولة")),
            el("button", { class: "action", type: "button", onclick: () => openToolsModal() }, icon("tools", 15), el("span", {}, "عقود الأدوات")),
          ),
        ),
      );
    },
  };
  node.setStage = (name, extra = {}) => {
    node.stages = [...node.stages, { stage: name, t_ms: extra.t_ms ?? extra.ms ?? null, ...extra }];
    pipelineStepper(dom.stepper(), node.stages, null);
    const label = node.card.querySelector(".thinking-text");
    if (label) label.textContent = stageWord(name);
    return node;
  };
  node.setDelta = (chunk) => {
    if (!node.text && node.card.querySelector(".skeleton-block")) {
      node.card.querySelector(".skeleton-block")?.remove();
      node.card.classList.remove("live-card");
    }
    node.text += chunk;
    let holder = node.card.querySelector(".stream-text");
    if (!holder) {
      holder = el("div", { class: "stream-text stream-caret", dir: "auto" });
      node.card.append(holder);
    }
    holder.textContent = node.text;
    return node;
  };
  return node;
}

const stageWord = (name) =>
  ({
    intake: "استلمت طلبك",
    llm_call: "بafsّر النية",
    schema_validation: "بchecking عقد الأداة",
    authz: "بأتشهر على صلاحياتك",
    await_signature: "محتاج توقيعك",
    execute: "بتنفيذ على Odoo",
    verification: "براجع رد Odoo",
    audit: "بكتب في سلسلة التدقيق",
    answer: "بجهز الكارت",
    narrative_start: "بكتب القراءة التحليلية",
    slash_command: "أمر مباشر",
    repair_attempt: "بصلح الطلب",
  })[name] || "شغال";

function renderAnswerNode(payload, { intent = "" } = {}) {
  const answer = payload?.answer;
  const nodes = [];
  if (answer && (answer.headline || answer.sections?.length || answer.kpis?.length)) {
    nodes.push(
      answerCard(answer, {
        engine: payload?.meta?.engine,
        plain: payload.response_ar || answer.headline || "",
        markdown: payload.answer_markdown || payload.response_markdown || "",
        onRun: (text) => sendTurn(text),
        onLink: (action) => (action.prompt ? sendTurn(action.prompt) : null),
        onOpenAudit: (auditId) => openAuditModal({ focus: auditId }),
        onRowClick: (row, section) => openRowModal(row, section, answer),
        onExport: () => exportAnswerCsv(answer),
        onRegenerate: intent ? () => sendTurn(intent) : null,
      }),
    );
  } else {
    nodes.push(
      proseCard(payload?.response_ar || "", {
        markdown: payload?.response_markdown || "",
        tone: payload?.success === false ? "danger" : "neutral",
      }),
    );
  }
  if (payload?.proposal?.proposal_id) nodes.push(renderProposal(payload.proposal));
  const wrap = el("div", { class: "turn-body" });
  append(wrap, nodes);
  return wrap;
}

function renderProposal(proposal) {
  state.proposals.set(proposal.proposal_id, proposal);
  return proposalCard(proposal, {
    onConfirm: (row) => decideProposal("confirm", row),
    onDecline: (row) => decideProposal("decline", row),
    onAmend: (row, deltas) => amendProposal(row, deltas),
    onExpire: () => {
      proposal.expired = true;
      toast({ tone: "warn", title: "العرض اتقادم", message: "صلاحية التوقيع خلصت — اطلب الأمر من جديد، الأمان قبل السرعة." });
    },
    onOpenAudit: (auditId) => openAuditModal({ focus: auditId }),
  });
}

function replaceTurnCard(node, payload) {
  const holder = node.li.querySelector(".turn-body") || node.li;
  holder.replaceChildren(renderAnswerNode(payload, { intent: node.intent }));
}

/* ------------------------------------------------------------------ send a turn */

async function sendTurn(text, options = {}) {
  const message = String(text ?? "").trim();
  if (!message) return;
  if (state.busy) {
    toast({ tone: "warn", message: "في طلب شغال دلوقتي — استناه يخلص أو دوس Esc للإلغاء." });
    return;
  }
  state.busy = true;
  state.controller = new AbortController();
  state.lastIntent = message;
  setComposerBusy(true);
  const thread = dom.thread().querySelector(".thread-inner");
  thread?.append(addUserTurn(message));
  state.turns.push({ role: "user", text: message, at: Date.now() });
  persistTurns();
  scrollThread();

  const node = beginLiveTurn(message);
  let payload = null;
  const started = performance.now();
  try {
    if (state.streaming && !options.forceBuffered) {
      payload = await runStream(message, node, state.controller.signal);
    } else {
      node.setStage("intake");
      payload = await api("/api/chat", { method: "POST", body: { message, session_id: state.sessionId || undefined }, signal: state.controller.signal });
    }
    node.finish(payload);
    if (payload?.session_id) {
      state.sessionId = payload.session_id;
      store.set("session", state.sessionId);
    }
    state.turns.push({ role: "assistant", text: payload?.response_ar || payload?.answer?.headline || "", at: Date.now(), status: payload?.status });
    persistTurns();
  } catch (error) {
    if (error?.name === "AbortError") node.fail("ألغيت الطلب.", "cancelled");
    else node.fail(errorText(error), error?.code);
    if (error?.name !== "AbortError") toast({ tone: "danger", title: "الطلب فشل", message: errorText(error) });
  } finally {
    state.latencySeries = [...state.latencySeries, performance.now() - started].slice(-40);
    state.busy = false;
    state.controller = null;
    setComposerBusy(false);
    pipelineStepper(dom.stepper(), node.stages, null);
    setTimeout(() => pipelineStepper(dom.stepper(), [], null), 2400);
    fetchTelemetry();
    loadAudit({ silent: true });
  }
}

async function runStream(message, node, signal) {
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", abort);
  let finalPayload = null;
  let errorFrame = null;
  let sawDone = false;
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: state.sessionId || undefined }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new ApiError(response.status, "STREAM", "البث مكمّلش.");
    await readSse(response, (type, data) => {
      if (type === "stage") node.setStage(data.stage, data);
      else if (type === "delta") node.setDelta(data.text || "");
      else if (type === "answer") finalPayload = data;
      else if (type === "error") errorFrame = data;
      else if (type === "done") sawDone = true;
    }, { stopAfter: () => sawDone, idleMs: STREAM_IDLE_MS, signal: controller.signal });
  } catch (error) {
    if (signal?.aborted) throw error;
    /* Only replay the turn over the buffered endpoint when the stream died before
       the first stage arrived: once the server has started executing, re-posting
       the same intent would be a second write attempt, and that is a governance
       problem, not a transport one. */
    if (finalPayload || node.stages.length) throw new ApiError(503, "STREAM_INTERRUPTED", errorText(error) || "البث اتقطع قبل ما الرد يوصل.");
    finalPayload = await api("/api/chat", { method: "POST", body: { message, session_id: state.sessionId || undefined }, signal });
  } finally {
    signal?.removeEventListener("abort", abort);
  }
  if (errorFrame && !finalPayload) throw new ApiError(500, errorFrame.code, errorFrame.message, errorFrame);
  if (!finalPayload) throw new ApiError(504, "STREAM_INCOMPLETE", "الرد ما كملش — الخادم قفل الاتصال قبل `answer`. جرّب تاني.");
  return finalPayload;
}

const STREAM_IDLE_MS = 60000;

async function readSse(response, onEvent, { stopAfter = null, idleMs = 0, signal = null } = {}) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let idle = null;
  const armIdle = () => {
    clearTimeout(idle);
    if (!idleMs) return;
    idle = setTimeout(() => {
      try {
        reader.cancel(new DOMException("idle", "TimeoutError"));
      } catch {
        /* already closed */
      }
    }, idleMs);
  };
  armIdle();
  try {
    for (;;) {
      let chunk;
      try {
        chunk = await reader.read();
      } catch {
        return;
      }
      if (chunk.done) return;
      buffer += decoder.decode(chunk.value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        let type = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) type = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) onEvent(type, {});
        else {
          try {
            onEvent(type, JSON.parse(data));
          } catch {
            /* a malformed frame must never abort a live stream */
          }
        }
        armIdle();
        if (stopAfter?.()) return;
      }
    }
  } finally {
    clearTimeout(idle);
    if (stopAfter?.() || signal?.aborted) {
      try {
        reader.releaseLock();
      } catch {
        /* read loop still owns it */
      }
    }
  }
}

function setComposerBusy(busy) {
  const send = dom.send();
  if (send) {
    send.disabled = busy;
    send.querySelector("span") && (send.querySelector("span").textContent = busy ? "شغال…" : "أرسل");
    send.classList.toggle("is-stop", busy);
  }
  dom.form()?.classList.toggle("is-busy", busy);
  dom.input()?.setAttribute("aria-busy", busy ? "true" : "false");
}

function scrollThread() {
  const thread = dom.thread();
  requestAnimationFrame(() => thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" }));
}

function persistTurns() {
  store.set("turns", state.turns.slice(-40));
}

/* ------------------------------------------------------- decisions (confirm/etc) */

async function decideProposal(kind, proposal) {
  const card = document.querySelector(`.proposal-card[data-proposal="${proposal.proposal_id}"]`);
  const button = card?.querySelector(kind === "confirm" ? ".primary-btn" : ".danger-ghost-btn");
  if (button) {
    button.disabled = true;
    button.replaceChildren(spinner(kind === "confirm" ? "بتنفيذ…" : "بأرفض…"));
  }
  try {
    const payload = await api(`/api/${kind}`, { method: "POST", body: { proposal_id: proposal.proposal_id, session_id: state.sessionId || undefined } });
    if (card) {
      const holder = card.parentElement;
      holder.replaceChildren(renderAnswerNode(payload, { intent: "" }));
    }
    state.proposals.delete(proposal.proposal_id);
    toast({
      tone: payload?.success ? "success" : "warn",
      title: payload?.success ? "اتنفذ على Odoo" : "القرار اتسجل",
      message: payload?.response_ar || "",
      timeout: 6000,
    });
    loadAudit({ silent: true });
  } catch (error) {
    toast({ tone: "danger", title: "القرار فشل", message: errorText(error), action: { label: "إعادة", run: () => decideProposal(kind, proposal) } });
    if (button) {
      button.disabled = false;
      button.replaceChildren(icon(kind === "confirm" ? "check" : "close", 16), el("span", {}, kind === "confirm" ? "نفّذ بعد التوقيع" : "ارفض"));
    }
  }
}

async function amendProposal(proposal, arguments_) {
  try {
    const payload = await api("/api/amend", { method: "POST", body: { proposal_id: proposal.proposal_id, arguments: arguments_, session_id: state.sessionId || undefined } });
    const card = document.querySelector(`.proposal-card[data-proposal="${proposal.proposal_id}"]`);
    if (card) card.parentElement.replaceChildren(renderAnswerNode(payload, { intent: "" }));
    toast({ tone: "success", title: "اتعدّل", message: payload?.response_ar || "تم تعديل المقترح وتوقيعه من جديد." });
  } catch (error) {
    toast({ tone: "danger", title: "التعديل فشل", message: errorText(error) });
  }
}

/* --------------------------------------------------------------------- exports */

function exportAnswerCsv(answer) {
  const table = (answer.sections || []).find((section) => section.kind === "table");
  if (!table) {
    const fields = (answer.sections || []).flatMap((section) => section.fields || []);
    if (!fields.length) {
      toast({ tone: "warn", message: "مفيش جدول في الرد ده أتصدره." });
      return;
    }
    downloadCsv(`حقل,قيمة\n${fields.map((row) => `${csvSafe(row.label)},${csvSafe(row.value)}`).join("\n")}`, "mizan-answer.csv");
    toast({ tone: "success", message: "اتنزل ملف CSV." });
    return;
  }
  downloadCsv(sectionToCsv(table), `mizan-${(answer.tool || "result").replace(/\./g, "-")}.csv`);
  toast({ tone: "success", message: "اتنزل ملف CSV." });
}

const csvSafe = (value) => {
  const text = value == null ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

function openRowModal(row, section, answer) {
  const body = el("div", { class: "markdown" }, el("p", { class: "panel-sub" }, `صف من ${answer?.tool || "ERP"}`));
  const grid = el("dl", { class: "fields-grid" });
  for (const [key, value] of Object.entries(row)) {
    grid.append(
      el("div", { class: "field" }, el("dt", {}, key.replace(/_/g, " ")), el("dd", { dir: "auto" }, value == null ? "—" : String(value))),
    );
  }
  body.append(grid);
  modal({ title: row.name || row.display_name || row.product || `#${row.id ?? ""}`, subtitle: answer?.tool || "", body, footer: el("button", { class: "ghost-btn", type: "button", onclick: () => copyText(JSON.stringify(row, null, 2), "نسخ السطر كـ JSON") }, icon("copy", 14), el("span", {}, "نسخ JSON")) });
}

/* ------------------------------------------------------------------------ rail */

const RAIL_TABS = [
  { id: "context", label: "السياق", glyph: "user" },
  { id: "tools", label: "الأدوات", glyph: "tools" },
  { id: "audit", label: "التدقيق", glyph: "audit" },
  { id: "integrations", label: "تكاملات", glyph: "plug" },
  { id: "settings", label: "الإعدادات", glyph: "settings" },
];

function buildRail() {
  const tabs = $("rail-tabs");
  if (!tabs) return;
  const panels = dom.railPanels();
  tabs.replaceChildren(
    ...RAIL_TABS.map((tab) =>
      el(
        "button",
        {
          class: "rail-tab",
          id: `rail-tab-${tab.id}`,
          role: "tab",
          type: "button",
          "aria-selected": state.railTab === tab.id ? "true" : "false",
          onclick: () => selectRailTab(tab.id),
        },
        icon(tab.glyph, 14),
        el("span", {}, tab.label),
      ),
    ),
  );
  panels.replaceChildren(
    ...RAIL_TABS.map((tab) => el("section", { class: "panel", id: `panel-${tab.id}`, role: "tabpanel", "aria-labelledby": `rail-tab-${tab.id}`, hidden: tab.id !== state.railTab })),
  );
  renderContextPanel();
  renderToolsPanel();
  renderAuditPanel();
  renderIntegrationsPanel();
  renderSettingsPanel();
}

function selectRailTab(id) {
  state.railTab = id;
  store.set("railTab", id);
  for (const tab of RAIL_TABS) {
    $(`rail-tab-${tab.id}`)?.setAttribute("aria-selected", tab.id === id ? "true" : "false");
    const panel = $(`panel-${tab.id}`);
    if (panel) panel.hidden = tab.id !== id;
  }
  document.querySelector(".layout")?.setAttribute("data-rail", "open");
  if (id === "audit") loadAudit();
  if (id === "settings") loadSettings();
  if (id === "integrations") loadIntegrations();
  if (id === "tools") renderToolsPanel();
}

function toggleRail(force) {
  const layout = document.querySelector(".layout");
  if (!layout) return;
  const closed = layout.getAttribute("data-rail") === "closed";
  layout.setAttribute("data-rail", force === false ? "closed" : closed ? "open" : "closed");
}

function renderContextPanel() {
  const panel = $("panel-context");
  if (!panel) return;
  const data = state.telemetry || {};
  const agent = data.agent || {};
  const model = data.model || {};
  const sessions = data.sessions || {};
  const odoo = data.odoo || {};
  panel.replaceChildren(
    el(
      "div",
      { class: "card" },
      el("h4", { class: "panel-title" }, el("span", {}, "الجلسة الحالية"), chip(`${sessions.turns ?? 0} لفة`, {})),
      el("div", { class: "grid-2" }, keyValue("الـ session", el("span", { class: "mono", dir: "ltr" }, state.sessionId ? `${state.sessionId.slice(0, 8)}…` : "يُنشأ أول ما تكتب")), keyValue("الذاكرة", `${agent.memory_turns ?? "—"} لفة`)),
      el(
        "div",
        { class: "row" },
        el("button", { class: "ghost-btn", type: "button", onclick: () => newSession() }, icon("refresh", 14), el("span", {}, "سياق جديد")),
        el("button", { class: "ghost-btn", type: "button", onclick: () => copyTranscript() }, icon("copy", 14), el("span", {}, "نسخ المحادثة")),
      ),
    ),
    el(
      "div",
      { class: "card" },
      el("h4", { class: "panel-title" }, el("span", {}, "النموذج والرد"), statusBadge(model.engine === "simulated" ? "pending" : "accepted", model.engine === "simulated" ? "offline engine" : "live provider")),
      el("div", { class: "grid-2" }, keyValue("الاسم", el("span", { class: "mono", dir: "ltr" }, model.name || "—")), keyValue("المزوّد", model.provider || "—"), keyValue("temperature", model.temperature ?? "—"), keyValue("max tokens", model.max_tokens ?? "—")),
      el("div", { class: "grid-2" }, keyValue("أسلوب الرد", agent.response_style || "—"), keyValue("اللهجة", agent.dialect || "—"), keyValue("قراءة تحليلية", agent.narrative_composer ? "مفتوحة" : "مقفولة"), keyValue("prompt", el("span", { class: "mono", dir: "ltr" }, model.prompt_version || "—"))),
      el(
        "div",
        { class: "row" },
        el(
          "label",
          { class: "switch", title: "بث الرد حرف بحرف (SSE)" },
          el("input", { type: "checkbox", onchange: (event) => { state.streaming = event.target.checked; store.set("streaming", event.target.checked); $("btn-stream-state") && ($("btn-stream-state").textContent = event.target.checked ? "مفتوح" : "مقفل"); } }),
          el("i"),
        ),
        el("span", { class: "panel-sub" }, "بث مباشر: ", el("b", { id: "btn-stream-state" }, state.streaming ? "مفتوح" : "مقفل")),
      ),
    ),
    el(
      "div",
      { class: "card card-compact" },
      el("h4", { class: "panel-title" }, el("span", {}, "الاتصال"), statusBadge(odoo.online ? "accepted" : "error", odoo.online ? "ERP online" : "ERP offline")),
      el("div", { class: "grid-2" }, keyValue("قاعدة البيانات", el("span", { class: "mono", dir: "ltr" }, odoo.database || "—")), keyValue("زمن الاستجابة", `${num(odoo.latency_ms ?? 0, 1)} ms`), keyValue("النقل", shorten(odoo.transport || "—", 26)), keyValue("breaker", (data.circuit_breaker || {}).state || "closed")),
    ),
    quickPromptsCard(),
  );
}

const DEFAULT_PROMPTS = [
  { icon: "🔍", label: "دوّر على عميل", prompt: "دوّر على العميل محمد أحمد" },
  { icon: "📦", label: "المخزون والأسعار", prompt: "اعرض قائمة المنتجات والأسعار والمخزون المتاح" },
  { icon: "🧾", label: "أمر بيع", prompt: "اعمل طلب بيع للعميل 42 لعدد 15 من المنتج 55" },
  { icon: "🛡️", label: "منع التكرار", prompt: "/demo replay" },
  { icon: "🔎", label: "حالة السلسلة", prompt: "/audit 5" },
];

function quickPrompts() {
  return state.quickPrompts || DEFAULT_PROMPTS;
}

function quickPromptsCard() {
  return el(
    "div",
    { class: "card card-compact" },
    el(
      "h4",
      { class: "panel-title" },
      el("span", {}, "اختصاراتك"),
      el("button", { class: "util-btn", type: "button", onclick: () => editQuickPrompts() }, icon("edit", 13), el("span", {}, "عدّل")),
    ),
    el(
      "div",
      { class: "actions" },
      ...quickPrompts().map((row) => el("button", { class: "action", type: "button", onclick: () => sendTurn(row.prompt), title: row.prompt }, el("span", { class: "action-emoji" }, row.icon || "→"), el("span", { dir: "auto" }, row.label))),
    ),
  );
}

function editQuickPrompts() {
  const current = quickPrompts()
    .map((row) => `${row.label} ||| ${row.prompt}`)
    .join("\n");
  const area = el("textarea", { rows: 8, class: "md-code", dir: "auto", style: "inline-size:100%;font-family:var(--font-sans);font-size:var(--text-sm)" });
  area.value = current;
  const body = el("div", { class: "markdown" }, el("p", { class: "panel-sub" }, "سطر لكل اختصار: الاسم ||| النص اللي يتبعت. السطور الفاضلة تتشال."), area);
  modal({
    title: "اختصارات شريط الإدخال",
    subtitle: "بتتخزن على جهازك بس",
    body,
    footer: el(
      "div",
      { class: "row", style: "inline-size:100%" },
      el("button", { class: "ghost-btn", type: "button", onclick: () => { state.quickPrompts = DEFAULT_PROMPTS; store.set("quickPrompts", null); renderContextPanel(); renderSuggestions(); closeTopModal(); toast({ tone: "success", message: "رجّعنا الاختصارات الافتراضية." }); } }, "استرجاع الافتراضي"),
      el("button", { class: "primary-btn", type: "button", onclick: () => { const rows = area.value.split("\n").map((line) => line.split("|||")).filter((parts) => parts.length >= 2).map((parts) => ({ icon: "→", label: parts[0].trim(), prompt: parts[1].trim() })); state.quickPrompts = rows; store.set("quickPrompts", rows); renderContextPanel(); renderSuggestions(); closeTopModal(); toast({ tone: "success", message: `تم ${rows.length} اختصار.` }); } }, icon("check", 16), el("span", {}, "احفظ")),
    ),
  });
}

function renderSuggestions() {
  const row = dom.suggestions();
  if (!row) return;
  row.replaceChildren(
    ...quickPrompts().map((item) => el("button", { class: "suggest", type: "button", onclick: () => sendTurn(item.prompt), title: item.prompt }, el("span", { class: "action-emoji" }, item.icon || "→"), " ", item.label)),
  );
}

function newSession() {
  state.sessionId = "";
  state.turns = [];
  store.set("session", "");
  persistTurns();
  mountThread();
  toast({ tone: "success", message: "فتحنا سياق جديد — الـ history اتقفل عند الخادم للجلسة السابقة." });
  fetchTelemetry();
}

function copyTranscript() {
  const text = state.turns.map((row) => `${row.role === "user" ? "أنت" : "Mizan"}: ${row.text}`).join("\n\n");
  if (!text) {
    toast({ tone: "warn", message: "مفيش محادثة تتنسخ بعد." });
    return;
  }
  copyText(text, "اتنسخ نص المحادثة");
}

/* -------------------------------------------------------------------- tools UI */

async function loadTools() {
  try {
    const data = await api("/api/tools", { timeout: 8000 });
    state.tools = data.tools || [];
    renderToolsPanel();
    $("tools-count") && ($("tools-count").textContent = String(data.count ?? state.tools.length));
  } catch (error) {
    state.tools = [];
    renderToolsPanel();
  }
}

function renderToolsPanel() {
  const panel = $("panel-tools");
  if (!panel) return;
  const telemetry = state.telemetry || {};
  if (!state.tools.length) {
    panel.replaceChildren(emptyState("عقود الأدوات لسه محمّلتش", "اتأكد من الاتصال.", el("button", { class: "ghost-btn", type: "button", onclick: () => loadTools() }, icon("refresh", 14), el("span", {}, "أعد التحميل"))));
    return;
  }
  panel.replaceChildren(
    el(
      "div",
      { class: "row" },
      el("h4", { class: "panel-title" }, el("span", {}, `${state.tools.length} عقود موقّعة`)),
      el("span", { class: "panel-sub" }, "مقفلة على الخادم"),
    ),
    ...state.tools.map((tool) =>
      el(
        "div",
        { class: "tool-item" },
        el(
          "div",
          { class: "row" },
          el("span", { class: "tool-name", dir: "ltr" }, tool.name),
          statusBadge(tool.readOnly && !tool.destructive ? "success" : "warn", tool.readOnly ? "read" : "write"),
        ),
        el("p", { class: "panel-sub", dir: "auto" }, tool.description || ""),
        el("div", { class: "row" }, chip(`risk ${tool.risk_level || "R0"}`), chip(`v${tool.version}`), tool.requiresConfirmation ? chip("توقيع مطلوب") : null),
        el(
          "div",
          { class: "row" },
          el("button", { class: "ghost-btn", type: "button", onclick: () => sendTurn(sampleFor(tool)) }, icon("play", 13), el("span", {}, "جرّب")),
          tool.requiresConfirmation
            ? el("button", { class: "ghost-btn", type: "button", onclick: () => triggerDemoReplay() }, icon("shield", 13), el("span", {}, "منع التكرار"))
            : null,
        ),
        el(
          "details",
          { class: "schema" },
          el("summary", {}, "inputSchema"),
          el("pre", {}, JSON.stringify(tool.inputSchema || {}, null, 2)),
        ),
      ),
    ),
  );
}

const sampleFor = (tool) =>
  ({
    "customer.search": "دوّر على العميل علي",
    "customer.get": "افتح كارت العميل 42",
    "product.search": "اعرض المنتجات والمخزون",
    "sales.order.create": "اعمل طلب بيع للعميل 42 لعدد 5 من المنتج 55",
    "sales.order.get": "جيب أمر البيع 100",
  })[tool.name] || `استخدم ${tool.name}`;

function openToolsModal() {
  const body = el("div", { class: "markdown" });
  renderToolsInto(body);
  modal({ title: "عقود الأدوات", subtitle: `${state.tools.length} أدوات · تعريفات مقفلة على الخادم`, size: "lg", body, footer: el("button", { class: "ghost-btn", type: "button", onclick: () => selectRailTab("tools") }, el("span", {}, "افتحها في الشريط")) });
}

function renderToolsInto(host) {
  host.textContent = "";
  for (const tool of state.tools) {
    host.append(
      el(
        "div",
        { class: "tool-item" },
        el("div", { class: "row" }, el("span", { class: "tool-name", dir: "ltr" }, tool.name), statusBadge(tool.readOnly ? "success" : "warn", tool.destructive ? "write" : "read")),
        el("p", { class: "panel-sub" }, tool.description || ""),
        el("details", { class: "schema" }, el("summary", {}, "المخطط"), el("pre", {}, JSON.stringify(tool.inputSchema || {}, null, 2))),
      ),
    );
  }
}

/* --------------------------------------------------------------------- audit UI */

async function loadAudit({ silent = false } = {}) {
  try {
    const data = await api(`/api/audit?limit=${state.audit.records.length ? Math.min(200, Math.max(25, state.audit.records.length)) : 25}`, { timeout: 8000 });
    state.audit = { records: data.records || [], chain_valid: Boolean(data.chain_valid), count: data.count || 0, verification: data.verification };
    renderAuditPanel();
    $("audit-count") && ($("audit-count").textContent = String(data.count ?? 0));
    $("audit-chain-badge") && setChainBadge($("audit-chain-badge"), Boolean(data.chain_valid));
    if (!silent && data.records?.length) toast({ tone: "info", message: `${data.records.length} كتلة في السلسلة.`, timeout: 1800 });
  } catch (error) {
    if (!silent) toast({ tone: "danger", title: "سجل التدقيق", message: errorText(error) });
  }
}

function setChainBadge(node, valid) {
  node.replaceChildren(el("span", { class: `dot ${valid ? "dot-on" : "dot-off"}` }), el("span", {}, valid ? "السلسلة سليمة" : "السلسلة مكسورة"));
  node.title = valid ? "كل كتلة موقّعة بسابقة الكتلة — مفيش تعديل retrospective." : "في كسر في سلسلة الهashes — راجع الـ audit store.";
  node.className = `stat ${valid ? "stat-ok" : "stat-bad"}`;
}

function renderAuditPanel() {
  const panel = $("panel-audit");
  if (!panel) return;
  const records = state.audit.records || [];
  panel.replaceChildren(
    el(
      "div",
      { class: "row" },
      el("h4", { class: "panel-title" }, el("span", {}, "سلسلة التدقيق"), el("span", { class: `stat ${state.audit.chain_valid ? "stat-ok" : "stat-bad"}`, id: "rail-chain-badge" }, el("span", { class: `dot ${state.audit.chain_valid ? "dot-on" : "dot-off"}` }), el("span", {}, state.audit.chain_valid ? "سليمة" : "مكسورة"))),
      el("button", { class: "ghost-btn", type: "button", onclick: () => loadAudit() }, icon("refresh", 14), el("span", {}, "تحديث")),
    ),
    el(
      "div",
      { class: "row" },
      el("input", { class: "table-filter", type: "search", placeholder: "فلتر حسب الأداة أو الحالة…", "aria-label": "فلتر سجل التدقيق", oninput: (event) => filterAudit(event.target.value) }),
      chip(`${records.length} كتلة`),
    ),
    el("div", { class: "audit-list", id: "audit-records-list" }, ...records.map(auditItem)),
  );
}

function auditItem(record) {
  const status = record.result_status || record.status || "—";
  const tone = /denied|error|conflict/i.test(String(status)) ? "danger" : /confirm|pending/i.test(String(status)) ? "warn" : "success";
  const node = el(
    "div",
    { class: "audit-item", dataset: { id: String(record.id ?? ""), tool: String(record.tool_name || "") } },
    el(
      "div",
      { class: "audit-item-head" },
      el("b", { class: "mono" }, `#${record.id ?? "—"}`),
      el("span", { class: "mono", dir: "ltr" }, shorten(record.tool_name || "—", 24)),
      statusBadge(status, record.decision ? `${status}/${record.decision}` : status),
      el("span", { class: "meta-dim" }, utcTime(record.ts || record.created_at)),
      el("span", { class: "meta-dim" }, relTime(record.ts || record.created_at)),
    ),
    el(
      "div",
      { class: "row" },
      el("span", { class: "meta-dim mono", dir: "ltr" }, `${String(record.block_hash || record.hash || "").slice(0, 14)}…`),
      el("button", { class: "audit-toggle", type: "button", onclick: (event) => { const pre = node.querySelector("pre"); const open = pre.classList.toggle("is-hidden"); event.currentTarget.querySelector("span").textContent = open ? "إخفاء التفاصيل" : "التفاصيل"; } }, icon("eye", 12), el("span", {}, "التفاصيل")),
    ),
    el("pre", { class: "is-hidden" }, JSON.stringify(record, null, 2)),
  );
  return node;
}

function filterAudit(needle) {
  const text = needle.trim().toLowerCase();
  document.querySelectorAll("#audit-records-list .audit-item").forEach((node) => {
    const hit = !text || node.textContent.toLowerCase().includes(text);
    node.classList.toggle("is-hidden", !hit);
  });
}

function openAuditModal({ focus = null } = {}) {
  const body = el("div", { class: "markdown" });
  const summary = el(
    "div",
    { class: "row" },
    el("span", { class: `stat ${state.audit.chain_valid ? "stat-ok" : "stat-bad"}` }, el("span", { class: `dot ${state.audit.chain_valid ? "dot-on" : "dot-off"}` }), el("span", {}, state.audit.chain_valid ? "السلسلة سليمة" : "السلسلة مكسورة")),
    el("span", { class: "panel-sub" }, `${state.audit.count ?? 0} كتلة معروضة · ${state.audit.records?.length ?? 0} مفهرسة`),
    el("button", { class: "ghost-btn", type: "button", onclick: () => { downloadCsv(auditCsv(), "mizan-audit.csv"); toast({ tone: "success", message: "اتنزل CSV من السجل." }); } }, icon("download", 14), el("span", {}, "CSV")),
  );
  const list = el("div", { class: "audit-list", id: "audit-records-list" }, ...(state.audit.records || []).map(auditItem));
  body.append(summary, list);
  const { panel } = modal({ title: "سجل التدقيق", subtitle: "append-only · كل كتلة موقّعة بسابقتها", size: "lg", body, onClose: () => renderAuditPanel() });
  if (focus != null) {
    const target = panel.querySelector(`.audit-item[data-id="${focus}"]`);
    if (target) {
      target.classList.add("is-dirty");
      target.style.borderColor = "var(--accent)";
      target.querySelector("pre")?.classList.remove("is-hidden");
      target.scrollIntoView({ block: "center" });
    }
  }
  loadAudit({ silent: true });
}

function auditCsv() {
  const rows = state.audit.records || [];
  const keys = ["id", "ts", "tool_name", "result_status", "decision", "user_id", "request_id", "block_hash"];
  return [keys.join(","), ...rows.map((row) => keys.map((key) => csvSafe(row[key])).join(","))].join("\n");
}

async function triggerDemoReplay() {
  try {
    const data = await api("/api/test/replay", { method: "POST", body: { idempotency_key: "a".repeat(32) }, allow: [409] });
    const card = renderAnswerNode(data, { intent: "/demo replay" });
    const li = turnShell("assistant");
    li.append(el("div", { class: "turn-meta" }, el("span", { class: "avatar" }, "⚖️"), el("b", {}, "Mizan"), el("span", { class: "meta-dim" }, "محاكاة هجوم التكرار")), card);
    dom.thread().querySelector(".thread-inner")?.append(li);
    scrollThread();
    $("replay-feedback") && ($("replay-feedback").textContent = data?.answer?.headline || "اتحجب الطلب المكرر");
    toast({ tone: "warn", title: "الـ ERP اتحمى", message: "محاكاة إعادة نفس مفتار التكرار اترفضت بـ 409 زي ما المفروض." });
  } catch (error) {
    toast({ tone: "danger", message: errorText(error) });
  }
}

/* ------------------------------------------------------------------ integrations */

async function loadIntegrations() {
  try {
    const data = await api("/api/integrations", { timeout: 8000 });
    state.integrations = data;
    renderIntegrationsPanel();
  } catch (error) {
    state.integrations = null;
    renderIntegrationsPanel();
  }
}

function renderIntegrationsPanel() {
  const panel = $("panel-integrations");
  if (!panel) return;
  const data = state.integrations;
  if (!data) {
    panel.replaceChildren(emptyState("التكاملات محملتتش", "الاتصال بالـ cockpit اتقطع.", el("button", { class: "ghost-btn", type: "button", onclick: () => loadIntegrations() }, icon("refresh", 14), el("span", {}, "أعد المحاولة"))));
    return;
  }
  const specs = data.integrations || [];
  panel.replaceChildren(
    el(
      "div",
      { class: "card card-compact" },
      el("h4", { class: "panel-title" }, el("span", {}, "إشعارات ما بعد التنفيذ"), statusBadge(data.enabled_globally ? "accepted" : "pending", data.enabled_globally ? "مفعّلة" : "مقفولة")),
      el("p", { class: "panel-sub" }, "ميزان مابيطلّعش أي حاجة برا الجهاز غير لما تفعّلها وتحفظ الإعدادات؛ وضع المعاينة (dry-run) بيطبع الرسالة من غير ما يبعت."),
      el(
        "label",
        { class: "row" },
        el("span", { class: "setting-label" }, el("b", {}, "السماح بالإشعارات الصادرة"), el("small", {}, "features.outbound_notifications")),
        switchControl("features.outbound_notifications", Boolean(data.enabled_globally)),
      ),
    ),
    ...specs.map((spec) => integrationCard(spec)),
  );
}

function switchControl(key, checked) {
  return el(
    "label",
    { class: "switch" },
    el("input", {
      type: "checkbox",
      checked,
      onchange: async (event) => {
        await applySettings({ [key]: event.target.checked });
      },
    }),
    el("i"),
  );
}

function integrationCard(spec) {
  const values = { ...(spec.values || {}) };
  const fields = el(
    "div",
    { class: "integration-fields" },
    ...(spec.fields || []).map((field) =>
      el(
        "div",
        { class: "ifield" },
        el("label", { for: `int-${spec.id}-${field.key}` }, el("span", {}, field.label || field.key), field.required ? el("b", { class: "meta-dim" }, " *") : null),
        field.type === "choice"
          ? el(
              "select",
              { id: `int-${spec.id}-${field.key}`, dataset: { field: field.key } },
              ...(field.choices || []).map((choice) => el("option", { value: choice, selected: String(values[field.key]) === String(choice) ? "true" : null }, choice)),
            )
          : el("input", { id: `int-${spec.id}-${field.key}`, type: field.secret ? "password" : field.type === "number" ? "number" : "text", value: values[field.key] ?? "", dataset: { field: field.key }, placeholder: field.placeholder || "", autocomplete: "off" }),
      ),
    ),
    el("p", { class: "panel-sub", dir: "auto" }, spec.delivery_detail || ""),
    el(
      "div",
      { class: "row" },
      el("button", { class: "ghost-btn", type: "button", onclick: (event) => testIntegration(spec, collectConfig(event), false) }, icon("play", 13), el("span", {}, "معاينة")),
      el("button", { class: "ghost-btn", type: "button", onclick: (event) => testIntegration(spec, collectConfig(event), true) }, icon("bolt", 13), el("span", {}, "إرسال حقيقي")),
      el("button", { class: "ghost-btn", type: "button", onclick: (event) => saveIntegration(spec, event) }, icon("check", 13), el("span", {}, "حفظ")),
    ),
  );
  const card = el(
    "div",
    { class: "integration", dataset: { id: spec.id } },
    el("span", { class: "integration-icon" }, spec.icon || "📡"),
    el("div", {}, el("h5", {}, spec.name), el("p", { dir: "auto" }, spec.description || ""), el("div", { class: "row" }, chip(spec.kind), ...(spec.events || []).slice(0, 3).map((event) => el("span", { class: "meta-dim mono", dir: "ltr" }, event)))),
    el(
      "div",
      { class: "row" },
      el("span", { class: `dot ${spec.enabled ? "dot-on" : ""}`, title: spec.enabled ? "مفعل" : "مقفل" }),
      el("button", { class: "icon-btn", type: "button", "aria-label": "إعدادات التكامل", onclick: () => card.classList.toggle("is-open") }, icon("chevron", 15, "icon-flip")),
    ),
    fields,
  );
  return card;
}

function collectConfig(event) {
  const card = event.currentTarget.closest(".integration");
  const config = {};
  card.querySelectorAll("[data-field]").forEach((input) => {
    const value = input.value;
    if (value === "••••") return;
    config[input.dataset.field] = input.type === "checkbox" ? input.checked : value;
  });
  return config;
}

async function testIntegration(spec, config, live) {
  try {
    const data = await api("/api/integrations/test", { method: "POST", body: { id: spec.id, config, live } });
    const delivery = data.delivery || {};
    const message = delivery.detail || (delivery.preview ? Object.values(delivery.preview)[0] : "") || delivery.status;
    toast({
      tone: data.success ? "success" : "warn",
      title: `${spec.name} · ${delivery.status || "—"}`,
      message: String(message).slice(0, 220),
      timeout: 7000,
    });
    const card = document.querySelector(`.integration[data-id="${spec.id}"]`);
    if (card) {
      const holder = card.querySelector(".panel-sub");
      if (holder) holder.textContent = `${delivery.status || ""} — ${String(message).slice(0, 160)}`;
    }
    loadIntegrations();
  } catch (error) {
    toast({ tone: "danger", title: "اختبار التكامل فشل", message: errorText(error) });
  }
}

async function saveIntegration(spec, event) {
  const config = collectConfig(event);
  const all = { ...((state.integrations?.integrations || []).reduce((acc, row) => ({ ...acc, [row.id]: row.values || {} }), {})) };
  all[spec.id] = { ...(all[spec.id] || {}), ...config, enabled: !spec.enabled ? true : spec.enabled };
  await applySettings({ "integrations.config": all });
  loadIntegrations();
}

/* ---------------------------------------------------------------------- settings */

async function loadSettings() {
  try {
    const data = await api("/api/settings", { timeout: 8000 });
    state.settings = data.settings || null;
    renderSettingsPanel();
  } catch (error) {
    renderSettingsPanel(error);
  }
}

function settingsItems() {
  const groups = state.settings?.groups || [];
  return groups.flatMap((group) => (group.items || []).map((item) => ({ ...item, group: group.id })));
}

function renderSettingsPanel(loadError = null) {
  const panel = $("panel-settings");
  if (!panel) return;
  if (loadError || !state.settings) {
    panel.replaceChildren(
      emptyState("الإعدادات محملتتش", loadError ? errorText(loadError) : "جرب تحملها تاني.", el("button", { class: "ghost-btn", type: "button", onclick: () => loadSettings() }, icon("refresh", 14), el("span", {}, "أعد التحميل"))),
    );
    return;
  }
  const groups = state.settings.groups || [];
  const filter = el("input", { class: "table-filter", type: "search", placeholder: "دوّر في ~" + settingsItems().length + " إعداد…", "aria-label": "بحث في الإعدادات", oninput: (event) => filterSettings(event.target.value) });
  panel.replaceChildren(
    el(
      "div",
      { class: "row" },
      el("h4", { class: "panel-title" }, el("span", {}, "كل إعدادات المحرك"), chip(`v${state.settings.version ?? 1}`)),
      filter,
    ),
    el(
      "div",
      { class: "row" },
      el("button", { class: "ghost-btn", type: "button", id: "settings-apply", disabled: true, onclick: () => applyDraft() }, icon("check", 14), el("span", {}, "طبّق التغييرات")),
      el("button", { class: "ghost-btn", type: "button", onclick: () => resetAllSettings() }, icon("refresh", 14), el("span", {}, "استرجاع الكل")),
      el("button", { class: "ghost-btn", type: "button", onclick: () => downloadCsv(JSON.stringify(state.settings, null, 2), "mizan-settings.json") }, icon("download", 14), el("span", {}, "تصدير")),
    ),
    ...groups.map((group) =>
      el(
        "div",
        { class: "setting-group", "data-group": group.id },
        el("h5", { class: "setting-group-head" }, el("span", {}, group.icon || "⚙️"), el("span", {}, group.label)),
        ...(group.items || []).map((item) => settingRow(item)),
      ),
    ),
    el("div", { class: "card card-compact" }, el("h5", { class: "setting-group-head" }, el("span", {}, "📜"), el("span", {}, "آخر التغييرات")), ...(state.settings.history || []).length ? (state.settings.history || []).map((row) => el("p", { class: "panel-sub", dir: "auto" }, `${row.actor || "operator"} · ${row.changed ? `${Object.keys(row.changed).length} مفتاح` : row.keys ? `${row.keys.length} مفتاح` : ""} · ${utcTime(row.at || row.ts || "")}`)) : [el("p", { class: "panel-sub" }, "مفيش تغييرات مسجلة.")]),
  );
  $("settings-dirty-count") && $("settings-dirty-count").remove();
}

function filterSettings(needle) {
  const text = needle.trim().toLowerCase();
  document.querySelectorAll("#panel-settings .setting").forEach((node) => {
    const hit = !text || node.textContent.toLowerCase().includes(text) || node.dataset.key?.includes(text);
    node.classList.toggle("is-hidden", !hit);
  });
  document.querySelectorAll("#panel-settings .setting-group").forEach((group) => {
    const visible = group.querySelectorAll(".setting:not(.is-hidden)").length;
    group.classList.toggle("is-hidden", !visible && Boolean(text));
  });
}

function settingRow(item) {
  const draft = state.settingsDraft[item.key];
  const value = draft === undefined ? item.value : draft;
  const control = controlFor(item, value);
  const dirty = draft !== undefined && String(draft) !== String(item.value);
  const row = el(
    "div",
    { class: `setting${dirty ? " is-dirty" : ""}`, dataset: { key: item.key, type: item.type } },
    el(
      "label",
      { class: "setting-label", for: `set-${item.key}` },
      el("b", {}, item.label || item.key),
      item.help ? el("small", { dir: "auto" }, item.help) : null,
      el("span", { class: "setting-key", dir: "ltr" }, item.key),
    ),
    el(
      "div",
      { class: "setting-control" },
      item.restart_required ? chip("إعادة تشغيل", { title: "المحرك بياخد القيمة دي عند التشغيل من جديد" }) : null,
      control,
      el("button", { class: "reset-key", type: "button", title: "ارجع للقيمة الافتراضية", "aria-label": `استرجاع ${item.key}`, onclick: () => resetKeys([item.key]) }, icon("refresh", 12)),
    ),
  );
  if (item.is_default === false && !dirty) row.querySelector(".setting-label").append(el("small", { class: "meta-dim" }, `الافتراضي: ${formatValue(item.default)}`));
  return row;
}

const formatValue = (value) => (typeof value === "boolean" ? (value ? "مفتوح" : "مقفل") : value === "" || value == null ? "—" : String(value));

function controlFor(item, value, onInput = null) {
  const mark = (next) => {
    state.settingsDraft[item.key] = next;
    const row = document.querySelector(`#panel-settings .setting[data-key="${item.key}"]`);
    row?.classList.toggle("is-dirty", String(next) !== String(item.value));
    const apply = $("settings-apply");
    if (apply) apply.disabled = !Object.keys(state.settingsDraft).length;
    onInput?.(next);
  };
  if (item.control === "toggle" || item.type === "bool") {
    return el("label", { class: "switch" }, el("input", { type: "checkbox", id: `set-${item.key}`, checked: Boolean(value), onchange: (event) => mark(event.target.checked) }), el("i"));
  }
  if (item.control === "select" || item.choices?.length) {
    return el(
      "select",
      { id: `set-${item.key}`, onchange: (event) => mark(event.target.value) },
      ...(item.choices || []).map((choice) => el("option", { value: choice, selected: String(choice) === String(value) ? "true" : null }, choice)),
    );
  }
  if (item.control === "number") {
    const step = item.type === "float" ? "0.1" : "1";
    return el("input", {
      id: `set-${item.key}`,
      type: "number",
      value: value ?? "",
      min: item.min ?? undefined,
      max: item.max ?? undefined,
      step,
      inputmode: "decimal",
      oninput: (event) => mark(event.target.value === "" ? null : Number(event.target.value)),
    });
  }
  if (item.control === "json" || item.type === "dict") {
    return el("textarea", { id: `set-${item.key}`, rows: 3, spellcheck: false, oninput: (event) => mark(event.target.value) });
  }
  return el("input", {
    id: `set-${item.key}`,
    type: item.secret ? "password" : "text",
    value: value ?? "",
    autocomplete: "off",
    placeholder: item.secret && !item.configured ? "غير مضبوط" : "",
    oninput: (event) => mark(event.target.value),
  });
}

async function applyDraft() {
  const payload = {};
  for (const [key, raw] of Object.entries(state.settingsDraft)) {
    const item = settingsItems().find((row) => row.key === key);
    let value = raw;
    if (item?.type === "dict" && typeof value === "string") {
      try {
        value = JSON.parse(value);
      } catch {
        toast({ tone: "danger", title: `JSON غلط في ${key}`, message: "راجع الأقواس والفواصل." });
        return;
      }
    }
    payload[key] = value;
  }
  await applySettings(payload);
}

async function applySettings(payload) {
  try {
    const data = await api("/api/settings", { method: "POST", body: { settings: payload }, timeout: 15000 });
    if (data.errors && Object.keys(data.errors).length) {
      toast({ tone: "danger", title: "في إعدادات مرفوضة", message: Object.entries(data.errors).map(([key, message]) => `${key}: ${message}`).join(" — ").slice(0, 260), timeout: 8000 });
      for (const [key, message] of Object.entries(data.errors)) {
        const row = document.querySelector(`#panel-settings .setting[data-key="${key}"]`);
        if (row) {
          row.classList.add("tone-danger");
          row.title = message;
        }
      }
    }
    const changed = data.changed || [];
    if (changed.length) toast({ tone: "success", title: `اتطبّق ${changed.length} إعداد`, message: data.requires_restart ? "بعضها محتاج إعادة تشغيل السيرفر." : "القيم بقت سارية على الجلسة الحيّة." });
    state.settingsDraft = {};
    state.settings = data.settings || state.settings;
    renderSettingsPanel();
    renderContextPanel();
    fetchTelemetry();
    return data;
  } catch (error) {
    toast({ tone: "danger", title: "حفظ الإعدادات فشل", message: errorText(error) });
    return null;
  }
}

async function resetKeys(keys) {
  try {
    const data = await api("/api/settings/reset", { method: "POST", body: { keys } });
    state.settings = data.settings || state.settings;
    state.settingsDraft = {};
    renderSettingsPanel();
    toast({ tone: "success", message: `استرجعنا ${data.restored?.length ?? keys.length} إعداد للافتراضي.` });
    fetchTelemetry();
  } catch (error) {
    toast({ tone: "danger", message: errorText(error) });
  }
}

function resetAllSettings() {
  const dismiss = toast({
    tone: "warn",
    title: "استرجاع كل الإعدادات؟",
    message: "كل القيم هترجع للافتراضي بتاع الخادم (مافيش مفتاح API هيتشال).",
    timeout: 0,
    action: { label: "استرجع", run: () => { dismiss?.(); resetKeys(settingsItems().filter((row) => row.is_default === false).map((row) => row.key)); } },
  });
}

function openSettingsModal() {
  const body = el("div", { class: "markdown" });
  const inner = el("div", { id: "panel-settings" });
  body.append(inner);
  modal({ title: "الإعدادات", subtitle: `${settingsItems().length} مفتاح — كله بيتطبق على السيرفر الحي`, size: "lg", body, footer: el("button", { class: "ghost-btn", type: "button", onclick: () => { closeTopModal(); selectRailTab("settings"); } }, el("span", {}, "افتح في الشريط الجانبي")) });
  state.settings && renderSettingsPanel();
}

/* ------------------------------------------------------------- composer wiring */

function autoGrow(input) {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, Math.round(window.innerHeight * 0.32))}px`;
}

function renderPalette(items, selectedIndex) {
  const palette = dom.palette();
  if (!palette) return;
  if (!items.length) {
    palette.hidden = true;
    return;
  }
  palette.hidden = false;
  const list = el("ul", { class: "palette-list", role: "listbox" });
  items.forEach((item, index) => {
    list.append(
      el(
        "li",
        { class: "palette-item", role: "option", "aria-selected": index === selectedIndex ? "true" : "false", dataset: { index: String(index) }, onclick: () => commitPalette(item) },
        el("span", { class: "cmd", dir: "rtl" }, item.command),
        el("span", { class: "desc", dir: "auto" }, item.help || ""),
        el("span", { class: "meta-dim" }, item.group || ""),
      ),
    );
  });
  palette.replaceChildren(
    list,
    el("div", { class: "palette-foot" }, el("span", {}, el("kbd", {}, "↑"), el("kbd", {}, "↓"), " تنقّل"), el("span", {}, el("kbd", {}, "Tab"), " أكمل"), el("span", {}, el("kbd", {}, "Enter"), " نفّذ"), el("span", {}, el("kbd", {}, "Esc"), " اقفل")),
  );
  palette.setAttribute("role", "listbox");
}

let paletteState = { items: [], index: 0, open: false };

function syncPalette(force = false) {
  const input = dom.input();
  const palette = dom.palette();
  if (!input || !palette) return;
  const value = input.value;
  if (!value.startsWith("/") || value.includes("\n")) {
    if (paletteState.open) {
      palette.hidden = true;
      paletteState = { items: [], index: 0, open: false };
    }
    return;
  }
  const needle = value.slice(1).trim().toLowerCase();
  const items = (state.commands || []).filter((row) => !needle || row.command.toLowerCase().includes(needle) || (row.help || "").toLowerCase().includes(needle));
  if (!items.length) {
    palette.hidden = false;
    palette.replaceChildren(el("div", { class: "palette-item" }, el("span", { class: "cmd" }, "/؟"), el("span", { class: "desc" }, "مفيش أمر بيطابق — دوس /help")) );
    paletteState = { items: [], index: 0, open: true };
    return;
  }
  const index = Math.min(paletteState.index, items.length - 1);
  if (!force && items.length === paletteState.items.length && index === paletteState.index && items.every((row, position) => row.command === paletteState.items[position]?.command)) return;
  paletteState = { items, index, open: true };
  renderPalette(items, index);
}

function commitPalette(item) {
  const input = dom.input();
  if (!item) return;
  const placeholder = item.command.match(/<[^>]+>/);
  dom.palette().hidden = true;
  paletteState = { items: [], index: 0, open: false };
  if (!placeholder) {
    /* A parameterless command is an action, not a draft — run it directly. */
    input.value = "";
    autoGrow(input);
    sendTurn(item.command.trim());
    return;
  }
  /* Complete the command and select the hole, so the next keystroke types over it. */
  input.value = item.command;
  const at = item.command.indexOf(placeholder[0]);
  autoGrow(input);
  input.focus();
  input.setSelectionRange(at, at + placeholder[0].length);
}

function handleInputKeydown(event) {
  const input = event.currentTarget;
  if (paletteState.open && paletteState.items.length) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      paletteState.index = (paletteState.index + delta + paletteState.items.length) % paletteState.items.length;
      renderPalette(paletteState.items, paletteState.index);
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      commitPalette(paletteState.items[paletteState.index]);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      dom.palette().hidden = true;
      paletteState = { items: [], index: 0, open: false };
      return;
    }
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    dom.form().requestSubmit();
    return;
  }
  if (event.key === "Escape" && state.busy && !modalOpen()) {
    event.preventDefault();
    state.controller?.abort();
    toast({ tone: "info", message: "ألغينا العرض — التنفيذ اللي بدأ في ERP بيكمّل بيتسجل.", timeout: 3000 });
  }
  store.set("draft", input.value);
}

/* ------------------------------------------------------------------- voice (opt) */

function toggleVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    toast({ tone: "warn", title: "المتصفح مالوش تعرّف صوتي", message: "استخدم Chrome أو Edge — أو اكتب طلبك، الاتنين بيوصلوا لنفس البوابة." });
    return;
  }
  if (state.recognition) {
    state.recognition.stop();
    state.recognition = null;
    $("btn-voice")?.setAttribute("aria-pressed", "false");
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = "ar-EG";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.onresult = (event) => {
    const text = Array.from(event.results).map((row) => row[0].transcript).join("");
    dom.input().value = text;
    autoGrow(dom.input());
  };
  recognition.onerror = () => toast({ tone: "warn", message: "المايك ماستمعتش كويس — قرب شوية أو اتكلم أوضح." });
  recognition.onend = () => {
    state.recognition = null;
    $("btn-voice")?.setAttribute("aria-pressed", "false");
  };
  state.recognition = recognition;
  recognition.start();
  $("btn-voice")?.setAttribute("aria-pressed", "true");
  toast({ tone: "info", message: "أنا سامع… اتكلم بالعامية.", timeout: 2400 });
}

/* ---------------------------------------------------------------- command palette */

function openCommandPalette() {
  const input = el("input", { class: "table-filter", type: "search", placeholder: "اكتب أمر أو إعداد أو حالة…", style: "inline-size:100%;font-size:var(--text-sm)", autofocus: true });
  const list = el("ul", { class: "palette-list", style: "inline-size:100%" });
  const commands = (state.commands || []).map((row) => ({ kind: "أمر", label: row.command, help: row.help, run: () => sendTurn(row.command.replace(/<[^>]+>/g, "")) }));
  const settings = settingsItems().map((row) => ({ kind: "إعداد", label: row.label || row.key, help: `${row.key} · ${formatValue(row.value)}`, run: () => { closeTopModal(); selectRailTab("settings"); filterIntoSettings(row.key); } }));
  const actions = [
    { kind: "إجراء", label: "سياق جديد", help: "يقفل الـ history دي", run: () => { closeTopModal(); newSession(); } },
    { kind: "إجراء", label: "محاكاة هجوم التكرار", help: "/demo replay", run: () => { closeTopModal(); triggerDemoReplay(); } },
    { kind: "إجراء", label: "سجل التدقيق", help: "افتح السلسلة", run: () => { closeTopModal(); openAuditModal({}); } },
    { kind: "إجراء", label: "وضع ليلي/نهاري", help: "تبديل السمة", run: () => { closeTopModal(); setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"); } },
  ];
  const all = [...commands, ...actions, ...settings];
  const draw = (needle) => {
    const text = needle.trim().toLowerCase();
    const items = all.filter((row) => !text || `${row.label} ${row.help}`.toLowerCase().includes(text)).slice(0, 40);
    list.replaceChildren(
      ...items.map((row, index) =>
        el(
          "li",
          { class: "palette-item", role: "option", "aria-selected": index === 0 ? "true" : "false", onclick: () => { closeTopModal(); row.run(); } },
          el("span", { class: "cmd", dir: "auto" }, row.label),
          el("span", { class: "desc", dir: "auto" }, row.help || ""),
          el("span", { class: "meta-dim" }, row.kind),
        ),
      ),
      items.length ? null : el("li", { class: "palette-item" }, el("span", { class: "desc" }, "مفيش نتيجة.")),
    );
  };
  draw("");
  input.addEventListener("input", () => draw(input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      list.querySelector('[aria-selected="true"]')?.click();
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const options = [...list.querySelectorAll(".palette-item")];
      const current = options.findIndex((node) => node.getAttribute("aria-selected") === "true");
      const next = (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % Math.max(1, options.length);
      options.forEach((node, index) => node.setAttribute("aria-selected", index === next ? "true" : "false"));
    }
  });
  modal({ title: "لوحة الأوامر", subtitle: "كل حاجة توصلها من الكيبورد", body: el("div", {}, input, list), onClose: () => dom.input()?.focus() });
  setTimeout(() => input.focus(), 30);
}

function filterIntoSettings(key) {
  const input = document.querySelector('#panel-settings input[type="search"]');
  if (input) {
    input.value = key;
    filterSettings(key);
  }
}

/* ------------------------------------------------------------------------- init */

async function loadCommands() {
  try {
    const data = await api("/api/commands", { timeout: 6000 });
    state.commands = data.commands || [];
  } catch {
    state.commands = [];
  }
}

function mountThread() {
  const thread = dom.thread();
  if (!thread) return;
  const inner = thread.querySelector(".thread-inner") || el("div", { class: "thread-inner" });
  clear(inner);
  if (!state.turns.length) {
    inner.append(
      el(
        "div",
        { class: "welcome" },
        el("h2", {}, "السلام عليكم ⚖️"),
        (() => {
        const intro = el("div", { class: "welcome-sub", dir: "auto" });
        if (state.greeting?.markdown) renderMarkdown(intro, state.greeting.markdown);
        else intro.textContent = "قولي محتاج إيه في Odoo — أنا بدوّر بقرأ، وبأنفّذ بعد توقيعك.";
        return intro;
      })(),
        el("div", { class: "actions" }, ...quickPrompts().map((row) => el("button", { class: "action", type: "button", onclick: () => sendTurn(row.prompt) }, el("span", { class: "action-emoji" }, row.icon || "→"), el("span", { dir: "auto" }, row.label)))),
      ),
    );
  } else {
    for (const turn of state.turns) {
      inner.append(
        turn.role === "user"
          ? addUserTurn(turn.text)
          : el("article", { class: "turn turn-assistant" }, el("div", { class: "turn-meta" }, el("span", { class: "avatar" }, "⚖️"), el("b", {}, "Mizan"), statusBadge(turn.status || "text_only", "من السجل")), el("div", { class: "bubble", dir: "auto" }, turn.text)),
      );
    }
  }
  thread.append(inner);
  scrollThread();
}

function wireComposer() {
  const form = dom.form();
  const input = dom.input();
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) {
      toast({ tone: "info", message: "اكتب طلبك الأول — مثلاً: دوّر على العميل علي.", timeout: 2600 });
      input.focus();
      return;
    }
    input.value = "";
    autoGrow(input);
    store.set("draft", "");
    dom.palette().hidden = true;
    sendTurn(value);
  });
  input.addEventListener("keydown", handleInputKeydown);
  input.addEventListener("input", () => {
    autoGrow(input);
    syncPalette(true);
  });
  input.addEventListener("blur", () => setTimeout(() => { if (!input.value.startsWith("/")) dom.palette().hidden = true; }, 120));
  input.value = store.get("draft", "");
  autoGrow(input);
  const stop = $("btn-stop");
  stop?.addEventListener("click", () => {
    if (state.busy) state.controller?.abort();
  });
  $("btn-voice")?.addEventListener("click", toggleVoice);
  $("btn-audit-drawer")?.addEventListener("click", () => openAuditModal({}));
  $("btn-tools-modal")?.addEventListener("click", openToolsModal);
  $("btn-settings")?.addEventListener("click", () => (window.innerWidth > 960 ? selectRailTab("settings") : openSettingsModal()));
  $("btn-commands")?.addEventListener("click", openCommandPalette);
  $("btn-rail-toggle")?.addEventListener("click", () => toggleRail());
  $("btn-replay-demo")?.addEventListener("click", triggerDemoReplay);
  $("btn-refresh-telemetry")?.addEventListener("click", () => { fetchTelemetry(); toast({ tone: "info", message: "تحديث القياسات.", timeout: 1500 }); });
}

function wireGlobalKeys() {
  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openCommandPalette();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && typing) {
      event.preventDefault();
      dom.form().requestSubmit();
      return;
    }
    if (event.key === "Escape" && modalOpen()) {
      closeTopModal();
      return;
    }
    if (event.key === "/" && !typing && !modalOpen()) {
      event.preventDefault();
      dom.input().value = "/";
      dom.input().focus();
      syncPalette(true);
    }
  });
  document.addEventListener("click", (event) => {
    const chip = event.target.closest(".md-command");
    if (chip?.dataset.command) {
      if (chip.dataset.command.startsWith("/")) sendTurn(chip.dataset.command);
      else copyText(chip.dataset.command, "اتنسخ");
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) fetchTelemetry();
  });
}

async function boot() {
  initTheme();
  buildRail();
  wireComposer();
  wireGlobalKeys();
  renderSuggestions();
  mountThread();
  try {
    const data = await api("/api/greeting", { timeout: 5000 });
    state.greeting = data;
    if (!state.turns.length) mountThread();
  } catch {
    /* greeting is decorative; the cockpit works without it */
  }
  await Promise.all([loadCommands(), loadTools(), fetchTelemetry(), loadAudit({ silent: true }), loadSettings(), loadIntegrations()]);
  dom.input()?.focus();
  setInterval(() => {
    if (!document.hidden) fetchTelemetry();
  }, 15000);
}

boot();
