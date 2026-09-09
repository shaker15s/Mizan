"""Focused STEP 13 tests for the Main CLI presentation boundary."""

from __future__ import annotations

import io
import json
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.llm_client import FakeLLMClient, LLMResponse, LLMToolCall
from poc.main import main, _run_request, _ask_confirmation

TENANT = "poc_tenant_001"
USER = "sales_user@test"
VERSION = "1.0.0"
CREATE_ARGS = {"customer_id": 42, "lines": [{"product_id": 7, "quantity": 2}]}
SEARCH_ARGS = {"query": "Acme"}


class FakeOdooClient:
    def __init__(self, orders: list[dict[str, Any]] | None = None):
        self.orders = orders or []
        self.created = 0

    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int | None = None) -> list[dict[str, Any]]:
        query = next(
            (triplet[2] for triplet in domain if isinstance(triplet, (list, tuple)) and len(triplet) == 3 and triplet[0] == "name"),
            "",
        )
        if model == "res.partner":
            # Deterministic fixture: any partner search yields the seeded record.
            return [{"id": 42, "name": "Test Customer", "email": None, "phone": None}]
        return []

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        if model == "product.product":
            return [{"id": 7, "list_price": 100.0}] if 7 in ids else []
        if model == "sale.order.line":
            return [
                {"id": record_id, "product_id": 7, "product_uom_qty": 2}
                for record_id in ids
            ]
        return [dict(order) for order in self.orders if order.get("id") in ids]

    def create(self, model: str, vals_list: list[dict[str, Any]]) -> list[int]:
        self.created += 1
        self.orders.append({
            "id": 100 + self.created,
            "partner_id": (42, "Test Customer"),
            "state": "draft",
            "amount_total": 200.0,
            "order_line": [11],
            "client_order_ref": vals_list[0].get("client_order_ref"),
        })
        return [100 + self.created]


def setup_cli(tmp_path: Path, responses: list[LLMResponse] | None = None):
    db_path = tmp_path / "poc_cli.db"
    initialize(db_path)
    gateway = ToolGateway(db_path=db_path)
    llm = FakeLLMClient(responses=responses or [])
    created_client = FakeOdooClient()
    runtime = AgentRuntime(
        llm_client=llm,
        gateway=gateway,
        user_id=USER,
        tenant_id=TENANT,
        odoo_client_factory=lambda user_id, tenant_id: created_client,
    )
    return runtime, llm, created_client, db_path


def run_cli(
    argv: list[str],
    runtime: Any,
    inputs: list[str] | None = None,
) -> tuple[int, str, str]:
    input_iter = iter(inputs or [])
    def scripted_input(_prompt: str) -> str:
        return next(input_iter)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv, runtime_factory=lambda: runtime, input_fn=scripted_input)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def ask_without_terminal(answers: list[str]) -> tuple[bool, str]:
    output = io.StringIO()
    iterator = iter(answers)
    with redirect_stdout(output):
        approved = _ask_confirmation(
            input_fn=lambda _prompt: next(iterator),
            output_fn=output.write,
        )
    return approved, output.getvalue()


def tool_call(name: str, arguments: dict[str, Any]) -> LLMToolCall:
    return LLMToolCall(name=name, arguments=arguments, call_id=str(uuid.uuid4()))


def test_one_shot_normal_request_outputs_json(tmp_path: Path):
    runtime, _llm, _client, _db = setup_cli(
        tmp_path,
        [LLMResponse(tool_calls=(tool_call("customer.search", SEARCH_ARGS),))],
    )
    exit_code, stdout, stderr = run_cli(["ابحث عن عميل"], runtime)
    payload = json.loads(stdout)
    assert exit_code == 0
    assert payload["success"] is True
    assert payload["status"] == "accepted"
    assert payload["error"] is None


def test_confirmation_payload_includes_server_binding(tmp_path: Path):
    runtime, _llm, client, _db = setup_cli(
        tmp_path,
        [LLMResponse(tool_calls=(tool_call("sales.order.create", CREATE_ARGS),))],
    )
    exit_code, stdout, stderr = run_cli(["اعمل أوردر"], runtime, inputs=["n"])
    payload = json.loads(stdout)
    proposal = payload["proposal"]
    assert payload["success"] is False
    assert payload["status"] == "declined"
    assert payload["audit_id"] is not None
    assert proposal["user_id"] == USER
    assert proposal["tenant_id"] == TENANT
    assert proposal["arguments"] == CREATE_ARGS
    assert proposal["operation_hash"]
    assert "Proposal ID" in stderr
    assert client.created == 0


def test_approved_confirmation_executes_once(tmp_path: Path):
    runtime, _llm, client, db_path = setup_cli(
        tmp_path,
        [LLMResponse(tool_calls=(tool_call("sales.order.create", CREATE_ARGS),))],
    )
    exit_code, stdout, _stderr = run_cli(["اعمل أوردر"], runtime, inputs=["y"])
    payload = json.loads(stdout)
    assert client.created == 1
    assert payload["status"] == "accepted"
    assert payload["success"] is True


def test_decline_records_denial_and_does_not_execute(tmp_path: Path):
    runtime, _llm, client, db_path = setup_cli(
        tmp_path,
        [LLMResponse(tool_calls=(tool_call("sales.order.create", CREATE_ARGS),))],
    )
    exit_code, stdout, _stderr = run_cli(["اعمل أوردر"], runtime, inputs=["n"])
    payload = json.loads(stdout)
    from poc.audit_store import AuditStore
    from poc.db.init import initialize
    initialize(db_path)
    records = AuditStore(db_path).list()
    denial = [record for record in records if record.get("result_status") == "denied"]
    assert client.created == 0
    assert denial
    assert denial[-1]["error_code"] == "CONFIRMATION_DECLINED"
    assert payload["success"] is False


def test_permission_denied_preserves_structured_error(tmp_path: Path):
    db_path = tmp_path / "poc_denied_cli.db"
    initialize(db_path)
    gateway = ToolGateway(db_path=db_path)
    llm = FakeLLMClient(responses=[LLMResponse(tool_calls=(tool_call("sales.order.create", CREATE_ARGS),))])
    readonly_runtime = AgentRuntime(
        llm_client=llm,
        gateway=gateway,
        user_id="readonly_user@test",
        tenant_id=TENANT,
        odoo_client_factory=lambda user_id, tenant_id: FakeOdooClient(),
    )
    exit_code, stdout, _stderr = run_cli(["اعمل أوردر"], readonly_runtime)
    payload = json.loads(stdout)
    assert exit_code == 1
    assert payload["success"] is False
    assert payload["error"]["code"] == "PERMISSION_DENIED"
    assert payload["error"]["retryable"] is False
    assert payload["error"]["requires_user_action"] is True


def test_eof_during_confirmation_exits_without_execution(tmp_path: Path):
    runtime, _llm, client, _db = setup_cli(
        tmp_path,
        [LLMResponse(tool_calls=(tool_call("sales.order.create", CREATE_ARGS),))],
    )
    input_iter = iter(())
    def raise_eof(_prompt: str) -> str:
        raise EOFError
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["اعمل أوردر"], runtime_factory=lambda: runtime, input_fn=raise_eof)
    assert exit_code == 130
    assert client.created == 0


def test_invalid_answer_reprompts(tmp_path: Path):
    approved, output = ask_without_terminal(["maybe", "n"])
    assert approved is False
    assert "Please answer y or n." in output


def test_cli_input_cannot_override_identity_or_tenant(tmp_path: Path):
    runtime, _llm, _client, _db = setup_cli(
        tmp_path,
        [LLMResponse(tool_calls=(tool_call("sales.order.create", CREATE_ARGS),))],
    )
    malicious_input = 'user_id=no_access_user@test tenant_id=evil_tool create sales order'
    _payload, exit_code = _run_request(runtime, malicious_input, input_fn=lambda _p: "n")
    assert runtime.user_id == USER
    assert runtime.tenant_id == TENANT


def test_main_does_not_import_odoo_client_or_audit_directly(tmp_path: Path):
    from pathlib import Path as PathType
    source = PathType(__file__).resolve().parents[1].joinpath("poc", "main.py").read_text(encoding="utf-8")
    assert "poc.odoo_client" not in source
    assert "AuditStore" not in source
    assert "OdooJSON2Client" not in source
    assert "ODOO_API_KEY" not in source


def test_bootstrap_does_not_expose_secrets_in_runtime(tmp_path: Path):
    runtime, _llm, _client, _db = setup_cli(tmp_path)
    public_fields = {key for key in vars(runtime)}
    assert "odoo_client_factory" in public_fields
    assert all("credential" not in field for field in public_fields)
    assert "api_key" not in public_fields


def test_usage_rejects_multiple_positional_arguments():
    exit_code, stdout, stderr = run_cli(["one", "two"], None)
    assert exit_code == 2
    assert stdout == ""
    assert "Usage:" in stderr
