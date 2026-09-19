/* Mizan cockpit — markdown → DOM, safely.
 *
 * Why hand-rolled instead of a CDN library: this renderer only has to handle the
 * markdown the composer emits (headings, tables, lists, bold/inline code,
 * blockquotes, links), it must build DOM nodes (never innerHTML from model text,
 * so a hallucinated/injected `<img onerror>` in an ERP note cannot execute), and
 * it must keep Arabic RTL while leaving IDs, phones and emails in LTR.
 *
 * The composer's markdown is a lossless projection of the structured Answer, so
 * the card UI renders from `answer` JSON and uses this renderer only for the
 * "copied/pasted prose" path and for narrative text.
 */

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

/* Unicode isolates keep LTR runs (IDs, emails, phone numbers, codes) from
   reordering when they sit inside an RTL sentence. */
const ISOLATE_RE = /([A-Za-z0-9._+\-#@:/]{4,})/g;

function applyIsolates(textNode) {
  const text = textNode.textContent || "";
  if (!ISOLATE_RE.test(text)) return textNode;
  ISOLATE_RE.lastIndex = 0;
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  for (const match of text.matchAll(ISOLATE_RE)) {
    if (match.index > cursor) fragment.append(document.createTextNode(text.slice(cursor, match.index)));
    const isolate = document.createElement("span");
    isolate.className = "ltr-isolate";
    isolate.dir = "ltr";
    isolate.textContent = match[0];
    fragment.append(isolate);
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) fragment.append(document.createTextNode(text.slice(cursor)));
  const holder = make("span", "isolated");
  holder.append(fragment);
  return holder;
}

/** Inline markdown: `code`, **bold**, *em*, [label](href). Returns a fragment. */
export function renderInline(target, input) {
  const text = String(input ?? "");
  let index = 0;
  const push = (slice, kind) => {
    if (!slice) return;
    if (kind === "code") {
      const code = make("code", "inline-code");
      code.textContent = slice;
      target.append(code);
      return;
    }
    if (kind === "bold") {
      const strong = make("strong");
      strong.append(applyIsolates(document.createTextNode(slice)));
      target.append(strong);
      return;
    }
    if (kind === "em") {
      const em = make("em");
      em.append(applyIsolates(document.createTextNode(slice)));
      target.append(em);
      return;
    }
    target.append(applyIsolates(document.createTextNode(slice)));
  };
  while (index < text.length) {
    const rest = text.slice(index);
    const code = rest.match(/^`([^`]+)`/);
    if (code) {
      push(code[1], "code");
      index += code[0].length;
      continue;
    }
    const bold = rest.match(/^\*\*([^*]+)\*\*/);
    if (bold) {
      push(bold[1], "bold");
      index += bold[0].length;
      continue;
    }
    const em = rest.match(/^\*([^*]+)\*/);
    if (em) {
      push(em[1], "em");
      index += em[0].length;
      continue;
    }
    const link = rest.match(/^\[([^\]]+)\]\(([^)\s]+)\)/);
    if (link) {
      const label = link[1];
      const href = link[2];
      if (/^(https?:|mailto:|tel:)/i.test(href)) {
        const anchor = make("a", "md-link");
        anchor.href = href;
        anchor.rel = "noopener noreferrer";
        anchor.target = "_blank";
        anchor.textContent = label;
        target.append(anchor);
      } else {
        /* Same-origin command references stay inert text — never a link to click. */
        const chip = make("span", "md-command");
        chip.dataset.command = href;
        chip.append(applyIsolates(document.createTextNode(label)));
        target.append(chip);
      }
      index += link[0].length;
      continue;
    }
    const next = rest.search(/[`*\[]/);
    if (next <= 0) {
      const chunk = next === 0 ? rest.slice(0, 1) : rest.slice(0, next === -1 ? rest.length : next);
      push(chunk, "text");
      index += chunk.length || 1;
      continue;
    }
    push(rest.slice(0, next), "text");
    index += next;
  }
  return target;
}

function renderList(items, ordered) {
  const list = make(ordered ? "ol" : "ul", "md-list");
  for (const item of items) {
    const li = make("li");
    li.dir = "auto";
    renderInline(li, item);
    list.append(li);
  }
  return list;
}

function splitRow(line) {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderTable(rows) {
  const table = make("table", "md-table");
  const head = splitRow(rows[0]);
  const thead = make("thead");
  const headRow = make("tr");
  for (const cell of head) {
    const th = make("th");
    th.dir = "auto";
    renderInline(th, cell);
    headRow.append(th);
  }
  thead.append(headRow);
  const body = make("tbody");
  for (const line of rows.slice(2)) {
    const cells = splitRow(line);
    const tr = make("tr");
    cells.forEach((cell, position) => {
      const td = make(position === 0 ? "th" : "td");
      td.scope = position === 0 ? "row" : undefined;
      td.dir = "auto";
      renderInline(td, cell);
      tr.append(td);
    });
    body.append(tr);
  }
  table.append(thead, body);
  const wrap = make("div", "md-table-wrap");
  wrap.append(table);
  return wrap;
}

/**
 * Markdown → DOM into `container` (replacing its children).
 * Supports: #/##/### headings, paragraphs, - / * / 1. lists, tables, > quotes,
 * ``` fences, --- rules, and inline bold/em/code/links.
 */
export function renderMarkdown(container, source) {
  const host = typeof container === "string" ? document.querySelector(container) : container;
  if (!host) return null;
  host.textContent = "";
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (/^\s*```/.test(line)) {
      const buffer = [];
      index += 1;
      while (index < lines.length && !/^\s*```/.test(lines[index])) buffer.push(lines[index++]);
      index += 1;
      const pre = make("pre", "md-code");
      pre.textContent = buffer.join("\n");
      host.append(pre);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      host.append(make("hr", "md-rule"));
      index += 1;
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = Math.min(4, heading[1].length + 2); // # → h3 inside a card
      const node = make(`h${level}`, "md-heading");
      node.dir = "auto";
      renderInline(node, heading[2]);
      host.append(node);
      index += 1;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[index + 1] || "")) {
      const rows = [];
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) rows.push(lines[index++]);
      host.append(renderTable(rows));
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) items.push(lines[index++].replace(/^\s*[-*+]\s+/, ""));
      host.append(renderList(items, false));
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) items.push(lines[index++].replace(/^\s*\d+[.)]\s+/, ""));
      host.append(renderList(items, true));
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const buffer = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) buffer.push(lines[index++].replace(/^\s*>\s?/, ""));
      const quote = make("blockquote", "md-quote");
      quote.dir = "auto";
      renderInline(quote, buffer.join(" "));
      host.append(quote);
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !/^\s*(#{1,4}\s|[-*+]\s|\d+[.)]\s|>|\||```|-{3,}$)/.test(lines[index])) {
      paragraph.push(lines[index++].trim());
    }
    const p = make("p", "md-paragraph");
    p.dir = "auto";
    renderInline(p, paragraph.join(" "));
    host.append(p);
  }
  return host;
}

/** Plain-text fallback for model prose: one paragraph per line group. */
export function textToNodes(text) {
  const fragment = document.createDocumentFragment();
  for (const block of String(text ?? "").split(/\n{2,}/)) {
    const p = make("p", "md-paragraph");
    p.dir = "auto";
    p.append(applyIsolates(document.createTextNode(block.trim())));
    fragment.append(p);
  }
  return fragment;
}

export const markdown = { render: renderMarkdown, renderInline, escapeHtml, textToNodes };
export default markdown;
