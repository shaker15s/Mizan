"""Focused deterministic tests for the Tool Gateway control-plane boundary."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from poc.audit_store import AuditStore
from poc.db.init import initialize
from poc.gateway import (
    ACCEPTED,
    CONFIRMATION_REQUIRED,
    CONFLICT,
    DENIED,
    GatewayResult,
    IdempotencyStore,
    IN_PROGRESS,
    INVALID_ARGUMENTS,
    INVALID_REQUEST,
    POLICY_DENIED,
    RECONCILIATION_REQUIRED,
    REPLAY,
    RESERVED,
    ToolGateway,
    ToolGatewayRequest,
    UNKNOWN_TOOL,
    UNSUPPORTED_TOOL_VERSION,
    VALIDATION_ERROR,
    compute_idempotency_key,
)

TENANT = "poc_tenant_001"
USER = "sales_user@test"
READONLY = "readonly_user@test"
NO_ACCESS = "no_access_user@test"
VERSION = "1.0.0"
CREATE_ARGS = {
    "customer_id": 42,
    "lines": [{"product_id": 7, "quantity": 2}],
}


def gateway(tmp_path: Path) -> ToolGateway:
    db_path = tmp_path / "poc_gateway.db"
    return ToolGateway(db_path=db_path)


def request(
    tool_name: str = "customer.search",
    arguments: dict | None = None,
    user_id: str = USER,
    tenant_id: str = TENANT,
    tool_version: str = VERSION,
    **extras,
) -> ToolGatewayRequest:
    payload = {
        "request_id": str(uuid.uuid4()),
        "user_id": user_id,
        "tenant_id": tenant_id,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "arguments": arguments if arguments is not None else {"query": "Acme"},
    }
    payload.update(extras)
    return ToolGatewayRequest.from_mapping(payload)


def idempotency_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        return connection.execute("SELECT count(*) FROM idempotency_keys").fetchone()[0]


def audit_records(store: AuditStore) -> list[dict]:
    return store.list(limit=100)


def test_read_only_request_is_accepted(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request())
    assert result.status == ACCEPTED
    assert result.error_code is None
    assert result.policy_decision == "allowed"
    assert result.requires_confirmation is False
    assert result.audit_id is not None
    assert idempotency_count(tmp_path / "poc_gateway.db") == 0


def test_unknown_tool_is_denied(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request(tool_name="sales.order.delete"))
    assert result.status == DENIED
    assert result.error_code == UNKNOWN_TOOL
    assert result.audit_id is not None


def test_unsupported_tool_version_is_denied(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request(tool_version="9.9.9"))
    assert result.status == DENIED
    assert result.error_code == UNSUPPORTED_TOOL_VERSION
    assert result.audit_id is not None


def test_missing_required_argument_is_rejected_before_authz(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request(arguments={}))
    assert result.status == VALIDATION_ERROR
    assert result.error_code == INVALID_ARGUMENTS
    assert result.audit_id is not None


def test_unknown_argument_is_rejected_before_authz(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request(arguments={"query": "Acme", "admin": True}))
    assert result.status == VALIDATION_ERROR
    assert result.error_code == INVALID_ARGUMENTS
    assert result.audit_id is not None


def test_invalid_argument_type_is_rejected_before_authz(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request(arguments={"query": 42}))
    assert result.status == VALIDATION_ERROR
    assert result.error_code == INVALID_ARGUMENTS
    assert result.audit_id is not None


def test_unauthorized_user_is_denied(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS, user_id=NO_ACCESS))
    assert result.status == DENIED
    assert result.error_code == POLICY_DENIED
    assert idempotency_count(tmp_path / "poc_gateway.db") == 0


def test_mutating_authorized_user_receives_confirmation_required(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS))
    assert result.status == CONFIRMATION_REQUIRED
    assert result.requires_confirmation is True
    assert result.idempotency_key is not None
    assert result.execution_id is not None
    assert idempotency_count(tmp_path / "poc_gateway.db") == 1


def test_same_mutating_request_is_in_progress(tmp_path: Path):
    gw = gateway(tmp_path)
    first = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS))
    second = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS))
    assert first.status == CONFIRMATION_REQUIRED
    assert second.status == IN_PROGRESS
    assert second.error_code == "IDEMPOTENCY_IN_PROGRESS"
    assert second.execution_id == first.execution_id


def test_completed_idempotent_request_replays(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    gw = gateway(tmp_path)
    first = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS))
    key = first.idempotency_key
    store = IdempotencyStore(db_path)
    record = store.get(key, TENANT, USER)
    completed = store.complete(
        key,
        request_fingerprint=record.request_fingerprint,
        tool_name="sales.order.create",
        tool_version=VERSION,
        tenant_id=TENANT,
        user_id=USER,
        execution_id=record.execution_id,
        result={"status": "success", "order_id": 88},
        external_record_id="88",
    )
    replay = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS))
    assert completed.state == "completed"
    assert replay.status == REPLAY
    assert replay.result == {"status": "success", "order_id": 88}
    assert replay.execution_id == first.execution_id
    assert idempotency_count(db_path) == 1


def test_different_arguments_same_computed_key_conflict(tmp_path: Path):
    gw = gateway(tmp_path)
    first = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS))
    modified = dict(CREATE_ARGS)
    modified["customer_id"] = 43
    second = gw.handle_request(
        request(
            tool_name="sales.order.create",
            arguments=modified,
            idempotency_key=first.idempotency_key,
        )
    )
    assert second.status == VALIDATION_ERROR
    assert second.error_code == INVALID_REQUEST
    assert idempotency_count(tmp_path / "poc_gateway.db") == 1


def test_unknown_idempotent_record_requires_reconciliation(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    gw = gateway(tmp_path)
    first = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS))
    store = IdempotencyStore(db_path)
    record = store.get(first.idempotency_key, TENANT, USER)
    store.fail(
        first.idempotency_key,
        request_fingerprint=record.request_fingerprint,
        tool_name="sales.order.create",
        tool_version=VERSION,
        tenant_id=TENANT,
        user_id=USER,
        execution_id=record.execution_id,
        outcome="ambiguous",
        error_code="ERP_CONNECTION_ERROR",
    )
    retry = gw.handle_request(request(tool_name="sales.order.create", arguments=CREATE_ARGS))
    assert retry.status == RECONCILIATION_REQUIRED
    assert retry.error_code == "RECONCILIATION_REQUIRED"
    assert idempotency_count(db_path) == 1


def test_request_envelope_rejects_arbitrary_fields(tmp_path: Path):
    gw = gateway(tmp_path)
    payload = request(tool_name="sales.order.create", arguments=CREATE_ARGS)
    with pytest.raises(Exception):
        ToolGatewayRequest.from_mapping({"not": "enough"})
    with pytest.raises(Exception):
        ToolGatewayRequest.from_mapping(
            {
                **{
                    "request_id": payload.request_id,
                    "user_id": payload.user_id,
                    "tenant_id": payload.tenant_id,
                    "tool_name": payload.tool_name,
                    "tool_version": payload.tool_version,
                    "arguments": payload.arguments,
                },
                "role": "admin",
                "is_admin": True,
                "requires_confirmation": False,
                "readOnly": True,
                "model": "sale.order",
                "method": "unlink",
            }
        )


def test_caller_metadata_cannot_bypass_gateway(tmp_path: Path):
    gw = gateway(tmp_path)
    adversarial_arguments = {
        **CREATE_ARGS,
        "role": "admin",
        "is_admin": True,
        "requires_confirmation": False,
        "readOnly": True,
        "model": "sale.order",
        "method": "unlink",
    }
    result = gw.handle_request(request(tool_name="sales.order.create", arguments=adversarial_arguments))
    assert result.status == VALIDATION_ERROR
    assert result.error_code == INVALID_ARGUMENTS
    assert idempotency_count(tmp_path / "poc_gateway.db") == 0


def test_caller_tenant_override_is_denied(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request(tenant_id="other-tenant"))
    assert result.status == DENIED
    assert result.error_code == POLICY_DENIED


def test_invalid_request_still_audits(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request({"request_id": "x"})
    assert result.status == VALIDATION_ERROR
    assert result.error_code == INVALID_REQUEST
    assert result.audit_id is not None
    assert result.request_id == ""


def test_read_only_tools_do_not_reserve(tmp_path: Path):
    gw = gateway(tmp_path)
    gw.handle_request(request(tool_name="customer.get", arguments={"customer_id": 42}))
    gw.handle_request(request(tool_name="product.search", arguments={"query": "Acme"}))
    gw.handle_request(request(tool_name="sales.order.get", arguments={"order_id": 42}))
    assert idempotency_count(tmp_path / "poc_gateway.db") == 0


def test_structured_result_is_immutable_and_serializable(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request())
    with pytest.raises(Exception):
        result.status = DENIED
    encoded = json.dumps(
        {
            "status": result.status,
            "error_code": result.error_code,
            "reason": result.reason,
            "request_id": result.request_id,
            "user_id": result.user_id,
            "tenant_id": result.tenant_id,
            "tool_name": result.tool_name,
            "tool_version": result.tool_version,
            "policy_decision": result.policy_decision,
            "idempotency_key": result.idempotency_key,
            "execution_id": result.execution_id,
            "result": result.result,
            "audit_id": result.audit_id,
            "requires_confirmation": result.requires_confirmation,
        },
        sort_keys=True,
    )
    assert "error_code" in encoded


def test_audit_receives_no_secrets_or_arbitrary_metadata(tmp_path: Path):
    gw = gateway(tmp_path)
    result = gw.handle_request(request())
    store = gw.audit_store
    records = audit_records(store)
    assert len(records) == 1
    record = records[0]
    raw = json.dumps(record)
    assert "admin" not in raw
    assert "password" not in raw
    assert "token" not in raw
    assert "role" not in raw
    assert record["tool_name"] == "customer.search"
    assert record["tool_version"] == VERSION
    assert record["arguments_hash"]


def test_gateway_has_no_arbitrary_odoo_execution_surface():
    import inspect
    from poc.gateway import ToolGateway as Gateway

    methods = {name for name, _ in inspect.getmembers(Gateway, inspect.isfunction)}
    forbidden = {
        "execute_raw",
        "call_odoo",
        "call_model",
        "call_method",
        "execute_callback",
        "rpc",
    }
    assert methods.isdisjoint(forbidden)


def test_gateway_source_has_no_llm_or_odoo_execution_imports():
    source = Path("poc/gateway.py").read_text(encoding="utf-8")
    forbidden = (
        "import odoo",
        "from odoo",
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import urllib",
        "from urllib",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
        "xmlrpc",
    )
    for term in forbidden:
        assert term not in source.lower()


def test_repeated_read_only_request_is_deterministic(tmp_path: Path):
    gw = gateway(tmp_path)
    payload = request()
    first = gw.handle_request(payload)
    second = gw.handle_request(payload)
    assert first.status == second.status
    assert first.error_code == second.error_code
    assert first.reason == second.reason
    assert first.policy_decision == second.policy_decision
    assert first.audit_id != second.audit_id


def test_pipeline_order_schema_then_authz_then_idempotency(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    gw = gateway(tmp_path)

    invalid = gw.handle_request(
        request(tool_name="sales.order.create", arguments={}, user_id=NO_ACCESS)
    )
    assert invalid.status == VALIDATION_ERROR
    assert invalid.policy_decision == "denied"
    assert idempotency_count(db_path) == 0

    denied = gw.handle_request(
        request(tool_name="sales.order.create", arguments=CREATE_ARGS, user_id=NO_ACCESS)
    )
    assert denied.status == DENIED
    assert denied.policy_decision == "denied"
    assert idempotency_count(db_path) == 0

    confirmed = gw.handle_request(
        request(tool_name="sales.order.create", arguments=CREATE_ARGS)
    )
    assert confirmed.status == CONFIRMATION_REQUIRED
    assert confirmed.policy_decision == "confirmation_required"
    assert idempotency_count(db_path) == 1


def test_idempotency_key_must_match_server_derived_key(tmp_path: Path):
    gw = gateway(tmp_path)
    expected = compute_idempotency_key(TENANT, USER, "sales.order.create", CREATE_ARGS)
    wrong = gw.handle_request(
        request(
            tool_name="sales.order.create",
            arguments=CREATE_ARGS,
            idempotency_key="ffffffffffffffffffffffffffffffff",
        )
    )
    correct = gw.handle_request(
        request(
            tool_name="sales.order.create",
            arguments=CREATE_ARGS,
            idempotency_key=expected,
        )
    )
    assert wrong.status == VALIDATION_ERROR
    assert wrong.error_code == INVALID_REQUEST
    assert correct.status == CONFIRMATION_REQUIRED
    assert idempotency_count(tmp_path / "poc_gateway.db") == 1


def test_db_initialization_still_preserves_existing_data(tmp_path: Path):
    db_path = tmp_path / "poc_gateway.db"
    initialize(db_path)
    gw = ToolGateway(db_path=db_path)
    result = gw.handle_request(request())
    assert result.audit_id is not None
    assert gw.audit_store.verify_chain()["valid"] is True
