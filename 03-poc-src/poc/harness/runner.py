"""Execution engine of the Mizan evaluation harness.

Drives every case through the real chain — Arabic NL → agent runtime → tool
gateway → ERP double → read-back verification → audit — and grades each attempt.
Confirmation always goes through the proposal state machine (``confirm_and_
execute``); nothing here ever bypasses it.

Three things the previous harness could not do and this one must:

- **Speed.** One gateway + one store per shard, reused across cases, so the
  deterministic suite is a sub-second loop you will actually run while editing.
- **Parallelism without lying.** ``--jobs N`` shards by category and gives each
  shard its own store, so SQLite contention cannot fake a failure.
- **Faithful metrics.** ``repeat: same_request`` is honoured *inside* the case
  (execute → expect accepted; execute again → expect replay) instead of relying
  on global ordering, and a fake LLM's scripted answers are reported as N/A.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from poc.harness.cases import Case, Dataset, group_for_sharding
from poc.harness.environment import EvalEnvironment, fresh_environment
from poc.harness.graders import AttemptOutcome, failed_tags, grade_attempt
from poc.harness.metrics import READ_CATEGORIES, WRITE_CATEGORIES, collect_metrics
from poc.harness.records import AttemptRecord, CaseRecord, summarize_attempts
from poc.agent_runtime import CONFIRMED_EXECUTION
from poc.llm_client import (
    FakeLLMClient,
    LLMClientProtocol,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
    build_llm_client,
)

SRC_ROOT = Path(__file__).resolve().parents[2]
DETERMINISTIC = "deterministic"
LIVE = "live"
SIMULATED = "simulated"


@dataclass
class RunOptions:
    """Everything that shapes a run (and what gets recorded in the report)."""

    mode: str = DETERMINISTIC
    erp: str = "seeded"
    model: str | None = None
    repeat_reads: int = 3
    repeat_writes: int = 1
    jobs: int = 1
    keep_env: bool = False
    env_root: Path | None = None
    confirm_ttl_seconds: int | None = 30
    read_budget_ms: float | None = None
    write_budget_ms: float | None = None
    fail_fast: bool = False
    provider_timeout: float = 60.0
    provider_retries: int = 1
    temperature: float | None = None
    force_tool_choice: str | None = None
    thresholds_path: Path | None = None
    allow_cross_case_state: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def score_model(self) -> bool:
        """Model-intelligence metrics only mean something when the tool call was
        actually *decided* by an engine — not scripted from the expectation."""
        return self.mode in {LIVE, SIMULATED}

    def repeats_for(self, case: Case) -> int:
        if case.category in READ_CATEGORIES:
            return max(1, self.repeat_reads)
        if case.category in WRITE_CATEGORIES:
            return max(1, self.repeat_writes)
        return 1


class _InstrumentedLLM:
    """Wraps any client to capture latency and token usage per call."""

    def __init__(self, inner: LLMClientProtocol) -> None:
        self._inner = inner
        self.lock = threading.Lock()
        self.last_ms = 0.0
        self.last_usage: dict[str, int] = {"input": 0, "output": 0}
        self.calls = 0
        self.provider = str(getattr(inner, "provider", "unknown"))

    def chat(self, messages: list[LLMMessage], system: str | None = None, tools: list[LLMToolDefinition] | None = None) -> LLMResponse:
        started = time.perf_counter()
        response = self._inner.chat(messages, system=system, tools=tools)
        usage = getattr(response, "usage", None)
        with self.lock:
            self.last_ms = (time.perf_counter() - started) * 1000.0
            self.calls += 1
            if usage is not None:
                self.last_usage = {"input": int(getattr(usage, "input_tokens", 0) or 0), "output": int(getattr(usage, "output_tokens", 0) or 0)}
        return response


class Runner:
    """Runs a selected case list and returns fully graded :class:`CaseRecord`s."""

    def __init__(self, dataset: Dataset, cases: Sequence[Case], options: RunOptions) -> None:
        self.dataset = dataset
        self.cases = list(cases)
        self.options = options
        self._shared_client: LLMClientProtocol | None = None
        self._print_lock = threading.Lock()
        self._aborted = threading.Event()
        self._temp_roots: list[Path] = []
        self._env_root_path: Path | None = None
        # Parallel shards must never share state; sequential runs may opt in.
        self.allow_cross_case_state = bool(options.allow_cross_case_state) and options.jobs <= 1

    # --- llm ----------------------------------------------------------------
    def _base_client(self) -> LLMClientProtocol:
        if self._shared_client is None:
            if self.options.mode == SIMULATED:
                from poc.simulated_llm import SimulatedLLMClient

                self._shared_client = SimulatedLLMClient()
            elif self.options.mode == LIVE:
                self._shared_client = build_llm_client(
                    self.options.model,
                    temperature=self.options.temperature,
                    timeout=self.options.provider_timeout,
                    retries=self.options.provider_retries,
                    force_tool_choice=self.options.force_tool_choice,
                )
            else:  # deterministic: never touches the network
                self._shared_client = FakeLLMClient()
        return self._shared_client

    def _client_for(self, case: Case) -> LLMClientProtocol:
        if self.options.mode == DETERMINISTIC:  # scripted from the expectation → never scored
            if case.expected_tool is None or "text_only" in case.tags:
                # Cases that expect no tool call (conversation, prompt-injection
                # that must be refused, destructive unknown verbs): return a
                # text-only response so the runtime replies without invoking
                # anything. The grader then checks the gateway didn't write.
                return FakeLLMClient(responses=[LLMResponse(text="محادثة نصية فقط — مفيش استدعاء لأداة.", tool_calls=())])
            return FakeLLMClient(responses=[LLMResponse(tool_calls=(_tool_call_for(case),))])
        return _InstrumentedLLM(self._base_client())

    # --- execution ----------------------------------------------------------
    def run(self, *, progress: Any = None) -> dict[str, Any]:
        started = time.perf_counter()
        if self.allow_cross_case_state:
            # Legacy mode: one shared environment, dataset order, state leaks
            # across cases. Only used to reproduce the v1 harness behaviour.
            records, environments = self._run_group(self.cases, 0, progress=progress)
        else:
            # Hermetic by default: every case gets its own scratch environment,
            # so `--jobs N` parallelizes without contention and one case can
            # never inherit (or pollute) another case's idempotency state.
            if self.options.jobs <= 1:
                pairs = [self._run_group([case], index, progress=progress) for index, case in enumerate(self.cases)]
            else:
                with ThreadPoolExecutor(max_workers=self.options.jobs) as pool:
                    pairs = list(
                        pool.map(
                            lambda pair: self._run_group([pair[1]], pair[0], progress=progress),
                            list(enumerate(self.cases)),
                        )
                    )
            records = [record for group_records, _envs in pairs for record in group_records]
            environments = [row for _records, group_envs in pairs for row in group_envs]
        records.sort(key=lambda row: (row.case_id,))
        chain = {
            "valid": all(row.get("chain_valid", True) for row in environments),
            "blocks": sum(int(row.get("blocks", 0) or 0) for row in environments),
            "environments": len(environments),
            "erp_calls": sum(int(row.get("erp_calls", 0) or 0) for row in environments),
        }
        metrics = collect_metrics(records, score_model=self.options.score_model, chain=chain)
        metrics["governance"]["duplicate_orders"] = sum(int(row.get("duplicate_orders", 0) or 0) for row in environments)
        metrics["governance"]["idempotency_conflict_detected"] = self._conflict_probe()
        elapsed = time.perf_counter() - started
        metrics["runtime"] = {
            "wall_ms": round(elapsed * 1000, 1),
            "cases_per_second": round(len(self.cases) / max(0.001, elapsed), 2),
            "executions_per_second": round(len(records) / max(0.001, elapsed), 2),
            "jobs": self.options.jobs,
            "environments": len(environments),
            "hermetic": not self.allow_cross_case_state,
        }
        return {
            "records": records,
            "metrics": metrics,
            "environments": environments,
            "duration_ms": round(elapsed * 1000, 1),
        }

    def _run_group(
        self,
        group: Sequence[Case],
        index: int,
        *,
        progress: Any,
    ) -> tuple[list[CaseRecord], list[dict[str, Any]]]:
        """Run a list of cases inside one fresh environment, then tear it down."""
        environment = fresh_environment(
            index,
            root=self._env_root(),
            erp=self.options.erp,
            keep=self.options.keep_env,
            confirm_ttl_seconds=self.options.confirm_ttl_seconds,
        )
        records: list[CaseRecord] = []
        for case in group:
            if self._aborted.is_set():
                break
            records.append(self._run_case(case, environment, progress=progress))
        env_report = {
            "index": index,
            "case_id": group[0].id if len(group) == 1 else None,
            "db_path": str(environment.db_path),
            "chain_valid": bool(environment.verify_chain().get("valid")),
            "blocks": len(environment.audit_rows(limit=5000)),
            "duplicate_orders": environment.odoo.duplicate_order_count() if environment.odoo else 0,
            "erp_calls": environment.odoo.rpc_calls if environment.odoo else 0,
        }
        environment.cleanup()
        return records, [env_report]

    def _env_root(self) -> Path:
        """One scratch root per run (kept only with --keep-env)."""
        if self._env_root_path is not None:
            return self._env_root_path
        if self.options.env_root is not None:
            path = Path(self.options.env_root)
            path.mkdir(parents=True, exist_ok=True)
        else:
            path = Path(tempfile.mkdtemp(prefix="mizan-eval-"))
            self._temp_roots.append(path)
        self._env_root_path = path
        return path

    def cleanup(self) -> None:
        if self.options.keep_env:
            return
        for root in self._temp_roots:
            shutil.rmtree(root, ignore_errors=True)
        self._temp_roots = []

    # --- one case -----------------------------------------------------------
    def _run_case(self, case: Case, environment: EvalEnvironment, *, progress: Any) -> CaseRecord:
        repeats = self.options.repeats_for(case)
        attempts: list[AttemptRecord] = []
        for run_index in range(1, repeats + 1):
            for plan in case.attempts:
                record = self._run_attempt(case, plan, environment, run=run_index)
                attempts.append(record)
                if progress is not None:
                    message = progress(case, record)
                    if message:
                        with self._print_lock:
                            print(message, flush=True)
                if self.options.fail_fast and not record.passed:
                    self._aborted.set()
                    break
            if self._aborted.is_set():
                break
        passed, tags, failures = summarize_attempts(attempts)
        per_run = [all(row.passed for row in attempts if row.run == run) for run in range(1, repeats + 1)] or [passed]
        passes = sum(1 for ok in per_run if ok)
        return CaseRecord(
            case_id=case.id,
            category=case.category,
            user=case.user,
            input=case.input,
            expected_tool=case.expected_tool,
            expected_outcome=case.expected_outcome,
            attempts=attempts,
            repeats=repeats,
            passed=passed,
            flaky=repeats > 1 and 0 < passes < repeats,
            passes=passes,
            runs=max(1, repeats),
            tags=tags,
            failures=failures,
        )

    def _run_attempt(self, case: Case, plan: Any, environment: EvalEnvironment, *, run: int) -> AttemptRecord:
        client = self._client_for(case)
        runtime = environment.runtime(llm_client=client, user_id=case.user)
        allowed = _policy_allows(runtime)
        erp = environment.odoo
        reads_before = erp.rpc_calls if erp else 0
        creates_before = len(erp.create_calls) if erp else 0
        started = time.perf_counter()
        error: str | None = None
        agent_result = None
        gateway = None
        confirm_ms = 0.0
        signed = False
        bound_arguments: dict[str, Any] = {}
        try:
            agent_result = runtime.process(case.input, history=[])
            gateway = agent_result.gateway_result
            # A confirmed execution no longer carries the proposal, so snapshot the
            # arguments the gateway actually bound and hashed at proposal time.
            bound_arguments = dict((gateway.proposal or {}).get("arguments") or {}) if gateway is not None and gateway.proposal else {}
            if gateway is not None and gateway.proposal is not None and plan.auto_confirm:
                confirm_started = time.perf_counter()
                agent_result = runtime.confirm(str(gateway.proposal["proposal_id"]))
                gateway = agent_result.gateway_result
                confirm_ms = (time.perf_counter() - confirm_started) * 1000.0
                signed = agent_result.outcome == CONFIRMED_EXECUTION
        except Exception as exc:  # a crash is a finding, not an abort
            error = f"{type(exc).__name__}: {exc}"
        total_ms = (time.perf_counter() - started) * 1000.0

        tool_name, arguments, status, error_code, audited = _observe(gateway, agent_result, environment, bound_arguments)
        latencies = {"total": round(total_ms, 1), "confirm": round(confirm_ms, 1)}
        tokens = {"input": 0, "output": 0}
        if isinstance(client, _InstrumentedLLM):
            latencies["llm"] = round(client.last_ms, 1)
            tokens = dict(client.last_usage)
        outcome = AttemptOutcome(
            case=case,
            attempt=plan,
            agent_result=agent_result,
            gateway_result=gateway,
            error=error,
            latencies=latencies,
            erp_before=reads_before,
            erp_after=erp.rpc_calls if erp else reads_before,
            create_calls_before=creates_before,
            create_calls_after=len(erp.create_calls) if erp else creates_before,
            audited=audited,
            text=str(getattr(agent_result, "response_ar", "") or ""),
            tool=tool_name,
            arguments=arguments,
            status=status,
            error_code=error_code,
            tokens=tokens,
            signed=signed,
            stages=tuple(getattr(agent_result, "stages", ()) or ()),
            answer=(agent_result.answer.to_dict() if getattr(agent_result, "answer", None) is not None else {}),
            result_payload=dict(getattr(gateway, "result", None) or {}) if gateway is not None else {},
        )
        checks = grade_attempt(
            outcome,
            mode=self.options.mode,
            allowed_users=allowed,
            read_budget_ms=self.options.read_budget_ms,
            write_budget_ms=self.options.write_budget_ms,
        )
        return AttemptRecord(
            case_id=case.id,
            attempt=plan.index,
            total_attempts=plan.total,
            run=run,
            user=case.user,
            category=case.category,
            input=case.input,
            expected_tool=case.expected_tool,
            actual_tool=tool_name,
            arguments=arguments,
            status=status,
            error_code=error_code,
            expected_outcome=plan.expect_outcome,
            audited=audited,
            erp_reads=outcome.erp_after - outcome.erp_before,
            erp_creates=outcome.create_calls_after - outcome.create_calls_before,
            latency_ms=latencies,
            tokens=tokens,
            checks=[row.to_dict() for row in checks],
            tags=list(failed_tags(checks)),
            passed=all(row.passed is not False for row in checks),
            text=outcome.text,
            text_chars=len(outcome.text),
            sections=len(outcome.answer.get("sections") or []),
            kpis=len(outcome.answer.get("kpis") or []),
            error=error,
            stages=list(outcome.stages),
        )

    # --- governance probes --------------------------------------------------
    def _conflict_probe(self) -> bool:
        """Same key, different fingerprint ⇒ conflict (unreachable via the agent,
        because identical args derive an identical key — so assert it at the store)."""
        from poc.idempotency import IdempotencyStore, compute_idempotency_key, compute_request_fingerprint

        env_root = self._env_root()
        probe_db = Path(env_root) / f"mizan_probe_{os.getpid()}.db"
        try:
            environment = EvalEnvironment(db_path=probe_db, erp="seeded")
            arguments = {"customer_id": 42, "lines": [{"product_id": 55, "quantity": 20}]}
            other = {"customer_id": 43, "lines": [{"product_id": 55, "quantity": 20}]}
            key = compute_idempotency_key("poc_tenant_001", "sales_user@test", "sales.order.create", arguments)
            store = IdempotencyStore(probe_db)
            store.reserve(
                idempotency_key=key,
                request_fingerprint=compute_request_fingerprint("poc_tenant_001", "sales_user@test", "sales.order.create", "1.0.0", arguments),
                tool_name="sales.order.create",
                tool_version="1.0.0",
                tenant_id="poc_tenant_001",
                user_id="sales_user@test",
            )
            outcome = store.reserve(
                idempotency_key=key,
                request_fingerprint=compute_request_fingerprint("poc_tenant_001", "sales_user@test", "sales.order.create", "1.0.0", other),
                tool_name="sales.order.create",
                tool_version="1.0.0",
                tenant_id="poc_tenant_001",
                user_id="sales_user@test",
            )
            return outcome.status == "conflict"
        except Exception:
            return False
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(f"{probe_db}{suffix}").unlink(missing_ok=True)
                except OSError:
                    pass


# --------------------------------------------------------------------- helpers


def _tool_call_for(case: Case) -> LLMToolCall:
    arguments = dict(case.expected_args or {})
    if not arguments:
        arguments = {"query": "محمد"}
    return LLMToolCall(name=str(case.expected_tool or "customer.search"), arguments=arguments, call_id=f"{case.id}-scripted")


def _policy_allows(runtime: Any) -> dict[str, tuple[str, ...]]:
    """user → allowed tool names, straight from the server-owned policy engine."""
    engine = runtime.gateway.policy_engine
    registry = engine.registry
    out: dict[str, tuple[str, ...]] = {}
    for user_id in ("sales_user@test", "readonly_user@test", "no_access_user@test"):
        allowed: list[str] = []
        for name in registry.names():
            decision = engine.evaluate(
                {
                    "user_id": user_id,
                    "tenant_id": "poc_tenant_001",
                    "tool_name": name,
                    "tool_version": registry.get(name)["tool_version"],
                }
            )
            if decision.decision in ("allowed", "confirmation_required"):
                allowed.append(name)
        out[user_id] = tuple(allowed)
    return out


def _observe(
    gateway: Any,
    agent_result: Any,
    environment: EvalEnvironment,
    bound_arguments: Mapping[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any], str | None, str | None, bool]:
    tool_name = getattr(gateway, "tool_name", None) or getattr(getattr(agent_result, "tool_call", None), "name", None)
    arguments: dict[str, Any] = {}
    proposal = getattr(gateway, "proposal", None) if gateway is not None else None
    if isinstance(proposal, Mapping) and proposal.get("arguments"):
        arguments = dict(proposal["arguments"])  # what the gateway actually bound and hashed
    elif bound_arguments:
        arguments = dict(bound_arguments)
    elif agent_result is not None and getattr(agent_result, "tool_call", None) is not None:
        arguments = dict(agent_result.tool_call.arguments)
    status = getattr(gateway, "status", None)
    if status is None and agent_result is not None:
        status = getattr(agent_result, "outcome", None)
    error_code = getattr(gateway, "error_code", None)
    audited = False
    if gateway is not None:
        if getattr(gateway, "audit_id", None) is not None:
            audited = True
        elif getattr(gateway, "execution_id", None):
            rows = environment.audit_rows(limit=5000, request_id=f"verify-{gateway.execution_id}")
            audited = len(rows) == 1
    return tool_name, arguments, status, error_code, audited


__all__ = ["DETERMINISTIC", "LIVE", "SIMULATED", "Runner", "RunOptions"]
