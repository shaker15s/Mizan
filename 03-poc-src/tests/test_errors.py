"""STEP 11 tests for the canonical error taxonomy translation layer."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from poc.errors import (
    AUDIT_WRITE_FAILED,
    BUSINESS_RULE_VIOLATED,
    CONFIRMATION_EXPIRED,
    CONFIRMATION_HASH_MISMATCH,
    CONFIRMATION_REPLAY,
    CANONICAL_TAXONOMY,
    CATEGORY_CONTROL_PLANE,
    ENTITY_NOT_FOUND,
    ERP_CONNECTION_ERROR,
    ERP_VALIDATION_ERROR,
    IDEMPOTENCY_CONFLICT,
    LLM_ERROR,
    MAX_TOOL_CALLS_EXCEEDED,
    CONFIRMATION_DECLINED,
    NON_ERROR_CODES,
    PERMISSION_DENIED,
    SCHEMA_INVALID,
    TOOL_NOT_FOUND,
    TOOL_VERSION_MISMATCH,
    VERIFICATION_FAILED,
    translate_error,
    translate_odoo_exception,
)


EXPECTED_TAXONOMY = {
    SCHEMA_INVALID: (False, False, True),
    TOOL_NOT_FOUND: (False, False, True),
    TOOL_VERSION_MISMATCH: (False, True, False),
    PERMISSION_DENIED: (False, True, True),
    ENTITY_NOT_FOUND: (False, True, True),
    ERP_CONNECTION_ERROR: (True, False, True),
    ERP_VALIDATION_ERROR: (False, True, True),
    VERIFICATION_FAILED: (False, True, True),
    CONFIRMATION_EXPIRED: (False, True, True),
    CONFIRMATION_HASH_MISMATCH: (False, False, False),
    CONFIRMATION_REPLAY: (False, False, False),
    IDEMPOTENCY_CONFLICT: (False, True, True),
    "AMBIGUOUS_OUTCOME": (False, True, True),
    BUSINESS_RULE_VIOLATED: (False, True, True),
    MAX_TOOL_CALLS_EXCEEDED: (False, True, True),
    CONFIRMATION_DECLINED: (False, False, True),
    LLM_ERROR: (True, True, False),
}


def test_canonical_taxonomy_is_complete_and_metadata_is_explicit() -> None:
    assert set(CANONICAL_TAXONOMY) == set(EXPECTED_TAXONOMY)
    assert set(CANONICAL_TAXONOMY) == {
        code for code in CANONICAL_TAXONOMY
    }
    for code, (retryable, requires_user_action, model_visible) in EXPECTED_TAXONOMY.items():
        metadata = CANONICAL_TAXONOMY[code]
        assert metadata.code == code
        assert metadata.retryable is retryable
        assert metadata.requires_user_action is requires_user_action
        assert metadata.model_visible is model_visible
        assert metadata.default_message


@pytest.mark.parametrize(
    ("internal_code", "expected_code", "retryable", "requires_user_action"),
    [
        ("INVALID_ARGUMENTS", SCHEMA_INVALID, False, False),
        ("INVALID_REQUEST", SCHEMA_INVALID, False, False),
        ("UNKNOWN_TOOL", TOOL_NOT_FOUND, False, False),
        ("UNSUPPORTED_TOOL_VERSION", TOOL_VERSION_MISMATCH, False, True),
        ("POLICY_DENIED", PERMISSION_DENIED, False, True),
        ("CONFIRMATION_EXPIRED", CONFIRMATION_EXPIRED, False, True),
        ("CONFIRMATION_HASH_MISMATCH", CONFIRMATION_HASH_MISMATCH, False, False),
        ("CONFIRMATION_REPLAY", CONFIRMATION_REPLAY, False, False),
        ("IDEMPOTENCY_IN_PROGRESS", IDEMPOTENCY_CONFLICT, False, True),
        ("RECONCILIATION_REQUIRED", "AMBIGUOUS_OUTCOME", False, True),
        ("llm_error", LLM_ERROR, True, True),
    ],
)
def test_internal_gateway_codes_translate_with_flags(
    internal_code: str,
    expected_code: str,
    retryable: bool,
    requires_user_action: bool,
) -> None:
    structured = translate_error(internal_code)
    assert structured is not None
    assert structured.code == expected_code
    assert structured.retryable is retryable
    assert structured.requires_user_action is requires_user_action


def test_control_plane_audit_failure_is_not_erp_business_failure() -> None:
    structured = translate_error(AUDIT_WRITE_FAILED, "audit database unavailable")
    assert structured is not None
    assert structured.code == AUDIT_WRITE_FAILED
    assert structured.category == CATEGORY_CONTROL_PLANE
    assert structured.model_visible is False
    assert structured.retryable is False
    assert structured.requires_user_action is True


@pytest.mark.parametrize(
    "code", ["CONFIRMATION_REQUIRED", "ACCEPTED", "REPLAY", "IN_PROGRESS"]
)
def test_states_are_not_translated_as_errors(code: str) -> None:
    assert code in NON_ERROR_CODES
    assert translate_error(code) is None


def test_none_is_absence_of_error() -> None:
    assert translate_error(None) is None


def test_unknown_internal_code_uses_conservative_fallback() -> None:
    structured = translate_error("UNEXPECTED_INTERNAL_CODE", "unsafe detail")
    assert structured is not None
    assert structured.code == ERP_CONNECTION_ERROR
    assert structured.message == "unsafe detail"
    assert structured.retryable is False
    assert structured.requires_user_action is True


def test_unknown_internal_code_does_not_expose_message_by_default() -> None:
    structured = translate_error("UNEXPECTED_INTERNAL_CODE")
    assert structured is not None
    assert "UNEXPECTED_INTERNAL_CODE" not in structured.message


def test_to_dict_matches_ss8_wire_shape() -> None:
    structured = translate_error(PERMISSION_DENIED, "User lacks permission.")
    assert structured is not None
    assert structured.to_dict() == {
        "success": False,
        "error": {
            "code": PERMISSION_DENIED,
            "message": "User lacks permission.",
            "retryable": False,
            "requires_user_action": True,
        },
    }


class _FakeOdooBase(Exception):
    pass


class OdooTimeoutError(_FakeOdooBase):
    pass


class OdooConnectionError(_FakeOdooBase):
    pass


class OdooServerError(_FakeOdooBase):
    pass


class OdooAuthenticationError(_FakeOdooBase):
    pass


class OdooAuthorizationError(_FakeOdooBase):
    pass


class OdooValidationError(_FakeOdooBase):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OdooClientError(_FakeOdooBase):
    pass


@pytest.mark.parametrize(
    ("error", "expected_code", "retryable", "requires_user_action", "expected_message"),
    [
        (OdooTimeoutError("timed out"), ERP_CONNECTION_ERROR, True, False, "The ERP did not respond in time; the operation may be retried."),
        (OdooConnectionError("reset"), ERP_CONNECTION_ERROR, True, False, "The ERP could not be reached."),
        (OdooServerError("internal"), ERP_CONNECTION_ERROR, True, False, "The ERP reported an unexpected server-side failure."),
        (OdooAuthenticationError("bad key"), ERP_CONNECTION_ERROR, False, True, "ERP authentication failed; the configured credentials need attention."),
        (OdooAuthorizationError("denied"), PERMISSION_DENIED, False, True, "The ERP denied the operation for this user."),
        (OdooClientError("bad request"), ERP_CONNECTION_ERROR, False, True, "The ERP request failed."),
    ],
)
def test_odoo_exception_translation_uses_exception_semantics(
    error: Exception,
    expected_code: str,
    retryable: bool,
    requires_user_action: bool,
    expected_message: str,
) -> None:
    structured = translate_odoo_exception(error)
    assert structured.code == expected_code
    assert structured.retryable is retryable
    assert structured.requires_user_action is requires_user_action
    assert structured.message == expected_message


def test_odoo_validation_error_prefers_sanitized_exception_message() -> None:
    structured = translate_odoo_exception(OdooValidationError("Odoo rejected quantity"))
    assert structured.code == ERP_VALIDATION_ERROR
    assert structured.message == "Odoo rejected quantity"
    assert structured.retryable is False
    assert structured.requires_user_action is True


def test_odoo_translation_can_be_capped_with_explicit_message() -> None:
    structured = translate_odoo_exception(OdooValidationError("Odoo rejected quantity"), "Safe message")
    assert structured.message == "Safe message"


def test_unknown_odoo_exception_is_conservative() -> None:
    structured = translate_odoo_exception(RuntimeError("transport collapsed"))
    assert structured.code == ERP_CONNECTION_ERROR
    assert structured.retryable is False
    assert structured.requires_user_action is True


def test_errors_module_is_a_translation_boundary() -> None:
    source = Path(__file__).resolve().parents[1].joinpath("poc", "errors.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
    assert imported_modules == {"__future__", "dataclasses", "typing"}
    assert not any(name.startswith("poc.") or name.startswith("poc") for name in imported_modules)


def test_deferred_codes_are_metadata_only() -> None:
    deferred = {ENTITY_NOT_FOUND, VERIFICATION_FAILED, MAX_TOOL_CALLS_EXCEEDED, BUSINESS_RULE_VIOLATED}
    assert deferred <= set(CANONICAL_TAXONOMY)
    source = Path(__file__).resolve().parents[1].joinpath("poc", "errors.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert not any(function and any(code.lower() in function.lower() for code in deferred) for function in functions)
