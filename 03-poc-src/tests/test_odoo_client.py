"""Unit tests for the thin Odoo JSON-2 client using mocked HTTP responses."""

from __future__ import annotations

import httpx
import pytest

from poc.odoo_client import (
    OdooAuthenticationError,
    OdooAuthorizationError,
    OdooClientError,
    OdooConfig,
    OdooJSON2Client,
    OdooServerError,
    OdooTimeoutError,
    OdooValidationError,
)

CONFIG = OdooConfig(
    url="http://localhost:8069",
    database="poc_test",
    api_key="unit-test-key",
    timeout_seconds=0.1,
)


def client() -> OdooJSON2Client:
    return OdooJSON2Client(CONFIG)


def patch_post(monkeypatch, response):
    def fake_post(url, **kwargs):
        return response

    monkeypatch.setattr(httpx, "post", fake_post)


def test_search_read_success(monkeypatch):
    patch_post(monkeypatch, httpx.Response(200, json=[{"id": 9, "name": "Acme"}]))
    result = client().search_read("res.partner", [["id", "=", 9]], ["name"], limit=1)
    assert result == [{"id": 9, "name": "Acme"}]


def test_read_success(monkeypatch):
    patch_post(monkeypatch, httpx.Response(200, json=[{"id": 9, "name": "Acme"}]))
    result = client().read("res.partner", [9], ["name"])
    assert result == [{"id": 9, "name": "Acme"}]


def test_create_success(monkeypatch):
    patch_post(monkeypatch, httpx.Response(200, json=[27]))
    result = client().create("sale.order", [{"partner_id": 9}])
    assert result == [27]


def test_authentication_failure(monkeypatch):
    body = {"error": {"name": "Access Denied", "message": "Invalid token.", "debug": "secret"}}
    patch_post(monkeypatch, httpx.Response(401, json=body))
    with pytest.raises(OdooAuthenticationError) as exc:
        client().search_read("res.partner", [], ["name"])
    assert exc.value.status == 401
    assert "secret" not in str(exc.value)


def test_authorization_failure(monkeypatch):
    body = {
        "error": {
            "name": "odoo.exceptions.AccessError",
            "message": "You are not allowed to create sale.order records.",
            "debug": "hidden",
        }
    }
    patch_post(monkeypatch, httpx.Response(403, json=body))
    with pytest.raises(OdooAuthorizationError) as exc:
        client().create("sale.order", [{"partner_id": 9}])
    assert exc.value.name == "odoo.exceptions.AccessError"
    assert exc.value.status == 403
    assert "hidden" not in str(exc.value)


def test_validation_failure(monkeypatch):
    body = {"error": {"name": "ValidationError", "message": "Invalid value."}}
    patch_post(monkeypatch, httpx.Response(422, json=body))
    with pytest.raises(OdooValidationError):
        client().create("sale.order", [{"partner_id": None}])


def test_timeout(monkeypatch):
    def fake_post(url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(OdooTimeoutError):
        client().search_read("res.partner", [], ["name"])


def test_malformed_json_response(monkeypatch):
    patch_post(monkeypatch, httpx.Response(200, content=b"<html>"))
    with pytest.raises(OdooClientError) as exc:
        client().search_read("res.partner", [], ["name"])
    assert isinstance(exc.value, OdooServerError)


def test_malformed_error_response_is_safe(monkeypatch):
    patch_post(monkeypatch, httpx.Response(500, content=b"internal failure"))
    with pytest.raises(OdooClientError) as exc:
        client().search_read("res.partner", [], ["name"])
    assert "internal failure" not in str(exc.value)
    assert exc.value.status == 500


def test_success_response_strips_debug_fields(monkeypatch):
    patch_post(
        monkeypatch,
        httpx.Response(200, json=[{"id": 9, "name": "Acme", "debug": "hidden"}]),
    )
    result = client().search_read("res.partner", [], ["name"])
    assert result == [{"id": 9, "name": "Acme"}]


def test_negative_limit_rejected_without_http_call(monkeypatch):
    patch_post(monkeypatch, httpx.Response(200, json=[]))
    with pytest.raises(OdooValidationError):
        client().search_read("res.partner", [], ["name"], limit=-1)