"""Structured answer contract — the shape the cockpit renders.

The agent runtime never hands the UI a wall of prose. Every outcome is
compiled into an :class:`Answer`: a headline, KPI strip, typed sections
(tables / fields / bullet lists / chart), notices, next steps, and a
governance block. Three renderers read the same object:

- ``to_dict()``   → the web cockpit renders native components (cards, tables)
- ``to_markdown()``→ a portable transcript (chat export, PR comments, reports)
- ``to_text()``   → the CLI / eval-harness log line (no markup at all)

Pure stdlib, deterministic, no I/O: the same gateway result always compiles to
the same answer, which is what makes the answer auditable and diffable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

TONE_SUCCESS = "success"
TONE_PENDING = "pending"
TONE_DENIED = "denied"
TONE_ERROR = "error"
TONE_INFO = "info"
TONE_EMPTY = "empty"

SECTION_TABLE = "table"
SECTION_FIELDS = "fields"
SECTION_LIST = "list"
SECTION_BARS = "bars"
SECTION_STEPS = "steps"



def format_number(value: Any, decimals: int = 0) -> str:
    """Western-digit, comma-grouped number (Arabic UI convention)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value if value is not None else "—")
    if decimals <= 0 and number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.{decimals}f}"


def format_egp(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{format_number(number, 2 if abs(number % 1) > 0.004 else 0)} ج.م"


def format_date_ar(value: Any) -> str:
    """Best-effort Gregorian date render, e.g. ``19 سبتمبر 2026``."""
    if not value:
        return "—"
    text = str(value)
    months = {
        1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
        7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
    }
    try:
        year, month, day = int(text[0:4]), int(text[5:7]), int(text[8:10])
        return f"{day} {months.get(month, month)} {year}"
    except (ValueError, IndexError):
        return text


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str
    hint: str = ""
    tone: str = TONE_INFO
    icon: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"label": self.label, "value": self.value, "tone": self.tone}
        if self.hint:
            payload["hint"] = self.hint
        if self.icon:
            payload["icon"] = self.icon
        return payload


@dataclass(frozen=True)
class Action:
    """A one-click follow-up the cockpit can offer as a chip."""

    label: str
    prompt: str = ""
    kind: str = "prompt"  # prompt | copy | link
    icon: str = "→"

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "prompt": self.prompt, "kind": self.kind, "icon": self.icon}


@dataclass(frozen=True)
class Section:
    """A typed content block inside an answer card."""

    kind: str
    title: str = ""
    icon: str = ""
    columns: tuple[dict[str, str], ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    fields: tuple[Mapping[str, Any], ...] = ()
    items: tuple[str, ...] = ()
    caption: str = ""
    foot: Mapping[str, Any] = ()  # totals / summary row for tables
    searchable: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.title:
            payload["title"] = self.title
        if self.icon:
            payload["icon"] = self.icon
        if self.kind == SECTION_TABLE:
            payload["columns"] = [dict(col) for col in self.columns]
            payload["rows"] = [dict(row) for row in self.rows]
            if self.foot:
                payload["foot"] = dict(self.foot)
            payload["searchable"] = self.searchable
        elif self.kind == SECTION_FIELDS:
            payload["fields"] = [dict(item) for item in self.fields]
        elif self.kind in {SECTION_LIST, SECTION_STEPS}:
            payload["items"] = list(self.items)
        if self.caption:
            payload["caption"] = self.caption
        return payload

    def to_markdown(self) -> list[str]:
        lines: list[str] = []
        if self.title:
            lines.append(f"### {f'{self.icon} ' if self.icon else ''}{self.title}".strip())
            lines.append("")
        if self.kind == SECTION_TABLE and self.columns:
            headers = [col.get("label", col.get("key", "")) for col in self.columns]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "|".join([" --- "] * len(headers)) + "|")
            for row in self.rows:
                cells = []
                for col in self.columns:
                    value = row.get(col.get("key", ""), "—")
                    cells.append("—" if value in (None, "", "—") else str(value))
                lines.append("| " + " | ".join(cells) + " |")
            if self.foot:
                cells = [str(self.foot.get(col.get("key", ""), "")) if col is not self.columns[0] else str(self.foot.get("label", "الإجمالي")) for col in self.columns]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
        elif self.kind == SECTION_FIELDS:
            for item in self.fields:
                lines.append(f"- **{item.get('label', '')}:** {item.get('value', '—')}")
            lines.append("")
        elif self.kind in {SECTION_LIST, SECTION_STEPS}:
            for index, item in enumerate(self.items, start=1):
                prefix = f"{index}." if self.kind == SECTION_STEPS else "-"
                lines.append(f"{prefix} {item}")
            lines.append("")
        elif self.kind == SECTION_BARS:
            for item in self.fields:
                caption = f" — {item.get('caption')}" if item.get("caption") else ""
                lines.append(f"- **{item.get('label', '')}:** {item.get('value', '—')}{caption}")
            lines.append("")
        if self.caption:
            lines.append(f"_{self.caption}_")
            lines.append("")
        return lines

    def to_text(self) -> list[str]:
        lines: list[str] = []
        if self.title:
            lines.append(self.title)
        if self.kind == SECTION_TABLE:
            for row in self.rows:
                cells = [str(row.get(col.get("key", ""), "")).strip() for col in self.columns if row.get(col.get("key", "")) not in (None, "")]
                lines.append(" • ".join(cell for cell in cells if cell))
        elif self.kind == SECTION_FIELDS:
            for item in self.fields:
                lines.append(f"{item.get('label', '')}: {item.get('value', '—')}")
        elif self.kind in {SECTION_LIST, SECTION_STEPS}:
            lines.extend(self.items)
        elif self.kind == SECTION_BARS:
            for item in self.fields:
                lines.append(f"{item.get('label', '')}: {item.get('value', '—')}")
        if self.caption:
            lines.append(self.caption)
        return lines


@dataclass
class Answer:
    """The complete, renderable answer to one turn."""

    status: str = "info"           # gateway/agent status token (accepted, denied, ...)
    tone: str = TONE_INFO          # visual tone
    title: str = ""                # card title, e.g. "استعلام عملاء"
    headline: str = ""             # one-line human answer
    tool: str = ""
    tool_version: str = ""
    kpis: list[Kpi] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    analysis: str = ""             # model prose (markdown), when available
    notices: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_steps: list[Action] = field(default_factory=list)
    governance: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)  # machine payload for cards (customers/products/order)

    def with_analysis(self, analysis: str) -> "Answer":
        self.analysis = (analysis or "").strip()
        return self

    # --- renderers ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "tone": self.tone,
            "title": self.title,
            "headline": self.headline,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "kpis": [kpi.to_dict() for kpi in self.kpis],
            "sections": [section.to_dict() for section in self.sections],
            "analysis": self.analysis or None,
            "notices": list(self.notices),
            "warnings": list(self.warnings),
            "next_steps": [action.to_dict() for action in self.next_steps],
            "governance": dict(self.governance),
            "data": dict(self.data),
        }

    def to_markdown(self) -> str:
        lines: list[str] = [f"**{self.headline}**" if self.headline else f"**{self.title}**", ""]
        if self.kpis:
            for kpi in self.kpis:
                hint = f" — {kpi.hint}" if kpi.hint else ""
                lines.append(f"- **{kpi.label}:** {kpi.value}{hint}")
            lines.append("")
        for section in self.sections:
            lines.extend(section.to_markdown())
        if self.analysis:
            lines.extend(["### 💡 قراءة تحليلية", "", self.analysis, ""])
        if self.warnings:
            lines.extend(["### ⚠️ تنبيهات", ""] + [f"- {row}" for row in self.warnings] + [""])
        if self.notices:
            lines.extend([f"> {row}" for row in self.notices] + [""])
        if self.next_steps:
            lines.extend(["### 🔜 خطوات تالية", ""] + [f"- {action.label}" for action in self.next_steps] + [""])
        if self.governance:
            bits = ", ".join(f"{key}: {value}" for key, value in self.governance.items() if value not in (None, "", False))
            if bits:
                lines.append(f"<sub>🛡️ {bits}</sub>")
        return "\n".join(lines).strip()

    def to_text(self) -> str:
        lines: list[str] = [self.headline or self.title]
        for section in self.sections:
            for row in section.to_text():
                if row:
                    lines.append(row)
        for warning in self.warnings:
            lines.append(f"تنبيه: {warning}")
        if self.analysis:
            lines.append(self.analysis)
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            text = str(line).strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        return "\n".join(out)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


def table_section(
    title: str,
    columns: Sequence[Mapping[str, str]],
    rows: Iterable[Mapping[str, Any]],
    *,
    icon: str = "",
    caption: str = "",
    foot: Mapping[str, Any] | None = None,
    searchable: bool = False,
) -> Section:
    return Section(
        kind=SECTION_TABLE,
        title=title,
        icon=icon,
        columns=tuple(dict(col) for col in columns),
        rows=tuple(dict(row) for row in rows),
        caption=caption,
        foot=dict(foot) if foot else {},
        searchable=searchable,
    )


def fields_section(title: str, fields: Sequence[Mapping[str, Any]], *, icon: str = "", caption: str = "") -> Section:
    return Section(kind=SECTION_FIELDS, title=title, icon=icon, fields=tuple(dict(item) for item in fields), caption=caption)


def list_section(title: str, items: Sequence[str], *, icon: str = "", caption: str = "") -> Section:
    return Section(kind=SECTION_LIST, title=title, icon=icon, items=tuple(str(item) for item in items), caption=caption)


def steps_section(title: str, items: Sequence[str], *, icon: str = "🧭", caption: str = "") -> Section:
    return Section(kind=SECTION_STEPS, title=title, icon=icon, items=tuple(str(item) for item in items), caption=caption)


def bars_section(title: str, items: Sequence[Mapping[str, Any]], *, icon: str = "", caption: str = "") -> Section:
    """A tiny inline bar chart (stock depth, spend by product) rendered by CSS."""
    return Section(kind=SECTION_BARS, title=title, icon=icon, fields=tuple(dict(item) for item in items), caption=caption)


__all__ = [
    "Action",
    "Answer",
    "Kpi",
    "Section",
    "TONE_DENIED",
    "TONE_EMPTY",
    "TONE_ERROR",
    "TONE_INFO",
    "TONE_PENDING",
    "TONE_SUCCESS",
    "bars_section",
    "fields_section",
    "format_date_ar",
    "format_egp",
    "format_number",
    "list_section",
    "steps_section",
    "table_section",
    "SECTION_BARS",
    "SECTION_FIELDS",
    "SECTION_LIST",
    "SECTION_STEPS",
    "SECTION_TABLE",
]
