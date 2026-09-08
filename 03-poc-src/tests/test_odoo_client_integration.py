"""Live Odoo integration tests for the thin JSON-2 client."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from poc.odoo_client import (
    OdooAuthorizationError,
    OdooAuthenticationError,
    OdooConfig,
    OdooJSON2Client,
    OdooTimeoutError,
)

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def client(env: dict[str, str], key_name: str = "ODOO_API_KEY_SALES_USER") -> OdooJSON2Client:
    return OdooJSON2Client(
        OdooConfig(
            url=env["ODOO_URL"],
            database=env["ODOO_DATABASE"],
            api_key=env[key_name],
        )
    )


def make_order_vals(partner_id: int, product_id: int, marker: str) -> list[dict]:
    return [
        {
            "partner_id": partner_id,
            "client_order_ref": marker,
            "order_line": [[0, 0, {"product_id": product_id, "product_uom_qty": 1}]],
        }
    ]


@pytest.mark.integration
def test_customer_and_product_search():
    env = load_env(ENV_PATH)
    odoo = client(env)
    partners = odoo.search_read("res.partner", [["name", "=", "Acme Corporation"]], ["id", "name"])
    assert len(partners) == 1
    products = odoo.search_read("product.product", [], ["id", "name"], limit=1)
    assert len(products) == 1


@pytest.mark.integration
def test_order_create_read_and_provenance_marker():
    env = load_env(ENV_PATH)
    odoo = client(env)
    partners = odoo.search_read("res.partner", [["name", "=", "Acme Corporation"]], ["id"])
    assert partners
    products = odoo.search_read("product.product", [], ["id"], limit=1)
    assert products
    marker = "STEP3-PROBE-" + uuid.uuid4().hex
    created_ids = odoo.create("sale.order", make_order_vals(partners[0]["id"], products[0]["id"], marker))
    assert len(created_ids) == 1
    orders = odoo.read("sale.order", created_ids, ["name", "client_order_ref", "state"])
    assert len(orders) == 1
    assert orders[0]["client_order_ref"] == marker
    assert orders[0]["state"] == "draft"
    matches = odoo.search_read("sale.order", [["client_order_ref", "=", marker]], ["id"])
    assert len(matches) == 1
    assert matches[0]["id"] == created_ids[0]


@pytest.mark.integration
def test_readonly_user_create_is_denied():
    env = load_env(ENV_PATH)
    readonly = client(env, "ODOO_API_KEY_READONLY_USER")
    with pytest.raises(OdooAuthorizationError) as exc:
        readonly.create("sale.order", [{"partner_id": 9, "client_order_ref": "STEP3-DENIED"}])
    assert exc.value.status == 403


@pytest.mark.integration
def test_invalid_api_key_is_rejected():
    env = load_env(ENV_PATH)
    invalid = OdooJSON2Client(
        OdooConfig(url=env["ODOO_URL"], database=env["ODOO_DATABASE"], api_key="definitely-invalid-key")
    )
    with pytest.raises(OdooAuthenticationError) as exc:
        invalid.search_read("res.partner", [], ["name"])
    assert exc.value.status == 401


@pytest.mark.integration
def test_unreachable_port_times_out():
    env = load_env(ENV_PATH)
    unreachable = OdooJSON2Client(
        OdooConfig(url="http://10.255.255.1:81", database=env["ODOO_DATABASE"], api_key="unreachable")
    )
    with pytest.raises(OdooTimeoutError):
        unreachable.search_read("res.partner", [], ["name"], limit=1)