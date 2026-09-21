"""Strict, default-deny redaction for decision state (plan §24, §68).

Two independent mechanisms, because one is never enough:

1. **Construction allowlist.** :class:`DecisionStateBuilder` only ever emits
   fields from an explicit per-purpose allowlist. Anything not named cannot be
   added, even by calling code, because the builder validates the final shape.
2. **Content scan (defence in depth).** :func:`assert_no_secrets` walks the
   serialized state and *rejects* the call if a credential-shaped key or value
   appears. Rejection — not silent stripping — because a leak attempt is a bug
   worth seeing, and a silently-stripped secret would still have left the
   process if the scan had a hole (plan §68: never send secrets to Jev).

What is always denied: API keys, passwords, auth headers, cookies, session
tokens, signing keys, private keys, raw stack traces, the full audit chain,
internal system prompts, and whole ERP records.

The redactor is pure and deterministic so it can be tested directly, and it
never needs network access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from poc.decision.models import DecisionError, DecisionErrorCode

# --- forbidden key names (case-insensitive, matched as whole tokens) ---------
FORBIDDEN_KEY_TOKENS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "session",
    "cookie",
    "authorization",
    "auth_header",
    "bearer",
    "private_key",
    "signing_key",
    "signature",
    "credential",
    "client_secret",
    "webhook_url",
    "dsn",
    "connection_string",
    "system_prompt",
    "hidden_instruction",
    "audit_chain",
    "stack_trace",
    "traceback",
    "odoo_api_key",
    "typesafe_api_key",
)

# --- forbidden value shapes --------------------------------------------------
_SECRET_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_header", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE)),
    ("basic_header", re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{16,}", re.IGNORECASE)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("openai_style_key", re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9]{16,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{16,}\b")),
    ("typesafe_style_key", re.compile(r"\bts_[A-Za-z0-9]{16,}\b")),
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{12,}\b")),
    ("url_with_credentials", re.compile(r"[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s:/@]+@", re.IGNORECASE)),
    ("email_like_pii", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("long_digit_run", re.compile(r"\b\d{12,}\b")),
    ("iban_like", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
)

# Email-shaped text is PII. The *whole* state must be free of it for external
# providers; the harness and offline profiles send synthetic addresses, which
# are explicitly allowed through `allow_synthetic_emails`.
_SYNTHETIC_EMAIL_HOSTS = ("example.com", "example.org", "example.net", "test.invalid", "mizan.local")

MAX_STATE_CHARS = 24_000
MAX_STATE_BYTES = 64_000


@dataclass(frozen=True)
class RedactionPolicy:
    """Explicit, testable description of what a provider is allowed to receive."""

    allowed_keys: frozenset[str]
    allow_synthetic_emails: bool = True
    max_chars: int = MAX_STATE_CHARS
    max_bytes: int = MAX_STATE_BYTES
    notes: str = ""
    #: Extra literal strings that must never appear (e.g. the active system prompt).
    forbidden_literals: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_keys": sorted(self.allowed_keys),
            "allow_synthetic_emails": self.allow_synthetic_emails,
            "max_chars": self.max_chars,
            "max_bytes": self.max_bytes,
            "forbidden_literals": len(self.forbidden_literals),
            "notes": self.notes,
        }


def _key_is_forbidden(key: str) -> bool:
    lowered = str(key).lower()
    tokens = re.split(r"[^a-z0-9]+", lowered)
    joined = " ".join(tokens)
    for forbidden in FORBIDDEN_KEY_TOKENS:
        if "_" in forbidden or "-" in forbidden:
            if forbidden in lowered or forbidden.replace("-", "_") in lowered:
                return True
            continue
        if forbidden in tokens or forbidden in joined:
            return True
    return False


def _iter_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _iter_strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            yield from _iter_strings(item, f"{path}[{index}]")


def _iter_keys(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield child, str(key)
            yield from _iter_keys(item, child)
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            yield from _iter_keys(item, f"{path}[{index}]")


def scan_for_violations(state: Any, policy: RedactionPolicy) -> list[dict[str, str]]:
    """Return every violation found in ``state`` (empty list == clean)."""
    violations: list[dict[str, str]] = []

    for path, key in _iter_keys(state):
        if _key_is_forbidden(key):
            violations.append({"kind": "forbidden_key", "path": path, "detail": f"key {key!r} is not allowed"})
        elif policy.allowed_keys and _root_key(path) not in policy.allowed_keys and not path.startswith(("state.", "questions.")):
            # Only the top-level shape is allowlisted; nested keys inherit their
            # parent's admission. A top-level field outside the allowlist is a
            # construction bug and must fail closed.
            violations.append({"kind": "key_not_allowlisted", "path": path, "detail": f"top-level key {path!r} not allowlisted"})

    for path, text in _iter_strings(state):
        for name, pattern in _SECRET_VALUE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            if name == "email_like_pii" and policy.allow_synthetic_emails and _is_synthetic_email(match.group(0)):
                continue
            violations.append({"kind": "forbidden_value", "path": path, "detail": f"{name} pattern detected"})
        for literal in policy.forbidden_literals:
            if literal and literal in text:
                violations.append({"kind": "forbidden_literal", "path": path, "detail": "reserved literal present in state"})

    return violations


def _root_key(path: str) -> str:
    return str(path).split(".", 1)[0].split("[", 1)[0]


def _is_synthetic_email(value: str) -> bool:
    lowered = value.lower()
    return any(lowered.endswith(host) for host in _SYNTHETIC_EMAIL_HOSTS)


def assert_no_secrets(state: Any, policy: RedactionPolicy) -> None:
    """Raise :class:`DecisionError` when the state would leak something.

    Failing closed is intentional: an over-eager redactor is a support ticket,
    a leaked credential is an incident.
    """
    violations = scan_for_violations(state, policy)
    if violations:
        first = violations[0]
        raise DecisionError(
            DecisionErrorCode.REDACTION_REJECTED,
            f"decision state rejected by redactor: {first['kind']} at {first['path']} ({first['detail']})",
        )


def enforce_size(state: Any, policy: RedactionPolicy, *, serialized: str | None = None) -> str:
    """Return the serialized state, or raise when it exceeds the budget."""
    from poc.decision.models import canonical_json

    payload = serialized if serialized is not None else canonical_json(state)
    if len(payload) > policy.max_chars:
        raise DecisionError(
            DecisionErrorCode.OVERSIZED_STATE,
            f"decision state is {len(payload)} chars, over the {policy.max_chars} budget",
        )
    if len(payload.encode("utf-8")) > policy.max_bytes:
        raise DecisionError(
            DecisionErrorCode.OVERSIZED_STATE,
            f"decision state is over the {policy.max_bytes} byte budget",
        )
    return payload


class DecisionStateBuilder:
    """Allowlist-first state construction.

    Usage::

        builder = DecisionStateBuilder(routing_policy())
        builder.set("user_text", utterance)
        state = builder.build()          # raises on anything not allowlisted

    Unknown keys raise immediately, so a future refactor cannot quietly start
    shipping a customer record to an external provider.
    """

    def __init__(self, policy: RedactionPolicy) -> None:
        self.policy = policy
        self._data: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> "DecisionStateBuilder":
        if key not in self.policy.allowed_keys:
            raise DecisionError(
                DecisionErrorCode.REDACTION_REJECTED,
                f"{key!r} is not in the allowlist for this decision purpose",
            )
        if value is None or value == "" or value == [] or value == {}:
            return self  # never emit empty fields: less state, less leakage
        self._data[key] = value
        return self

    def update(self, values: Mapping[str, Any]) -> "DecisionStateBuilder":
        for key, value in values.items():
            self.set(key, value)
        return self

    def build(self, *, enforce_size: bool = True) -> dict[str, Any]:
        state = dict(self._data)
        assert_no_secrets(state, self.policy)
        if enforce_size:
            _enforce_size(state, self.policy)
        return state


def _enforce_size(state: Any, policy: RedactionPolicy) -> None:
    from poc.decision.models import canonical_json

    payload = canonical_json(state)
    if len(payload) > policy.max_chars:
        raise DecisionError(
            DecisionErrorCode.OVERSIZED_STATE,
            f"decision state is {len(payload)} chars, over the {policy.max_chars} budget",
        )


_MASK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("<email>", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("<account>", re.compile(r"\b\d{12,}\b")),
    ("<account>", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
)


def mask_pii(text: str) -> str:
    """Replace PII-shaped tokens with stable placeholders.

    Masking (rather than rejecting) keeps the decision layer useful on real
    utterances while still preventing an identifier from leaving the process.
    Small integers are left intact on purpose: ``customer 42`` is the *content*
    the classifier needs, and it is not PII on its own.
    """
    if not text:
        return text
    masked = text
    for replacement, pattern in _MASK_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def sanitize_tool_metadata(contracts: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Reduce server-owned tool contracts to the minimum a classifier needs.

    Tool names, human descriptions and read-only flags only. Schemas, Odoo
    mappings, risk internals and policy details stay server-side: a classifier
    does not need them, and shipping them would widen the leak surface for no
    measurable accuracy gain (plan §11).
    """
    out: list[dict[str, str]] = []
    for contract in contracts:
        name = str(contract.get("name") or "")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": str(contract.get("description") or "")[:400],
                "read_only": "true" if contract.get("readOnly") else "false",
            }
        )
    return out


__all__ = [
    "DecisionStateBuilder",
    "FORBIDDEN_KEY_TOKENS",
    "MAX_STATE_BYTES",
    "MAX_STATE_CHARS",
    "RedactionPolicy",
    "assert_no_secrets",
    "enforce_size",
    "mask_pii",
    "sanitize_tool_metadata",
    "scan_for_violations",
]
