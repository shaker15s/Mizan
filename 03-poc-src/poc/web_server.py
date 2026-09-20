"""Enterprise Web Cockpit HTTP server for the Agent-Native ERP.

Zero-framework, zero-dependency control surface for the cockpit: it serves the
static UI and a small JSON/SSE API, and delegates everything that matters —
orchestration to :class:`~poc.agent_runtime.AgentRuntime`, governance to
:class:`~poc.gateway.ToolGateway`. It owns no policy of its own.

Endpoints
    GET  /                          cockpit (static, gzipped, ETag-cached)
    GET  /api/health              liveness + engine identity
    GET  /api/telemetry           model/erp/breaker/sessions/settings snapshot
    GET  /api/tools               server-owned tool contracts
    GET  /api/audit               hash-chained audit ledger (+ filters)
    GET  /api/settings            grouped, secret-masked settings for the panel
    POST /api/settings            validated partial update (hot-applied)
    POST /api/settings/reset      restore defaults for given keys
    GET  /api/commands            slash-command catalog (for autocomplete)
    GET  /api/integrations        integration catalog + configuration state
    POST /api/integrations/test   preview/live deliver a synthetic event
    GET  /api/sessions            recent chat sessions
    POST /api/chat                one turn, JSON
    POST /api/chat/stream         one turn, Server-Sent Events (stages + deltas)
    POST /api/confirm             sign a proposal and execute it
    POST /api/amend               re-issue a proposal with edited arguments
    POST /api/decline             deny a proposal
    POST /api/test/replay         idempotency-conflict demonstration

Security notes for the served page: a strict CSP (no CDN, no inline scripts
beyond a tiny bootstrap block), nosniff, frame deny, and a microphone policy
that *allows* self (Web Speech input) while denying camera and geolocation.
"""

from __future__ import annotations

import argparse
import gzip
import http
import http.server
import io
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Callable, Mapping

from poc.agent_runtime import AgentRuntime
from poc.bootstrap import build_llm_client_from_settings, build_runtime
from poc.integrations import EVENT_ORDER_DECLINED, EVENT_ORDER_EXECUTED, catalog as integration_catalog, deliver as deliver_event
from poc.main import _build_payload
from poc.odoo_client import get_circuit_breaker
from poc.prompts import PROMPT_VERSION, greeting
from poc.responder import compose_answer
from poc.sessions import get_session_store
from poc.settings import get_settings
from poc.slash import execute as run_slash
from poc.slash import is_slash
from poc.tool_contracts import get_registry
from poc.web_security import (
    DEV_ROUTE_PREFIXES,
    DEFAULT_SECURITY_HEADERS,
    RateLimiter,
    RequestTooLarge,
    SecurityConfig,
    apply_cors,
    client_ip,
    enforce_content_length,
    is_dev_route,
    resolve_identity,
    wrap_security_headers,
)

LOGGER = logging.getLogger("erp.web_server")
_LEGACY_WEB_DIR = Path(__file__).resolve().parent / "web"
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
WEB_DIR = _FRONTEND_DIST if (_FRONTEND_DIST / "index.html").is_file() else _LEGACY_WEB_DIR
LOGGER.info("web root: %s", WEB_DIR)
API_VERSION = "1.1.0"
MAX_BODY_BYTES = 128 * 1024
COMPRESSIBLE = {".js", ".css", ".html", ".svg", ".json", ".map"}
MIN_COMPRESS_BYTES = 1200
_CSP = DEFAULT_SECURITY_HEADERS["Content-Security-Policy"]
# Voice / microphone is allowed from 'self' per plan §8.H (feature-flag policy
# declared in Permissions-Policy); browsers only prompt after user gesture.

# --- Security config --------------------------------------------------------
def _security_config() -> SecurityConfig:
    """Resolve security config from environment/settings.

    * MIZAN_DEV=1 enables development mode (wildcard CORS local, dev routes).
    * MIZAN_ALLOWED_ORIGINS is a comma-separated list of allowed origins.
    * Production default is same-origin (no CORS headers) with dev routes disabled.
    """
    dev_mode = os.environ.get("MIZAN_DEV", "0") == "1"
    origins_raw = os.environ.get("MIZAN_ALLOWED_ORIGINS", "")
    allowed_origins = tuple(o.strip() for o in origins_raw.split(",") if o.strip())
    if dev_mode and not allowed_origins:
        allowed_origins = ()  # development mode reflects any origin
    return SecurityConfig(
        development_mode=dev_mode,
        allowed_origins=allowed_origins,
        request_size_limit_bytes=512 * 1024,
        rate_limit_per_minute=120,
        rate_limit_window_seconds=60,
        force_hsts=False,
        default_user_id=os.environ.get("POC_USER_ID", "sales_user@test"),
        default_tenant_id=os.environ.get("POC_TENANT_ID", "poc_tenant_001"),
        require_authentication=False,  # real auth Phase 9 continuation
    )


_RATE_LIMITERS: dict[str, RateLimiter] = {}


class ERPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """REST + SSE endpoints over a static, hardened file server."""

    runtime: AgentRuntime

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    # --- plumbing ---------------------------------------------------------
    def send_response(self, code: int, message: str | None = None) -> None:
        self.status_code = code
        super().send_response(code, message)

    def handle_one_request(self) -> None:
        start_time = time.time()
        self.status_code = 500
        try:
            super().handle_one_request()
        finally:
            command = getattr(self, "command", "-")
            path = getattr(self, "path", "-")
            if command and path != "-":
                duration_ms = int((time.time() - start_time) * 1000)
                if not path.startswith(("/styles.css", "/app.js", "/markdown.js", "/ui.js", "/favicon")):
                    LOGGER.info("%s %s - %s - %dms", command, path.split("?")[0], getattr(self, "status_code", "-"), duration_ms)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # replaced by the structured log in handle_one_request

    @property
    def runtime(self) -> AgentRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    @property
    def settings(self):
        return self.server.settings  # type: ignore[attr-defined]

    @property
    def sessions(self):
        return self.server.sessions  # type: ignore[attr-defined]

    @property
    def security_config(self) -> SecurityConfig:
        cfg = getattr(self.server, "security_config", None)
        if cfg is None:
            cfg = _security_config()
            self.server.security_config = cfg  # type: ignore[attr-defined]
        return cfg

    def _rate_limiter(self) -> RateLimiter:
        cfg = self.security_config
        key = f"rl-{cfg.rate_limit_window_seconds}-{cfg.rate_limit_per_minute}"
        rl = _RATE_LIMITERS.get(key)
        if rl is None:
            rl = RateLimiter(cfg.rate_limit_window_seconds, cfg.rate_limit_per_minute)
            _RATE_LIMITERS[key] = rl
        return rl

    def _is_https(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") == "https") or str(self.headers.get(":scheme") or "") == "https"

    def _origin(self) -> str | None:
        return self.headers.get("Origin")

    def _reject(self, status: int, code: str, message_ar: str) -> None:
        self._send_json(status, {
            "success": False,
            "error": {"code": code, "message": message_ar},
            "response_ar": message_ar,
        })

    def _security_headers(self) -> None:
        # Headers are applied via wrap_security_headers in _set_headers.
        pass

    def _set_headers(
        self,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        *,
        cors: bool = True,
        extra: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        base = {"Content-Type": content_type, "Cache-Control": "no-cache, no-store, must-revalidate"}
        headers = wrap_security_headers(
            base,
            config=self.security_config,
            is_https=self._is_https(),
            content_type=content_type,
        )
        if cors:
            headers = apply_cors(
                headers,
                request_origin=self._origin(),
                config=self.security_config,
                allow_methods="GET, POST, OPTIONS",
            )
            headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Mizan-Session, X-Mizan-Request-ID"
        headers["Server"] = "mizan"
        for key, value in {**headers, **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        limit = self.security_config.request_size_limit_bytes
        if length > limit:
            LOGGER.warning("Oversized request body rejected: %d bytes (limit %d)", length, limit)
            self._reject(413, "REQUEST_TOO_LARGE", "حجم الطلب أكبر من الحد المسموح.")
            return {}
        try:
            enforce_content_length({"Content-Length": str(length)}, limit)
        except RequestTooLarge:
            self._reject(413, "REQUEST_TOO_LARGE", "حجم الطلب أكبر من الحد المسموح.")
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            LOGGER.warning("Malformed JSON received: %s", err)
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _enforce_rate_limit(self) -> bool:
        """Check rate limit; stash remaining so _set_headers can attach X-RateLimit-* headers.

        Returns True if allowed; sends 429 and returns False otherwise.
        """
        ip = client_ip(self)
        allowed, remaining = self._rate_limiter().check(ip)
        self._rl_remaining = (self.security_config.rate_limit_per_minute, max(0, remaining))
        if not allowed:
            # We cannot attach headers after send_response; emit a self-contained response.
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", str(self.security_config.rate_limit_window_seconds))
            wrap = wrap_security_headers({}, config=self.security_config, is_https=self._is_https())
            for k, v in wrap.items():
                self.send_header(k, v)
            self.end_headers()
            payload = json.dumps({
                "success": False,
                "error": {"code": "RATE_LIMITED", "message": "عدد الطلبات كتير جداً، استنى ثواني."},
                "response_ar": "عدد الطلبات كتير جداً، استنى ثواني.",
            }, ensure_ascii=False).encode("utf-8")
            self.wfile.write(payload)
            return False
        return True

    def _gate_dev_routes(self, path: str) -> bool:
        """Block dev routes unless development_mode is enabled. Returns True if blocked."""
        if is_dev_route(path) and not self.security_config.development_mode:
            self._reject(404, "NOT_FOUND", "الطلب ده غير متاح في وضع الإنتاج.")
            return True
        return False

    def _send_json(self, status: int, data: Mapping[str, Any]) -> None:
        self._set_headers(status)
        try:
            payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        except (TypeError, ValueError):
            payload = json.dumps({"success": False, "error": {"code": "SERIALIZATION_ERROR"}}).encode("utf-8")
        self.wfile.write(payload)

    def _session_id(self) -> str | None:
        header = self.headers.get("X-Mizan-Session")
        return (header or "").strip() or None

    # --- verbs ------------------------------------------------------------
    def do_OPTIONS(self) -> None:
        # Preflight doesn't consume a rate limit token but gets CORS/security headers.
        self._set_headers(http.HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            if self._gate_dev_routes(path):
                return
            if not self._enforce_rate_limit():
                return
        routes: dict[str, Callable[[str], None]] = {
            "/api/health": lambda _q: self._handle_get_health(),
            "/api/telemetry": lambda _q: self._handle_get_telemetry(),
            "/api/tools": lambda _q: self._handle_get_tools(),
            "/api/audit": self._handle_get_audit,
            "/api/settings": lambda _q: self._send_json(200, {"success": True, "settings": self.settings.snapshot()}),
            "/api/commands": lambda _q: self._handle_get_commands(),
            "/api/integrations": lambda _q: self._handle_get_integrations(),
            "/api/sessions": lambda _q: self._handle_get_sessions(),
            "/api/greeting": lambda _q: self._send_json(200, {"success": True, **greeting(str(self.settings.get("agent.dialect", "ar-EG")))}),
            "/api/me": lambda _q: self._handle_get_me(),
            "/api/history": self._handle_get_history,
        }
        handler = routes.get(path)
        if handler is not None:
            handler(parsed.query)
            return
        if path.startswith("/api/"):
            self._send_json(404, {"success": False, "error": {"code": "NOT_FOUND", "message": f"لا يوجد endpoint باسم {path}."}})
            return
        target = (WEB_DIR / path.lstrip("/")).resolve() if path != "/" else None
        if path == "/" or target is None or not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self.path = "/index.html"
        self._serve_static()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            if self._gate_dev_routes(path):
                return
            if not self._enforce_rate_limit():
                return
        if path == "/api/chat":
            self._handle_post_chat()
            return
        if path == "/api/chat/stream":
            self._handle_post_chat_stream()
            return
        if path == "/api/confirm":
            self._handle_post_decide("confirm")
            return
        if path == "/api/decline":
            self._handle_post_decide("decline")
            return
        if path == "/api/amend":
            self._handle_post_amend()
            return
        if path == "/api/settings":
            self._handle_post_settings()
            return
        if path == "/api/settings/reset":
            self._handle_post_settings_reset()
            return
        if path == "/api/sessions/clear":
            body = self._read_json_body()
            session_id = body.get("session_id") or self._session_id() or ""
            self._send_json(200, {"success": True, "cleared": bool(self.sessions.clear(session_id))})
            return
        if path == "/api/integrations/test":
            self._handle_post_integration_test()
            return
        if path == "/api/test/replay":
            self._handle_post_test_replay()
            return
        self._send_json(404, {"success": False, "error": {"code": "NOT_FOUND", "message": f"لا يوجد endpoint باسم {path}."}})

    # --- static assets ----------------------------------------------------
    def _serve_static(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        file_path = (WEB_DIR / path.lstrip("/")).resolve() if path != "/" else (WEB_DIR / "index.html")
        # Defence in depth: only files inside the web root are ever readable.
        if WEB_DIR.resolve() not in file_path.parents or not file_path.name:
            self._send_json(404, {"success": False, "error": {"code": "NOT_FOUND"}})
            return
        if not file_path.is_file():
            self._send_json(404, {"success": False, "error": {"code": "NOT_FOUND"}})
            return
        stat = file_path.stat()
        etag = f'W/"{int(stat.st_mtime)}-{stat.st_size}-{file_path.name}"'
        if (self.headers.get("If-None-Match") or "").strip() == etag:
            self.send_response(http.HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            return
        data = file_path.read_bytes()
        suffix = file_path.suffix.lower()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".woff2": "font/woff2",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(suffix, "application/octet-stream")
        compressed = None
        if "gzip" in (self.headers.get("Accept-Encoding") or "") and suffix in COMPRESSIBLE and len(data) > MIN_COMPRESS_BYTES:
            buffer = io.BytesIO()
            with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6, mtime=0) as handle:
                handle.write(data)
            compressed = buffer.getvalue()
        body = compressed if compressed is not None else data
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", self.date_time_string(int(stat.st_mtime)))
        self.send_header("Cache-Control", "no-cache" if suffix == ".html" else "public, max-age=300")
        if compressed is not None:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        if suffix == ".html":
            self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # --- read endpoints ---------------------------------------------------
    def _handle_get_health(self) -> None:
        uptime = int(time.time() - self.server.start_time)  # type: ignore[attr-defined]
        self._send_json(
            200,
            {
                "status": "healthy",
                "version": API_VERSION,
                "uptime_seconds": uptime,
                "engine": self.runtime.engine,
                "prompt_version": PROMPT_VERSION,
                "settings_version": self.settings.version,
            },
        )

    def _handle_get_tools(self) -> None:
        registry = get_registry()
        tools = []
        for name in registry.names():
            contract = registry.get(name)
            tools.append(
                {
                    "name": name,
                    "title": contract.get("title", ""),
                    "version": contract.get("tool_version", "1.0.0"),
                    "description": contract.get("description", ""),
                    "readOnly": contract.get("readOnly", True),
                    "destructive": contract.get("destructive", False),
                    "requiresConfirmation": contract.get("requiresConfirmation", False),
                    "risk_level": contract.get("risk_level", "R0"),
                    "inputSchema": contract.get("inputSchema", {}),
                    "outputSchema": contract.get("outputSchema", {}),
                }
            )
        self._send_json(200, {"success": True, "count": len(tools), "tools": tools})

    def _handle_get_audit(self, query_str: str) -> None:
        params = urllib.parse.parse_qs(query_str)
        try:
            limit = max(1, min(500, int(params.get("limit", [str(self.settings.int_of("governance.audit_page_size"))])[0])))
        except ValueError:
            limit = 50
        store = self.runtime.gateway.audit_store
        tool_filter = (params.get("tool") or [""])[0]
        status_filter = (params.get("status") or [""])[0]
        records = store.list(limit=min(500, limit * 4 if (tool_filter or status_filter) else limit))
        if tool_filter:
            records = [row for row in records if row.get("tool_name") == tool_filter]
        if status_filter:
            records = [row for row in records if str(row.get("result_status")) == status_filter]
        records = records[:limit]
        chain = store.verify_chain()
        self._send_json(
            200,
            {
                "success": True,
                "chain_valid": bool(chain.get("valid")),
                "verification": dict(chain),
                "count": len(records),
                "records": records,
            },
        )

    def _handle_get_commands(self) -> None:
        from poc.slash import catalog

        self._send_json(200, {"success": True, "commands": catalog()})

    def _handle_get_integrations(self) -> None:
        configs = dict(self.settings.get("integrations.config", {}) or {})
        self._send_json(
            200,
            {
                "success": True,
                "enabled_globally": self.settings.bool_of("features.outbound_notifications"),
                "integrations": integration_catalog(configs),
            },
        )

    def _handle_get_me(self) -> None:
        self._send_json(
            200,
            {
                "success": True,
                "user_id": self.runtime.user_id,
                "tenant_id": self.runtime.tenant_id or "poc_tenant_001",
                "role": getattr(self.runtime, "role", None) or "sales_user",
            },
        )

    def _handle_get_history(self, _query: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        sid = (params.get("session_id") or [""])[0]
        if not sid:
            self._send_json(400, {"success": False, "reason": "session_id مطلوب", "error_code": "MISSING_SESSION"})
            return
        try:
            turns = list(self.sessions.turns(sid) or [])
        except Exception:
            turns = []
        self._send_json(200, {"success": True, "turns": turns, "active_proposal": None})

    def _handle_get_sessions(self) -> None:
        rows = self.sessions.list_sessions(user_id=self.runtime.user_id, limit=25)
        self._send_json(200, {"success": True, "count": len(rows), "sessions": rows, "stats": self.sessions.stats()})

    def _handle_get_telemetry(self) -> None:
        odoo_url = str(self.settings.get("erp.base_url") or os.environ.get("ODOO_URL", "http://localhost:8069"))
        odoo_online, latency_ms = _probe_tcp(odoo_url, timeout=0.8)
        uptime_secs = int(time.time() - getattr(self.server, "start_time", time.time()))
        breaker = get_circuit_breaker()
        provider = str(self.settings.get("model.provider", "anthropic"))
        model_name = str(self.settings.get("model.name", ""))
        configs = dict(self.settings.get("integrations.config", {}) or {})
        self._send_json(
            200,
            {
                "success": True,
                "status": "online",
                "api_version": API_VERSION,
                "uptime_seconds": uptime_secs,
                "uptime_human": f"{uptime_secs // 3600}h {uptime_secs % 3600 // 60}m {uptime_secs % 60}s",
                "circuit_breaker": breaker.get_status(),
                "odoo": {
                    "online": odoo_online,
                    "url": odoo_url,
                    "database": str(self.settings.get("erp.database") or os.environ.get("ODOO_DATABASE", "poc_test")),
                    "latency_ms": latency_ms,
                    "transport": "JSON-2 (server-owned credentials)",
                },
                "model": {
                    "name": model_name or "غير محدد",
                    "provider": provider,
                    "engine": self.runtime.engine,
                    "simulated": self.runtime.engine == "simulated",
                    "endpoint": str(self.settings.get("model.base_url") or os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"),
                    "temperature": self.settings.float_of("model.temperature"),
                    "max_tokens": self.settings.int_of("model.max_tokens"),
                    "force_tool_choice": str(self.settings.get("model.force_tool_choice", "off")),
                    "prompt_version": PROMPT_VERSION,
                },
                "agent": {
                    "memory_turns": self.settings.int_of("agent.memory_turns"),
                    "narrative_composer": self.settings.bool_of("agent.narrative_composer"),
                    "response_style": str(self.settings.get("agent.response_style", "balanced")),
                    "dialect": str(self.settings.get("agent.dialect", "ar-EG")),
                    "streaming": self.settings.bool_of("ui.stream_responses"),
                },
                "sessions": self.sessions.stats(),
                "settings": {"version": self.settings.version, "confirm_ttl_seconds": self.settings.int_of("governance.confirm_ttl_seconds")},
                "integrations": {
                    "globally_enabled": self.settings.bool_of("features.outbound_notifications"),
                    "configured": sorted(key for key, value in configs.items() if isinstance(value, Mapping) and value.get("enabled")),
                },
                "security": {
                    "user_id": self.runtime.user_id,
                    "tenant_id": self.runtime.tenant_id,
                    "role": "R1_SALES (Full Sales Operator)",
                    "idempotency_enforced": True,
                    "zero_direct_credentials": True,
                    "sqlite_wal_mode": True,
                    "policy_source": "users.yaml (server-owned, not model-editable)",
                },
            },
        )

    # --- turn endpoints ---------------------------------------------------
    def _prepare_turn(self, body: Mapping[str, Any]) -> tuple[str, str | None]:
        message = str(body.get("message") or body.get("input") or "").strip()
        session_id = body.get("session_id") or self._session_id()
        max_chars = max(200, self.settings.int_of("agent.max_input_chars") or 2000)
        if len(message) > max_chars:
            raise ValueError(f"MESSAGE_TOO_LONG|الرسالة طويلة جداً. الحد الأقصى {max_chars} حرف.")
        return message, session_id

    def _handle_post_chat(self) -> None:
        body = self._read_json_body()
        try:
            message, session_id = self._prepare_turn(body)
        except ValueError as error:
            code, _, text = str(error).partition("|")
            self._send_json(400, {"success": False, "error": {"code": code, "message": text}, "response_ar": text})
            return
        if not message:
            self._send_json(
                400,
                {
                    "success": False,
                    "error": {"code": "EMPTY_MESSAGE", "message": "الرسالة فارغة."},
                    "response_ar": "اكتب طلبك الأول — مثلاً: دوّر على عميل محمد أحمد.",
                },
            )
            return
        session = self.sessions.get_or_create(session_id, user_id=self.runtime.user_id or "", tenant_id=self.runtime.tenant_id or "")
        self.sessions.append(session.session_id, "user", message, user_id=session.user_id, tenant_id=session.tenant_id)
        LOGGER.info("[CHAT_IN] session=%s text=%r", session.session_id, message[:120])
        started = time.perf_counter()
        try:
            if is_slash(message) and self.settings.bool_of("agent.slash_commands"):
                payload = self._run_slash(message, session.session_id)
            else:
                result = self.runtime.process(message, history=self._turn_history(session.session_id))
                payload = self._finish_turn(result, session.session_id, "llm")
        except Exception as error:  # never leak a traceback to the browser
            LOGGER.exception("Error processing message: %s", error)
            self._send_json(
                500,
                {
                    "success": False,
                    "status": "server_error",
                    "response_ar": "حدث خطأ غير متوقع في الخادم أثناء معالجة الطلب.",
                    "error": {"code": "SERVER_ERROR", "message": str(error) if self.settings.bool_of("features.verbose_errors") else "Internal server error"},
                    "answer": compose_answer(outcome="erp_error", structured_error=None, timings={"total_ms": round((time.perf_counter() - started) * 1000, 1)}).to_dict(),
                },
            )
            return
        self._send_json(200, payload)

    def _handle_post_chat_stream(self) -> None:
        """One chat turn as Server-Sent Events: stages live, deltas token by token."""
        body = self._read_json_body()
        try:
            message, session_id = self._prepare_turn(body)
        except ValueError as error:
            code, _, text = str(error).partition("|")
            self._send_json(400, {"success": False, "error": {"code": code, "message": text}})
            return
        # This server answers over HTTP/1.1 without chunked framing, so the end of
        # an SSE body has to be signalled by closing the connection. Advertise that
        # up front (and honour it) — otherwise browsers/undici keep waiting for a
        # terminator that never comes and the client's stream never completes.
        self.close_connection = True
        self._set_headers(
            200,
            "text/event-stream; charset=utf-8",
            extra={"X-Accel-Buffering": "no", "Connection": "close", "Cache-Control": "no-cache, no-transform"},
        )
        writer = _SseWriter(self.wfile)
        if not message:
            writer.emit("error", {"code": "EMPTY_MESSAGE", "message": "اكتب طلبك الأول."})
            writer.close()
            return
        session = self.sessions.get_or_create(session_id, user_id=self.runtime.user_id or "", tenant_id=self.runtime.tenant_id or "")
        self.sessions.append(session.session_id, "user", message, user_id=session.user_id, tenant_id=session.tenant_id)
        writer.emit("stage", {"stage": "intake", "chars": len(message)})
        started = time.perf_counter()
        try:
            if is_slash(message) and self.settings.bool_of("agent.slash_commands"):
                writer.emit("stage", {"stage": "slash_command"})
                payload = self._run_slash(message, session.session_id)
            else:
                streaming = self.settings.bool_of("ui.stream_responses") and self.settings.bool_of("agent.narrative_composer")

                def on_stage(name: str, extra: Mapping[str, Any]) -> None:
                    writer.emit("stage", {"stage": name, **dict(extra or {})})

                def on_delta(delta: str) -> None:
                    writer.emit("delta", {"text": delta})

                result = self.runtime.process(
                    message,
                    history=self._turn_history(session.session_id),
                    on_stage=on_stage,
                    on_delta=on_delta if streaming else None,
                )
                payload = self._finish_turn(result, session.session_id, "llm")
            writer.emit("answer", payload)
            writer.emit("done", {"total_ms": round((time.perf_counter() - started) * 1000, 1)})
        except Exception as error:
            LOGGER.exception("Streaming turn failed: %s", error)
            writer.emit(
                "error",
                {
                    "code": "SERVER_ERROR",
                    "message": str(error) if self.settings.bool_of("features.verbose_errors") else "خطأ داخلي في الخادم.",
                },
            )
            writer.emit("done", {"total_ms": round((time.perf_counter() - started) * 1000, 1)})
        finally:
            writer.close()

    def _finish_turn(self, result, session_id: str, via: str) -> dict[str, Any]:
        payload = _build_payload(result)
        payload["via"] = via
        payload["session_id"] = session_id
        proposal_id = ""
        if isinstance(payload.get("proposal"), Mapping):
            proposal_id = str(payload["proposal"].get("proposal_id") or "")
        self.sessions.set_pending_proposal(session_id, proposal_id or None)
        assistant_text = str(payload.get("response_markdown") or payload.get("response_ar") or "")[:4000]
        self.sessions.append(
            session_id,
            "assistant",
            assistant_text,
            tool=payload.get("tool") or (payload.get("meta") or {}).get("tool"),
            status=str(payload.get("status") or ""),
            headline=str(((payload.get("answer") or {}).get("headline")) or payload.get("response_ar") or "")[:160],
        )
        self._maybe_notify(payload, result)
        return payload

    def _turn_history(self, session_id: str) -> list[dict[str, str]]:
        """Bounded conversation window (0 memory => single-turn, never unbounded)."""
        depth = self.settings.int_of("agent.memory_turns")
        if depth <= 0:
            return []
        history = self.sessions.history(session_id, limit=depth * 2)
        return history[:-1]

    def _run_slash(self, message: str, session_id: str) -> dict[str, Any]:
        outcome = run_slash(message, self.runtime)
        if not outcome.handled:
            result = self.runtime.process(message, history=self._turn_history(session_id))
            return self._finish_turn(result, session_id, "llm")
        answer = outcome.answer
        if outcome.gateway_result is not None:
            self.sessions.set_pending_proposal(
                session_id,
                str((outcome.gateway_result.proposal or {}).get("proposal_id")) if outcome.gateway_result.proposal else None,
            )
        payload: dict[str, Any] = {
            "success": True if answer is not None else None,
            "status": (answer.status if answer is not None else "info"),
            "response_ar": (answer.to_text() if answer is not None else outcome.text) or outcome.text,
            "result": dict(outcome.gateway_result.result) if outcome.gateway_result is not None and outcome.gateway_result.result else None,
            "error": None,
            "audit_id": outcome.gateway_result.audit_id if outcome.gateway_result is not None else None,
            "answer": answer.to_dict() if answer is not None else None,
            "answer_markdown": answer.to_markdown() if answer is not None else None,
            "response_markdown": answer.to_markdown() if answer is not None else outcome.text,
            "via": "slash",
            "session_id": session_id,
            "meta": {"engine": "slash", "timings": {}, "stages": [{"stage": "slash", "tool": outcome.tool_name}], "tool": outcome.tool_name},
            "ui_action": outcome.action,
        }
        if outcome.gateway_result is not None and outcome.gateway_result.proposal is not None:
            payload["proposal"] = dict(outcome.gateway_result.proposal)
        if outcome.action in {"settings", "integrations", "replay"}:
            payload["ui_action"] = outcome.action
        self.sessions.append(
            session_id,
            "assistant",
            str(payload.get("response_markdown") or "")[:4000],
            tool=outcome.tool_name,
            status=str(payload.get("status")),
            headline=str(((payload.get("answer") or {}).get("headline"))) or "",
        )
        if outcome.gateway_result is not None:
            self._maybe_notify(payload, None)
        return payload

    def _maybe_notify(self, payload: Mapping[str, Any], result) -> None:
        """Best-effort outbound fan-out; never part of the transaction outcome."""
        if not self.settings.bool_of("features.outbound_notifications"):
            return
        status = str(payload.get("status") or "")
        event = ""
        if status in {"accepted", "confirmed_execution", "success"} and (payload.get("result") or {}).get("verified"):
            event = EVENT_ORDER_EXECUTED
        elif status in {"declined", "confirmation_declined"}:
            event = EVENT_ORDER_DECLINED
        if not event:
            return
        created = dict((payload.get("answer") or {}).get("data") or {}).get("created") or {}
        event_payload = {
            "event": event,
            "tool": (payload.get("meta") or {}).get("tool"),
            "user": self.runtime.user_id,
            "order_id": created.get("order_id"),
            "customer_id": created.get("customer_id"),
            "amount_total": created.get("amount_total"),
            "audit_id": payload.get("audit_id"),
        }
        configs = dict(self.settings.get("integrations.config", {}) or {})
        deliveries = []
        for spec_id in configs:
            deliveries.append(
                deliver_event(spec_id, event_payload, dict(configs.get(spec_id) or {})).to_dict()
            )
        if deliveries:
            payload.setdefault("notifications", deliveries)  # type: ignore[call-overload]

    # --- decision endpoints ----------------------------------------------
    def _handle_post_decide(self, action: str) -> None:
        body = self._read_json_body()
        proposal_id = str(body.get("proposal_id", "")).strip()
        if not proposal_id:
            code = "MISSING_PROPOSAL_ID"
            self._send_json(400, {"success": False, "error": {"code": code, "message": "معرف المقترح مفقود."}})
            return
        session_id = body.get("session_id") or self._session_id()
        started = time.perf_counter()
        try:
            if action == "confirm":
                result = self.runtime.confirm(proposal_id)
            else:
                result = self.runtime.decline(proposal_id)
        except Exception as error:
            LOGGER.exception("Decision (%s) failed: %s", action, error)
            self._send_json(
                500,
                {
                    "success": False,
                    "status": "server_error",
                    "response_ar": "حدث خطأ أثناء تنفيذ القرار على المقترح.",
                    "error": {"code": "CONFIRM_ERROR" if action == "confirm" else "DECLINE_ERROR", "message": str(error) if self.settings.bool_of("features.verbose_errors") else "Internal server error"},
                },
            )
            return
        payload = self._finish_turn(result, session_id or "", f"decision:{action}")
        payload["timings"] = {"total_ms": round((time.perf_counter() - started) * 1000, 1)}
        self._send_json(200, payload)

    def _handle_post_amend(self) -> None:
        body = self._read_json_body()
        proposal_id = str(body.get("proposal_id", "")).strip()
        arguments = body.get("arguments")
        if not proposal_id or not isinstance(arguments, Mapping):
            self._send_json(400, {"success": False, "error": {"code": "INVALID_AMENDMENT", "message": "المقترح والمعاملات الجديدة مطلوبة."}})
            return
        try:
            result = self.runtime.amend_proposal(proposal_id, arguments)
        except Exception as error:
            LOGGER.exception("Amendment failed: %s", error)
            self._send_json(500, {"success": False, "error": {"code": "AMEND_ERROR", "message": str(error) if self.settings.bool_of("features.verbose_errors") else "Internal server error"}})
            return
        payload = self._finish_turn(result, body.get("session_id") or self._session_id() or "", "decision:amend")
        self._send_json(200, payload)

    # --- settings / integrations ------------------------------------------
    def _handle_post_settings(self) -> None:
        body = self._read_json_body()
        changes = body.get("settings") if isinstance(body.get("settings"), Mapping) else body
        changed, errors = self.settings.update(dict(changes or {}))
        if changed:
            self._apply_settings(changed)
        snapshot = self.settings.snapshot()
        status = 200 if not errors else (200 if changed else 422)
        self._send_json(
            status,
            {
                "success": not errors,
                "changed": sorted(changed),
                "errors": errors,
                "settings": snapshot,
                "requires_restart": False,
            },
        )

    def _handle_post_settings_reset(self) -> None:
        body = self._read_json_body()
        keys = body.get("keys")
        restored = self.settings.reset(keys if isinstance(keys, list) else None)
        self._apply_settings(restored)
        self._send_json(200, {"success": True, "restored": sorted(restored), "settings": self.settings.snapshot()})

    def _apply_settings(self, changed: Mapping[str, Any]) -> None:
        """Hot-apply operator changes to the live runtime (identity is untouched)."""
        touch_llm = any(key.startswith("model.") or key == "features.simulated_llm" for key in changed)
        if touch_llm:
            if self.settings.bool_of("features.simulated_llm"):
                from poc.simulated_llm import SimulatedLLMClient

                self.runtime.set_llm_client(SimulatedLLMClient())
            else:
                try:
                    self.runtime.set_llm_client(build_llm_client_from_settings(self.settings))
                except Exception as error:
                    LOGGER.warning("LLM hot-swap failed (keeping previous client): %s", error)
        self.runtime.reconfigure(
            dialect=str(self.settings.get("agent.dialect", "ar-EG")),
            response_style=str(self.settings.get("agent.response_style", "balanced")),
            history_turns=self.settings.int_of("agent.memory_turns"),
            enable_narrative=self.settings.bool_of("agent.narrative_composer"),
            max_repair_turns=1 if self.settings.bool_of("agent.narrative_composer") else 0,
        )
        ttl = self.settings.int_of("governance.confirm_ttl_seconds")
        store = getattr(self.runtime.gateway, "confirmation_store", None)
        if store is not None and ttl:
            store.expiry_seconds = int(ttl)

    def _handle_post_integration_test(self) -> None:
        body = self._read_json_body()
        spec_id = str(body.get("id") or "").strip()
        live = bool(body.get("live")) and self.settings.bool_of("features.outbound_notifications")
        config = dict((self.settings.get("integrations.config", {}) or {}).get(spec_id) or {})
        if isinstance(body.get("config"), Mapping):
            config.update({key: value for key, value in body["config"].items() if str(value) != "••••"})
            if body["config"].get("enabled") is not None:
                config["enabled"] = bool(body["config"]["enabled"])
            self.settings.update({"integrations.config": {**dict(self.settings.get("integrations.config", {}) or {}), spec_id: config}})
        event = {
            "event": str(body.get("event") or EVENT_ORDER_EXECUTED),
            "tool": "sales.order.create",
            "order_name": "S00101 (تجريبي)",
            "customer": "محمد أحمد",
            "amount_total": 180,
            "audit_id": 0,
            "user": self.runtime.user_id,
        }
        result = deliver_event(spec_id, event, config, live=live, secret_store={})
        self._send_json(200, {"success": result.status in {"sent", "preview"}, "delivery": result.to_dict()})

    # --- demo endpoint -----------------------------------------------------
    def _handle_post_test_replay(self) -> None:
        """Demonstrate the idempotency guard without touching the ERP."""
        body = self._read_json_body()
        key = str(body.get("idempotency_key") or "a" * 32)
        record = self.runtime.gateway.idempotency_store.get(key, self.runtime.tenant_id or "", self.runtime.user_id or "")
        self._send_json(
            409,
            {
                "success": False,
                "status": "conflict",
                "error": {
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "تم رفض العملية لمنع تكرار القيد في Odoo 19 (Idempotency Key Conflict).",
                    "details": {
                        "idempotency_key": key,
                        "protection": "content-addressable key + request fingerprint",
                        "existing_state": getattr(record, "state", None) if record is not None else "not_recorded",
                    },
                },
                "response_ar": "تم حجب العملية المكررة بنجاح وحماية قاعدة بيانات Odoo من التكرار.",
                "answer": compose_answer(
                    outcome="conflict",
                    structured_error=None,
                    timings={"engine": "demo"},
                ).to_dict(),
            },
        )


class _SseWriter:
    """Minimal Server-Sent Events writer with heartbeat and dead-client tolerance."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._closed = False

    def emit(self, event: str, data: Mapping[str, Any]) -> None:
        if self._closed:
            return
        try:
            body = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            body = "{}"
        frame = f"event: {event}\ndata: {body}\n\n".encode("utf-8")
        with self._lock:
            if self._closed:
                return
            try:
                self._stream.write(frame)
                self._stream.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._closed = True

    def close(self) -> None:
        self._closed = True


def _probe_tcp(url: str, *, timeout: float = 0.8) -> tuple[bool, float | None]:
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 8069)
        started = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.perf_counter() - started) * 1000, 1)
    except Exception:
        return False, None


class ERPWebServer(http.server.ThreadingHTTPServer):
    """Multi-threaded HTTP server carrying the active AgentRuntime + settings."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], runtime: AgentRuntime, *, settings=None, sessions=None, security_config: SecurityConfig | None = None) -> None:
        self.runtime = runtime
        self.settings = settings or get_settings()
        self.sessions = sessions or get_session_store()
        self.security_config = security_config or _security_config()
        self.start_time = time.time()
        super().__init__(server_address, ERPRequestHandler)


def run_server(port: int = 8080, open_browser: bool = False, runtime: AgentRuntime | None = None) -> None:
    """Start the ERP Web Cockpit server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    settings = get_settings()
    resolved_runtime = runtime or build_runtime(settings=settings)
    server = ERPWebServer(("0.0.0.0", port), resolved_runtime, settings=settings)
    url = f"http://localhost:{port}"
    engine = resolved_runtime.engine
    banner = "\n".join(
        [
            "",
            "  ⚖️  MIZAN — Agent-Native ERP Cockpit",
            f"  → {url}",
            f"  engine: {engine}   model: {settings.get('model.name')}",
            f"  streaming: {'on' if settings.bool_of('ui.stream_responses') else 'off'}   "
            f"narrative: {'on' if settings.bool_of('agent.narrative_composer') else 'off'}   "
            f"memory: {settings.int_of('agent.memory_turns')} turns",
            "",
        ]
    )
    print(banner, flush=True)
    if engine == "simulated":
        print("  ℹ️  وضع المحاكاة شغال: المسار الكامل (بوابة + توقيع + تدقيق) بيتنفذ حرفيًا، الفرق إن النية بتتحدد بقواعد مش بنموذج.", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  إيقاف cockpit…", flush=True)
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent-Native ERP Web Cockpit server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("COCKPIT_PORT", "8080")), help="port to bind (default 8080)")
    parser.add_argument("--open", action="store_true", help="open the default browser on launch")
    parser.add_argument("--simulate", action="store_true", help="run the offline rule engine (no API key needed)")
    parser.add_argument("--no-browser", action="store_true", help="never open a browser")
    args = parser.parse_args()
    if args.simulate:
        get_settings().update({"features.simulated_llm": True})
    if args.no_browser:
        args.open = False
    try:
        run_server(port=args.port, open_browser=args.open)
    except OSError as error:
        print(f"✗ ما قدرتش أفتح المنفذ {args.port}: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
