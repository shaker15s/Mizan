"""Canonical error taxonomy and structured-error translation for the POC.

STEP 11 (`TECHNICAL_DESIGN.md` SS8). This module is the single source of
truth for the canonical POC error taxonomy and for translating existing
internal error codes from the Gateway, Idempotency, Confirmation, Agent
Runtime, and Odoo adapter boundaries into that taxonomy.

Normative boundaries:
- Translation layer only: nothing here raises, logs, audits, retries, or
  executes.
- Stdlib-only imports. This module must never import ``poc.gateway``,
  ``poc.authz``, ``poc.confirmation``, ``poc.idempotency``, or
  ``poc.odoo_client`` (the design makes the Gateway the only importer of
  the Odoo adapter).
- Odoo adapter exceptions are classified by exception class name via an MRO
  walk, so the adapter module is not imported here.
- ``retryable`` and ``requires_user_action`` are decided per underlying
  exception semantics, never from the canonical code alone (an Odoo timeout
  and an Odoo authentication failure share ``ERP_CONNECTION_ERROR`` but have
  different retry semantics).
- ``CONFIRMATION_REQUIRED`` is a state, not an error, and is deliberately
  absent from this taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# --- Canonical SS8 codes ----------------------------------------------------

SCHEMA_INVALID = "SCHEMA_INVALID"
TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
TOOL_VERSION_MISMATCH = "TOOL_VERSION_MISMATCH"
PERMISSION_DENIED = "PERMISSION_DENIED"
ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
ERP_CONNECTION_ERROR = "ERP_CONNECTION_ERROR"
ERP_VALIDATION_ERROR = "ERP_VALIDATION_ERROR"
VERIFICATION_FAILED = "VERIFICATION_FAILED"
CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
CONFIRMATION_HASH_MISMATCH = "CONFIRMATION_HASH_MISMATCH"
CONFIRMATION_REPLAY = "CONFIRMATION_REPLAY"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
AMBIGUOUS_OUTCOME = "AMBIGUOUS_OUTCOME"
BUSINESS_RULE_VIOLATED = "BUSINESS_RULE_VIOLATED"
MAX_TOOL_CALLS_EXCEEDED = "MAX_TOOL_CALLS_EXCEEDED"
CONFIRMATION_DECLINED = "CONFIRMATION_DECLINED"
LLM_ERROR = "LLM_ERROR"

# Internal control-plane failure (deliberately outside SS8). It must never
# be presented to the agent/user as an Odoo or business-operation failure.
AUDIT_WRITE_FAILED = "AUDIT_WRITE_FAILED"

CATEGORY_ERP = "erp"
CATEGORY_GATEWAY = "gateway"
CATEGORY_LLM = "llm"
CATEGORY_CONTROL_PLANE = "control_plane"

# Gateway/idempotency states that are not errors.
NON_ERROR_CODES = frozenset({"CONFIRMATION_REQUIRED", "ACCEPTED", "REPLAY", "IN_PROGRESS"})


@dataclass(frozen=True)
class ErrorMetadata:
    """Taxonomy metadata for one canonical error code."""

    code: str
    retryable: bool
    requires_user_action: bool
    model_visible: bool
    category: str
    default_message: str


@dataclass(frozen=True)
class StructuredError:
    """Structured error in the SS8 response format.

    ``model_visible`` and ``category`` are taxonomy metadata for consumers;
    the wire format emitted by ``to_dict`` matches the design exactly.
    """

    code: str
    message: str
    retryable: bool
    requires_user_action: bool
    model_visible: bool
    category: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "requires_user_action": self.requires_user_action,
            },
        }


CANONICAL_TAXONOMY: Mapping[str, ErrorMetadata] = {
    SCHEMA_INVALID: ErrorMetadata(
        code=SCHEMA_INVALID,
        retryable=False,
        requires_user_action=False,
        model_visible=True,
        category=CATEGORY_GATEWAY,
        default_message="Arguments failed schema validation.",
    ),
    TOOL_NOT_FOUND: ErrorMetadata(
        code=TOOL_NOT_FOUND,
        retryable=False,
        requires_user_action=False,
        model_visible=True,
        category=CATEGORY_GATEWAY,
        default_message="The requested tool is not available.",
    ),
    TOOL_VERSION_MISMATCH: ErrorMetadata(
        code=TOOL_VERSION_MISMATCH,
        retryable=False,
        requires_user_action=True,
        model_visible=False,
        category=CATEGORY_GATEWAY,
        default_message="The bound tool version no longer matches the registry; re-request required.",
    ),
    PERMISSION_DENIED: ErrorMetadata(
        code=PERMISSION_DENIED,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_GATEWAY,
        default_message="The user does not have permission to perform this operation.",
    ),
    ENTITY_NOT_FOUND: ErrorMetadata(
        code=ENTITY_NOT_FOUND,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The referenced record does not exist.",
    ),
    ERP_CONNECTION_ERROR: ErrorMetadata(
        code=ERP_CONNECTION_ERROR,
        retryable=True,
        requires_user_action=False,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The ERP is unreachable or timed out.",
    ),
    ERP_VALIDATION_ERROR: ErrorMetadata(
        code=ERP_VALIDATION_ERROR,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The ERP rejected the operation.",
    ),
    VERIFICATION_FAILED: ErrorMetadata(
        code=VERIFICATION_FAILED,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The write result could not be verified.",
    ),
    CONFIRMATION_EXPIRED: ErrorMetadata(
        code=CONFIRMATION_EXPIRED,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_GATEWAY,
        default_message="The confirmation proposal expired; re-request required.",
    ),
    CONFIRMATION_HASH_MISMATCH: ErrorMetadata(
        code=CONFIRMATION_HASH_MISMATCH,
        retryable=False,
        requires_user_action=False,
        model_visible=False,
        category=CATEGORY_CONTROL_PLANE,
        default_message="Operation integrity check failed; the operation cannot proceed.",
    ),
    CONFIRMATION_REPLAY: ErrorMetadata(
        code=CONFIRMATION_REPLAY,
        retryable=False,
        requires_user_action=False,
        model_visible=False,
        category=CATEGORY_CONTROL_PLANE,
        default_message="The confirmation proposal was already consumed.",
    ),
    IDEMPOTENCY_CONFLICT: ErrorMetadata(
        code=IDEMPOTENCY_CONFLICT,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_GATEWAY,
        default_message="A matching request is already being processed.",
    ),
    AMBIGUOUS_OUTCOME: ErrorMetadata(
        code=AMBIGUOUS_OUTCOME,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The write outcome is unconfirmed; check with an administrator.",
    ),
    BUSINESS_RULE_VIOLATED: ErrorMetadata(
        code=BUSINESS_RULE_VIOLATED,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_GATEWAY,
        default_message="The request violates a business rule.",
    ),
    MAX_TOOL_CALLS_EXCEEDED: ErrorMetadata(
        code=MAX_TOOL_CALLS_EXCEEDED,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_GATEWAY,
        default_message="The tool call budget for this request is exhausted.",
    ),
    CONFIRMATION_DECLINED: ErrorMetadata(
        code=CONFIRMATION_DECLINED,
        retryable=False,
        requires_user_action=False,
        model_visible=True,
        category=CATEGORY_CONTROL_PLANE,
        default_message="The user declined confirmation; no operation was executed.",
    ),
    LLM_ERROR: ErrorMetadata(
        code=LLM_ERROR,
        retryable=True,
        requires_user_action=True,
        model_visible=False,
        category=CATEGORY_LLM,
        default_message="The LLM provider call failed.",
    ),
}

# Internal control-plane taxonomy (never surfaced to the model).
_INTERNAL_TAXONOMY: Mapping[str, ErrorMetadata] = {
    AUDIT_WRITE_FAILED: ErrorMetadata(
        code=AUDIT_WRITE_FAILED,
        retryable=False,
        requires_user_action=True,
        model_visible=False,
        category=CATEGORY_CONTROL_PLANE,
        default_message="Internal control-plane failure; the operation was not completed.",
    ),
}

# Internal error code -> canonical SS8 code.
_INTERNAL_RENAMES: Mapping[str, str] = {
    "INVALID_ARGUMENTS": SCHEMA_INVALID,
    "INVALID_REQUEST": SCHEMA_INVALID,
    "UNKNOWN_TOOL": TOOL_NOT_FOUND,
    "UNSUPPORTED_TOOL_VERSION": TOOL_VERSION_MISMATCH,
    "POLICY_DENIED": PERMISSION_DENIED,
    "IDEMPOTENCY_IN_PROGRESS": IDEMPOTENCY_CONFLICT,
    "RECONCILIATION_REQUIRED": AMBIGUOUS_OUTCOME,
    "llm_error": LLM_ERROR,
}

_TRANSLATION_TABLE: Mapping[str, str] = {
    **{code: code for code in CANONICAL_TAXONOMY},
    **{code: code for code in _INTERNAL_TAXONOMY},
    **_INTERNAL_RENAMES,
}

# Odoo adapter exception class name -> metadata. Keyed by class name so this
# module never imports the adapter (Gateway-only importer invariant).
_ODOO_EXCEPTION_METADATA: Mapping[str, ErrorMetadata] = {
    "OdooTimeoutError": ErrorMetadata(
        code=ERP_CONNECTION_ERROR,
        retryable=True,
        requires_user_action=False,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The ERP did not respond in time; the operation may be retried.",
    ),
    "OdooConnectionError": ErrorMetadata(
        code=ERP_CONNECTION_ERROR,
        retryable=True,
        requires_user_action=False,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The ERP could not be reached.",
    ),
    "OdooServerError": ErrorMetadata(
        code=ERP_CONNECTION_ERROR,
        retryable=True,
        requires_user_action=False,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The ERP reported an unexpected server-side failure.",
    ),
    "OdooAuthenticationError": ErrorMetadata(
        code=ERP_CONNECTION_ERROR,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="ERP authentication failed; the configured credentials need attention.",
    ),
    "OdooAuthorizationError": ErrorMetadata(
        code=PERMISSION_DENIED,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The ERP denied the operation for this user.",
    ),
    "OdooValidationError": ErrorMetadata(
        code=ERP_VALIDATION_ERROR,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The ERP rejected the operation.",
    ),
    "OdooClientError": ErrorMetadata(
        code=ERP_CONNECTION_ERROR,
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
        default_message="The ERP request failed.",
    ),
}


def _lookup_metadata(canonical_code: str) -> ErrorMetadata | None:
    return CANONICAL_TAXONOMY.get(canonical_code) or _INTERNAL_TAXONOMY.get(canonical_code)


def _fallback_error(message: str | None = None) -> StructuredError:
    """Conservative fallback for unrecognized internal codes: never retryable."""
    return StructuredError(
        code=ERP_CONNECTION_ERROR,
        message=message
        or "An unexpected error occurred while processing the request; the operation was not completed.",
        retryable=False,
        requires_user_action=True,
        model_visible=True,
        category=CATEGORY_ERP,
    )


def translate_error(
    internal_code: str | None,
    message: str | None = None,
) -> StructuredError | None:
    """Translate an internal error code into the canonical structured error.

    Returns ``None`` when the code is absent (no error) or names a
    non-error state such as ``CONFIRMATION_REQUIRED``.
    """
    if internal_code is None:
        return None
    if internal_code in NON_ERROR_CODES:
        return None
    canonical_code = _TRANSLATION_TABLE.get(internal_code)
    if canonical_code is None:
        return _fallback_error(message)
    metadata = _lookup_metadata(canonical_code)
    if metadata is None:
        return _fallback_error(message)
    return StructuredError(
        code=metadata.code,
        message=message or metadata.default_message,
        retryable=metadata.retryable,
        requires_user_action=metadata.requires_user_action,
        model_visible=metadata.model_visible,
        category=metadata.category,
    )


def translate_odoo_exception(
    error: BaseException,
    message: str | None = None,
) -> StructuredError:
    """Translate an Odoo adapter exception using per-exception semantics."""
    metadata: ErrorMetadata | None = None
    for klass in type(error).__mro__:
        candidate = _ODOO_EXCEPTION_METADATA.get(klass.__name__)
        if candidate is not None:
            metadata = candidate
            break
    if metadata is None:
        return _fallback_error(message)
    resolved_message = message or metadata.default_message
    if metadata.code == ERP_VALIDATION_ERROR and message is None:
        raw = getattr(error, "message", None)
        if isinstance(raw, str) and raw.strip():
            resolved_message = raw.strip()
    return StructuredError(
        code=metadata.code,
        message=resolved_message,
        retryable=metadata.retryable,
        requires_user_action=metadata.requires_user_action,
        model_visible=metadata.model_visible,
        category=metadata.category,
    )
