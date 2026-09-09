"""Focused tests for the static server-side tool contract registry."""

from __future__ import annotations

import jsonschema
import pytest

from poc.tool_contracts import TOOL_VERSION, ToolRegistry, get_registry

EXPECTED_TOOLS = {
    "customer.search",
    "customer.get",
    "product.search",
    "sales.order.create",
    "sales.order.get",
}


def validate(instance, schema):
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda error: error.json_path)
    if errors:
        raise errors[0]


def test_registry_contains_exactly_five_required_tools():
    registry = get_registry()
    assert set(registry.names()) == EXPECTED_TOOLS
    assert len(registry.names()) == 5


def test_all_tool_versions_are_pinned():
    registry = get_registry()
    for name in registry.names():
        assert registry.get(name)["tool_version"] == TOOL_VERSION == "1.0.0"


def test_unknown_tool_lookup_is_rejected():
    registry = get_registry()
    with pytest.raises(KeyError, match="Unknown tool: does.not.exist"):
        registry.get("does.not.exist")


@pytest.mark.parametrize(
    "tool_name,instance",
    [
        ("customer.search", {"query": "Acme", "unexpected": True}),
        ("customer.get", {"customer_id": 9, "unexpected": True}),
        ("product.search", {"query": "Desk", "unexpected": True}),
        (
            "sales.order.create",
            {
                "customer_id": 9,
                "lines": [{"product_id": 15, "quantity": 1}],
                "unexpected": True,
            },
        ),
        ("sales.order.get", {"order_id": 26, "unexpected": True}),
    ],
)
def test_input_schemas_reject_unknown_properties(tool_name, instance):
    registry = get_registry()
    with pytest.raises(jsonschema.ValidationError):
        validate(instance, registry.get(tool_name)["inputSchema"])


@pytest.mark.parametrize(
    "tool_name,instance",
    [
        ("customer.search", {}),
        ("customer.get", {}),
        ("product.search", {}),
        ("sales.order.create", {"customer_id": 9}),
        ("sales.order.get", {}),
    ],
)
def test_input_schemas_reject_missing_required_properties(tool_name, instance):
    registry = get_registry()
    with pytest.raises(jsonschema.ValidationError):
        validate(instance, registry.get(tool_name)["inputSchema"])


@pytest.mark.parametrize(
    "tool_name,instance",
    [
        ("customer.search", {"query": 123}),
        ("customer.get", {"customer_id": "9"}),
        ("product.search", {"query": None}),
        ("sales.order.create", {"customer_id": "9", "lines": [{"product_id": 15, "quantity": 1}]}),
        ("sales.order.create", {"customer_id": 9, "lines": [{"product_id": 15, "quantity": "1"}]}),
        ("sales.order.create", {"customer_id": 9, "lines": []}),
        ("sales.order.create", {"customer_id": 9, "lines": [{"product_id": 15, "quantity": 0}]}),
        ("sales.order.get", {"order_id": "26"}),
    ],
)
def test_input_schemas_reject_invalid_types_and_values(tool_name, instance):
    registry = get_registry()
    with pytest.raises(jsonschema.ValidationError):
        validate(instance, registry.get(tool_name)["inputSchema"])


def test_valid_inputs_are_accepted():
    registry = get_registry()
    validate({"query": "Acme"}, registry.get("customer.search")["inputSchema"])
    validate({"customer_id": 9}, registry.get("customer.get")["inputSchema"])
    validate({"query": "Desk"}, registry.get("product.search")["inputSchema"])
    validate(
        {
            "customer_id": 9,
            "lines": [
                {"product_id": 15, "quantity": 1},
                {"product_id": 16, "quantity": 2.5},
            ],
        },
        registry.get("sales.order.create")["inputSchema"],
    )
    validate({"order_id": 26}, registry.get("sales.order.get")["inputSchema"])


def test_mutating_and_confirmation_risk_metadata():
    registry = get_registry()
    create = registry.get("sales.order.create")
    assert create["readOnly"] is False
    assert create["destructive"] is False
    assert create["requiresConfirmation"] is True
    assert create["risk_level"] == "R2"


def test_read_tools_are_not_mutating():
    registry = get_registry()
    for name in EXPECTED_TOOLS - {"sales.order.create"}:
        contract = registry.get(name)
        assert contract["readOnly"] is True
        assert contract["destructive"] is False
        assert contract["requiresConfirmation"] is False


def test_every_tool_has_exact_allowed_odoo_mapping_keys():
    registry = get_registry()
    for name in registry.names():
        mapping = registry.get(name)["odoo"]
        assert mapping["model"] in {"res.partner", "product.product", "sale.order"}
        assert mapping["method"] in {"search_read", "read", "create"}
        if mapping["method"] == "search_read":
            assert set(mapping) == {"model", "method", "domain_template", "fields", "limit_cap"}
        elif mapping["method"] == "read":
            assert set(mapping) == {"model", "method", "fields"}
        else:
            assert set(mapping) == {"model", "method", "payload_template", "fields_to_verify"}


def test_no_contract_exposes_arbitrary_rpc_surface():
    registry = get_registry()
    for name in registry.names():
        contract = registry.get(name)
        assert set(contract) == {
            "name",
            "tool_version",
            "title",
            "description",
            "risk_level",
            "readOnly",
            "destructive",
            "requiresConfirmation",
            "idempotent",
            "inputSchema",
            "outputSchema",
            "odoo",
        }
        assert "domain" not in contract["inputSchema"].get("properties", {})
        assert "fields" not in contract["inputSchema"].get("properties", {})
        assert "model" not in contract["inputSchema"].get("properties", {})
        assert "method" not in contract["inputSchema"].get("properties", {})


def test_returned_contracts_are_deep_copies():
    registry = get_registry()
    contract = registry.get("customer.search")
    contract["title"] = "mutated"
    contract["inputSchema"]["required"].append("hack")
    fresh = registry.get("customer.search")
    assert fresh["title"] != "mutated"
    assert fresh["inputSchema"]["required"] == ["query"]


def test_sales_order_create_payload_semantics():
    registry = get_registry()
    contract = registry.get("sales.order.create")
    template = contract["odoo"]["payload_template"]
    assert template["partner_id"] == "{customer_id}"
    assert template["client_order_ref"] == "{idempotency_key}"
    assert template["order_line"] == [[0, 0, {"product_id": "{product_id}", "product_uom_qty": "{quantity}"}]]
    assert contract["odoo"]["fields_to_verify"] == ["partner_id", "state", "amount_total", "order_line"]


def test_tool_version_is_semver():
    major, minor, patch = TOOL_VERSION.split(".")
    assert (major, minor, patch) == ("1", "0", "0")