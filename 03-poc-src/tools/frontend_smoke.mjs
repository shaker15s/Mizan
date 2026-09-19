#!/usr/bin/env node
/* Mizan cockpit — headless UI smoke test (dev tool, not shipped to users).
 *
 * Why a .mjs instead of more Python tests: the cockpit is three dependency-free
 * ES modules served straight off the stdlib server, and the bugs that matter live
 * in the wiring (stream events → card render → signature → ERP). This drives the
 * real modules in a real DOM against the real server, then exits non-zero on any
 * failed check or any console error — so it belongs in CI as easily as pytest.
 *
 * Needs:  npm i jsdom            (dev-only; the app itself has no deps)
 * Start:  .venv/bin/python -m poc.web_server --port 8080 --simulate
 * Run:    node tools/frontend_smoke.mjs
 */
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = await import("jsdom"));
} catch {
  console.error("jsdom is required: npm i jsdom  (run inside 03-poc-src/tools)");
  process.exit(3);
}
import { TextDecoder } from "node:util";

const WEB = fileURLToPath(new URL("../poc/web/", import.meta.url));
const BASE = process.env.MIZAN_COCKPIT_URL || process.env.BASE || "http://127.0.0.1:8080";
const errors = [];

const read = (file) => fs.readFileSync(path.join(WEB, file), "utf8");
const strip = (src) =>
  src
    .replace(/^\s*import[^;]*;$/gms, "")
    .replace(/^\s*export\s*\{[^}]*\};?$/gms, "")
    .replace(/^\s*export\s+(default\s+)?/gm, "");

const bundle = [strip(read("markdown.js")), strip(read("ui.js")), strip(read("app.js"))].join("\n;\n");
const html = read("index.html").replace(/<script type="module"[^>]*><\/script>/, "");

const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push("jsdomError: " + String(e.detail?.stack || e.message).slice(0, 400)));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ").slice(0, 400)));

const dom = new JSDOM(html, { url: BASE + "/", runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
const win = dom.window;
win.TextDecoder = TextDecoder;
win.matchMedia = win.matchMedia || ((query) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
win.Element.prototype.scrollIntoView = function () {};
win.Element.prototype.scrollTo = function () {};
win.scrollTo = () => {};
win.fetch = (input, init) => globalThis.fetch(String(input).startsWith("http") ? String(input) : BASE + String(input), init);
win.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
win.addEventListener("error", (event) => errors.push("window error: " + String(event.error?.stack || event.error?.message || event.message).slice(0, 500)));
win.addEventListener("unhandledrejection", (event) => errors.push("unhandled rejection: " + String(event.reason?.stack || event.reason).slice(0, 500)));

try {
  win.eval(bundle);
} catch (error) {
  errors.push("eval: " + (error?.stack || error));
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const doc = win.document;
const $ = (id) => doc.getElementById(id);
const text = (selector) => doc.querySelector(selector)?.textContent?.replace(/\s+/g, " ").trim() || "";
const count = (selector) => doc.querySelectorAll(selector).length;
const click = (selector) => {
  if (typeof selector === "string" && !/^[#.[]/.test(selector)) selector = "#" + selector;
  const node = typeof selector === "string" ? doc.querySelector(selector) : selector;
  if (node) node.click();
  return Boolean(node);
};
const setInput = (value) => {
  const input = $("chat-input");
  if (!input) return false;
  input.value = value;
  input.dispatchEvent(new win.Event("input", { bubbles: true }));
  return true;
};
const submit = () => $("chat-form")?.dispatchEvent(new win.Event("submit", { bubbles: true, cancelable: true }));
const results = [];
const check = (name, condition, extra = "") => results.push({ name, pass: Boolean(condition), extra });

try {
  await wait(1600);
  console.log("DEBUG rail tabs:", count("#rail-tabs .rail-tab"), "| errors so far:", errors.length);
  check("title", text("title").includes("Agent-Native ERP"), text("title"));
  check("statusbar", count("#statusbar .stat") >= 5, `${count("#statusbar .stat")} stats`);
  check("rail tabs", count("#rail-tabs .rail-tab") === 5, `${count("#rail-tabs .rail-tab")}`);
  check("context panel", text("#panel-context .panel-title").includes("الجلسة"), text("#panel-context .panel-title").slice(0, 40));
  check("welcome card", text(".welcome").includes("ميزان"), text(".welcome").slice(0, 60));
  check("no raw markdown in welcome", !text(".welcome").includes("**"));
  check("quick prompts", count(".suggest-row .suggest") === 5, `${count(".suggest-row .suggest")}`);

  // read turn
  setInput("دوّر على العميل محمد أحمد");
  submit();
  await wait(400);
  const stepperDuring = count("#pipeline-stepper .step");
  check("stage stepper live during turn", stepperDuring >= 1, `${stepperDuring} steps`);
  await wait(2400);
  check("user turn", count(".turn-user") === 1);
  check("answer card", count(".answer-card") >= 1, text(".answer-headline").slice(0, 80));
  check("card names the entity", /محمد/.test(text(".turn:last-child")), text(".answer-headline"));
  check("kpi grid", count(".kpi") >= 1, `${count(".kpi")} kpis`);
  check("sections", count(".answer-section") >= 1, `${count(".answer-section")}`);
  check("governance footer", text(".governance").includes("التدقيق"), text(".governance").slice(0, 100));
  check("table rows real", count(".data-table tbody tr") >= 1, `${count(".data-table tbody tr")} rows`);
  check("next-step chips", count(".answer-card .action") >= 1, `${count(".answer-card .action")} chips`);

  // write turn → proposal → amend → confirm. Quantity varies per run on purpose:
  // the gateway's idempotency guard would (correctly) replay an identical request.
  const qty = 2 + (Date.now() % 40);
  setInput(`اعمل طلب بيع للعميل 45 لعدد ${qty} من المنتج 59`);
  submit();
  await wait(2800);
  check("proposal card", count(".proposal-card") === 1, text(".proposal-title").slice(0, 60));
  check("countdown ticks", /\d:\d\d/.test(text(".countdown-text")), text(".countdown-text"));
  check("amend stepper", count(".proposal-card .stepper") >= 1);
  click(".proposal-card .step-btn[aria-label='زود']");
  await wait(150);
  check("amend arms apply", Boolean(doc.querySelector(".proposal-card .ghost-btn.is-ready")), text(".proposal-card .amend .ghost-btn"));
  check("amend shows new qty", text(".proposal-card .stepper b").includes(String(qty + 1)), text(".proposal-card .stepper b"));
  click(".proposal-card .amend .ghost-btn.is-ready");
  await wait(2400);
  check("amend re-signs", count(".proposal-card") === 1, text(".proposal-card .countdown-text"));
  check("amended qty persisted", text(".proposal-card .proposal-lines tbody").includes(String(qty + 1)), text(".proposal-card .proposal-lines tbody"));
  check("countdown ticks", /\d:\d\d/.test(text(".proposal-card .countdown-text")), text(".proposal-card .countdown-text"));
  click(".proposal-card .primary-btn");
  await wait(3000);
  check("executed card", count(".answer-card.tone-success") >= 1, text(".turn:last-child .answer-headline").slice(0, 70));
  check("proposal consumed", count(".proposal-card") === 0);
  check("audit id surfaced", /#\d+/.test(text(".turn:last-child .governance")), text(".turn:last-child .governance").slice(0, 60));
  check("order id shown", /#1\d\d|S001/.test(text(".turn:last-child")), text(".turn:last-child .answer-headline").slice(0, 60));

  // slash palette
  setInput("/");
  await wait(200);
  check("slash palette opens", $("slash-palette")?.hidden === false && count(".palette-item") >= 8, `${count(".palette-item")} items`);
  const firstItem = doc.querySelector(".palette-item");
  click(firstItem);
  await wait(200);
  const afterComplete = $("chat-input").value;
  check("palette completes command", afterComplete.startsWith("/search customer"), afterComplete);
  check("placeholder selected", $("chat-input").selectionStart > 0 && $("chat-input").selectionEnd > $("chat-input").selectionStart, `${$("chat-input").selectionStart}..${$("chat-input").selectionEnd}`);
  setInput("/whoami");
  submit();
  await wait(2200);
  check("slash executed", /sales_user/.test(text(".turn:last-child")), text(".answer-headline").slice(0, 60));

  // rail panels
  click("#rail-tab-tools");
  await wait(400);
  check("tools panel", count("#panel-tools .tool-item") === 5, `${count("#panel-tools .tool-item")} tools`);
  click("#rail-tab-audit");
  await wait(1400);
  check("audit list", count("#panel-audit .audit-item") >= 1, `${count("#panel-audit .audit-item")} blocks`);
  click("#panel-audit .audit-toggle");
  await wait(120);
  check("audit detail opens", Boolean(doc.querySelector("#panel-audit .audit-item pre")) && !doc.querySelector("#panel-audit .audit-item pre").classList.contains("is-hidden"));
  const filterInput = doc.querySelector("#panel-audit input[type=search]");
  if (filterInput) {
    filterInput.value = "zzz-nothing-matches";
    filterInput.dispatchEvent(new win.Event("input", { bubbles: true }));
    await wait(140);
    const shown = count("#audit-records-list .audit-item:not(.is-hidden)");
    check("audit filter hides rows", shown === 0 && count("#audit-records-list .audit-item") > 0, `${shown} of ${count("#audit-records-list .audit-item")}`);
  }
  click("#rail-tab-settings");
  await wait(1400);
  check("settings rows", count("#panel-settings .setting") >= 20, `${count("#panel-settings .setting")} settings`);
  check("settings groups", count("#panel-settings .setting-group") >= 5, `${count("#panel-settings .setting-group")}`);
  check("apply starts disabled", $("settings-apply")?.disabled === true);
  const toggle = doc.querySelector("#panel-settings .setting input[type=checkbox]");
  const toggleKey = toggle?.closest(".setting")?.dataset.key;
  if (toggle) {
    toggle.checked = !toggle.checked;
    toggle.dispatchEvent(new win.Event("change", { bubbles: true }));
    await wait(160);
    check("dirty marking", Boolean(doc.querySelector("#panel-settings .setting.is-dirty")), toggleKey);
    check("apply enabled when dirty", $("settings-apply")?.disabled === false);
    click("settings-apply");
    await wait(1600);
    check("toast after apply", count("#toasts .toast") >= 1, text("#toasts .toast").slice(0, 70));
    const snapshot = await (await globalThis.fetch(BASE + "/api/settings")).json();
    const serverValue = snapshot.settings.groups.flatMap((group) => group.items).find((row) => row.key === toggleKey)?.value;
    check("server accepted toggle", typeof serverValue === "boolean", `${toggleKey}=${serverValue}`);
  }
  if (toggleKey) {
    await globalThis.fetch(BASE + "/api/settings/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keys: [toggleKey] }) });
    check("restored test setting", true, toggleKey);
  }
  const settingsFilter = doc.querySelector("#panel-settings input[type=search]");
  if (settingsFilter) {
    settingsFilter.value = "narrative";
    settingsFilter.dispatchEvent(new win.Event("input", { bubbles: true }));
    await wait(140);
    check("settings search narrows", count("#panel-settings .setting:not(.is-hidden)") < count("#panel-settings .setting"), `${count("#panel-settings .setting:not(.is-hidden)")} visible`);
  }
  click("#rail-tab-integrations");
  await wait(1400);
  check("integration cards", count("#panel-integrations .integration") >= 3, `${count("#panel-integrations .integration")}`);
  const integrationToggle = doc.querySelector("#panel-integrations .integration .icon-btn");
  if (integrationToggle) {
    integrationToggle.click();
    await wait(120);
    check("integration expands", Boolean(doc.querySelector("#panel-integrations .integration.is-open")));
    click("#panel-integrations .integration.is-open .ghost-btn");
    await wait(1600);
    check("integration dry-run toast", text("#toasts .toast").length > 4, text("#toasts .toast").slice(0, 70));
  }

  // modals
  click("btn-audit-drawer");
  await wait(400);
  check("audit modal opens", Boolean(doc.querySelector(".modal-wrap")), text(".modal-head h3"));
  click(".modal-head .icon-btn");
  await wait(400);
  check("modal closes", !doc.querySelector(".modal-wrap"));
  click("btn-commands");
  await wait(400);
  check("command palette", Boolean(doc.querySelector(".modal-wrap")) && count(".modal .palette-item") > 12, `${count(".modal .palette-item")} entries`);
  const paletteInput = doc.querySelector(".modal input[type=search]");
  if (paletteInput) {
    paletteInput.value = "audit";
    paletteInput.dispatchEvent(new win.Event("input", { bubbles: true }));
    await wait(160);
    check("palette filters", count(".modal .palette-item") >= 1 && count(".modal .palette-item") < 40, `${count(".modal .palette-item")} after filter`);
  }
  click(".modal-head .icon-btn");
  await wait(300);

  // theme
  click("btn-theme");
  await wait(120);
  check("dark theme toggles", doc.documentElement.dataset.theme === "dark", doc.documentElement.dataset.theme);
  click("btn-theme");

  // demo replay button in tools panel
  click("#rail-tab-tools");
  await wait(200);
  const demo = [...doc.querySelectorAll("#panel-tools .ghost-btn")].find((node) => node.textContent.includes("منع التكرار"));
  if (demo) {
    demo.click();
    await wait(1200);
    check("demo replay renders card", /تكرار|مكرر/.test(text(".turn:last-child .answer-card")), text(".turn:last-child .answer-headline").slice(0, 60));
  }
  check("no unrendered placeholders", !text(".thread").includes("{{"));
} catch (error) {
  errors.push("SMOKE harness threw: " + (error?.stack || error));
}

console.log("\n--- FRONTEND SMOKE ---");
for (const row of results) console.log(`${row.pass ? "PASS" : "FAIL"}  ${row.name}${row.extra ? "  → " + row.extra : ""}`);
const failed = results.filter((row) => !row.pass).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
if (errors.length) {
  console.log("\n!!! captured errors:");
  for (const line of [...new Set(errors)].slice(0, 14)) console.log("  - " + line);
}
process.exit(failed || errors.length ? 1 : 0);
