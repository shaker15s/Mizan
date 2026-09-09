"""Thin synchronous Odoo JSON-2 HTTP client.

This module is intentionally transport-only. Authorization policy, confirmation,
idempotency, audit, and business rules belong to later POC layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 10.0
_ERROR_DEBUG_KEYS = ("debug",)


class OdooClientError(Exception):
    """Base exception for safe Odoo client failures."""

    def __init__(self, name: str, message: str, status: int | None = None) -> None:
        self.name = name
        self.message = message
        self.status = status
        super().__init__(f"{name}: {message}")


class OdooConnectionError(OdooClientError):
    """The Odoo server could not be reached."""


class OdooTimeoutError(OdooConnectionError):
    """Odoo did not respond within the configured timeout."""


class OdooAuthenticationError(OdooClientError):
    """The API key was rejected or is invalid."""


class OdooAuthorizationError(OdooClientError):
    """Odoo denied the operation for the authenticated user."""


class OdooValidationError(OdooClientError):
    """The request payload or values were rejected as invalid."""


class OdooNotFoundError(OdooClientError):
    """The requested record or endpoint does not exist."""


class OdooServerError(OdooClientError):
    """Odoo returned an unexpected server-side failure."""


@dataclass(frozen=True)
class OdooConfig:
    """Immutable connection settings for one Odoo API identity."""

    url: str
    database: str
    api_key: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def _safe_error_name(status: int) -> str:
    return {
        401: "authentication_error",
        403: "authorization_error",
        404: "not_found",
        422: "validation_error",
    }.get(status, "server_error")


def _extract_error_fields(body: Any) -> tuple[str, str]:
    if not isinstance(body, dict):
        return _safe_error_name(500), "Odoo returned a malformed error response."

    candidates: list[dict[str, Any]] = []
    for candidate in (body.get("data"), body.get("error"), body):
        if isinstance(candidate, dict):
            candidates.append(candidate)

    name = "odoo_error"
    message = "Odoo returned an error response."
    for candidate in candidates:
        if isinstance(candidate.get("name"), str) and candidate["name"].strip():
            name = candidate["name"].strip()
            break
    for candidate in candidates:
        if isinstance(candidate.get("message"), str) and candidate["message"].strip():
            message = candidate["message"].strip()
            break
    if isinstance(body.get("data"), dict) and isinstance(body["data"].get("debug"), str):
        candidates.append(body["data"])
    return name, message


def _raise_for_status(status: int, body: Any) -> None:
    if 200 <= status < 300:
        return
    name, message = _extract_error_fields(body)
    if status == 401:
        raise OdooAuthenticationError(name, message, status)
    if status == 403:
        raise OdooAuthorizationError(name, message, status)
    if status == 422:
        raise OdooValidationError(name, message, status)
    if status == 404:
        raise OdooNotFoundError(name, message, status)
    raise OdooServerError(name, message, status)


def _strip_debug_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_debug_fields(child)
            for key, child in value.items()
            if key.lower() not in _ERROR_DEBUG_KEYS
        }
    if isinstance(value, list):
        return [_strip_debug_fields(item) for item in value]
    return value


def _parse_json_response(response: httpx.Response) -> Any:
    try:
        parsed = response.json()
    except ValueError as error:
        raise OdooServerError(
            "malformed_response",
            "Odoo returned a non-JSON response.",
            response.status_code,
        ) from error
    return _strip_debug_fields(parsed)


class OdooJSON2Client:
    """Synchronous client exposing only the JSON-2 primitives used by the POC."""

    def __init__(self, config: OdooConfig) -> None:
        if not config.url or not config.database or not config.api_key:
            raise ValueError("OdooConfig requires url, database, and api_key.")
        self._base_url = config.url.rstrip("/")
        self._database = config.database
        self._api_key = config.api_key
        self._timeout_seconds = config.timeout_seconds

    def _post(self, model: str, method: str, payload: dict[str, Any]) -> Any:
        url = f"{self._base_url}/json/2/{model}/{method}"
        headers = {
            "Authorization": f"bearer {self._api_key}",
            "X-Odoo-Database": self._database,
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=self._timeout_seconds)
        except httpx.TimeoutException as error:
            raise OdooTimeoutError("timeout", "The Odoo request timed out.", None) from error
        except httpx.HTTPError as error:
            raise OdooConnectionError("connection_error", "Could not reach the Odoo server.", None) from error

        body = _parse_json_response(response)
        _raise_for_status(response.status_code, body)
        return body

    def search_read(
        self,
        model: str,
        domain: list[Any],
        fields: list[str],
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search records and return selected fields."""
        if limit is not None and limit < 0:
            raise OdooValidationError("invalid_limit", "limit must not be negative.", None)
        payload: dict[str, Any] = {"domain": domain, "fields": fields}
        if limit is not None:
            payload["limit"] = limit
        result = self._post(model, "search_read", payload)
        return self._require_list(result)

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        """Read records by database IDs."""
        payload = {"ids": ids, "fields": fields}
        result = self._post(model, "read", payload)
        return self._require_list(result)

    def create(self, model: str, vals_list: list[dict[str, Any]]) -> list[int]:
        """Create one or more records and return their IDs."""
        payload = {"vals_list": vals_list}
        result = self._post(model, "create", payload)
        return self._require_list(result)

    @staticmethod
    def _require_list(result: Any) -> list[Any]:
        if isinstance(result, list):
            return result
        raise OdooServerError("malformed_response", "Odoo returned an unexpected response shape.", None)