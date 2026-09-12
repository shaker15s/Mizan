"""Enterprise Web Cockpit HTTP Server for Agent-Native ERP.

Provides zero-dependency RESTful endpoints and serves the daylight
corporate UI. Delegates all agent orchestration to AgentRuntime and all
governance/mutations to ToolGateway.
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
from pathlib import Path
import socket
import sys
import urllib.parse
import webbrowser
from typing import Any, Mapping

from poc.agent_runtime import AgentResult, AgentRuntime, CONFIRMATION_REQUIRED, CONFIRMED_EXECUTION
from poc.bootstrap import build_runtime
from poc.main import _build_payload
from poc.tool_contracts import get_registry

LOGGER = logging.getLogger("erp.web_server")
WEB_DIR = Path(__file__).resolve().parent / "web"


class ERPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler serving REST APIs and static daylight web assets."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    @property
    def runtime(self) -> AgentRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    def _set_headers(self, status: int = 200, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._set_headers(200)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode("utf-8"))
        except Exception as err:
            LOGGER.warning("Malformed JSON received: %s", err)
            return {}

    def _send_json(self, status: int, data: Mapping[str, Any]) -> None:
        self._set_headers(status, "application/json; charset=utf-8")
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/audit":
            self._handle_get_audit(parsed.query)
            return

        if path == "/api/telemetry":
            self._handle_get_telemetry()
            return

        if path == "/api/tools":
            self._handle_get_tools()
            return

        target_file = WEB_DIR / path.lstrip("/")
        if path == "/" or not target_file.exists():
            # Default fallback to index.html
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/chat":
            self._handle_post_chat()
            return

        if path == "/api/confirm":
            self._handle_post_confirm()
            return

        if path == "/api/decline":
            self._handle_post_decline()
            return

        if path == "/api/test/replay":
            self._handle_post_test_replay()
            return

        self._send_json(404, {"error": "Not Found", "path": path})

    def _handle_get_tools(self) -> None:
        registry = get_registry()
        tools = []
        for name in registry.names():
            contract = registry.get(name)
            tools.append({
                "name": name,
                "version": contract.get("tool_version", "1.0.0"),
                "description": contract.get("description", ""),
                "readOnly": contract.get("readOnly", True),
                "requiresConfirmation": contract.get("requiresConfirmation", False),
                "inputSchema": contract.get("inputSchema", {}),
            })
        self._send_json(200, {"success": True, "count": len(tools), "tools": tools})

    def _handle_get_audit(self, query_str: str) -> None:
        params = urllib.parse.parse_qs(query_str)
        limit = int(params.get("limit", [50])[0])
        audit_store = self.runtime.gateway.audit_store

        records = audit_store.list(limit=limit)
        chain_verification = audit_store.verify_chain()

        self._send_json(200, {
            "success": True,
            "chain_valid": chain_verification.get("valid", False),
            "verification": chain_verification,
            "count": len(records),
            "records": records,
        })

    def _handle_get_telemetry(self) -> None:
        odoo_url = os.environ.get("ODOO_URL", "http://localhost:8069")
        odoo_online = False
        try:
            parsed_odoo = urllib.parse.urlparse(odoo_url)
            host = parsed_odoo.hostname or "localhost"
            port = parsed_odoo.port or 8069
            with socket.create_connection((host, port), timeout=0.8):
                odoo_online = True
        except Exception:
            odoo_online = False

        llm_model = os.environ.get("POC_LLM_MODEL", "claude-haiku-4-20250514")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8082")

        self._send_json(200, {
            "success": True,
            "status": "online",
            "odoo": {
                "online": odoo_online,
                "url": odoo_url,
                "database": os.environ.get("ODOO_DATABASE", "poc_test"),
            },
            "model": {
                "name": llm_model,
                "provider": "Free Claude Code (Proxy)" if "127.0.0.1" in base_url or "localhost" in base_url else "Anthropic Direct",
                "endpoint": base_url,
            },
            "security": {
                "user_id": self.runtime.user_id,
                "tenant_id": self.runtime.tenant_id,
                "role": "R1_SALES (Full Sales Operator)",
                "idempotency_enforced": True,
                "zero_direct_credentials": True,
            },
        })

    def _handle_post_chat(self) -> None:
        body = self._read_json_body()
        message = body.get("message", "").strip()
        if not message:
            self._send_json(400, {"success": False, "error": {"code": "EMPTY_MESSAGE", "message": "الرسالة فارغة."}})
            return

        try:
            result = self.runtime.process(message)
            payload = _build_payload(result)
            self._send_json(200, payload)
        except Exception as err:
            LOGGER.exception("Error processing message: %s", err)
            self._send_json(500, {
                "success": False,
                "status": "server_error",
                "response_ar": "حدث خطأ غير متوقع في الخادم أثناء معالجة الطلب.",
                "error": {"code": "SERVER_ERROR", "message": str(err)},
            })

    def _handle_post_confirm(self) -> None:
        body = self._read_json_body()
        proposal_id = body.get("proposal_id", "").strip()
        if not proposal_id:
            self._send_json(400, {"success": False, "error": {"code": "MISSING_PROPOSAL_ID", "message": "معرف المقترح مفقود."}})
            return

        try:
            result = self.runtime.confirm(proposal_id)
            payload = _build_payload(result)
            self._send_json(200, payload)
        except Exception as err:
            LOGGER.exception("Error confirming proposal: %s", err)
            self._send_json(500, {
                "success": False,
                "status": "server_error",
                "response_ar": "حدث خطأ أثناء اعتماد وتنفيذ المقترح.",
                "error": {"code": "CONFIRM_ERROR", "message": str(err)},
            })

    def _handle_post_decline(self) -> None:
        body = self._read_json_body()
        proposal_id = body.get("proposal_id", "").strip()
        if not proposal_id:
            self._send_json(400, {"success": False, "error": {"code": "MISSING_PROPOSAL_ID", "message": "معرف المقترح مفقود."}})
            return

        try:
            result = self.runtime.decline(proposal_id)
            payload = _build_payload(result)
            self._send_json(200, payload)
        except Exception as err:
            LOGGER.exception("Error declining proposal: %s", err)
            self._send_json(500, {
                "success": False,
                "status": "server_error",
                "response_ar": "حدث خطأ أثناء إلغاء المقترح.",
                "error": {"code": "DECLINE_ERROR", "message": str(err)},
            })

    def _handle_post_test_replay(self) -> None:
        """Simulate an idempotency conflict replay attack demonstration."""
        body = self._read_json_body()
        key = body.get("idempotency_key", "a" * 32)
        record = self.runtime.gateway.idempotency_store.get(key, self.runtime.tenant_id or "", self.runtime.user_id or "")

        self._send_json(409, {
            "success": False,
            "status": "conflict",
            "error": {
                "code": "IDEMPOTENCY_CONFLICT",
                "message": "تم رفض العملية لمنع تكرار القيد في Odoo 19 (Idempotency Key Conflict).",
                "details": {
                    "idempotency_key": key,
                    "simulated_protection": "Duplicate Prevention Guard Active",
                    "existing_state": record.state if record else "recorded",
                },
            },
            "response_ar": "تم حجب العملية المكررة بنجاح وحماية قاعدة بيانات Odoo من التكرار.",
        })


class ERPWebServer(http.server.ThreadingHTTPServer):
    """Multi-threaded HTTP server carrying the active AgentRuntime."""

    def __init__(self, server_address: tuple[str, int], runtime: AgentRuntime) -> None:
        self.runtime = runtime
        super().__init__(server_address, ERPRequestHandler)


def run_server(port: int = 8080, open_browser: bool = False, runtime: AgentRuntime | None = None) -> None:
    """Start the ERP Web Cockpit Server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    resolved_runtime = runtime or build_runtime()
    server_address = ("0.0.0.0", port)
    httpd = ERPWebServer(server_address, resolved_runtime)

    url = f"http://localhost:{port}"
    print(f"\n=======================================================")
    print(f"🚀 Agent-Native ERP Daylight Web Cockpit running at:")
    print(f"👉 {url}")
    print(f"=======================================================\n")

    if open_browser:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ERP Web Cockpit Server...")
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent-Native ERP Web Cockpit Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind server (default: 8080)")
    parser.add_argument("--open", action="store_true", help="Open default web browser on launch")
    args = parser.parse_args()

    run_server(port=args.port, open_browser=args.open)


if __name__ == "__main__":
    main()
