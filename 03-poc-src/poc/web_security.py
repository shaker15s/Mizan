"""Web security middleware for production hardening (Phase 9).

This module provides:
  - Security headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy)
  - Configurable CORS (default: same-origin; wildcard only in dev mode)
  - Request body size cap (configurable)
  - In-memory rate limiter (per IP, sliding window)
  - Dev-route isolation: /api/replay, /api/test/*, /api/eval/* are disabled
    unless ``development_mode=True`` is explicitly set.
  - A ``get_session_identity`` helper that resolves server-owned identity from
    a session token (in POC mode, falls back to the configured default user
    when no auth is present — Phase 9 rolls out real tokens).

Design:
  The module is stdlib-only and wraps an existing WSGI/HTTP handler rather
  than forcing a framework rewrite. It is imported by ``web_server.py``.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable


# --- Security header presets ------------------------------------------------

DEFAULT_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "  # needed for RTL Arabic UI styles; no external sources
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    # HSTS is only applied over HTTPS; we set it in wrap_headers when the
    # request is HTTPS or when force_hsts=True.
}

DEV_ROUTE_PREFIXES: tuple[str, ...] = (
    "/api/replay",
    "/api/test",
    "/api/dev",
    "/api/eval",
    "/api/debug",
)


@dataclass
class SecurityConfig:
    """Runtime security configuration."""

    development_mode: bool = False
    allowed_origins: tuple[str, ...] = ()            # e.g. ("https://mizan.example.com",)
    request_size_limit_bytes: int = 512 * 1024      # 512 KB default
    rate_limit_per_minute: int = 120
    rate_limit_window_seconds: int = 60
    force_hsts: bool = False
    default_user_id: str | None = "sales_user@test"  # POC fallback (only in dev mode)
    default_tenant_id: str | None = "poc_tenant_001"
    require_authentication: bool = False            # when True, 401 on no session


def is_dev_route(path: str) -> bool:
    return any(path.startswith(p) for p in DEV_ROUTE_PREFIXES)


def wrap_security_headers(
    headers: dict[str, str],
    *,
    config: SecurityConfig,
    is_https: bool = False,
    content_type: str | None = None,
) -> dict[str, str]:
    """Return a new headers dict with security headers applied."""
    merged = dict(headers)
    merged.update(DEFAULT_SECURITY_HEADERS)
    if config.force_hsts or is_https:
        merged["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    # Remove server fingerprint
    merged["Server"] = "mizan"
    if content_type:
        merged["Content-Type"] = content_type
    return merged


def apply_cors(
    headers: dict[str, str],
    *,
    request_origin: str | None,
    config: SecurityConfig,
    allow_methods: str = "GET, POST, OPTIONS",
) -> dict[str, str]:
    """Apply CORS based on config.

    * In development_mode: reflects the origin (permissive for local dev).
    * Otherwise: only allows configured origins.
    """
    merged = dict(headers)
    if config.development_mode:
        if request_origin:
            merged["Access-Control-Allow-Origin"] = request_origin
        else:
            merged["Access-Control-Allow-Origin"] = "*"
        merged["Access-Control-Allow-Credentials"] = "true"
        merged["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Mizan-Request-ID"
        merged["Access-Control-Allow-Methods"] = allow_methods
    else:
        if request_origin and request_origin in config.allowed_origins:
            merged["Access-Control-Allow-Origin"] = request_origin
            merged["Access-Control-Allow-Credentials"] = "true"
            merged["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Mizan-Request-ID"
            merged["Access-Control-Allow-Methods"] = allow_methods
        elif not config.allowed_origins:
            # Same-origin only — omit CORS header entirely.
            pass
        # Disallowed origin → no CORS headers -> browser enforces same-origin.
    merged["Vary"] = "Origin"
    return merged


# --- Simple in-memory rate limiter (sufficient for POC / single-process) ----

class RateLimiter:
    """Sliding-window rate limiter per key (IP or user). Thread-safe enough for
    single-process POC; production would use Redis."""

    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self.window = window_seconds
        self.max = max_requests
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Returns (allowed, remaining). If allowed, records the hit."""
        now = now if now is not None else time.time()
        bucket = self._hits.setdefault(key, deque())
        # Evict outside window
        while bucket and bucket[0] <= now - self.window:
            bucket.popleft()
        if len(bucket) >= self.max:
            return False, 0
        bucket.append(now)
        return True, max(0, self.max - len(bucket))

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


# --- Request-size guard -----------------------------------------------------

class RequestTooLarge(Exception):
    pass


def enforce_content_length(headers: dict[str, str], limit: int) -> None:
    """Raise RequestTooLarge if Content-Length exceeds limit."""
    cl = headers.get("Content-Length")
    if cl is None:
        return
    try:
        n = int(cl)
    except ValueError:
        return
    if n > limit:
        raise RequestTooLarge(f"request body of {n} bytes exceeds limit {limit}")


# --- Client IP / identity helpers -------------------------------------------

def client_ip(handler: Any) -> str:
    """Extract the client IP from a BaseHTTPRequestHandler.

    Only trust X-Forwarded-For in production when deployed behind a known
    reverse proxy; for the POC/sandbox, fall back to `client_address[0]`.
    """
    forwarded = handler.headers.get("X-Forwarded-For") if hasattr(handler, "headers") else None
    if forwarded:
        return str(forwarded).split(",")[0].strip()
    addr = getattr(handler, "client_address", None)
    if isinstance(addr, tuple) and addr:
        return str(addr[0])
    return "unknown"


def resolve_identity(
    environ: dict[str, Any],
    config: SecurityConfig,
) -> tuple[str | None, str | None, str | None]:
    """Return (user_id, tenant_id, session_id) from the request.

    In POC/dev mode without authentication, falls back to default_user_id
    / default_tenant_id when require_authentication is False. When auth is
    required and no session is present, returns (None, None, None).
    """
    # TODO Phase 9 real auth: parse signed cookie, verify, look up session.
    auth = environ.get("HTTP_AUTHORIZATION")
    cookie = environ.get("HTTP_COOKIE", "")
    has_session_token = "mizan_session=" in cookie or auth is not None
    if has_session_token:
        # POC: not cryptographically verified — placeholder.
        # In real auth we would verify signature, look up session store, then
        # populate user_id/tenant_id from the server-owned session record.
        return None, None, None  # placeholder: no real tokens yet
    if config.require_authentication:
        return None, None, None
    # Dev fallback
    if config.development_mode:
        return config.default_user_id, config.default_tenant_id, None
    return None, None, None
