"""Tests for Phase 9 web security hardening (poc/web_security.py)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.llm_client import FakeLLMClient
from poc.web_server import ERPWebServer


class _FakeOdoo:
    def search_read(self, *a, **kw):
        return []
    def read(self, *a, **kw):
        return []


def _start_server(tmp_path, monkeypatch, env_overrides=None):
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
    db_path = tmp_path / "ws.db"
    initialize(db_path)
    gateway = ToolGateway(db_path=db_path)
    runtime = AgentRuntime(
        llm_client=FakeLLMClient(responses=[]),
        gateway=gateway,
        user_id="sales_user@test",
        tenant_id="poc_tenant_001",
        odoo_client_factory=lambda u, t: _FakeOdoo(),
    )
    server = ERPWebServer(("127.0.0.1", 0), runtime)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


import urllib.request, urllib.error


def _get(url, origin=None):
    req = urllib.request.Request(url)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


def _post(url, payload, origin=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


def test_dev_routes_disabled_in_production_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("MIZAN_DEV", raising=False)
    server, url = _start_server(tmp_path, monkeypatch)
    try:
        status, body, _ = _post(f"{url}/api/test/replay", {"idempotency_key": "c" * 32})
        assert status == 404
        data = json.loads(body)
        assert data["error"]["code"] == "NOT_FOUND"
    finally:
        server.shutdown()
        server.server_close()


def test_security_headers_present(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZAN_DEV", "0")
    server, url = _start_server(tmp_path, monkeypatch)
    try:
        status, _, h = _get(f"{url}/api/health")
        assert status == 200
        assert h.get("X-Content-Type-Options") == "nosniff"
        assert h.get("X-Frame-Options") == "DENY"
        assert "default-src 'self'" in h.get("Content-Security-Policy", "")
        assert "strict-origin-when-cross-origin" in h.get("Referrer-Policy", "")
        # CORS wildcard must NOT be present in production mode.
        assert h.get("Access-Control-Allow-Origin") != "*"
    finally:
        server.shutdown()
        server.server_close()


def test_cors_allows_explicit_origin_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZAN_DEV", "0")
    monkeypatch.setenv("MIZAN_ALLOWED_ORIGINS", "https://mizan.example.com")
    server, url = _start_server(tmp_path, monkeypatch)
    try:
        status, _, h = _get(f"{url}/api/health", origin="https://mizan.example.com")
        assert status == 200
        assert h.get("Access-Control-Allow-Origin") == "https://mizan.example.com"
        # Disallowed origin -> no CORS header
        status2, _, h2 = _get(f"{url}/api/health", origin="https://evil.example")
        assert status2 == 200
        assert h2.get("Access-Control-Allow-Origin") is None
    finally:
        server.shutdown()
        server.server_close()


def test_rate_limit_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZAN_DEV", "1")
    server, url = _start_server(tmp_path, monkeypatch)
    # Tighten limit via direct attribute for test speed. Ensure attribute exists first.
    cfg = server.security_config
    server.security_config = cfg.__class__(
        development_mode=True,
        allowed_origins=(),
        request_size_limit_bytes=512 * 1024,
        rate_limit_per_minute=5,
        rate_limit_window_seconds=60,
        force_hsts=False,
        default_user_id=cfg.default_user_id,
        default_tenant_id=cfg.default_tenant_id,
        require_authentication=False,
    )
    from poc.web_server import _RATE_LIMITERS
    _RATE_LIMITERS.clear()
    try:
        statuses = []
        for _ in range(8):
            s, _, _ = _get(f"{url}/api/health")
            statuses.append(s)
        assert 429 in statuses
    finally:
        server.shutdown()
        server.server_close()
