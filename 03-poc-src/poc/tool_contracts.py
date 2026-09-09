"""Server-owned, versioned POC tool contracts.

This module defines deterministic tool contracts only. It does not execute
Odoo operations, authorize users, confirm actions, audit writes, or generate
tools from model input.
"""

from __future__ import annotations

import copy
from typing import Any

TOOL_VERSION = "1.0.0"


def _contract(name: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "tool_version": TOOL_VERSION,
    }
    base.update(overrides)
    return base


_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "customer.search": _contract(
        name="customer.search",
        title="Search customers",
        description=(
            "Search for customers (partners in Odoo) by name. Returns a list of matching customers with their ID, name, email, and phone. Use this tool when the user wants to find a customer by name. If the search returns multiple results, present them to the user for disambiguation."
        ),
        risk_level="R0",
        readOnly=True,
        destructive=False,
        requiresConfirmation=False,
        idempotent=True,
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Customer name or partial name to search for. Matched against the customer's display name in Odoo.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "customers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                            "email": {"type": ["string", "null"]},
                            "phone": {"type": ["string", "null"]},
                        },
                    },
                },
                "count": {"type": "integer"},
            },
        },
        odoo={
            "model": "res.partner",
            "method": "search_read",
            "domain_template": '["name", "ilike", "{query}"]',
            "fields": ["id", "name", "email", "phone"],
            "limit_cap": 20,
        },
    ),
    "customer.get": _contract(
        name="customer.get",
        title="Get customer details",
        description=(
            "Retrieve detailed information for a specific customer by their ID. Returns the customer's name, email, phone, and street address. Use this tool when you have a specific customer ID and need more details. If you do not have the customer ID, use customer.search first."
        ),
        risk_level="R0",
        readOnly=True,
        destructive=False,
        requiresConfirmation=False,
        idempotent=True,
        inputSchema={
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "integer",
                    "description": "The Odoo partner ID of the customer. Obtain this from customer.search first.",
                }
            },
            "required": ["customer_id"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "customer": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "email": {"type": ["string", "null"]},
                        "phone": {"type": ["string", "null"]},
                        "street": {"type": ["string", "null"]},
                    },
                },
            },
        },
        odoo={
            "model": "res.partner",
            "method": "read",
            "fields": ["id", "name", "email", "phone", "street"],
        },
    ),
    "product.search": _contract(
        name="product.search",
        title="Search products",
        description=(
            "Search for products by name. Returns a list of matching products with their ID, name, list price, and available quantity. Use this tool when the user mentions a product name or wants to see available products. If the search returns multiple results, present them to the user."
        ),
        risk_level="R0",
        readOnly=True,
        destructive=False,
        requiresConfirmation=False,
        idempotent=True,
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Product name or partial name to search for.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "products": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                            "list_price": {"type": "number"},
                            "qty_available": {"type": "number"},
                        },
                    },
                },
                "count": {"type": "integer"},
            },
        },
        odoo={
            "model": "product.product",
            "method": "search_read",
            "domain_template": '["name", "ilike", "{query}"]',
            "fields": ["id", "name", "list_price", "qty_available"],
            "limit_cap": 20,
        },
    ),
    "sales.order.create": _contract(
        name="sales.order.create",
        title="Create a draft sales order",
        description=(
            "Create a new draft sales order for a specific customer with one or more product lines. This is a write operation that requires user confirmation before execution. The order is created in draft state. It is NOT confirmed or posted. Use this tool only when the user has explicitly requested to create a sales order."
        ),
        risk_level="R2",
        readOnly=False,
        destructive=False,
        requiresConfirmation=True,
        idempotent=True,
        inputSchema={
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "integer",
                    "description": "The Odoo partner ID of the customer. Must exist. Obtain from customer.search.",
                },
                "lines": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {
                                "type": "integer",
                                "description": "The Odoo product ID. Must exist. Obtain from product.search.",
                            },
                            "quantity": {
                                "type": "number",
                                "minimum": 1,
                                "description": "Quantity of this product. Must be at least 1.",
                            },
                        },
                        "required": ["product_id", "quantity"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["customer_id", "lines"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "order_id": {
                    "type": "integer",
                    "description": "Numeric sale.order database id (returned by JSON-2 create and confirmed by read-back)",
                },
                "order_name": {"type": "string", "description": "Odoo order reference, e.g. S00001"},
                "customer_id": {"type": "integer"},
                "state": {"type": "string"},
                "amount_total": {"type": "number"},
            },
        },
        odoo={
            "model": "sale.order",
            "method": "create",
            "payload_template": {
                "partner_id": "{customer_id}",
                "client_order_ref": "{idempotency_key}",
                "order_line": [
                    [0, 0, {"product_id": "{product_id}", "product_uom_qty": "{quantity}"}]
                ],
            },
            "fields_to_verify": ["partner_id", "state", "amount_total", "order_line"],
        },
    ),
    "sales.order.get": _contract(
        name="sales.order.get",
        title="Get sales order details",
        description=(
            "Retrieve details for a specific sales order by its ID. Returns the order's reference, customer name, state, and total amount. Use this tool when you have a sales order ID and need its current status. Also used internally by the gateway for post-write verification."
        ),
        risk_level="R0",
        readOnly=True,
        destructive=False,
        requiresConfirmation=False,
        idempotent=True,
        inputSchema={
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The numeric Odoo sale.order database id (as returned by sales.order.create or prior tool results). Obtain via search if only a name is known.",
                }
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "order": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "partner_id": {"type": "integer"},
                        "partner_name": {"type": "string"},
                        "state": {"type": "string"},
                        "amount_total": {"type": "number"},
                    },
                },
            },
        },
        odoo={
            "model": "sale.order",
            "method": "read",
            "fields": ["id", "name", "partner_id", "state", "amount_total"],
        },
    ),
}


class ToolRegistry:
    """Static, server-owned registry of the five authorized POC tools."""

    def __init__(self) -> None:
        self._contracts: dict[str, dict[str, Any]] = _TOOL_CONTRACTS

    def get(self, tool_name: str) -> dict[str, Any]:
        """Return a defensive copy of one tool contract."""
        if tool_name not in self._contracts:
            raise KeyError(f"Unknown tool: {tool_name}")
        return copy.deepcopy(self._contracts[tool_name])

    def names(self) -> tuple[str, ...]:
        """Return the exact tool names in deterministic definition order."""
        return tuple(self._contracts.keys())


def get_registry() -> ToolRegistry:
    """Return the single static registry instance."""
    return ToolRegistry()