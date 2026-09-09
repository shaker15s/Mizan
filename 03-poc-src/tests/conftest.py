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
