"""STEP 2 smoke-test utility — raw Odoo JSON-2 API checks.

No POC adapter, no gateway, no agent logic. Reads secrets only from 03-poc-src/.env.
Never prints API keys. Not part of the production application.
"""
import json
import os
import sys
import uuid
import urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_env(path):
    env = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def call(env, user_key_name, model, method, payload, expect_error=False):
    url = env["ODOO_URL"] + "/json/2/" + model + "/" + method
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": "bearer " + env[user_key_name],
            "X-Odoo-Database": env["ODOO_DATABASE"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        status = error.code
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = {"raw": body}
    return {"status": status, "body": parsed}


def search_read(env, key_name, model, domain, fields):
    return call(env, key_name, model, "search_read", {"domain": domain, "fields": fields})


def extract(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        return body.get("result", [])
    return []


def main():
    env = load_env(ENV_PATH)
    missing = [name for name in (
        "ODOO_API_KEY_SALES_USER",
        "ODOO_API_KEY_READONLY_USER",
        "ODOO_API_KEY_NO_ACCESS_USER",
    ) if not env.get(name)]
    if missing:
        print("MISSING KEYS:", ", ".join(missing))
        return 2

    results = {}

    # Before counts, using sales_user's read access.
    before = search_read(env, "ODOO_API_KEY_SALES_USER", "sale.order", [], ["id"])
    results["before_orders"] = before

    # TEST 1: sales_user customer read.
    results["test1_sales_customer_read"] = search_read(
        env, "ODOO_API_KEY_SALES_USER", "res.partner", [("customer_rank", ">", 0)], ["id", "name"]
    )

    # TEST 2: readonly_user customer read.
    results["test2_readonly_customer_read"] = search_read(
        env, "ODOO_API_KEY_READONLY_USER", "res.partner", [("customer_rank", ">", 0)], ["id", "name"]
    )

    # TEST 3: sales_user product read.
    results["test3_sales_product_read"] = search_read(
        env, "ODOO_API_KEY_SALES_USER", "product.product", [], ["id", "name"]
    )

    # TEST 4: sales_user order create.
    partners = extract(results["test1_sales_customer_read"]["body"])
    products = extract(results["test3_sales_product_read"]["body"])
    if not partners or not products:
        print("No demo partner/product found.")
        return 2
    partner_id = partners[0]["id"]
    product_id = products[0]["id"]
    unique_ref = "STEP2-PROBE-" + uuid.uuid4().hex[:8]
    create_payload = {
        "vals_list": [{
            "partner_id": partner_id,
            "client_order_ref": unique_ref,
            "order_line": [
                [0, 0, {"product_id": product_id, "product_uom_qty": 1}],
            ],
        }]
    }
    for v in create_payload["vals_list"]:
        if "order_line" in v and isinstance(v["order_line"], list) and isinstance(v["order_line"][0], list):
            v["order_line"][0] = tuple(v["order_line"][0])
    results["test4_sales_order_create"] = call(
        env, "ODOO_API_KEY_SALES_USER", "sale.order", "create", create_payload
    )
    created_id = extract(results["test4_sales_order_create"]["body"])
    if isinstance(created_id, list):
        created_id = created_id[0]
    if isinstance(created_id, int):
        order_read = call(
            env,
            "ODOO_API_KEY_SALES_USER",
            "sale.order",
            "read",
            {"ids": [created_id], "fields": ["name", "partner_id", "state", "order_line"]},
        )
        lines_read = call(
            env,
            "ODOO_API_KEY_SALES_USER",
            "sale.order.line",
            "read",
            {"ids": extract(order_read["body"])[0]["order_line"], "fields": ["product_id", "product_uom_qty"]},
        )
        results["test4_order_read"] = order_read
        results["test4_order_lines_read"] = lines_read
    results["probe"] = {
        "label": "STEP_2_PROBE_ORDER",
        "client_order_ref": unique_ref,
    }

    # TEST 5: readonly_user create denial.
    results["test5_readonly_create_denial"] = call(
        env, "ODOO_API_KEY_READONLY_USER", "sale.order", "create", create_payload
    )

    # TEST 6: no_access_user create denial.
    results["test6_no_access_create_denial"] = call(
        env, "ODOO_API_KEY_NO_ACCESS_USER", "sale.order", "create", create_payload
    )

    # After counts and probe verification.
    after = search_read(env, "ODOO_API_KEY_SALES_USER", "sale.order", [], ["id", "client_order_ref"])
    results["after_orders"] = after

    # Safe output only.
    for test_name, result in results.items():
        body = result.get("body") if isinstance(result, dict) else None
        if isinstance(body, dict) and "error" in body:
            error = body["error"]
            safe_error = {
                "http_status": result.get("status"),
                "name": error.get("name") or error.get("data", {}).get("name"),
                "message": error.get("message") or error.get("data", {}).get("message"),
            }
            print(test_name, "=>", json.dumps(safe_error))
        else:
            print(test_name, "=> HTTP", result.get("status"), "OK")

    with open(Path(__file__).with_name("step2_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
