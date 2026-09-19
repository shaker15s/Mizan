"""Report renderers: console (ANSI), JSON, Markdown (PR body), self-contained HTML."""

from __future__ import annotations

from poc.harness.render import console, html, json_report, markdown

__all__ = ["console", "html", "json_report", "markdown"]
