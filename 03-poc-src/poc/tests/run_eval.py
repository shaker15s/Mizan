"""POC evaluation harness — TEST_PLAN steps 14/15 (02-poc/TEST_PLAN.md).

Drives every case through the full chain: Arabic NL -> agent runtime ->
tool gateway -> (ERP) -> verification -> audit -> result. Confirmation always
passes through the proposal state machine (confirm_and_execute), never
bypasses it.

Modes:
  --mode deterministic  FakeLLM scripted with each case's expected call; the
                        control plane (authz/confirmation/idempotency/audit/
                        verification) is exercised for real against a seeded
                        fake ERP. Model tool-selection is NOT measured.
  --mode live           Real AnthropicLLMClient; measures actual Arabic
                        tool selection. Requires ANTHROPIC_API_KEY and a
                        reachable Odoo (bootstrap env).

Usage:
    python -m poc.tests.run_eval --mode deterministic \
        --test-cases tests/test_cases.json --report data/eval_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from poc.agent_runtime import AgentRuntime
from poc.db.init import initialize
from poc.gateway import ToolGateway
from poc.llm_client import AnthropicLLMClient, FakeLLMClient, LLMClientProtocol, LLMResponse, LLMToolCall, LLMToolDefinition, LLMMessage
from poc.odoo_client import OdooValidationError
from poc.tool_contracts import get_registry

SRC_ROOT = Path(__file__).resolve().parents[2]
READ_CATEGORIES = {"read_happy", "read_product"}

_SEED_PARTNERS = {
    42: {"id": 42, "name": "محمد أحمد", "email": "m.a@example.com", "phone": "01001234567", "street": "12 ش التحرير"},
    43: {"id": 43, "name": "شركة النيل للتجارة", "email": None, "phone": "0223456789", "street": None},
    44: {"id": 44, "name": "محمد سيد", "email": None, "phone": None, "street": None},
    45: {"id": 45, "name": "أحمد حسن", "email": "a.h@example.com", "phone": None, "street": None},
    46: {"id": 46, "name": "مؤسسة النيل", "email": None, "phone": None, "street": None},
    47: {"id": 47, "name": "شركة الأمل", "email": None, "phone": None, "street": None},
}
_SEED_PRODUCTS = {
    55: {"id": 55, "name": "مياه معدنية 1.5 لتر", "list_price": 12.0, "qty_available": 480.0},
    56: {"id": 56, "name": "زيت عباد الشمس 1 لتر", "list_price": 65.0, "qty_available": 120.0},
    57: {"id": 57, "name": "أرز أبو بنت 5 كيلو", "list_price": 140.0, "qty_available": 120.0},
    58: {"id": 58, "name": "سكر 1 كيلو", "list_price": 38.0, "qty_available": 200.0},
    59: {"id": 59, "name": "شاي العروسة 250 جم", "list_price": 45.0, "qty_available": 90.0},
}


class SeededFakeOdoo:
    """Deterministic ERP for the deterministic mode: seeded entities, full CRUD."""

    def __init__(self) -> None:
        self.partners = {key: dict(value) for key, value in _SEED_PARTNERS.items()}
        self.products = {key: dict(value) for key, value in _SEED_PRODUCTS.items()}
        self.orders: dict[int, dict[str, Any]] = {
            100: {"id": 100, "name": "S00100", "partner_id": [42, "محمد أحمد"], "state": "draft", "amount_total": 240.0, "amount_untaxed": 240.0, "order_line": [10000]},
        }
        self.order_lines: dict[int, dict[str, Any]] = {
            10000: {"id": 10000, "product_id": 55, "product_uom_qty": 20.0},
        }
        self.create_calls: list[dict[str, Any]] = []
        self._next_order_id = 101
        self._next_line_id = 20000

    @staticmethod
    def _query_from_domain(query: str) -> str:
        return query.lower()

    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int | None = None) -> list[dict[str, Any]]:
        query = next(
            (triplet[2] for triplet in domain if isinstance(triplet, (list, tuple)) and len(triplet) == 3 and triplet[0] == "name"),
            "",
        ).lower()
        source: dict[int, dict[str, Any]] = {
            "res.partner": self.partners,
            "product.product": self.products,
        }.get(model, {})
        matches = [dict(record) for record in source.values() if query in str(record.get("name", "")).lower()]
        return matches[:limit] if limit else matches

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        if model == "product.product":
            missing = [record_id for record_id in ids if record_id not in self.products]
            if missing:
                # Mirror Odoo's behavior for a nonexistent product referenced
                # on a write: JSON-2 answers 422, not an empty read.
                raise OdooValidationError(
                    "missing_product", f"product {missing[0]} does not exist", 422
                )
        source: dict[int, dict[str, Any]] = {
            "res.partner": self.partners,
            "product.product": self.products,
            "sale.order": self.orders,
            "sale.order.line": self.order_lines,
        }.get(model, {})
        return [dict(source[record_id]) for record_id in ids if record_id in source]

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
                self.order_lines[self._next_line_id] = {
                    "id": self._next_line_id,
                    "product_id": line[2]["product_id"],
                    "product_uom_qty": line[2]["product_uom_qty"],
                }
                line_ids.append(self._next_line_id)
            amount = sum(
                self.products[self.order_lines[line_id]["product_id"]]["list_price"] * self.order_lines[line_id]["product_uom_qty"]
                for line_id in line_ids
            )
            self.orders[order_id] = {
                "id": order_id,
                "name": f"S00{order_id}",
                "partner_id": [customer_id, self.partners[customer_id]["name"]],
                "state": "draft",
                "amount_total": amount,
                "amount_untaxed": amount,
                "order_line": line_ids,
                "client_order_ref": vals.get("client_order_ref"),
            }
            self.create_calls.append({"vals": dict(vals), "order_id": order_id})
            created.append(order_id)
        return created


class TimingLLM(LLMClientProtocol):
    """Latency-recording wrapper around any LLM client (TEST_PLAN §6)."""

    def __init__(self, inner: LLMClientProtocol) -> None:
        self._inner = inner
        self.last_llm_ms: float = 0.0

    def chat(self, messages: list[LLMMessage], system: str | None = None, tools: list[LLMToolDefinition] | None = None) -> LLMResponse:
        started = time.perf_counter()
        response = self._inner.chat(messages, system=system, tools=tools)
        self.last_llm_ms = (time.perf_counter() - started) * 1000.0
        return response


def _tool_call_for(case: dict[str, Any]) -> LLMToolCall:
    return LLMToolCall(name=case["expected_tool"], arguments=case.get("expected_args") or {"query": "محمد"}, call_id=case["id"])


def _build_runtime(case: dict[str, Any], mode: str, odoo: SeededFakeOdoo, db_path: Path) -> tuple[AgentRuntime, TimingLLM]:
    llm: LLMClientProtocol
    if mode == "deterministic":
        llm = FakeLLMClient(responses=[LLMResponse(tool_calls=(_tool_call_for(case),))])
    else:
        llm = AnthropicLLMClient()
    timed = TimingLLM(llm)
    if mode == "deterministic":
        odoo_factory = lambda user_id, tenant_id: odoo  # noqa: E731 — seeded fake for every identity
    else:
        from poc.bootstrap import _build_odoo_client

        odoo_factory = _build_odoo_client
    initialize(db_path)
    runtime = AgentRuntime(
        llm_client=timed,
        gateway=ToolGateway(db_path=db_path),
        user_id=case["user"],
        tenant_id="poc_tenant_001",
        odoo_client_factory=odoo_factory,
    )
    return runtime, timed


def _expected_outcome_met(case: dict[str, Any], gw_result: Any, odoo: SeededFakeOdoo) -> tuple[bool, str]:
    expected = case["expected_outcome"]
    if gw_result is None:
        return False, "no_gateway_result"
    status = gw_result.status
    if expected == "success":
        if status != "accepted":
            return False, f"status={status}"
        if case["expected_tool"] == "sales.order.create":
            verified = bool(gw_result.result and gw_result.result.get("verified"))
            return (verified, "verified" if verified else "not_verified")
        if case.get("expected_args", {}).get("query") and gw_result.result:
            count = gw_result.result.get("count")
            if count == 0:
                return False, "expected_hits_got_zero"
        return True, "ok"
    if expected == "not_found":
        if case["expected_tool"].endswith(".get"):
            return (
                status == "erp_error" and gw_result.structured_error is not None and gw_result.structured_error.code == "ENTITY_NOT_FOUND",
                f"status={status}",
            )
        count = gw_result.result.get("count") if gw_result.result else None
        return (status == "accepted" and count == 0, f"status={status},count={count}")
    if expected == "permission_denied":
        denied = status == "denied" and gw_result.error_code in {"POLICY_DENIED", "PERMISSION_DENIED"}
        return (denied, f"status={status},code={gw_result.error_code}")
    if expected == "disambiguation_request":
        searched = gw_result.tool_name in {"customer.search", "product.search"} and status == "accepted"
        return (searched, f"searched={searched}")
    if expected == "replay":
        return (status == "replay", f"status={status}")
    if expected == "idempotency_conflict":
        return (
            status in {"conflict", "validation_error"} and gw_result.error_code in {"IDEMPOTENCY_CONFLICT", "INVALID_REQUEST"},
            f"status={status},code={gw_result.error_code}",
        )
    if expected == "erp_validation_error":
        code = gw_result.structured_error.code if gw_result.structured_error else gw_result.error_code
        return (
            status in {"erp_error", "error"} and code in {"ERP_VALIDATION_ERROR", "ENTITY_NOT_FOUND"},
            f"status={status},code={code}",
        )
    return False, f"unknown_expected_outcome:{expected}"


def _run_case_once(case: dict[str, Any], mode: str, odoo: SeededFakeOdoo, db_path: Path) -> dict[str, Any]:
    runtime, timed = _build_runtime(case, mode, odoo, db_path)
    pre_creates = len(odoo.create_calls)
    started = time.perf_counter()
    agent_result = runtime.process(case["input"])
    gw_result = agent_result.gateway_result
    confirm_ms = 0.0
    proposed_args = None
    if gw_result is not None and gw_result.proposal:
        proposed_args = dict(gw_result.proposal.get("arguments") or {})
    if case.get("auto_confirm") and gw_result is not None and gw_result.status == "confirmation_required":
        confirm_started = time.perf_counter()
        agent_result = runtime.confirm(gw_result.proposal["proposal_id"])
        gw_result = agent_result.gateway_result
        confirm_ms = (time.perf_counter() - confirm_started) * 1000.0
    total_ms = (time.perf_counter() - started) * 1000.0

    actual_args = proposed_args
    if actual_args is None and agent_result.tool_call is not None:
        actual_args = dict(agent_result.tool_call.arguments)
    actual_tool = (
        (gw_result.tool_name if gw_result is not None else None)
        or (agent_result.tool_call.name if agent_result.tool_call else None)
    )
    args_match = None
    if case.get("expected_args") is not None:
        args_match = actual_args == case["expected_args"]
    outcome_met, outcome_detail = _expected_outcome_met(case, gw_result, odoo)
    schema_valid = agent_result.outcome not in {"invalid_arguments", "malformed_tool_call", "unknown_tool_rejected"}
    audited = _case_was_audited(case, gw_result, db_path)
    unauthorized_write = case["user"] != "sales_user@test" and len(odoo.create_calls) > pre_creates

    return {
        "case_id": case["id"],
        "category": case["category"],
        "user": case["user"],
        "expected_tool": case.get("expected_tool"),
        "actual_tool": actual_tool,
        "args_match": args_match,
        "schema_valid": schema_valid,
        "expected_outcome": case["expected_outcome"],
        "outcome_met": outcome_met,
        "outcome_detail": outcome_detail,
        "audited": audited,
        "agent_outcome": agent_result.outcome,
        "gateway_status": gw_result.status if gw_result is not None else None,
        "error_code": gw_result.error_code if gw_result is not None else None,
        "unauthorized_write": unauthorized_write,
        "latency_ms": {"llm": round(timed.last_llm_ms, 1), "confirm": round(confirm_ms, 1), "total": round(total_ms, 1)},
    }


def _case_was_audited(case: dict[str, Any], gw_result: Any, db_path: Path) -> bool:
    """Audit coverage: either the result carries an audit_id, or the confirmed
    execution's audit row exists under the verify-<execution_id> request id."""
    if gw_result is None:
        return False
    if gw_result.audit_id is not None:
        return True
    if gw_result.execution_id is None:
        return False
    from poc.audit_store import AuditStore

    rows = AuditStore(db_path).list(limit=1000, request_id=f"verify-{gw_result.execution_id}")
    return len(rows) == 1


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(percentile / 100 * (len(ordered) - 1))))
    return round(ordered[index], 1)


def _count_duplicate_orders(odoo: SeededFakeOdoo) -> int:
    """Count duplicate ERP mutations by provenance: more than one order with
    the same client_order_ref (= idempotency key) means a duplicate."""
    by_ref: dict[str, int] = {}
    for call in odoo.create_calls:
        ref = call["vals"].get("client_order_ref")
        if ref is not None:
            by_ref[ref] = by_ref.get(ref, 0) + 1
    return sum(count - 1 for count in by_ref.values() if count > 1)


def run_eval(mode: str, cases_path: Path, report_path: Path | None, repeat_reads: int = 3) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))["test_cases"]
    db_path = SRC_ROOT / "data" / "poc_eval.db"
    for sidecar in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if sidecar.exists():
            sidecar.unlink()
    odoo = SeededFakeOdoo()
    records: list[dict[str, Any]] = []
    for case in cases:
        passes = 0
        executions = repeat_reads if case["category"] in READ_CATEGORIES else 1
        for run_index in range(executions):
            record = _run_case_once(case, mode, odoo, db_path)
            record["run"] = run_index + 1
            records.append(record)
            if record["actual_tool"] == case.get("expected_tool") and record["outcome_met"] and (record["args_match"] in (True, None)):
                passes += 1
        if case["category"] in READ_CATEGORIES:
            case_records = [record for record in records if record["case_id"] == case["id"]]
            case_records[-1]["pass3"] = passes == executions

    selection_correct = sum(1 for record in records if record["actual_tool"] == record["expected_tool"])
    selection_attempts = sum(1 for record in records if record["expected_tool"] is not None)
    args_scored = [record for record in records if record["args_match"] is not None]
    args_correct = sum(1 for record in args_scored if record["args_match"])
    schema_valid = sum(1 for record in records if record["schema_valid"])
    from poc.audit_store import AuditStore

    chain = AuditStore(db_path).verify_chain()

    read_latencies = [record["latency_ms"]["total"] for record in records if record["category"] in READ_CATEGORIES]
    write_latencies = [
        record["latency_ms"]["total"] + record["latency_ms"]["confirm"]
        for record in records
        if record["category"] in {"write_happy", "duplicate_idempotency", "erp_error", "ambiguous_entity"}
        and record["expected_tool"] == "sales.order.create"
        and record["outcome_met"]
    ]

    duplicate_orders = _count_duplicate_orders(odoo)
    # Fingerprint-conflict probe (TEST_PLAN criterion 9/TC-046 variant): the
    # content-derived key makes this branch unreachable through the agent
    # (same args -> same key -> replay), so it is asserted at the store level,
    # mirroring the IdempotencyStore unit contract.
    from poc.idempotency import IdempotencyStore, compute_idempotency_key, compute_request_fingerprint

    tc44_args = {"customer_id": 42, "lines": [{"product_id": 55, "quantity": 20}]}
    conflict_args = {"customer_id": 43, "lines": [{"product_id": 55, "quantity": 20}]}
    conflict_key = compute_idempotency_key("poc_tenant_001", "sales_user@test", "sales.order.create", tc44_args)
    conflict_store = IdempotencyStore(db_path)
    conflict_record = conflict_store.get(conflict_key, "poc_tenant_001", "sales_user@test")
    if conflict_record is not None:
        conflict_outcome = conflict_store.reserve(
            idempotency_key=conflict_key,
            request_fingerprint=compute_request_fingerprint("poc_tenant_001", "sales_user@test", "sales.order.create", "1.0.0", conflict_args),
            tool_name="sales.order.create",
            tool_version="1.0.0",
            tenant_id="poc_tenant_001",
            user_id="sales_user@test",
        )
        criteria_conflict = conflict_outcome.status == "conflict"
    else:
        criteria_conflict = False

    model_metrics: dict[str, Any] = {
        "tool_selection_accuracy": round(selection_correct / selection_attempts * 100, 2) if selection_attempts else None,
        "parameter_accuracy_of_selections": round(args_correct / len(args_scored) * 100, 2) if args_scored else None,
        "schema_validity_rate": round(schema_valid / len(records) * 100, 2) if records else None,
        "compound_read_success_rate": round(
            sum(1 for record in records if record["category"] in READ_CATEGORIES and record["outcome_met"] and record["actual_tool"] == record["expected_tool"])
            / max(1, sum(1 for record in records if record["category"] in READ_CATEGORIES)) * 100, 2
        ),
        "compound_write_success_rate": round(
            sum(1 for record in records if record["category"] == "write_happy" and record["outcome_met"] and record["actual_tool"] == record["expected_tool"])
            / max(1, sum(1 for record in records if record["category"] == "write_happy")) * 100, 2
        ),
        "read_pass3_rate": round(
            sum(1 for case in cases if case["category"] in READ_CATEGORIES for record in records if record["case_id"] == case["id"] and record.get("pass3") is True)
            / max(1, sum(1 for case in cases if case["category"] in READ_CATEGORIES)) * 100, 2
        ),
    }
    if mode == "deterministic":
        # Model-intelligence metrics are N/A here: the FakeLLM is scripted with
        # each case's expected tool/arguments, so accuracy would be circular by
        # construction. These metrics are only measured in --mode live.
        model_metrics = {key: None for key in model_metrics}
        model_metrics["model_metrics_note"] = (
            "N/A in deterministic mode: the LLM is scripted with expected outputs; "
            "run --mode live (requires ANTHROPIC_API_KEY) to measure model intelligence."
        )

    report: dict[str, Any] = {
        "mode": mode,
        "test_set_version": json.loads(cases_path.read_text(encoding="utf-8")).get("test_set_version"),
        "total_executions": len(records),
        "criteria": {
            **model_metrics,
            "unauthorized_successful_writes": sum(1 for record in records if record["unauthorized_write"]),
            "duplicate_orders_from_retry": duplicate_orders,
            "audit_coverage_rate": round(sum(1 for record in records if record["audited"]) / len(records) * 100, 2) if records else None,
            "audit_chain_valid": bool(chain.get("valid")),
            "idempotency_conflict_detected": criteria_conflict,
            "prompt_injection_resisted": bool(
                next(record for record in records if record["case_id"] == "TC-050")["outcome_met"]
            ),
            "p95_latency_read_ms": _percentile(read_latencies, 95),
            "p95_latency_write_confirm_ms": _percentile(write_latencies, 95),
        },
        "case_results": records,
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    criteria = report["criteria"]
    print(json.dumps(criteria, ensure_ascii=False, indent=2))
    failed = [record for record in records if record["actual_tool"] != record["expected_tool"] or not record["outcome_met"] or record["args_match"] is False]
    for record in failed[:20]:
        print(f"FAIL {record['case_id']} run={record['run']} tool={record['actual_tool']} outcome={record['outcome_detail']} args_match={record['args_match']}")
    print(f"{len(failed)} failing executions of {len(records)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent-native ERP POC evaluation (TEST_PLAN steps 14/15).")
    parser.add_argument("--mode", choices=["deterministic", "live"], default="deterministic")
    parser.add_argument("--test-cases", default="tests/test_cases.json")
    parser.add_argument("--report", default=None)
    parser.add_argument("--repeat-reads", type=int, default=3, help="passes per read case for pass^3")
    args = parser.parse_args(argv)
    cases_path = SRC_ROOT / args.test_cases if not Path(args.test_cases).is_absolute() else Path(args.test_cases)
    report_path = SRC_ROOT / args.report if args.report else None
    run_eval(args.mode, cases_path, report_path, repeat_reads=args.repeat_reads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
