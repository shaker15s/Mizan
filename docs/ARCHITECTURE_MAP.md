# ARCHITECTURE_MAP — Symbol-level map of the current POC

**Generated:** 2026-09-20
**Source of truth:** `03-poc-src/poc/**/*.py` (read 2026-09-20 against commit `fb869d6`)

---

## 1. Layer diagram (runtime wiring)

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                         BROWSER (vanilla ES)                            │
│  index.html · styles.css · app.js · ui.js · markdown.js                 │
└───────────────┬──────────────────────────────────────────────────────────┘
                │  HTTP + SSE (stdlib http.server)
                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     WEB SERVER  (poc/web_server.py)                     │
│  Routes:                                                                │
│   GET  /           → static files                                       │
│   POST /api/chat        → AgentRuntime.process()                        │
│   POST /api/confirm     → AgentRuntime.confirm()                        │
│   POST /api/decline     → AgentRuntime.decline()                        │
│   POST /api/amend       → AgentRuntime.amend_proposal()                 │
│   GET  /api/audit       → AuditStore.list()                             │
│   GET  /api/settings    → Settings manifest                             │
│   POST /api/settings    → Settings hot-update                           │
│   GET  /api/health      → liveness                                      │
│   POST /api/replay      ← DEV ONLY                                      │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ in-process calls (POC: module isolation, not security)
                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│            AGENT RUNTIME  (poc/agent_runtime.py)                        │
│                                                                         │
│  · Builds system prompt (poc/prompts.py)                                │
│  · Emits tool definitions from ToolRegistry                             │
│  · Calls LLM (poc/llm_client.py → Anthropic / OpenAI-compat / FakeLLM)  │
│  · Validates tool call against registry (jsonschema)                    │
│  · Optional bounded repair turn                                         │
│  · Issues stage events (intake→llm_call→schema_validation→authz→        │
│    execute→await_signature→verification→audit→answer)                   │
│  · Wraps result into Answer (poc/answer.py, poc/responder.py)           │
│  · confirm() / decline() / amend_proposal() are forwarded to Gateway    │
│                                                                         │
│  INBOX: natural language Arabic (or code-switched)                      │
│  OUTBOX: AgentResult → composed Arabic answer + structured Answer       │
└───────────────┬──────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│            TOOL GATEWAY  (poc/gateway.py)  ── AUTHORITY BOUNDARY        │
│                                                                         │
│  ToolGatewayRequest envelope validation                                 │
│     └─ ToolRegistry lookup + version pinning                            │
│     └─ jsonschema validation                                            │
│     └─ PolicyEngine.evaluate        (poc/authz.py)                      │
│     └─ IdempotencyStore.reserve     (poc/idempotency.py)                │
│          → RESERVED / IN_PROGRESS / CONFLICT /                          │
│            REPLAYED / RECONCILIATION_REQUIRED                           │
│     └─ ConfirmationStore.create_proposal for mutating (poc/confirmation)│
│     └─ (reads) _execute_read  → Odoo client                             │
│     └─ AuditStore.append   (poc/audit_store.py)                         │
│                                                                         │
│  confirm_and_execute:                                                   │
│     └─ ConfirmationStore.approve (9-point recheck)                      │
│     └─ execute_verified                                                 │
│           ├─ product existence read                                     │
│           ├─ Odoo create                                                │
│           ├─ verify_sales_order_creation (poc/verification.py)          │
│           ├─ IdempotencyStore.complete / fail (AMBIGUOUS)               │
│           └─ audit verification record                                  │
└─────────┬──────────────────────┬────────────────────────┬───────────────┘
          │                      │                        │
          ▼                      ▼                        ▼
┌──────────────────┐  ┌─────────────────────┐  ┌─────────────────────────┐
│   Odoo client    │  │   SQLite stores     │  │   Harness (test)        │
│ (poc/odoo_client) │  │  · audit_log        │  │ (poc/harness/*)         │
│  · JSON-2 RPC    │  │  · idempotency_keys │  │  · cases.py             │
│  · global CB     │  │  · proposals        │  │  · runner.py            │
│  · structured    │  │  WAL mode           │  │  · graders.py           │
│    exceptions    │  │  init: poc/db/init  │  │  · render/*             │
└──────────────────┘  └─────────────────────┘  └─────────────────────────┘
```

**Important:** In today's POC the LLM, Gateway, Odoo client, and Web server all live in **one OS process** (module isolation). That is fine for a POC but is NOT a production trust boundary. The plan (§40, §52) calls for a separate secret/connector boundary later.

---

## 2. Concrete symbol index

### 2.1 `poc.tool_contracts`
* `TOOL_VERSION = "1.0.0"`
* `ToolRegistry`
  * `get(name) -> dict` (deep copy)
  * `names() -> tuple[str, ...]` (deterministic order)
* `get_registry() -> ToolRegistry` — returns a fresh static registry (not a singleton; callers are expected to share one)
* `_TOOL_CONTRACTS` — dict keyed by tool name

### 2.2 `poc.authz`
* `PolicyEngine(policy_path, registry)`
  * `evaluate(request: {user_id, tenant_id, tool_name, tool_version}) -> PolicyDecision`
* `PolicyDecision(decision, user_id, tenant_id, tool_name, tool_version, reason, policy_rule_id, requires_confirmation)`
* `PolicyUser(user_id, tenant_id, role, allowed_tools, denied_tools)`
* Decisions: `ALLOWED = "allowed"`, `DENIED = "denied"`, `CONFIRMATION_REQUIRED = "confirmation_required"`
* Rule IDs: `RULE_MALFORMED_REQUEST`, `RULE_UNKNOWN_USER`, `RULE_UNKNOWN_TENANT`, `RULE_TOOL_NOT_FOUND`, `RULE_TOOL_VERSION_MISMATCH`, `RULE_EXPLICIT_DENY`, `RULE_PERMISSION_DENIED`, `RULE_CONFIRMATION_REQUIRED`, `RULE_ALLOWED`

### 2.3 `poc.idempotency`
* States (top-level): `RESERVED`, `REPLAYED`, `IN_PROGRESS`, `CONFLICT`, `RECONCILIATION_REQUIRED` (outcomes from reserve)
* DB row states: `STATE_PENDING="pending"`, `STATE_COMPLETED="completed"`, `STATE_UNKNOWN="unknown"`
* Failure outcomes: `DEFINITIVE_ERROR`, `AMBIGUOUS`, `ADOPTED`
* `IdempotencyRecord(tenant_id, user_id, idempotency_key, request_fingerprint, tool_name, tool_version, execution_id, state, result, external_record_id, created_at, updated_at)`
* `IdempotencyOutcome(status, error_code, reason, record)`
* `IdempotencyStore(db_path)`
  * `reserve(...)` — INSERT OR IGNORE + state machine; auto-renews expired pending after 300s
  * `complete(...)` — pending→completed, or unknown→completed only with reconciliation=adopted
  * `fail(..., outcome, error_code)` — pending→unknown (AMBIGUOUS) or →complete with error
  * `release(...)` — DELETE pending reservation (owner-gated)
  * `get(key, tenant, user) -> IdempotencyRecord | None`
* `compute_idempotency_key(tenant, user, tool, args) -> str[32]` — first 32 hex chars of sha256(tenant+user+tool+canonical_args)
* `compute_request_fingerprint(tenant, user, tool, ver, args) -> str[64]` — full sha256

### 2.4 `poc.confirmation`
* `CONFIRMATION_EXPIRY_SECONDS = 300`
* Proposal states: `PROPOSED`, `CONFIRMED`, `EXECUTING`, `COMPLETED`, `FAILED`
* Approval status: `APPROVED`, `ALREADY_CONFIRMED`, `EXPIRED`, `REPLAY`, `HASH_MISMATCH`, `NOT_FOUND`, `IDENTITY_MISMATCH`, `TENANT_MISMATCH`, `TOOL_MISSING`, `VERSION_MISMATCH`, `POLICY_DENIED`, `NOT_CONFIRMATION_REQUIRED`, `DECLINED`
* `Proposal(proposal_id, tool_name, tool_version, arguments, operation_hash, user_id, tenant_id, state, created_at, expires_at, confirmed_at, executed_at)`
* `ApprovalResult(status, error_code, reason, proposal_id, tool_name, tool_version, user_id, tenant_id, operation_hash, state, idempotency_key, execution_id, arguments)`
* `CreationResult(status, proposal, error_code, reason, idempotency_key)`
* `ConfirmationStore(db_path, registry, policy_engine, expiry_seconds)`
  * `create_proposal(...) -> CreationResult`
  * `get_proposal(proposal_id) -> Proposal | None`
  * `approve(proposal_id, user, tenant) -> ApprovalResult` — 9-point check
  * `decline(proposal_id, user, tenant) -> ApprovalResult`
* `compute_operation_hash(tool, ver, args, user, tenant, created_at) -> sha256hex`

### 2.5 `poc.audit_store`
* `AuditStore(db_path)`
  * `append(record) -> dict` — validates, chains hash, inserts
  * `get(audit_id) -> dict`
  * `list(limit, offset, request_id) -> list[dict]`
  * `verify_chain() -> {valid, reason, audit_id}`
  * `initialize() -> dict`
* `GENESIS_HASH = "0"*64`
* `compute_arguments_hash(args) -> sha256hex`
* `compute_row_hash(payload_columns, previous_hash) -> sha256hex`
* `AUDIT_COLUMNS` — 29 columns (audit_id through created_at)
* Allow-listed argument keys for sanitization: `query`, `customer_id`, `order_id`, `limit`, `lines[{product_id, quantity}]`

### 2.6 `poc.verification`
* `VerificationResult(passed, error)`
* `verify_sales_order_creation(client, order_id, expected_customer_id, expected_lines, expected_client_order_ref)`
* Checks:
  * record exists, exactly 1
  * `client_order_ref == idempotency_key` (provenance)
  * `partner_id == expected_customer_id`
  * `state == "draft"`
  * `amount_total ≥ 0`, `amount_untaxed ≥ 0`
  * line count matches; per-line product+quantity match via Decimal comparison
* Does NOT reproduce pricelist/tax math (correct per plan §12).

### 2.7 `poc.errors`
* `StructuredError(code, message, retryable, requires_user_action, model_visible, category)`
* Categories: `CATEGORY_GATEWAY`, `CATEGORY_POLICY`, `CATEGORY_CONFIRMATION`, `CATEGORY_IDEMPOTENCY`, `CATEGORY_IDENTITY`, `CATEGORY_VALIDATION`, `CATEGORY_ERP`, `CATEGORY_AGENT`, `CATEGORY_INTERNAL`
* Canonical codes include (not exhaustive): `INVALID_REQUEST`, `UNKNOWN_TOOL`, `UNSUPPORTED_TOOL_VERSION`, `INVALID_ARGUMENTS`, `POLICY_DENIED`, `CONFIRMATION_REQUIRED`, `IDEMPOTENCY_CONFLICT`, `IDEMPOTENCY_IN_PROGRESS`, `RECONCILIATION_REQUIRED`, `ENTITY_NOT_FOUND`, `ERP_CONNECTION_ERROR`, `ERP_TIMEOUT`, `ERP_AUTHENTICATION_ERROR`, `PERMISSION_DENIED`, `ERP_VALIDATION_ERROR`, `LLM_PROVIDER_ERROR`, `MALFORMED_TOOL_CALL`, …
* `translate_error(internal_code, message) -> StructuredError | None`
* `translate_odoo_exception(error) -> StructuredError` — MRO walk against known exception class names

### 2.8 `poc.gateway`
* Status constants: `ACCEPTED`, `DENIED`, `VALIDATION_ERROR`, `CONFIRMATION_REQUIRED`, `REPLAY`, `DECLINED`, plus idempotency outcomes (`CONFLICT`, `IN_PROGRESS`, `RECONCILIATION_REQUIRED`) and `erp_error`.
* Error codes: `INVALID_REQUEST`, `UNKNOWN_TOOL`, `UNSUPPORTED_TOOL_VERSION`, `INVALID_ARGUMENTS`, `POLICY_DENIED`, `CONFIRMATION_REQUIRED_ERROR`, `IDEMPOTENCY_IN_PROGRESS`, `AUDIT_WRITE_FAILED`, `CONFIRMATION_DECLINED`, `IDEMPOTENCY_CONFLICT`, `RECONCILIATION_REQUIRED_ERROR`, `ENTITY_NOT_FOUND`.
* `ToolGatewayRequest(request_id, user_id, tenant_id, tool_name, tool_version, arguments, idempotency_key)` — strict envelope with field whitelist
* `GatewayResult(status, error_code, reason, request_id, user_id, tenant_id, tool_name, tool_version, policy_decision, idempotency_key, execution_id, result, audit_id, requires_confirmation, proposal, structured_error_override)`
* `ToolGateway(registry, policy_engine, idempotency_store, audit_store, confirmation_store, db_path, confirm_ttl_seconds)`
  * `handle_request(request, odoo_client=None) -> GatewayResult`
  * `confirm_and_execute(proposal_id, user_id, tenant_id, odoo_client) -> GatewayResult`
  * `execute_verified(idempotency_key, execution_id, arguments, odoo_client, tenant_id, user_id) -> GatewayResult`
  * `record_confirmation_denial(proposal_id, user_id, tenant_id) -> GatewayResult`
* Read execution shapes results via `_shape_read_result(tool_name, records)` (per-tool projection).
* Arabic query fallback ladder via `_query_variants(query)` → normalized + hamza form (max 2 additional calls).

### 2.9 `poc.agent_runtime`
* Outcomes: `TOOL_CALL`, `TEXT_ONLY`, `UNKNOWN_TOOL_REJECTED`, `MALFORMED_TOOL_CALL`, `INVALID_ARGUMENTS`, `MULTIPLE_TOOL_CALLS`, `CONFIRMED_EXECUTION`, `CONFIRMATION_DECLINED`, `CONFIRMATION_AMENDED` (plus `llm_error`, `erp_error`).
* `AgentResult(outcome, response_ar, gateway_result, tool_call, error, structured_error_data, answer, stages, timings, engine, repaired)`
* `AgentRuntime(llm_client, gateway, registry, user_id, tenant_id, odoo_client_factory, settings, dialect, response_style, history_turns, enable_narrative, max_repair_turns)`
  * `process(user_input, history, on_stage, on_delta) -> AgentResult`
  * `confirm(proposal_id, on_stage) -> AgentResult`
  * `decline(proposal_id) -> AgentResult`
  * `amend_proposal(proposal_id, new_arguments) -> AgentResult`
  * `reconfigure(**changes)` — only permits the runtime-behavior settings, never identity
* Emits stages: `intake`, `llm_call`, `llm_error`, `json_recovery`, `schema_validation`, `repair_attempt`, `authz`, `execute`, `await_signature`, `verification`, `audit`, `answer`, `erp_error`, `narrative_start`, `narrative_done`, `narrative_skipped`, `delta`.
* `_validate_tool_call(call) -> (version, error, reason)` — strict against server-owned registry, no unknown tools.
* Read-only tools set: `{customer.search, customer.get, product.search, sales.order.get}`.

### 2.10 `poc.llm_client`
* `LLMClientProtocol` — interface used by runtime
* `LLMMessage(role, content)`
* `LLMResponse(text, tool_calls, usage)`
* `LLMToolCall(name, arguments, call_id)`
* `LLMToolDefinition(name, description, input_schema)`
* `AnthropicClient` and `OpenAICompatibleClient` (provider-neutral)
* `LLMProviderError` for provider failures

### 2.11 `poc.answer`, `poc.responder`
* `Answer` (headline, kpis, sections, governance, notices, analysis, status)
* `compose_answer(outcome, gateway_result, model_text, tool_name, tool_arguments, structured_error, timings) -> Answer`
* Projections: `answer.to_text()`, `answer.to_card()` (dict), HTML serialization in web server.

### 2.12 `poc.odoo_client`
* `OdooClient(url, db, username, api_key, timeout, circuit_breaker)`
  * `login()` — JSON-2 authentication
  * `search_read(model, domain, fields, limit)`
  * `read(model, ids, fields)`
  * `create(model, records)`
  * `write(model, ids, values)`
  * `unlink(model, ids)`
* Circuit breaker is **global** (one CB across all tenants/users) — flagged for improvement (plan §7.I).
* Exceptions: `OdooConnectionError`, `OdooTimeoutError`, `OdooAuthenticationError`, `OdooAuthorizationError`, `OdooValidationError`, `OdooClientError`, `OdooNotFoundError`.

### 2.13 `poc.normalization`
* `normalize_arabic(text) -> str`
* Folds: أ/إ/آ/ا → ا, ة → ه, ى/ي → ي (no diacritic stripping yet).

### 2.14 `poc.db.init`
* `DEFAULT_DB_PATH = 03-poc-src/data/poc_gateway.db`
* `initialize(db_path)` — creates tables if missing, enables WAL, returns status
* Tables: `audit_log`, `idempotency_keys`, `proposals` (DDL in this file).
* `main()` — CLI (`python -m poc.db.init`).

### 2.15 `poc.web_server`
* Factory: `build_server(host, port, runtime_provider, settings, ...)`.
* Serves static files from `poc/web/`.
* JSON API under `/api/*`; SSE streaming for chat responses via `text/event-stream`.
* CORS: `Access-Control-Allow-Origin: *` (POC — flagged).
* No authentication (POC — flagged).
* Dev routes co-exist with production routes (flagged).

### 2.16 `poc.harness`
* Entry: `python -m poc.harness --mode {deterministic,simulated,live} ...`
* Modes: deterministic (FakeLLM + seeded ERP), simulated (rule engine + seeded ERP), live (real model + sandbox Odoo).
* Per-case hermetic DB + ERP fixtures.
* Graders: tool selection, parameters, schema, outcome, truthfulness (unsupported claims), audit chain, idempotency, duplicates, prompt injection, reasoning leaks, latency, p95, p{k} repeatability.

---

## 3. Cross-cutting invariants currently enforced

The following invariants hold in today's code and are verified by existing tests.
They must not regress as we introduce the canonical state machine:

1. **The LLM never picks a tool outside the server-owned registry.**
   `agent_runtime._validate_tool_call` raises `UNKNOWN_TOOL_REJECTED`;
   there is no "invoke arbitrary ERP RPC" code path.
2. **The LLM never supplies identity or tenant.**
   `AgentRuntime.user_id/tenant_id` come from server config, never from the model.
3. **Arguments are JSON-Schema validated before any side effect.**
4. **Mutation tools require confirmation; they cannot silently execute.**
   `contract["requiresConfirmation"] is True` → path goes through proposal.
5. **Idempotency key is server-derived from tenant+user+tool+canonical args.**
   Client-supplied keys are validated to equal the server-derived key, else rejected.
6. **A proposal can only be approved by the same user/tenant that created it, within expiry, with unchanged operation hash, against unchanged policy and unchanged tool version.**
7. **The audit chain is SHA-256 linked to GENESIS; `audit_store.verify_chain()` validates it.**
8. **Sensitive keys (api_key, password, token, secret, …) are stripped before persisting into idempotency results.**
9. **Audit argument sanitization allowlist prevents PII/credential leakage into audit rows.**
10. **Successful sales-order claims report the real Odoo order_id from read-back verification; no fabricated IDs.**
11. **Post-write errors are classified as AMBIGUOUS when a create was attempted, preventing blind retry.**
12. **No stack traces leak out of the web error path** (structured errors only).
