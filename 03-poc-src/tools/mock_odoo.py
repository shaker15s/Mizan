"""Mock Odoo (JSON-2) — a deterministic ERP double for local demos and the browser.

Why this exists: the cockpit and the eval harness are supposed to be exercised
end-to-end without a full Odoo install. The harness already ships a seeded ERP
double (``poc.harness.environment.SeededOdoo``); this file only adds an HTTP
façade so *any* transport-level client — the browser via the cockpit, tools/
step2_smoke_test.py, curl — can talk to exactly the same data the eval dataset
asserts on. It is a development tool: no auth backend, no business logic, no
persistence beyond the process. Never point a real deployment at it.

Usage
-----
    PYTHONPATH=. .venv/bin/python tools/mock_odoo.py --port 8069
    POC_USER_ID=sales_user@test ODOO_URL=http://127.0.0.1:8069 \\
      ODOO_DB=poc_test ODOO_API_KEY=mock-key \\
      .venv/bin/python -m poc.web_server --port 8080 --simulate

Wire contract implemented (same as poc/odoo_client.OdooJSON2Client expects):

    POST /json/2/<model>/<method>
      headers:  Authorization: bearer <key>, X-Odoo-Database: <db>
      body:     {"domain": [...], "fields": [...], "limit": n}   search_read
                {"ids": [...], "fields": [...]}                  read
                {"vals_list": [...]}                             create
      response: JSON array on 200, {"error": {...}} on 4xx/5xx
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

sys.path.insert(0, ".")

from poc.harness.environment import SEED_PARTNERS, SEED_PRODUCTS, SeededOdoo  # noqa: E402
from poc.odoo_client import OdooClientError  # noqa: E402

LOGGER = logging.getLogger("mizan.mock_odoo")
ALLOWED_METHODS = {"search_read", "read", "create"}
ALLOWED_MODELS = {"res.partner", "product.product", "sale.order", "sale.order.line"}


class MockOdoo(SeededOdoo):
    """Seeded double + the small amount of state a live session expects."""

    def fields_get(self, model: str, _fields: Any = None) -> dict[str, Any]:  # pragma: no cover - convenience
        return {"id": {"string": "ID", "type": "integer"}}


class MockOdooHandler(BaseHTTPRequestHandler):
    server_version = "MockOdoo/1.0"
    protocol_version = "HTTP/1.1"

    erp: MockOdoo
    database: str = "poc_test"
    api_key: str = "mock-key"
    quiet: bool = False

    # --- plumbing ---------------------------------------------------------
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        if not self.quiet:
            LOGGER.info("%s %s", self.address_string(), format % args)

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Mizan-Mock", "1")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, name: str, message: str) -> None:
        self._send(status, {"error": {"code": status, "name": name, "message": message}})

    # --- routes -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        if self.path.split("?")[0] in {"/healthz", "/json/2/meta/health"}:
            self._send(
                HTTPStatus.OK,
                {
                    "result": {
                        "status": "mock-odoo",
                        "database": self.database,
                        "partners": len(self.erp.partners),
                        "products": len(self.erp.products),
                        "orders": len(self.erp.orders),
                        "rpc_calls": self.erp.rpc_calls,
                        "seed": {"partners": sorted(self.erp.partners), "products": sorted(self.erp.products)},
                    }
                },
            )
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", f"no route {self.path}")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        auth = (self.headers.get("Authorization") or "").strip()
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            self._error(HTTPStatus.UNAUTHORIZED, "authentication_error", "missing bearer token")
            return
        if self.api_key and auth[7:].strip() != self.api_key and self.api_key != "mock-key":
            self._error(HTTPStatus.UNAUTHORIZED, "authentication_error", "unknown api key")
            return
        database = (self.headers.get("X-Odoo-Database") or self.database).strip()
        if database and self.database and database != self.database:
            self._error(HTTPStatus.NOT_FOUND, "database_not_found", f"no database {database!r} on this mock")
            return

        parts = [segment for segment in self.path.split("?")[0].split("/") if segment]
        if len(parts) != 4 or parts[0] != "json" or parts[1] != "2":
            self._error(HTTPStatus.NOT_FOUND, "not_found", f"no route {self.path}")
            return
        model, method = parts[2], parts[3]
        if method not in ALLOWED_METHODS:
            self._error(HTTPStatus.NOT_FOUND, "method_not_found", f"mock supports {sorted(ALLOWED_METHODS)}, not {method!r}")
            return
        if model not in ALLOWED_MODELS:
            self._error(HTTPStatus.NOT_FOUND, "model_not_found", f"mock has no model {model!r}")
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "validation_error", "malformed JSON body")
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "validation_error", "body must be a JSON object")
            return

        try:
            result = self._dispatch(model, method, body)
        except OdooClientError as error:
            self._send(error.status or HTTPStatus.UNPROCESSABLE_ENTITY, {"error": {"name": error.name, "message": error.message}})
            return
        except Exception as error:  # pragma: no cover - defensive
            LOGGER.exception("mock odoo failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "server_error", f"{type(error).__name__}: {error}")
            return
        self._send(HTTPStatus.OK, result)

    def _dispatch(self, model: str, method: str, body: dict[str, Any]) -> Any:
        if method == "search_read":
            return self.erp.search_read(
                model,
                list(body.get("domain") or []),
                list(body.get("fields") or []),
                body.get("limit"),
            )
        if method == "read":
            return self.erp.read(model, [int(row) for row in body.get("ids") or []], list(body.get("fields") or []))
        return self.erp.create(model, list(body.get("vals_list") or []))


def serve(*, port: int, host: str, database: str, api_key: str, quiet: bool) -> None:
    erp = MockOdoo()
    MockOdooHandler.erp = erp
    MockOdooHandler.database = database
    MockOdooHandler.api_key = api_key
    MockOdooHandler.quiet = quiet
    server = ThreadingHTTPServer((host, port), MockOdooHandler)
    server.daemon_threads = True
    print(
        "\n".join(
            [
                "",
                "  🧪 MIZAN MOCK ODOO — deterministic ERP double (dev only)",
                f"  → http://{host}:{port}   database: {database}",
                f"  seeds: {len(SEED_PARTNERS)} partners {sorted(SEED_PARTNERS)}, {len(SEED_PRODUCTS)} products {sorted(SEED_PRODUCTS)}, order 100",
                "  ⚠️  مش Odoo حقيقي: مفيش persistence ولا access rules — للعرض والاختبار بس.",
                "",
            ]
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  إيقاف mock Odoo…", flush=True)
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic mock of Odoo 19's JSON-2 API (dev tool)")
    parser.add_argument("--port", type=int, default=8069)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--database", default="poc_test")
    parser.add_argument("--api-key", default="mock-key", help="bearer token to accept ('mock-key' accepts anything)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")
    serve(port=args.port, host=args.host, database=args.database, api_key=args.api_key, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
