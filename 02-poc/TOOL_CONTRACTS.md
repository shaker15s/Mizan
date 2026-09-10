# Agent-Native ERP — POC Tool Contracts

**Date:** 7 September 2026 (revised after audit against `00-research/` — authoritative; changes: `version` field added, JSON-2 payload shapes made explicit, provenance marker on create, integer order ids, search-limit cap moved gateway-side)
**Status:** Design only. No code.

---

## Tool Contract Format

Every tool is defined as a JSON file. The schema follows the MCP tool definition format (for forward compatibility) but is not MCP-dependent.

```json
{
  "name": "tool.name",
  "tool_version": "1.0.0",
  "title": "Human-readable title",
  "description": "Detailed description for LLM tool selection. Must be specific enough to distinguish from similar tools.",
  "risk_level": "R0|R1|R2|R3|R4|R5",
  "readOnly": true|false,
  "destructive": true|false,
  "requiresConfirmation": true|false,
  "idempotent": true|false,
  "inputSchema": { "/* JSON Schema 2020-12 */": "" },
  "outputSchema": { "/* JSON Schema 2020-12 */": "" },
  "odoo": {
    "model": "res.partner",
    "method": "search_read",
    "domain_template": "...",
    "fields": ["id", "name", "email", "phone"],
    "limit_cap": 20
  }
}
```

- The `tool_version` field (semantic version string) enters `operation_hash` (see `SECURITY_MODEL.md` §6 for the exact canonical JSON serialization); a schema change is a version bump — never a silent edit. The registry records the active version; proposals store the version they were created against, and confirmation-execution re-reads the current version — a mismatch rejects the proposal (`TOOL_VERSION_MISMATCH`).
- The `odoo` object is consumed by the adapter, not by the LLM. It tells the adapter which Odoo model and method to call, what fields to read, and how to construct the domain filter.
- `limit_cap` is applied **by the adapter** (hard cap on result size); the LLM sees no `limit` parameter — one less schema surface to get wrong.
- Every adapter call sends the JSON-2 headers: `Authorization: bearer <per-user key>`, `X-Odoo-Database: poc_test`, `Content-Type: application/json; charset=utf-8`, `User-Agent: agent-native-erp-poc/0.1` (see `TECHNICAL_DESIGN.md` §3).

---

## Tool 1: `customer.search`

```json
{
  "name": "customer.search",
  "tool_version": "1.0.0",
  "title": "Search customers",
  "description": "Search for customers (partners in Odoo) by name. Returns a list of matching customers with their ID, name, email, and phone. Use this tool when the user wants to find a customer by name. If the search returns multiple results, present them to the user for disambiguation.",
  "risk_level": "R0",
  "readOnly": true,
  "destructive": false,
  "requiresConfirmation": false,
  "idempotent": true,
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Customer name or partial name to search for. Matched against the customer's display name in Odoo."
      }
    },
    "required": ["query"],
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "success": { "type": "boolean" },
      "customers": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "id": { "type": "integer" },
            "name": { "type": "string" },
            "email": { "type": ["string", "null"] },
            "phone": { "type": ["string", "null"] }
          }
        }
      },
      "count": { "type": "integer" }
    }
  },
  "odoo": {
    "model": "res.partner",
    "method": "search_read",
    "domain_template": "[[\"name\", \"ilike\", \"{query}\"]]",
    "fields": ["id", "name", "email", "phone"],
    "limit_cap": 20
  }
}
```

**Known limitations (documented, not hidden):**
- The `name ilike` domain matches **all partners**, not only customers. Whether to add `["customer_rank", ">", 0]` depends on the demo data's `customer_rank` values — validate on the sandbox in implementation Step 3 and fix the domain then (a contract `version` bump, not a silent edit).
- **Arabic orthographic normalization is out of POC scope.** Odoo's `ilike` does not normalize alef variants (أ/إ/ا), taa marbuta (ة/ه), or alif maqsura (ى/ي). The eval dataset therefore uses names whose orthography matches the Odoo demo data exactly; normalization is a pre-pilot engineering item (see `DECISIONS.md` ADR-17).

---

## Tool 2: `customer.get`

```json
{
  "name": "customer.get",
  "tool_version": "1.0.0",
  "title": "Get customer details",
  "description": "Retrieve detailed information for a specific customer by their ID. Returns the customer's name, email, phone, and street address. Use this tool when you have a specific customer ID and need more details. If you do not have the customer ID, use customer.search first.",
  "risk_level": "R0",
  "readOnly": true,
  "destructive": false,
  "requiresConfirmation": false,
  "idempotent": true,
  "inputSchema": {
    "type": "object",
    "properties": {
      "customer_id": {
        "type": "integer",
        "description": "The Odoo partner ID of the customer. Obtain this from customer.search first."
      }
    },
    "required": ["customer_id"],
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "success": { "type": "boolean" },
      "customer": {
        "type": "object",
        "properties": {
          "id": { "type": "integer" },
          "name": { "type": "string" },
          "email": { "type": ["string", "null"] },
          "phone": { "type": ["string", "null"] },
          "street": { "type": ["string", "null"] }
        }
      }
    }
  },
  "odoo": {
    "model": "res.partner",
    "method": "read",
    "fields": ["id", "name", "email", "phone", "street"]
  }
}
```

---

## Tool 3: `product.search`

```json
{
  "name": "product.search",
  "tool_version": "1.0.0",
  "title": "Search products",
  "description": "Search for products by name. Returns a list of matching products with their ID, name, list price, and available quantity. Use this tool when the user mentions a product name or wants to see available products. If the search returns multiple results, present them to the user.",
  "risk_level": "R0",
  "readOnly": true,
  "destructive": false,
  "requiresConfirmation": false,
  "idempotent": true,
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Product name or partial name to search for."
      }
    },
    "required": ["query"],
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "success": { "type": "boolean" },
      "products": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "id": { "type": "integer" },
            "name": { "type": "string" },
            "list_price": { "type": "number" },
            "qty_available": { "type": "number" }
          }
        }
      },
      "count": { "type": "integer" }
    }
  },
  "odoo": {
    "model": "product.product",
    "method": "search_read",
    "domain_template": "[[\"name\", \"ilike\", \"{query}\"]]",
    "fields": ["id", "name", "list_price", "qty_available"],
    "limit_cap": 20
  }
}
```

**Known limitation (documented):** demo-data product names are predominantly English/French ("Acoustic Bloc Screens", "Drawer"…). Test cases use the demo-data language so the tool contract is tested honestly; the Arabic product-name gap is an eval-data finding, not a contract bug.

---

## Tool 4: `sales.order.create`

```json
{
  "name": "sales.order.create",
  "tool_version": "1.0.0",
  "title": "Create a draft sales order",
  "description": "Create a new draft sales order for a specific customer with one or more product lines. This is a write operation that requires user confirmation before execution. The order is created in draft state. It is NOT confirmed or posted. Use this tool only when the user has explicitly requested to create a sales order.",
  "risk_level": "R2",
  "readOnly": false,
  "destructive": false,
  "requiresConfirmation": true,
  "idempotent": true,
  "inputSchema": {
    "type": "object",
    "properties": {
      "customer_id": {
        "type": "integer",
        "description": "The Odoo partner ID of the customer. Must exist. Obtain from customer.search."
      },
      "lines": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "properties": {
            "product_id": {
              "type": "integer",
              "description": "The Odoo product ID. Must exist. Obtain from product.search."
            },
            "quantity": {
              "type": "number",
              "minimum": 1,
              "description": "Quantity of this product. Must be at least 1."
            }
          },
          "required": ["product_id", "quantity"],
          "additionalProperties": false
        }
      }
    },
    "required": ["customer_id", "lines"],
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "success": { "type": "boolean" },
      "order_id": { "type": "integer", "description": "Numeric sale.order database id (returned by JSON-2 create and confirmed by read-back)" },
      "order_name": { "type": "string", "description": "Odoo order reference, e.g. S00001" },
      "customer_id": { "type": "integer" },
      "state": { "type": "string" },
      "amount_total": { "type": "number" }
    }
  },
  "odoo": {
    "model": "sale.order",
    "method": "create",
    "payload_template": {
      "partner_id": "{customer_id}",
      "client_order_ref": "{idempotency_key}",
      "order_line": "[[0, 0, {\"product_id\": {product_id}, \"product_uom_qty\": {quantity}}], ...]"
    },
    "fields_to_verify": ["partner_id", "state", "amount_total", "order_line"]
  }
}
```

**Odoo payload mapping (JSON-2, verified semantics):**
- JSON-2 `create` takes named arguments only. One2many lines are passed as ORM command tuples serialized to JSON arrays: `[[0, 0, {"product_id": 55, "product_uom_qty": 20}]]`.
- The line quantity field is `product_uom_qty` (the canonical `quantity` in our contract maps to it).
- **`client_order_ref` carries the gateway's content-addressable idempotency key** — the provenance marker that the reconciliation search (TECHNICAL_DESIGN §7) uses after ambiguous outcomes. Validated in implementation Step 3; if the field proves unusable, STOP and record a DECISIONS addendum (do not silently switch fields).
- Prices, taxes, and totals are computed by **Odoo** (pricelists/fiscal logic stay in the ERP — deterministic business rules never run in the LLM or the gateway).

**Business validation in the gateway (in addition to JSON schema):**
- `customer_id` must reference an existing `res.partner` record in the tenant Odoo instance (gateway pre-check; Odoo-side rejection is the backstop)
- Each `product_id` must reference an existing `product.product` record
- Each `quantity` must be ≥ 1 (already in schema but re-checked in gateway for defense-in-depth) and ≤ `MAX_LINE_QUANTITY`
- Total estimated amount must not exceed `MAX_ORDER_AMOUNT` (set via environment variable)

---

## Tool 5: `sales.order.get`

```json
{
  "name": "sales.order.get",
  "tool_version": "1.0.0",
  "title": "Get sales order details",
  "description": "Retrieve details for a specific sales order by its ID. Returns the order's reference, customer name, state, and total amount. Use this tool when you have a sales order ID and need its current status. Also used internally by the gateway for post-write verification.",
  "risk_level": "R0",
  "readOnly": true,
  "destructive": false,
  "requiresConfirmation": false,
  "idempotent": true,
  "inputSchema": {
    "type": "object",
    "properties": {
      "order_id": {
        "type": "integer",
        "description": "The numeric Odoo sale.order database id (as returned by sales.order.create or prior tool results). Obtain via search if only a name is known."
      }
    },
    "required": ["order_id"],
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "success": { "type": "boolean" },
      "order": {
        "type": "object",
        "properties": {
          "id": { "type": "integer" },
          "name": { "type": "string" },
          "partner_id": { "type": "integer" },
          "partner_name": { "type": "string" },
          "state": { "type": "string" },
          "amount_total": { "type": "number" }
        }
      }
    }
  },
  "odoo": {
    "model": "sale.order",
    "method": "read",
    "fields": ["id", "name", "partner_id", "state", "amount_total"]
  }
}
```

---

## Tool Registry

The tool registry is a server-side, versioned, curated list of tool contracts. It is NOT model-generated and NOT user-configurable in the POC.

**Loading:**
```python
# gateway.py loads all JSON files from the schemas/ directory at startup
tool_registry = {}
for schema_file in Path("schemas").glob("*.json"):
    schema = json.loads(schema_file.read_text())
    tool_registry[schema["name"]] = schema
```

**Validation at startup:**
- Every schema must parse as valid JSON
- Every `inputSchema` must be a valid JSON Schema 2020-12 document (validated by the `jsonschema` library)
- Every `name` must be unique; every `tool_version` must parse as `MAJOR.MINOR.PATCH`
- Every `odoo.model` must exist in Odoo (verified by a startup connectivity check)

**Governance:**
- Tools are reviewed as code (in a pull request)
- Schema changes require updating the associated test cases
- `tool_version` bumps are required for any change to `inputSchema`, `outputSchema`, `odoo` mapping, or business validation rules
- Tool descriptions are treated as code — reviewed for accuracy and quality (per research R8: Anthropic guidance says "extremely detailed descriptions" is the most important factor)

---

## Authorization Policy

Implemented schema (`03-poc-src/users.yaml`). The per-user Odoo credential
mapping deliberately does NOT live in the policy file — it is a bootstrap
concern (`poc/bootstrap.py::_ODOO_API_KEY_VARS`), keeping the policy purely
about authorization:

```yaml
# users.yaml (implemented schema)
sales_user@test:
  tenant_id: poc_tenant_001
  role: sales
  allowed_tools:
    - customer.search
    - customer.get
    - product.search
    - sales.order.create    # requires confirmation
    - sales.order.get

readonly_user@test:
  tenant_id: poc_tenant_001
  role: readonly
  allowed_tools:
    - customer.search
    - customer.get
    - product.search
    - sales.order.get

no_access_user@test:
  tenant_id: poc_tenant_001
  role: none
  allowed_tools: []
```

**Policy evaluation is deterministic.** A user is allowed when `tool_name` is
in `allowed_tools` and NOT in `denied_tools`; `denied_tools` takes precedence
over `allowed_tools` (documented precedence — audit F-19). Unknown users,
unknown tenants, malformed policy files, and unknown tools are denied
(fail-closed). The LLM is never consulted for authorization.

**Re-authorization at confirmation-execution time:** the policy above is evaluated when the proposal is created. It is evaluated AGAIN (same check, same YAML, current registry state, including the tool's current `tool_version`) when the user confirms. A proposal MUST NOT execute based only on the state captured when it was created — see `SECURITY_MODEL.md` §5.3 for the full re-authorization checklist.

**Defense-in-depth:** Odoo's own access rights also apply. Even if the gateway has a bug and allows an unauthorized call, Odoo rejects it based on the calling user's API key (which maps to a user without the relevant permissions). This is the two-layer defense the research requires.
