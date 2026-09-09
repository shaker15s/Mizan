"""Deterministic post-write verification for the POC.

The gateway is the only intended consumer. This module has no LLM, audit,
authorization, confirmation, idempotency, or Odoo imports: callers pass an
already-authenticated transport object with a server-owned ``read`` method.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


class VerificationReadTransport(Protocol):
    """Minimal transport surface used for read-back verification."""

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        """Read records by database IDs."""


@dataclass(frozen=True)
class VerificationResult:
    """Server-controlled result of one deterministic read-back comparison."""

    passed: bool
    error: str | None = None


def verify_sales_order_creation(
    client: VerificationReadTransport,
    order_id: int,
    expected_customer_id: int,
    expected_lines: Sequence[Mapping[str, Any]],
    expected_client_order_ref: str,
    expected_amount_total: float | None = None,
) -> VerificationResult:
    """Verify the created draft order against the gateway-owned request.

    The transport exceptions deliberately propagate so the Gateway can map
    them through the canonical error taxonomy instead of inventing another
    failure layer.
    """
    fields = ["partner_id", "state", "amount_total", "amount_untaxed", "order_line", "client_order_ref"]
    records = client.read("sale.order", [order_id], fields)
    if not records:
        return VerificationResult(
            passed=False,
            error="Verified sales order was not found after creation.",
        )
    if len(records) != 1:
        return VerificationResult(
            passed=False,
            error="Sales order read-back returned an ambiguous result.",
        )

    order = records[0]
    if order.get("client_order_ref") != expected_client_order_ref:
        return VerificationResult(
            passed=False,
            error="Sales order provenance does not match the authorized idempotency key.",
        )
    read_customer_id = order.get("partner_id")
    if isinstance(read_customer_id, (list, tuple)) and read_customer_id:
        read_customer_id = read_customer_id[0]
    if not isinstance(read_customer_id, int) or read_customer_id != expected_customer_id:
        return VerificationResult(passed=False, error="Sales order customer mismatch.")
    if order.get("state") != "draft":
        return VerificationResult(passed=False, error="Sales order is not in draft state.")
    actual_amount_total = order.get("amount_total")
    if isinstance(actual_amount_total, bool) or not isinstance(actual_amount_total, (int, float)):
        return VerificationResult(passed=False, error="Sales order total is missing or invalid.")
    if expected_amount_total is not None:
        # Compare against the tax-exclusive total: taxes and pricelist rules are
        # Odoo-owned, so amount_total can legitimately differ from the sum of
        # list_price x qty while the untaxed total may not (B5 fix).
        reference = order.get("amount_untaxed")
        if isinstance(reference, bool) or not isinstance(reference, (int, float)):
            reference = actual_amount_total
        if Decimal(str(reference)) != Decimal(str(expected_amount_total)):
            return VerificationResult(
                passed=False,
                error="Sales order total does not match the authorized operation.",
            )

    actual_lines = order.get("order_line")
    if not isinstance(actual_lines, list) or len(actual_lines) != len(expected_lines):
        return VerificationResult(
            passed=False,
            error="Sales order line count mismatch.",
        )
    actual_line_values: list[Mapping[str, Any]]
    if all(isinstance(line, Mapping) for line in actual_lines):
        actual_line_values = actual_lines
    elif all(isinstance(line, int) and not isinstance(line, bool) for line in actual_lines):
        line_records = client.read(
            "sale.order.line",
            actual_lines,
            ["product_id", "product_uom_qty"],
        )
        line_by_id = {record["id"]: record for record in line_records if isinstance(record, Mapping) and "id" in record}
        actual_line_values = []
        for line_id in actual_lines:
            line_record = line_by_id.get(line_id)
            if line_record is None:
                return VerificationResult(
                    passed=False,
                    error="Sales order line data is missing after creation.",
                )
            actual_line_values.append(line_record)
    else:
        return VerificationResult(
            passed=False,
            error="Sales order line data is invalid after creation.",
        )

    for expected_line, actual_line in zip(expected_lines, actual_line_values):
        actual_product_id = actual_line.get("product_id")
        if isinstance(actual_product_id, (list, tuple)) and actual_product_id:
            actual_product_id = actual_product_id[0]
        if actual_product_id != expected_line.get("product_id"):
            return VerificationResult(
                passed=False,
                error="Requested product line does not match the authorized operation.",
            )
        actual_quantity = actual_line.get("product_uom_qty")
        expected_quantity = expected_line.get("quantity")
        if isinstance(actual_quantity, bool) or isinstance(expected_quantity, bool):
            return VerificationResult(
                passed=False,
                error="Requested order line quantity does not match the authorized operation.",
            )
        try:
            quantity_matches = Decimal(str(actual_quantity)) == Decimal(str(expected_quantity))
        except (InvalidOperation, TypeError, ValueError):
            quantity_matches = False
        if not quantity_matches:
            return VerificationResult(
                passed=False,
                error="Requested order line quantity does not match the authorized operation.",
            )
    return VerificationResult(passed=True)
