"""Deterministic Tool Gateway orchestration boundary for the POC.

The gateway validates a structured request, resolves the server-owned tool
contract, validates arguments, evaluates authorization, applies idempotency
state for mutating tools, preserves confirmation semantics, and writes audit
records. It performs no Odoo execution, LLM calls, confirmation execution, or
agent runtime work.
"""

from __future__ import annotations

import json
import hashlib
import uuid
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from poc.audit_store import AuditStore
from poc.authz import PolicyEngine
from poc.confirmation import (
    ApprovalResult,
    ConfirmationStore,
    DECLINED,
    NOT_FOUND,
    REPLAY,
)
from poc.db.init import DEFAULT_DB_PATH
from poc.idempotency import (
    AMBIGUOUS,
    CONFLICT,
    IDEMPOTENCY_CONFLICT,
    IdempotencyStore,
    IdempotencyStoreError,
    IllegalIdempotencyTransition,
    RECONCILIATION_REQUIRED,
    RECONCILIATION_REQUIRED_ERROR,
    IN_PROGRESS,
    REPLAYED,
    RESERVED,
    compute_idempotency_key,
    compute_request_fingerprint,
)
from poc.tool_contracts import ToolRegistry, get_registry
from poc.errors import (
    ENTITY_NOT_FOUND,
    StructuredError,
    translate_error,
    translate_odoo_exception,
)
from poc.verification import verify_sales_order_creation

ACCEPTED = "accepted"
DENIED = "denied"
VALIDATION_ERROR = "validation_error"
CONFIRMATION_REQUIRED = "confirmation_required"
REPLAY = "replay"
DECLINED = "declined"

INVALID_REQUEST = "INVALID_REQUEST"
UNKNOWN_TOOL = "UNKNOWN_TOOL"
UNSUPPORTED_TOOL_VERSION = "UNSUPPORTED_TOOL_VERSION"
INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
POLICY_DENIED = "POLICY_DENIED"
CONFIRMATION_REQUIRED_ERROR = "CONFIRMATION_REQUIRED"
IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
AUDIT_WRITE_FAILED = "AUDIT_WRITE_FAILED"
CONFIRMATION_DECLINED = "CONFIRMATION_DECLINED"

_REQUEST_FIELDS = frozenset(
    {
        "request_id",
        "user_id",
        "tenant_id",
        "tool_name",
        "tool_version",
        "arguments",
        "idempotency_key",
    }
)
_REQUIRED_REQUEST_FIELDS = frozenset(
    {
        "request_id",
        "user_id",
        "tenant_id",
        "tool_name",
        "tool_version",
        "arguments",
    }
)
_IDEMPOTENCY_KEY_PATTERN = "^[0-9a-f]{32}$"
_REQUEST_ID_PATTERN = "^[A-Za-z0-9_.:@-]{1,128}$"


@dataclass(frozen=True)
class ToolGatewayRequest:
    """Strict, authenticated tool invocation envelope."""

    request_id: str
    user_id: str
    tenant_id: str
    tool_name: str
    tool_version: str
    arguments: Mapping[str, Any]
    idempotency_key: str | None = None

    @classmethod
    def from_mapping(cls, request: Mapping[str, Any]) -> "ToolGatewayRequest":
        if not isinstance(request, dict):
            raise InvalidGatewayRequest("request must be a mapping")
        missing = _REQUIRED_REQUEST_FIELDS - set(request)
        if missing:
            raise InvalidGatewayRequest("request has missing required fields")
        unknown = set(request) - _REQUEST_FIELDS
        if unknown:
            raise InvalidGatewayRequest("request has unsupported fields")
        return cls(
            request_id=request["request_id"],
            user_id=request["user_id"],
            tenant_id=request["tenant_id"],
            tool_name=request["tool_name"],
            tool_version=request["tool_version"],
            arguments=request["arguments"],
            idempotency_key=request.get("idempotency_key"),
        )


@dataclass(frozen=True)
class GatewayResult:
    """Immutable, machine-readable gateway decision."""

    status: str
    error_code: str | None
    reason: str
    request_id: str
    user_id: str
    tenant_id: str
    tool_name: str
    tool_version: str
    policy_decision: str
    idempotency_key: str | None
    execution_id: str | None
    result: Mapping[str, Any] | None
    audit_id: int | None
    requires_confirmation: bool
    proposal: Mapping[str, Any] | None = None
    structured_error_override: StructuredError | None = None

    @property
    def structured_error(self):
        return self.structured_error_override or translate_error(self.error_code, self.reason)


class InvalidGatewayRequest(ValueError):
    """Raised only by request parsing; never returned as the primary result."""


@dataclass
class _Context:
    record: ToolGatewayRequest
    tool_call_id: str
    policy_decision: str = "denied"
    idempotency_key: str | None = None
    execution_id: str | None = None
    replay_result: Mapping[str, Any] | None = None
    error_code: str | None = None
    requires_confirmation: bool = False
    odoo_client: Any = None


class ToolGateway:
    """Single server-side authorization and control-plane boundary."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        idempotency_store: IdempotencyStore | None = None,
        audit_store: AuditStore | None = None,
        confirmation_store: ConfirmationStore | None = None,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.registry = registry or get_registry()
        self.policy_engine = policy_engine or PolicyEngine()
        self.idempotency_store = idempotency_store or IdempotencyStore(db_path)
        self.audit_store = audit_store or AuditStore(db_path)
        self.confirmation_store = confirmation_store or ConfirmationStore(
            db_path=db_path,
            registry=self.registry,
            policy_engine=self.policy_engine,
        )
        self.audit_store.initialize()

    def _build_odoo_payload(self, arguments: Mapping[str, Any], idempotency_key: str) -> list[dict[str, Any]]:
        order_lines = [
            [0, 0, {"product_id": line["product_id"], "product_uom_qty": line["quantity"]}]
            for line in arguments["lines"]
        ]
        return [
            {
                "partner_id": arguments["customer_id"],
                "client_order_ref": idempotency_key,
                "order_line": order_lines,
            }
        ]

    def _verification_success(self, arguments: Mapping[str, Any], record: Any, order_id: int) -> Any:
        result = {
            "status": "success",
            "verified": True,
            "order_id": order_id,
            "customer_id": arguments["customer_id"],
            "lines": [dict(line) for line in arguments["lines"]],
        }
        return self._verification_outcome("accepted", None, "verified_sales_order_created", result, str(order_id))

    def _verification_failure(self, arguments: Mapping[str, Any], record: Any, reason: str) -> Any:
        return self._verification_outcome(
            "error",
            "VERIFICATION_FAILED",
            reason,
            None,
        )

    def _odoo_read_failure(self, arguments: Mapping[str, Any], record: Any, structured: Any) -> Any:
        return self._verification_outcome("error", structured.code, structured.message, None, structured_error=structured)

    def _odoo_write_failure(self, arguments: Mapping[str, Any], record: Any, structured: Any) -> Any:
        return self._verification_outcome("error", structured.code, structured.message, None, structured_error=structured)

    def _verification_outcome(
        self,
        status: str,
        error_code: str | None,
        reason: str,
        result: Mapping[str, Any] | None,
        external_record_id: str | None = None,
        structured_error: StructuredError | None = None,
    ) -> Any:
        return {
            "status": status,
            "error_code": error_code,
            "reason": reason,
            "result": result,
            "external_record_id": external_record_id,
            "reconciliation": None,
            "structured_error": structured_error,
        }

    def _verification_audit_record(
        self,
        record: Any,
        arguments: Mapping[str, Any],
        verification: Any,
    ) -> dict[str, Any]:
        return {
            "request_id": f"verify-{record.execution_id}",
            "tool_call_id": f"verify-{record.execution_id}",
            "tenant_id": record.tenant_id,
            "user_id": record.user_id,
            "odoo_user": record.user_id,
            "tool_name": record.tool_name,
            "tool_version": record.tool_version,
            "arguments": dict(arguments),
            "policy_decision": "confirmation_required",
            "proposal_id": None,
            "operation_hash": None,
            "approval_id": None,
            "start_time": _utc_now(),
            "end_time": _utc_now(),
            "result_status": verification["status"],
            "error_code": verification["error_code"],
            "external_system": "odoo",
            "external_record_id": verification["external_record_id"],
            "idempotency_key": record.idempotency_key,
            "execution_id": record.execution_id,
        }

    def _empty_result(
        self,
        *,
        status: str,
        error_code: str | None,
        reason: str,
        result: Mapping[str, Any] | None = None,
        structured_error: StructuredError | None = None,
        idempotency_key: str | None = None,
        user_id: str = "",
        tenant_id: str = "",
        execution_id: str | None = None,
    ) -> GatewayResult:
        return GatewayResult(
            status=status,
            error_code=error_code,
            reason=reason,
            request_id="",
            user_id=user_id,
            tenant_id=tenant_id,
            tool_name="sales.order.create",
            tool_version=self.registry.get("sales.order.create")["tool_version"],
            policy_decision="confirmation_required",
            idempotency_key=idempotency_key,
            execution_id=execution_id,
            result=result,
            audit_id=None,
            requires_confirmation=False,
            structured_error_override=structured_error,
        )

    def execute_verified(
        self,
        idempotency_key: str,
        execution_id: str,
        arguments: Mapping[str, Any],
        odoo_client: Any,
        tenant_id: str,
        user_id: str,
    ) -> GatewayResult:
        """Execute the confirmed mutation, verify its ERP state, then persist."""
        record = self.idempotency_store.get(idempotency_key, tenant_id, user_id)
        if record is None or record.execution_id != execution_id or record.state != "pending":
            return self._empty_result(
                status=CONFLICT,
                error_code=IDEMPOTENCY_CONFLICT,
                reason="confirmed execution reservation is no longer owned",
                user_id=user_id,
                tenant_id=tenant_id,
            )

        arguments_for_audit = dict(arguments)
        create_attempted = False

        try:
            product_ids = sorted({line["product_id"] for line in arguments["lines"]})
            product_records = odoo_client.read("product.product", product_ids, ["list_price"])
            price_by_id: dict[int, Decimal] = {}
            for product_record in product_records:
                if not isinstance(product_record, Mapping) or "id" not in product_record or "list_price" not in product_record:
                    continue
                try:
                    price_by_id[int(product_record["id"])] = Decimal(str(product_record["list_price"]))
                except (InvalidOperation, TypeError, ValueError):
                    continue
            if len(price_by_id) != len(product_ids):
                verification = self._verification_failure(
                    arguments,
                    record,
                    "Product pricing data is incomplete for amount verification.",
                )
            else:
                expected_amount_total = sum(
                    price_by_id[line["product_id"]] * Decimal(str(line["quantity"]))
                    for line in arguments["lines"]
                )
                payload = self._build_odoo_payload(arguments, record.idempotency_key)
                created_ids = odoo_client.create("sale.order", payload)
                create_attempted = True
                if not isinstance(created_ids, list) or len(created_ids) != 1 or not isinstance(created_ids[0], int):
                    verification = self._verification_failure(
                        arguments,
                        record,
                        "Odoo returned an invalid creation response.",
                    )
                else:
                    try:
                        passed = verify_sales_order_creation(
                            odoo_client,
                            created_ids[0],
                            arguments["customer_id"],
                            arguments["lines"],
                            record.idempotency_key,
                            expected_amount_total=float(expected_amount_total),
                        )
                    except Exception as error:
                        structured = translate_odoo_exception(error)
                        verification = self._odoo_read_failure(arguments, record, structured)
                    else:
                        if passed.passed:
                            verification = self._verification_success(arguments, record, created_ids[0])
                        else:
                            verification = self._verification_failure(arguments, record, passed.error)
        except Exception as error:
            structured = translate_odoo_exception(error)
            if structured.retryable:
                self.idempotency_store.fail(
                    record.idempotency_key,
                    request_fingerprint=record.request_fingerprint,
                    tool_name=record.tool_name,
                    tool_version=record.tool_version,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    execution_id=record.execution_id,
                    outcome="ambiguous",
                    error_code=structured.code,
                )
                verification = {
                    "status": "ambiguous",
                    "reason": structured.message,
                    "error_code": structured.code,
                    "external_record_id": None,
                }
                self.audit_store.append(
                    self._verification_audit_record(record, arguments_for_audit, verification)
                )
                return self._empty_result(
                    status="erp_error",
                    error_code=structured.code,
                    reason=structured.message,
                    structured_error=structured,
                    user_id=user_id,
                    tenant_id=tenant_id,
                )
            verification = self._odoo_write_failure(arguments, record, structured)

        if verification["status"] == "error":
            # Definitive failure. Whether the create call fired decides the
            # retry policy: pre-create failures are safe to release for retry;
            # post-create failures left ERP state unknown and reconcile as
            # ambiguous instead of replaying an error as success (B4 fix).
            if create_attempted:
                self.idempotency_store.fail(
                    record.idempotency_key,
                    request_fingerprint=record.request_fingerprint,
                    tool_name=record.tool_name,
                    tool_version=record.tool_version,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    execution_id=record.execution_id,
                    outcome=AMBIGUOUS,
                    error_code=verification["error_code"] or "UNKNOWN_ERROR",
                )
            else:
                try:
                    self.idempotency_store.release(
                        record.idempotency_key,
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        execution_id=record.execution_id,
                    )
                except (IdempotencyStoreError, IllegalIdempotencyTransition):
                    self.idempotency_store.fail(
                        record.idempotency_key,
                        request_fingerprint=record.request_fingerprint,
                        tool_name=record.tool_name,
                        tool_version=record.tool_version,
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        execution_id=record.execution_id,
                        outcome=AMBIGUOUS,
                        error_code=verification["error_code"] or "UNKNOWN_ERROR",
                    )
        else:
            self.idempotency_store.complete(
                record.idempotency_key,
                request_fingerprint=record.request_fingerprint,
                tool_name=record.tool_name,
                tool_version=record.tool_version,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                execution_id=record.execution_id,
                result=verification["result"],
                external_record_id=verification["external_record_id"],
                reconciliation=verification["reconciliation"],
            )
        self.audit_store.append(
            self._verification_audit_record(record, arguments_for_audit, verification)
        )
        return self._empty_result(
            status=verification["status"],
            error_code=verification["error_code"],
            reason=verification["reason"] or "verification_completed",
            result=verification["result"],
            structured_error=verification["structured_error"],
            idempotency_key=record.idempotency_key,
            user_id=user_id,
            tenant_id=tenant_id,
            execution_id=record.execution_id,
        )

    def confirm_and_execute(
        self,
        proposal_id: str,
        user_id: str,
        tenant_id: str,
        odoo_client: Any,
    ) -> GatewayResult:
        """Approve the server-created proposal and execute it through the gateway."""
        approval = self.confirmation_store.approve(proposal_id, user_id, tenant_id)
        if approval.status != "approved":
            return self._approval_failure(approval)
        return self.execute_verified(
            approval.idempotency_key,
            approval.execution_id,
            approval.arguments,
            odoo_client,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    def record_confirmation_denial(
        self,
        proposal_id: str,
        user_id: str,
        tenant_id: str,
    ) -> GatewayResult:
        """Deny a proposal and append the denial through the gateway audit boundary.

        Denial also releases the pending idempotency reservation so the user can
        correct the arguments and retry without manual SQLite surgery (B3 fix).
        """
        denial = self.confirmation_store.decline(proposal_id, user_id, tenant_id)
        if denial.status != DECLINED:
            return self._approval_failure(denial)
        proposal = self.confirmation_store.get_proposal(proposal_id)
        if proposal is None:
            return self._empty_result(
                status=REPLAY,
                error_code="CONFIRMATION_REPLAY",
                reason="proposal_disappeared_after_denial",
                user_id=user_id,
                tenant_id=tenant_id,
            )
        record = self.idempotency_store.get(denial.idempotency_key, tenant_id, user_id)
        if record is not None and record.state == "pending":
            try:
                self.idempotency_store.release(
                    record.idempotency_key,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    execution_id=record.execution_id,
                )
            except (IdempotencyStoreError, IllegalIdempotencyTransition):
                pass
        arguments_json = json.dumps(
            proposal.arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        end_time = _utc_now()
        try:
            audit_record = self.audit_store.append(
                {
                    "request_id": str(uuid.uuid4()),
                    "tool_call_id": str(uuid.uuid4()),
                    "trace_id": str(uuid.uuid4()),
                    "tenant_id": proposal.tenant_id,
                    "user_id": proposal.user_id,
                    "odoo_user": proposal.user_id,
                    "tool_name": proposal.tool_name,
                    "tool_version": proposal.tool_version,
                    "arguments_hash": hashlib.sha256(arguments_json.encode("utf-8")).hexdigest(),
                    "sanitized_arguments": None,
                    "policy_decision": "confirmation_required",
                    "proposal_id": proposal.proposal_id,
                    "operation_hash": proposal.operation_hash,
                    "start_time": end_time,
                    "end_time": end_time,
                    "result_status": "denied",
                    "error_code": CONFIRMATION_DECLINED,
                    "idempotency_key": denial.idempotency_key,
                    "execution_id": record.execution_id if record is not None else None,
                }
            )
        except Exception:
            return self._empty_result(
                status=DENIED,
                error_code=AUDIT_WRITE_FAILED,
                reason="audit_write_failed",
                user_id=user_id,
                tenant_id=tenant_id,
            )
        result = self._empty_result(
            status=DECLINED,
            error_code=None,
            reason="confirmation_declined_by_user",
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return _replace_audit_id(result, int(audit_record["audit_id"]))

    def _approval_failure(self, result: ApprovalResult) -> GatewayResult:
        if result.error_code == NOT_FOUND:
            return self._empty_result(
                status=DENIED,
                error_code=result.error_code,
                reason=result.reason,
                structured_error=None,
            )
        return self._empty_result(
            status=DENIED,
            error_code=result.error_code,
            reason=result.reason,
            structured_error=result.structured_error,
        )

    def handle_request(
        self,
        request: ToolGatewayRequest | Mapping[str, Any],
        odoo_client: Any = None,
    ) -> GatewayResult:
        """Process one structured request; executes read-only tools when a client is provided."""
        try:
            if isinstance(request, ToolGatewayRequest):
                record = request
            else:
                record = ToolGatewayRequest.from_mapping(request)
        except InvalidGatewayRequest:
            return self._audit_invalid_envelope()

        context = _Context(record=record, tool_call_id=str(uuid.uuid4()), odoo_client=odoo_client)
        result = self._process(context)
        audit_id = self._audit(context, result)
        if audit_id is None:
            return GatewayResult(
                status=DENIED,
                error_code=AUDIT_WRITE_FAILED,
                reason="audit_write_failed",
                request_id=context.record.request_id,
                user_id=context.record.user_id,
                tenant_id=context.record.tenant_id,
                tool_name=context.record.tool_name,
                tool_version=context.record.tool_version,
                policy_decision=context.policy_decision,
                idempotency_key=context.idempotency_key,
                execution_id=context.execution_id,
                result=None,
                audit_id=None,
                requires_confirmation=context.requires_confirmation,
            )
        return result if result.audit_id == audit_id else _replace_audit_id(result, audit_id)

    def _process(self, context: _Context) -> GatewayResult:
        try:
            self._validate_envelope(context)
        except InvalidGatewayRequest:
            return self._final_result(
                context,
                status=VALIDATION_ERROR,
                error_code=INVALID_REQUEST,
                reason="invalid_request_envelope",
            )

        try:
            contract = self.registry.get(context.record.tool_name)
        except KeyError:
            return self._final_result(
                context,
                status=DENIED,
                error_code=UNKNOWN_TOOL,
                reason="unknown_tool",
            )

        if contract["tool_version"] != context.record.tool_version:
            return self._final_result(
                context,
                status=DENIED,
                error_code=UNSUPPORTED_TOOL_VERSION,
                reason="unsupported_tool_version",
            )

        try:
            self._validate_arguments(context, contract)
        except jsonschema.ValidationError:
            return self._final_result(
                context,
                status=VALIDATION_ERROR,
                error_code=INVALID_ARGUMENTS,
                reason="argument_schema_failed",
            )

        policy_decision = self.policy_engine.evaluate(
            {
                "user_id": context.record.user_id,
                "tenant_id": context.record.tenant_id,
                "tool_name": context.record.tool_name,
                "tool_version": context.record.tool_version,
            }
        )
        context.policy_decision = policy_decision.decision
        if policy_decision.decision == "denied":
            return self._final_result(
                context,
                status=DENIED,
                error_code=POLICY_DENIED,
                reason=policy_decision.reason,
            )

        if contract["readOnly"] is False:
            return self._process_mutating(context, contract, policy_decision)

        if context.odoo_client is not None:
            return self._execute_read(context, contract)

        return self._final_result(
            context,
            status=ACCEPTED,
            error_code=None,
            reason="ready_for_execution",
        )

    def _execute_read(self, context: _Context, contract: Mapping[str, Any]) -> GatewayResult:
        """Execute one read-only tool call through the provided ERP client.

        The client is supplied by the caller inside the server-owned boundary;
        the gateway still owns validation, authorization, and audit for the read.
        """
        record = context.record
        odoo = contract["odoo"]
        model = odoo["model"]
        fields = list(odoo["fields"])
        limit_cap = odoo.get("limit_cap")
        try:
            if odoo["method"] == "search_read":
                query = str(context.record.arguments["query"])
                domain = [["name", "ilike", query]]
                limit = int(limit_cap) if limit_cap else None
                records = odoo_client_search_read(context.odoo_client, model, domain, fields, limit)
            elif odoo["method"] == "read":
                id_field = "customer_id" if "customer_id" in context.record.arguments else "order_id"
                record_id = int(context.record.arguments[id_field])
                records = context.odoo_client.read(model, [record_id], fields)
            else:
                raise ValueError(f"tool read method {odoo['method']!r} is not executable")
        except Exception as error:
            structured = translate_odoo_exception(error)
            return self._final_result(
                context,
                status="erp_error",
                error_code=structured.code,
                reason=structured.message,
                structured_error=structured,
            )

        if not records:
            if odoo["method"] == "read":
                # Read-by-id returning nothing means the referenced record does
                # not exist; a search with zero hits is a legitimate success.
                return self._final_result(
                    context,
                    status="erp_error",
                    error_code=ENTITY_NOT_FOUND,
                    reason="no_matching_records",
                    structured_error=translate_error(ENTITY_NOT_FOUND, "No matching records were found."),
                )
            return self._final_result(
                context,
                status=ACCEPTED,
                error_code=None,
                reason="read_completed",
                result=_shape_read_result(record.tool_name, []),
            )
        return self._final_result(
            context,
            status=ACCEPTED,
            error_code=None,
            reason="read_completed",
            result=_shape_read_result(record.tool_name, records),
        )

    def _validate_envelope(self, context: _Context) -> None:
        record = context.record
        for value in (record.request_id, record.user_id, record.tenant_id):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise InvalidGatewayRequest("request identity fields are invalid")
        for value in (record.tool_name, record.tool_version):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise InvalidGatewayRequest("tool identity fields are invalid")
        if not isinstance(record.arguments, dict):
            raise InvalidGatewayRequest("arguments must be a mapping")
        if record.idempotency_key is not None:
            if not isinstance(record.idempotency_key, str) or len(record.idempotency_key) != 32:
                raise InvalidGatewayRequest("idempotency_key is invalid")
            try:
                int(record.idempotency_key, 16)
            except ValueError as error:
                raise InvalidGatewayRequest("idempotency_key is invalid") from error
            if record.idempotency_key.lower() != record.idempotency_key:
                raise InvalidGatewayRequest("idempotency_key is invalid")

    def _validate_arguments(self, context: _Context, contract: Mapping[str, Any]) -> None:
        schema = contract["inputSchema"]
        jsonschema.validate(instance=context.record.arguments, schema=schema)

    def _process_mutating(
        self,
        context: _Context,
        contract: Mapping[str, Any],
        policy_decision: Any,
    ) -> GatewayResult:
        computed_key = compute_idempotency_key(
            context.record.tenant_id,
            context.record.user_id,
            context.record.tool_name,
            context.record.arguments,
        )
        if context.record.idempotency_key is not None and context.record.idempotency_key != computed_key:
            return self._final_result(
                context,
                status=VALIDATION_ERROR,
                error_code=INVALID_REQUEST,
                reason="idempotency_key_mismatch",
            )
        fingerprint = compute_request_fingerprint(
            context.record.tenant_id,
            context.record.user_id,
            context.record.tool_name,
            context.record.tool_version,
            context.record.arguments,
        )
        outcome = self.idempotency_store.reserve(
            idempotency_key=computed_key,
            request_fingerprint=fingerprint,
            tool_name=context.record.tool_name,
            tool_version=context.record.tool_version,
            tenant_id=context.record.tenant_id,
            user_id=context.record.user_id,
        )
        context.idempotency_key = computed_key
        context.execution_id = outcome.record.execution_id

        if outcome.status == CONFLICT:
            return self._final_result(
                context,
                status=CONFLICT,
                error_code=IDEMPOTENCY_CONFLICT,
                reason=outcome.reason,
            )
        if outcome.status == IN_PROGRESS:
            return self._final_result(
                context,
                status=IN_PROGRESS,
                error_code=IDEMPOTENCY_IN_PROGRESS,
                reason=outcome.reason,
            )
        if outcome.status == RECONCILIATION_REQUIRED:
            return self._final_result(
                context,
                status=RECONCILIATION_REQUIRED,
                error_code=RECONCILIATION_REQUIRED_ERROR,
                reason=outcome.reason,
            )
        if outcome.status == REPLAYED:
            context.replay_result = outcome.record.result
            return self._final_result(
                context,
                status=REPLAY,
                error_code=None,
                reason="completed_result_replayed",
                result=outcome.record.result,
            )
        if outcome.status == RESERVED and contract["requiresConfirmation"] is True:
            context.requires_confirmation = True
            return self._final_result(
                context,
                status=CONFIRMATION_REQUIRED,
                error_code=CONFIRMATION_REQUIRED_ERROR,
                reason="confirmation_required_before_execution",
                proposal=self._confirmation_proposal(context),
            )
        if outcome.status == RESERVED:
            return self._final_result(
                context,
                status=ACCEPTED,
                error_code=None,
                reason="ready_for_execution",
            )
        raise ValueError("unsupported idempotency outcome")

    def _final_result(
        self,
        context: _Context,
        *,
        status: str,
        error_code: str | None,
        reason: str,
        result: Mapping[str, Any] | None = None,
        proposal: Mapping[str, Any] | None = None,
        structured_error: StructuredError | None = None,
    ) -> GatewayResult:
        return GatewayResult(
            status=status,
            error_code=error_code,
            reason=reason,
            request_id=context.record.request_id,
            user_id=context.record.user_id,
            tenant_id=context.record.tenant_id,
            tool_name=context.record.tool_name,
            tool_version=context.record.tool_version,
            policy_decision=context.policy_decision,
            idempotency_key=context.idempotency_key,
            execution_id=context.execution_id,
            result=result,
            audit_id=None,
            requires_confirmation=context.requires_confirmation,
            proposal=proposal,
            structured_error_override=structured_error,
        )

    def _confirmation_proposal(self, context: _Context) -> Mapping[str, Any] | None:
        """Build the server-owned proposal payload from the current request."""
        record = context.record
        created = self.confirmation_store.create_proposal(
            tool_name=record.tool_name,
            tool_version=record.tool_version,
            arguments=dict(record.arguments),
            user_id=record.user_id,
            tenant_id=record.tenant_id,
        )
        if created.proposal is None:
            return None
        proposal = created.proposal
        return {
            "proposal_id": proposal.proposal_id,
            "tool_name": proposal.tool_name,
            "tool_version": proposal.tool_version,
            "arguments": dict(proposal.arguments),
            "operation_hash": proposal.operation_hash,
            "user_id": proposal.user_id,
            "tenant_id": proposal.tenant_id,
            "created_at": proposal.created_at,
            "expires_at": proposal.expires_at,
        }

    def _audit(self, context: _Context, result: GatewayResult) -> int | None:
        record = context.record
        end_time = _utc_now()
        if result.status == ACCEPTED:
            result_status = "success"
        elif result.status == REPLAY:
            replay_is_error = isinstance(result.result, Mapping) and result.result.get("status") == "error"
            result_status = "error" if replay_is_error else "success"
        elif result.status in {CONFIRMATION_REQUIRED, IN_PROGRESS, CONFLICT, RECONCILIATION_REQUIRED}:
            result_status = "pending"
        elif result.status == DENIED:
            result_status = "denied"
        else:
            result_status = "error"
        try:
            audit_record = self.audit_store.append(
                {
                    "request_id": record.request_id,
                    "tool_call_id": context.tool_call_id,
                    "tenant_id": record.tenant_id,
                    "user_id": record.user_id,
                    "odoo_user": record.user_id,
                    "tool_name": record.tool_name,
                    "tool_version": record.tool_version,
                    "arguments": dict(record.arguments),
                    "policy_decision": context.policy_decision,
                    "start_time": end_time,
                    "end_time": end_time,
                    "result_status": result_status,
                    "error_code": result.error_code,
                    "idempotency_key": context.idempotency_key,
                    "execution_id": context.execution_id,
                    "external_record_id": (
                        context.replay_result.get("order_id")
                        if context.replay_result
                        else None
                    ),
                }
            )
            return int(audit_record["audit_id"])
        except Exception:
            return None


    def _audit_invalid_envelope(self) -> GatewayResult:
        end_time = _utc_now()
        audit_id = None
        try:
            audit_record = self.audit_store.append(
                {
                    "request_id": "<invalid>",
                    "tool_call_id": str(uuid.uuid4()),
                    "tenant_id": "<unknown>",
                    "user_id": "<unknown>",
                    "odoo_user": "<unknown>",
                    "tool_name": "<unknown>",
                    "tool_version": "",
                    "arguments": {},
                    "policy_decision": "denied",
                    "start_time": end_time,
                    "end_time": end_time,
                    "result_status": "error",
                    "error_code": INVALID_REQUEST,
                }
            )
            audit_id = int(audit_record["audit_id"])
        except Exception:
            pass
        return _empty_invalid_result(audit_id)

def _replace_audit_id(result: GatewayResult, audit_id: int) -> GatewayResult:
    return GatewayResult(
        status=result.status,
        error_code=result.error_code,
        reason=result.reason,
        request_id=result.request_id,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        tool_name=result.tool_name,
        tool_version=result.tool_version,
        policy_decision=result.policy_decision,
        idempotency_key=result.idempotency_key,
        execution_id=result.execution_id,
        result=result.result,
        audit_id=audit_id,
        requires_confirmation=result.requires_confirmation,
        proposal=result.proposal,
        structured_error_override=result.structured_error_override,
    )


def _empty_invalid_result(audit_id: int | None = None) -> GatewayResult:
    return GatewayResult(
        status=VALIDATION_ERROR,
        error_code=INVALID_REQUEST,
        reason="invalid_request_envelope",
        request_id="",
        user_id="",
        tenant_id="",
        tool_name="",
        tool_version="",
        policy_decision="denied",
        idempotency_key=None,
        execution_id=None,
        result=None,
        audit_id=audit_id,
        requires_confirmation=False,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def odoo_client_search_read(client: Any, model: str, domain: list[Any], fields: list[str], limit: int | None) -> list[dict[str, Any]]:
    """Indirect search_read call so tests can stub without importing the client."""
    return client.search_read(model, domain, fields, limit)


def _shape_read_result(tool_name: str, records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Project raw Odoo records into the tool output shape defined by the registry."""
    if tool_name == "customer.search":
        customers = [
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "email": record.get("email"),
                "phone": record.get("phone"),
            }
            for record in records
        ]
        return {"success": True, "customers": customers, "count": len(customers)}
    if tool_name == "customer.get":
        record = records[0]
        customer = {
            "id": record.get("id"),
            "name": record.get("name"),
            "email": record.get("email"),
            "phone": record.get("phone"),
            "street": record.get("street"),
        }
        return {"success": True, "customer": customer}
    if tool_name == "product.search":
        products = [
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "list_price": record.get("list_price"),
                "qty_available": record.get("qty_available"),
            }
            for record in records
        ]
        return {"success": True, "products": products, "count": len(products)}
    if tool_name == "sales.order.get":
        record = records[0]
        partner = record.get("partner_id")
        if isinstance(partner, (list, tuple)):
            partner_id = partner[0] if partner else None
            partner_name = partner[1] if len(partner) > 1 else None
        else:
            partner_id, partner_name = partner, None
        order = {
            "id": record.get("id"),
            "name": record.get("name"),
            "partner_id": partner_id,
            "partner_name": partner_name,
            "state": record.get("state"),
            "amount_total": record.get("amount_total"),
        }
        return {"success": True, "order": order}
    return {"success": True, "records": list(records)}

