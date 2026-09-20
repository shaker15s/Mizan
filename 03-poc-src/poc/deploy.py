"""Phase 12 — Production deployment hardening.

Secrets handling, structured logging, readiness/liveness, and incident
primitives. These are safe defaults used by bootstrap / web_server but do
not introduce external infrastructure dependencies — the plan forbids
premature Kafka/K8s/complex mesh.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_DEPLOY_ENV = "pilot"
_DEPLOY_ENVS = {"dev", "pilot", "production"}

# Structured logger: single-line JSON-like records for easy scraping without
# requiring an external logging framework.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str = "INFO", *, json_lines: bool = False, log_file: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        ))
    fmt = logging.Formatter(LOG_FORMAT if not json_lines else "%(message)s")
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=handlers, force=True)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
# Required runtime secrets. The server refuses to start in `production` mode
# if any of these are set to the literal placeholder "change-me" or empty.
REQUIRED_SECRET_KEYS = (
    "MIZAN_SESSION_SECRET",
    "MIZAN_SIGNING_KEY",
)
PLACEHOLDER_VALUES = {"", "change-me", "changeme", "replace-me", "example", "test"}


@dataclass(frozen=True)
class SecretsReport:
    ok: bool
    missing: tuple[str, ...]
    placeholder: tuple[str, ...]
    env: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "env": self.env,
            "missing": list(self.missing),
            "placeholder": list(self.placeholder),
        }


def verify_secrets(env: str | None = None, environ: Mapping[str, str] | None = None) -> SecretsReport:
    env = (env or os.environ.get("MIZAN_ENV", DEFAULT_DEPLOY_ENV)).lower()
    if env not in _DEPLOY_ENVS:
        env = DEFAULT_DEPLOY_ENV
    src = environ if environ is not None else os.environ
    missing: list[str] = []
    placeholder: list[str] = []
    if env == "production":
        for key in REQUIRED_SECRET_KEYS:
            val = src.get(key, "")
            if not val:
                missing.append(key)
            elif val.lower() in PLACEHOLDER_VALUES:
                placeholder.append(key)
    ok = not missing and not placeholder
    return SecretsReport(ok=ok, missing=tuple(missing), placeholder=tuple(placeholder), env=env)


def generate_session_secret() -> str:
    """Cryptographically secure secret for signing session cookies.

    The plan insists the operator supplies this in production; this helper is
    for local dev and automated tests only.
    """
    return secrets.token_urlsafe(48)


# ---------------------------------------------------------------------------
# Readiness / liveness
# ---------------------------------------------------------------------------


@dataclass
class HealthStatus:
    alive: bool
    ready: bool
    checks: dict[str, bool]
    notes: dict[str, str]


def readiness_check(*, db_reachable: bool, odoo_reachable: bool, llm_reachable: bool,
                   policy_loaded: bool = True) -> HealthStatus:
    checks = {
        "db": db_reachable,
        "odoo": odoo_reachable,
        "llm": llm_reachable,
        "policy": policy_loaded,
    }
    alive = True
    ready = all(checks.values())
    return HealthStatus(alive=alive, ready=ready, checks=checks, notes={})


# ---------------------------------------------------------------------------
# Incident identifiers (for error reports / audit correlation)
# ---------------------------------------------------------------------------


def new_incident_id(prefix: str = "INC") -> str:
    return f"{prefix}-{secrets.token_hex(6).upper()}"


__all__ = [
    "configure_logging",
    "verify_secrets",
    "SecretsReport",
    "generate_session_secret",
    "readiness_check",
    "HealthStatus",
    "new_incident_id",
    "REQUIRED_SECRET_KEYS",
    "DEFAULT_DEPLOY_ENV",
]
