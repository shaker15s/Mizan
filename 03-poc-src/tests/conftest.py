"""Shared test fixtures and live-Odoo skip mechanism."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest


def _odoo_is_reachable() -> bool:
    url = os.environ.get("ODOO_URL", "")
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def pytest_collection_modifyitems(config, items):
    for item in items:
        has_integration_marker = any(marker.name == "integration" for marker in item.iter_markers())
        if has_integration_marker and not _odoo_is_reachable():
            item.add_marker(pytest.mark.skip(reason="Odoo not reachable"))


# --- TEMPORARY CI DIAGNOSTIC (removed once the 3.12 failure is identified) ---
# GitHub's log blob storage is unreachable from the development sandbox, but
# check-run *annotations* are readable through the API. Emitting each failure as
# a `::error::` workflow command turns the CI result into diagnostics that can
# actually be read.
def _emit_annotation(title: str, report) -> None:
    text = getattr(report, "longreprtext", "") or str(getattr(report, "longrepr", ""))
    flat = " | ".join(line.strip() for line in text.strip().splitlines()[:8])
    import sys as _sys

    _sys.stdout.write(f"::error title={title}::{flat[:900]}\n")
    _sys.stdout.flush()


def pytest_runtest_logreport(report):  # pragma: no cover - diagnostic only
    if report.failed:
        _emit_annotation(f"pytest-{report.when}-{report.nodeid}", report)


def pytest_collectreport(report):  # pragma: no cover - diagnostic only
    if report.failed:
        _emit_annotation(f"collect-{report.nodeid}", report)
