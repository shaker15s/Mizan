"""Unit and integration tests for the ERP Web Cockpit Server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import urllib.request
import urllib.error
import pytest

from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.llm_client import FakeLLMClient, LLMResponse, LLMToolCall
from poc.web_server import ERPWebServer


class SeededFakeOdoo:
    def __init__(self) -> None:
        self.partners = {
            42: {"id": 42, "name": "محمد أحمد", "email": "m@example.com"},
        }

    def search_read(self, model: str, domain: list, fields: list, limit: int | None = None) -> list[dict]:
        return list(self.partners.values())


@pytest.fixture
def test_server(tmp_path: Path, monkeypatch):
    # Enable development mode for tests so /api/test/replay etc. are reachable.
    monkeypatch.setenv("MIZAN_DEV", "1")
    db_path = tmp_path / "web_test.db"
    initialize(db_path)
    gateway = ToolGateway(db_path=db_path)
    fake_odoo = SeededFakeOdoo()
    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text=None,
                tool_calls=[
                    LLMToolCall(
                        name="customer.search",
                        arguments={"query": "محمد"},
                        call_id="call_001",
                    )
                ],
            )
        ]
    )
    runtime = AgentRuntime(
        llm_client=llm,
        gateway=gateway,
        user_id="sales_user@test",
        tenant_id="poc_tenant_001",
        odoo_client_factory=lambda u, t: fake_odoo,
    )

    server = ERPWebServer(("127.0.0.1", 0), runtime)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, server

    server.shutdown()
    server.server_close()


def http_get(url: str) -> tuple[int, dict | str, dict]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            headers = dict(resp.headers)
            try:
                data = json.loads(body)
            except Exception:
                data = body
            return resp.status, data, headers
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8")
        try:
            data = json.loads(body)
        except Exception:
            data = body
        return err.code, data, dict(err.headers)


def http_post(url: str, payload: dict) -> tuple[int, dict, dict]:
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body), dict(resp.headers)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8")
        return err.code, json.loads(body), dict(err.headers)


def test_static_files_served(test_server):
    base_url, _ = test_server

    # Index page
    status, body, headers = http_get(f"{base_url}/")
    assert status == 200
    assert "Agent-Native ERP" in body

    # Stylesheet
    status, body, headers = http_get(f"{base_url}/styles.css")
    assert status == 200
    assert "--font-sans" in body

    # App script
    status, body, headers = http_get(f"{base_url}/app.js")
    assert status == 200
    assert "fetchTelemetry" in body


def test_api_tools_registry(test_server):
    base_url, _ = test_server
    status, data, _ = http_get(f"{base_url}/api/tools")
    assert status == 200
    assert data["success"] is True
    assert data["count"] == 5
    tool_names = [t["name"] for t in data["tools"]]
    assert "customer.search" in tool_names
    assert "sales.order.create" in tool_names


def test_api_telemetry(test_server):
    base_url, _ = test_server
    status, data, _ = http_get(f"{base_url}/api/telemetry")
    assert status == 200
    assert data["success"] is True
    assert data["status"] == "online"
    assert "odoo" in data
    assert "security" in data
    assert data["security"]["role"] == "R1_SALES (Full Sales Operator)"


def test_api_audit_ledger(test_server):
    base_url, _ = test_server
    status, data, _ = http_get(f"{base_url}/api/audit")
    assert status == 200
    assert data["success"] is True
    assert data["chain_valid"] is True
    assert isinstance(data["records"], list)


def test_api_chat_empty_message(test_server):
    base_url, _ = test_server
    status, data, _ = http_post(f"{base_url}/api/chat", {"message": "   "})
    assert status == 400
    assert data["success"] is False
    assert data["error"]["code"] == "EMPTY_MESSAGE"


def test_api_chat_read_success(test_server):
    base_url, _ = test_server
    status, data, _ = http_post(f"{base_url}/api/chat", {"message": "ابحث عن محمد"})
    assert status == 200
    assert data["status"] == "accepted"
    assert data["result"] is not None
    assert "customers" in data["result"]
    assert len(data["result"]["customers"]) == 1


def test_api_confirm_and_decline_validation(test_server):
    base_url, _ = test_server
    # Confirm without proposal_id
    status, data, _ = http_post(f"{base_url}/api/confirm", {})
    assert status == 400
    assert data["error"]["code"] == "MISSING_PROPOSAL_ID"

    # Decline without proposal_id
    status, data, _ = http_post(f"{base_url}/api/decline", {})
    assert status == 400
    assert data["error"]["code"] == "MISSING_PROPOSAL_ID"


def test_api_replay_simulation(test_server):
    base_url, _ = test_server
    status, data, _ = http_post(f"{base_url}/api/test/replay", {"idempotency_key": "c" * 32})
    assert status == 409
    assert data["status"] == "conflict"
    assert data["error"]["code"] == "IDEMPOTENCY_CONFLICT"
