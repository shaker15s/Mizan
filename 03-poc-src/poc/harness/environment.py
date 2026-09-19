"""Seeded ERP + shared gateway fixture for the evaluation harness.

Speed comes from *not* rebuilding the world for every case: one SQLite store
and one fake ERP per shard, reused across cases (the previous harness
re-initialized the DB and the gateway 90 times per run). State carry-over is
explicit and documented: cases are hermetic (``repeat`` is honoured inside a
case), so a shard is just "the same seeded world".
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from poc.agent_runtime import AgentRuntime
from poc.audit_store import AuditStore
from poc.authz import PolicyEngine
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.llm_client import LLMClientProtocol
from poc.normalization import normalize_arabic
from poc.odoo_client import OdooValidationError
from poc.tool_contracts import get_registry

SEED_PARTNERS: dict[int, dict[str, Any]] = {
    42: {"id": 42, "name": "محمد أحمد", "email": "m.a@example.com", "phone": "01001234567", "street": "12 ش التحرير", "city": "القاهرة", "credit_limit": 50000.0, "vat": "EG123456789"},
    43: {"id": 43, "name": "شركة النيل للتجارة", "email": None, "phone": "0223456789", "street": None, "city": "الجيزة", "credit_limit": 120000.0},
    44: {"id": 44, "name": "محمد سيد", "email": None, "phone": None, "street": None, "city": "الإسكندرية", "credit_limit": 0.0},
    45: {"id": 45, "name": "أحمد حسن", "email": "a.h@example.com", "phone": None, "street": None, "city": "المنصورة", "credit_limit": 15000.0},
    46: {"id": 46, "name": "مؤسسة النيل", "email": None, "phone": None, "street": None, "city": "القاهرة", "credit_limit": 0.0},
    47: {"id": 47, "name": "شركة الأمل", "email": "amal@example.com", "phone": None, "street": None, "city": "طنطا", "credit_limit": 8000.0},
}

SEED_PRODUCTS: dict[int, dict[str, Any]] = {
    55: {"id": 55, "name": "مياه معدنية 1.5 لتر", "default_code": "WTR-1500", "list_price": 12.0, "qty_available": 480.0},
    56: {"id": 56, "name": "زيت عباد الشمس 1 لتر", "default_code": "OIL-1000", "list_price": 65.0, "qty_available": 120.0},
    57: {"id": 57, "name": "أرز أبو بنت 5 كيلو", "default_code": "RICE-5K", "list_price": 140.0, "qty_available": 120.0},
    58: {"id": 58, "name": "سكر 1 كيلو", "default_code": "SUG-1K", "list_price": 38.0, "qty_available": 200.0},
    59: {"id": 59, "name": "شاي العروسة 250 جم", "default_code": "TEA-250", "list_price": 45.0, "qty_available": 90.0},
}


class SeededOdoo:
    """Deterministic ERP double: seeded entities, ilike search, real create + read-back."""

    def __init__(self) -> None:
        self.partners = {key: dict(value) for key, value in SEED_PARTNERS.items()}
        self.products = {key: dict(value) for key, value in SEED_PRODUCTS.items()}
        self.orders: dict[int, dict[str, Any]] = {
            100: {
                "id": 100,
                "name": "S00100",
                "partner_id": [42, "محمد أحمد"],
                "state": "draft",
                "amount_total": 240.0,
                "amount_untaxed": 210.5,
                "date_order": "2026-09-17 10:15:00",
                "order_line": [10000],
                "client_order_ref": None,
            }
        }
        self.order_lines: dict[int, dict[str, Any]] = {10000: {"id": 10000, "product_id": 55, "product_uom_qty": 20.0, "price_unit": 12.0, "name": "مياه معدنية 1.5 لتر"}}
        self.create_calls: list[dict[str, Any]] = []
        self.rpc_calls = 0
        self._next_order_id = 101
        self._next_line_id = 20000

    # --- read paths ---------------------------------------------------------
    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int | None = None) -> list[dict[str, Any]]:
        self.rpc_calls += 1
        query = next(
            (triplet[2] for triplet in domain if isinstance(triplet, (list, tuple)) and len(triplet) == 3 and triplet[0] == "name"),
            "",
        )
        norm_query = normalize_arabic(str(query))
        source: Mapping[int, Mapping[str, Any]] = {"res.partner": self.partners, "product.product": self.products}.get(model, {})
        matches = [dict(record) for record in source.values() if norm_query in normalize_arabic(str(record.get("name", "")))]
        return self._project(matches[:limit] if limit else matches, fields)

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        self.rpc_calls += 1
        if model == "product.product":
            missing = [record_id for record_id in ids if record_id not in self.products]
            if missing:
                raise OdooValidationError("missing_product", f"product {missing[0]} does not exist", 422)
        source: Mapping[int, Mapping[str, Any]] = {
            "res.partner": self.partners,
            "product.product": self.products,
            "sale.order": self.orders,
            "sale.order.line": self.order_lines,
        }.get(model, {})
        return self._project([dict(source[record_id]) for record_id in ids if record_id in source], fields)

    @staticmethod
    def _project(records: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
        out = []
        for record in records:
            row = {"id": record.get("id")}
            for name in fields:
                if name == "id":
                    continue
                row[name] = record.get(name)
            out.append(row)
        return out

    # --- write path ---------------------------------------------------------
    def create(self, model: str, vals_list: list[dict[str, Any]]) -> list[int]:
        if model != "sale.order":
            raise OdooValidationError("invalid_model", f"create not supported for {model}", 422)
        created: list[int] = []
        for vals in vals_list:
            customer_id = vals.get("partner_id")
            if customer_id not in self.partners:
                raise OdooValidationError("missing_partner", f"partner {customer_id} does not exist", 422)
            for line in vals.get("order_line", []):
                if line[0] == 0 and line[2].get("product_id") not in self.products:
                    raise OdooValidationError("missing_product", f"product {line[2].get('product_id')} does not exist", 422)
            order_id = self._next_order_id
            self._next_order_id += 1
            line_ids: list[int] = []
            for line in vals.get("order_line", []):
                self._next_line_id += 1
                product_id = line[2]["product_id"]
                quantity = float(line[2].get("product_uom_qty") or 0)
                price = float(self.products[product_id]["list_price"])
                self.order_lines[self._next_line_id] = {
                    "id": self._next_line_id,
                    "product_id": product_id,
                    "product_uom_qty": quantity,
                    "price_unit": price,
                    "name": self.products[product_id]["name"],
                }
                line_ids.append(self._next_line_id)
            amount = sum(self.products[self.order_lines[line_id]["product_id"]]["list_price"] * self.order_lines[line_id]["product_uom_qty"] for line_id in line_ids)
            self.orders[order_id] = {
                "id": order_id,
                "name": f"S00{order_id}",
                "partner_id": [customer_id, self.partners[customer_id]["name"]],
                "state": "draft",
                "amount_total": amount,
                "amount_untaxed": amount,
                "date_order": "2026-09-19 12:00:00",
                "order_line": line_ids,
                "client_order_ref": vals.get("client_order_ref"),
            }
            self.create_calls.append({"vals": dict(vals), "order_id": order_id})
            created.append(order_id)
        return created

    # --- analysis used by the harness ---------------------------------------
    def duplicate_order_count(self) -> int:
        """Orders sharing one provenance key (idempotency key ⇒ client_order_ref)."""
        by_ref: dict[str, int] = {}
        for call in self.create_calls:
            ref = call["vals"].get("client_order_ref")
            if ref is not None:
                by_ref[ref] = by_ref.get(ref, 0) + 1
        return sum(count - 1 for count in by_ref.values() if count > 1)


@dataclass
class EvalEnvironment:
    """One shard: a seeded ERP, one gateway/DB, and cheap runtime factories."""

    db_path: Path
    erp: str = "seeded"
    tenant_id: str = "poc_tenant_001"
    confirm_ttl_seconds: int | None = None
    odoo: SeededOdoo | None = None
    gateway: ToolGateway | None = None
    created_here: bool = True
    _factories: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        initialize(self.db_path)
        if self.gateway is None:
            self.gateway = ToolGateway(
                registry=get_registry(),
                policy_engine=PolicyEngine(),
                db_path=self.db_path,
                confirm_ttl_seconds=self.confirm_ttl_seconds,
            )
        if self.erp == "seeded" and self.odoo is None:
            self.odoo = SeededOdoo()

    # --- runtime ------------------------------------------------------------
    def odoo_client_for(self, user_id: str, tenant_id: str) -> Any:
        if self.erp == "seeded":
            assert self.odoo is not None
            return self.odoo
        factory = self._factories.get("odoo")
        if factory is None:
            from poc.bootstrap import _build_odoo_client

            factory = _build_odoo_client
            self._factories["odoo"] = factory
        return factory(user_id, tenant_id)

    def runtime(self, *, llm_client: LLMClientProtocol, user_id: str, **options: Any) -> AgentRuntime:
        return AgentRuntime(
            llm_client=llm_client,
            gateway=self.gateway,  # type: ignore[arg-type]
            registry=get_registry(),
            user_id=user_id,
            tenant_id=self.tenant_id,
            odoo_client_factory=self.odoo_client_for,
            **options,
        )

    # --- inspection ---------------------------------------------------------
    def audit_rows(self, *, limit: int = 2000, request_id: str | None = None) -> list[dict[str, Any]]:
        return AuditStore(self.db_path).list(limit=limit, request_id=request_id)

    def verify_chain(self) -> dict[str, Any]:
        return AuditStore(self.db_path).verify_chain()

    def cleanup(self) -> None:
        if not self.created_here:
            return
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)
            except OSError:
                pass


def fresh_environment(index: int, *, root: Path, erp: str = "seeded", keep: bool = False, confirm_ttl_seconds: int | None = None) -> EvalEnvironment:
    """Create an isolated, pre-wiped gateway store for one shard."""
    db_path = Path(root) / f"mizan_eval_{os.getpid()}_{index}.db"
    environment = EvalEnvironment(db_path=db_path, erp=erp, confirm_ttl_seconds=confirm_ttl_seconds)
    if not keep:
        environment.created_here = True
    return environment


def timed(func: Callable[..., Any]) -> Callable[..., Any]:
    """Tiny decorator used by the runner to time stage boundaries."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            wrapper.last_ms = (time.perf_counter() - started) * 1000.0  # type: ignore[attr-defined]

    return wrapper


__all__ = ["EvalEnvironment", "SEED_PARTNERS", "SEED_PRODUCTS", "SeededOdoo", "fresh_environment", "timed"]
