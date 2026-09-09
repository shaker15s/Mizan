# Agent-Native ERP — POC Technical Design

**Date:** 7 September 2026 (final architecture correction pass: gateway store confirmed as SQLite (WAL mode) per locked POC scope — a documented deviation from the research package's PostgreSQL recommendation, see `DECISIONS.md` ADR-03; trust-boundary caveat; operation_hash binds tool_version with precise canonical JSON; re-authorization checklist at confirmation execution; idempotency split into execution correlation ID vs. dedup key; verification semantics + Odoo JSON-2 per-request-transaction warning; entity-resolution split; security claims classified)
**Status:** Design only. No code. No implementation.
**Research basis:** `00-research/` (authoritative)

---

## 1. Repository Structure

```
agent-native-erp/
├── 00-research/                      # (existing — research package)
├── 01-spec/                          # (existing — PRD)
├── 02-poc/                           # (this directory — design documents)
├── 03-poc-src/                       # POC source code (next phase)
│   ├── main.py                       # CLI entry point
│   ├── agent.py                      # Agent runtime (LLM calls, tool selection)
│   ├── gateway.py                    # Tool Gateway (trust boundary)
│   ├── odoo_client.py                # Thin Odoo JSON-2 adapter
│   ├── authz.py                      # Authorization policy evaluation
│   ├── confirmation.py               # Proposal state machine
│   ├── idempotency.py                # Idempotency store
│   ├── verification.py               # Post-write read-back verification
│   ├── audit.py                      # Audit logging with hash chain
│   ├── errors.py                     # Structured error taxonomy
│   ├── schemas/                      # JSON tool schemas
│   │   ├── customer_search.json
│   │   ├── customer_get.json
│   │   ├── product_search.json
│   │   ├── sales_order_create.json
│   │   └── sales_order_get.json
│   ├── tests/
│   │   ├── test_cases.json           # 50 test cases
│   │   ├── run_eval.py               # Test harness
│   │   └── verify_audit.py           # Hash chain integrity checker
│   ├── docker-compose.yml            # Odoo 19 + PostgreSQL (Odoo DB only)
│   ├── .env.example                  # Environment variable template
│   └── users.yaml                    # Test users + role assignments
```

---

## 2. Runtime Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Single Python Process                     │
│                                                              │
│  ┌──────────────────┐    ┌──────────────────────────────┐    │
│  │   Agent Runtime  │    │        Tool Gateway           │    │
│  │                  │    │                                │    │
│  │ - Sends NL to    │───▶│ - Validates JSON schema       │    │
│  │   LLM API        │    │ - Evaluates authorization     │    │
│  │ - Receives tool  │    │ - Manages confirmation        │    │
│  │   call from LLM  │    │   proposals                   │    │
│  │ - Constructs     │    │ - Assigns idempotency key     │    │
│  │   ToolCallRequest│    │ - Calls Odoo adapter          │    │
│  │ - Calls gateway  │    │ - Verifies post-write state   │    │
│  │                  │◀───│ - Writes audit record         │    │
│  │ - Formats result │    │ - Normalizes output           │    │
│  └──────────────────┘    └──────────────┬───────────────┘    │
│                                         │                    │
│                              ┌──────────▼───────────────┐    │
│                              │    Odoo Adapter           │    │
│                              │ - Translates to JSON-2    │    │
│                              │ - Carries API key         │    │
│                              └──────────┬───────────────┘    │
│                                         │ HTTP (localhost)    │
└─────────────────────────────────────────┼────────────────────┘
                                          │
                              ┌───────────▼──────────────┐
                              │    Odoo 19 Community      │
                              │    (Docker container)     │
                              └──────────────────────────┘
```

**Key boundary rules:**

- `agent.py` imports `gateway.py` and calls `gateway.handle_tool_call(request)`
- `agent.py` does NOT import `odoo_client.py`
- `agent.py` does NOT have access to Odoo API keys
- `gateway.py` is the ONLY module that imports `odoo_client.py`
- The LLM never holds Odoo credentials, decides authorization, bypasses confirmation, directly calls Odoo, writes audit records, or decides tenant identity

**Trust-boundary caveat (critical):** The single-process architecture provides **engineering/module isolation, NOT a true security boundary.** A compromised or buggy agent module running in the same OS process can, in principle, access the same memory, file handles, and environment variables as the gateway. The module-import rules above are enforced by convention, code review, and automated import-scanner tests — they are not OS-enforced isolation.

**Production requirement (future, out of POC scope):**
- The Agent Runtime and the Tool Gateway MUST run as separate OS processes or containers (distinct trust domains), with the gateway as the sole holder of Odoo credentials and gateway-store access.
- Secrets (Odoo API keys, store credentials) MUST NOT be accessible to the agent runtime's process environment.
- Until that separation exists, the POC's security posture is "demonstrated in POC by module discipline," not "production secure."

---

## 3. Process Boundaries

| Component | Process | Language | Justification |
|---|---|---|---|
| Agent Runtime | Same process as gateway | Python 3.12+ | Single process is simplest for POC. The trust boundary is enforced by module imports, not network separation. Production would separate. |
| Tool Gateway | Same process as agent | Python 3.12+ | The gateway is a Python class with its own interface. It receives `ToolCallRequest` objects and returns `ToolCallResponse` objects. |
| Odoo Adapter | Same process as gateway | Python 3.12+ | Thin HTTP client. 100–200 lines. |
| Audit Store | SQLite file `data/poc_gateway.db` (WAL mode) | Python stdlib `sqlite3` | Transactional, zero-setup, hash-chain-safe. Append-only is enforced by **code discipline + hash-chain verification** (no UPDATE/DELETE statements against `audit_log`; grep-enforced test), not by a database role. Classified **demonstrated in POC**; database-enforced append-only is a **production requirement** (`SECURITY_MODEL.md` §8). |
| Idempotency Store | Same SQLite file `data/poc_gateway.db` | Python stdlib `sqlite3` | Same transaction scope as audit; `BEGIN IMMEDIATE` insert-first test-and-set provides the exactly-once serialization required by §7. |
| Proposal Store | Same SQLite file `data/poc_gateway.db` | Python stdlib `sqlite3` | Confirmation lifecycle state machine (see §5a); one file, three tables. |
| Odoo 19 | Docker container | — | Official image `odoo:19.0` (tag verified on Docker Hub, last pushed 2026-08-20, amd64/arm64). |
| Odoo PostgreSQL | Docker container `db` | — | Official image `postgres:15`. Required by Odoo. Separate from the gateway store. |

**Why single process for the POC:** The POC must prove the composition (agent → gateway → Odoo), not prove network-level separation. Module-import boundaries are inspectable and testable. If the agent module has no import of `odoo_client`, it cannot call Odoo directly. This is verifiable by code review and by a simple import-scanner test. **This is module isolation, not a security boundary** — production requires the separation described in §2.

**Adapter HTTP contract (verified from Odoo master docs):** every JSON-2 request is `POST {ODOO_URL}/json/2/{model}/{method}` with headers `Authorization: bearer <per-user API key>`, `X-Odoo-Database: poc_test` (sent unconditionally — deterministic even though a single-database host may not require it), `Content-Type: application/json; charset=utf-8`, and `User-Agent: agent-native-erp-poc/0.1`. JSON-2 is **not** JSON-RPC 2.0: the body is a plain JSON object of named arguments (`ids`, `context`, plus method parameters); responses are the method's JSON return value (HTTP 200) or a structured JSON error object (HTTP 4xx/5xx, fields `name`, `message`, `arguments`, `context`, `debug`).

---

## 4. Agent Loop

```python
# agent.py (conceptual — no code written)

def run_agent(user_input: str, user_id: str, trace_id: str) -> AgentResult:
    """
    1. Build system prompt with tool catalog descriptions
    2. Send to LLM with tool definitions
    3. LLM returns either:
       a. A tool call (tool_name + arguments)
       b. A natural language response (no tool needed)
    4. If tool call:
       a. Construct ToolCallRequest(tool_name, arguments, user_id, trace_id)
       b. Call gateway.handle_tool_call(request)
       c. If gateway returns PROPOSAL_REQUIRED:
          - Present proposal to user (via CLI in POC)
          - If user confirms, call gateway.confirm_and_execute(proposal_id, user_id)
       d. Receive result (or error)
       e. Send result back to LLM for natural language formatting
    5. Return AgentResult(structured_result, human_readable_result)
    """
```

**Max tool calls per request:** 3 (bounded). If the agent exceeds this, return an error to the user.

**Stop conditions:**
- LLM returns a natural language answer (no tool call)
- Max tool calls reached
- Gateway returns a non-retryable error
- User declines confirmation

---

## 5. Tool Gateway

The gateway is the trust boundary. Every tool call passes through it. It performs these steps in order:

```
1. Validate arguments against tool's JSON schema
   → SCHEMA_INVALID if validation fails

2. Evaluate authorization policy
   → PERMISSION_DENIED if user's role does not allow this tool

3. Check if confirmation is required (tool metadata)
   → If yes, create proposal and return PROPOSAL_REQUIRED
   → Do NOT execute

4. Assign idempotency key (for write operations)
   → Check idempotency store first
   → If key found, return cached result

5. Call Odoo adapter
   → Catch connection errors, Odoo errors

6. Verify result (for write operations)
   → Read-back via sales.order.get
   → Compare expected fields

7. Write audit record (always)
   → Hash-chained append

8. Normalize result
   → Return structured JSON to agent
```

**The gateway is the ONLY component that:**
- Holds Odoo credentials (via the adapter)
- Writes audit records
- Assigns idempotency keys
- Evaluates authorization policy
- Manages confirmation proposals
- Performs verification

---

## 5a. Confirmation Lifecycle (summary — full spec in `SECURITY_MODEL.md` §5–6)

| Element | Definition |
|---|---|
| `proposal_id` | UUID v4 minted by the gateway when a `requiresConfirmation` tool call passes schema validation + authorization. Returned to the agent for display only; the agent cannot alter it. |
| `operation_hash` | SHA-256 over canonical JSON of `{tool_name, tool_version, arguments, user_id, tenant_id, created_at}`. Recomputed at **execution** time from the *stored* proposal values — any drift between what the user approved and what executes → `CONFIRMATION_HASH_MISMATCH`. |
| `expires_at` | `created_at + CONFIRMATION_EXPIRY_SECONDS` (default 300 s). Checked at execution time, not confirmation time. Expired → `CONFIRMATION_EXPIRED`; the user re-requests. |
| State machine | `proposed → confirmed → executing → completed / failed` — single-use, atomically transitioned (`UPDATE ... WHERE state='proposed'` row-locked). A second confirmation attempt hits a non-`proposed` state → `CONFIRMATION_REPLAY`. |

The idempotency key (§7) is derived from operation *content* and is therefore stable across re-proposals of the same intent; the `operation_hash` includes `created_at` and is therefore unique per proposal. These two mechanisms are deliberately different and serve different guarantees (retry-safety vs. confirmation-binding).

---

## 6. Verification

After every write, the gateway issues a read-back call to Odoo.

**What verification proves:**
- The record identified by the returned ID exists at read-back time.
- The fields the gateway compares (customer, state, amount) match the requested values at read-back time.

**What verification does NOT prove:**
- Transactional atomicity across the create and read-back calls. **Each Odoo JSON-2 API request runs in its own SQL transaction** (per official Odoo 19 documentation). The create commits when its request returns; the read-back is a separate transaction. If the process crashes between the two, the order exists but verification has not run.
- That no other change occurred between create and read-back (race window; see note below).
- That the record will remain in the verified state (future mutations are not covered).
- That any other Odoo-side side effects (email, stock reservation) completed.

**For `sales.order.create`:**
1. Odoo returns the new order's ID (e.g., `SO-00123`)
2. Gateway calls `sales.order.get` with that ID
3. Gateway checks:
   - Order exists
   - `customer_id` matches the requested customer
   - `state` equals `"draft"`
   - `amount_total` is present and non-negative
4. If any check fails → `VERIFICATION_FAILED` error, audit annotated, no user-facing success message

**Odoo JSON-2 transaction semantics (official Odoo 19 documentation):** each JSON-2 API request is dispatched and committed in its own SQL transaction. The design therefore MUST NOT pretend that create → read-back is one atomic transaction. If verification fails after a successful create, the order already exists in Odoo and the audit records the mismatch (`VERIFICATION_FAILED`); the gateway does not delete or roll back the created record in the POC.

**Race condition note (from research C11):** A read-back can race with concurrent writers. For the POC (single user, no concurrent traffic), this is acceptable. Production requires event subscription or invariant checks.

**For read operations:** No verification step (the result IS the read).

---

## 7. Idempotency

Two distinct identifiers are used. They are NOT the same value and MUST NOT be conflated:

| Identifier | Generated by | Purpose | Determinism |
|---|---|---|---|
| **A. Execution correlation ID** (`execution_id`) | The Tool Gateway (UUID v4) | Correlates one physical execution with its audit record, trace, and Odoo provenance marker. Always unique per attempt. | Random |
| **B. Caller/request idempotency key** (`idempotency_key`) | Deterministically derived from request content (or supplied deterministically by the test harness for retry tests) | Deduplicates logically identical write requests across retries. | Content-derived |

**Key derivation (POC default):**

```
idempotency_key = SHA256(tenant_id || user_id || tool_name || canonical_json(arguments)) → first 32 hex chars
```

where `canonical_json(arguments)` is a key-sorted, whitespace-free JSON serialization (identical to the arguments portion of `operation_hash`, minus `created_at`). The evaluation harness uses the same derivation (or an explicit `idempotency_key` field in the test case) so retry tests are deterministic and reproducible.

**Semantics (contract):**

| Scenario | Result |
|---|---|
| Same key + same request hash → return the original result (including a stored error); never execute twice | `IDEMPOTENCY_REPLAYED` (transparent success replay) |
| Same key + different request hash | `IDEMPOTENCY_CONFLICT` |
| Concurrent same-key execution (two callers arrive before either completes) | Exactly one executes; the other receives `IDEMPOTENCY_CONFLICT` and MUST NOT wait-and-replay in the POC |

**Why content-derived rather than UUID v4 for the dedup key:** when the gateway sends a create and the response is lost, a random key means the user's natural retry generates a brand-new key → guaranteed duplicate. A content-derived key means the retry carries the same key and hits the reconciliation path below. The gateway is both key issuer and retry handler, so the key must be a function of intent. Edge: two *legitimately identical* requests in the same second collide — for the POC this is acceptable and documented (the user changes quantity to disambiguate); production can add a client-supplied dedup salt.

**Store (SQLite WAL mode, file `data/poc_gateway.db`):**
```sql
CREATE TABLE idempotency_keys (
    tenant_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    request_hash TEXT NOT NULL,   -- SHA256(tool_name + canonical args + user + tenant)
    execution_id TEXT NOT NULL,   -- gateway-generated UUID v4, unique per attempt
    state      TEXT NOT NULL,     -- 'pending' | 'completed' | 'unknown'
    result     TEXT,              -- JSON string of normalized tool result (when completed)
    external_record_id TEXT,      -- e.g. sale.order id/name, when known
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tenant_id, user_id, key)
);
```

**Concurrency mechanism (required for exactly-once execution in the POC):**

1. **Insert-first test-and-set:** `PRAGMA journal_mode=WAL;` then `BEGIN IMMEDIATE; INSERT INTO idempotency_keys (..., state='pending') OR IGNORE; COMMIT;` — the `changes()` row-count result tells the gateway whether it won the key. SQLite primary-key uniqueness under WAL mode is the serialization point. `BEGIN IMMEDIATE` acquires a write lock before the INSERT, preventing lost-update races.
2. A losing insert (0 rows changed) re-reads the row and applies the semantics table below. Concurrent duplicates therefore get `IDEMPOTENCY_CONFLICT` — the second caller never executes and never waits.
3. The write path (insert → execute → update state → audit append) is NOT one database transaction (Odoo I/O cannot be inside a DB transaction). The `BEGIN IMMEDIATE` write lock is what serializes; the state update afterward records the outcome. A crash between insert and update leaves `state='pending'`; the reconciliation path (below) resolves it.
4. The audit write for the same tool call happens after the outcome is known; audit coverage is 100% including denials and conflicts.

**Flow for `sales.order.create`:**
1. Derive the content-addressable idempotency key; compute `request_hash`; mint `execution_id`.
2. `BEGIN IMMEDIATE; INSERT OR IGNORE with state='pending'; COMMIT;` — an atomic test-and-set. A zero-row insert means the key is already held.
3. If the row already exists:
   - `state='completed'` AND `request_hash` matches → return the stored `result` (never re-execute). **This is what makes "did my order go through?" a no-op.**
   - `state='completed'` AND `request_hash` differs → `IDEMPOTENCY_CONFLICT`.
   - `state='pending'` → another execution is in flight → `IDEMPOTENCY_CONFLICT`.
   - `state='unknown'` → **reconciliation path** (step 6) before any retry.
4. Execute via the Odoo adapter (Odoo I/O happens outside the database transaction; the insert-first test-and-set is the serialization point).
   - Success → verify (§6) → `UPDATE state='completed', result=..., external_record_id=...` → return result.
   - **Definitive Odoo rejection** (HTTP 4xx with a structured error, e.g. validation) → `UPDATE state='completed', result=error` (the first result, including errors, is replayed; a validation error will recur deterministically) → return the error.
   - **Ambiguous failure** (timeout / connection reset / HTTP 5xx — Odoo may or may not have committed) → `UPDATE state='unknown'`, return `ERP_CONNECTION_ERROR` to the agent, and **do not auto-retry**.
5. On a later request with the same key and `state='unknown'`: run the **reconciliation query** — search `sale.order` for records created under this idempotency key before any re-execution. This requires the write itself to carry provenance.
6. **Odoo-side provenance marker (required by the design):** the adapter sets `sale.order.client_order_ref` to the **idempotency key** at create time (`client_order_ref` is a standard sale.order field; the POC confirms its behavior on the sandbox in implementation Step 3 — the mechanism is validated there, not assumed). The `execution_id` is written to the audit record only (it does not need to travel to Odoo). Reconciliation = `search_read("sale.order", [["client_order_ref","=",key]])`:
   - exactly one match → adopt it: `state='completed'`, return its normalized result (the retry becomes a no-op — **zero duplicates**);
   - no match → safe to re-execute with the same key;
   - >1 match → stop, return `VERIFICATION_FAILED`-adjacent error, manual review (must not happen; logged as a defect signal).
7. Every branch of this flow writes an audit record carrying both `execution_id` and `idempotency_key` (research: audit coverage = 100% of tool calls, including denials and errors).

**Odoo does NOT natively support idempotency keys** (verified in research). The platform's dedup layer + provenance marker is the only protection against duplicate execution.

---

## 8. Error Taxonomy

| Code | Meaning | Retryable | Model sees it? | Agent behavior |
|---|---|---|---|---|
| `SCHEMA_INVALID` | Arguments fail JSON schema validation | No | Yes | LLM told the argument error; may retry with corrected args (counts toward max tool calls) |
| `TOOL_NOT_FOUND` | Requested tool is not in the registry (hallucinated name, or removed since proposal) | No | Yes | Agent receives catalog hint; hallucinated-tool calls are counted in the eval |
| `TOOL_VERSION_MISMATCH` | Proposal's bound `tool_version` no longer matches the registry's current version | No | No | Proposal rejected; user must re-request (a new proposal binds the new version) |
| `PERMISSION_DENIED` | User's role does not allow this tool | No | Yes | Agent informs user they lack permission |
| `ENTITY_NOT_FOUND` | Referenced customer/product does not exist | No | Yes | Agent informs user and suggests searching |
| `ERP_CONNECTION_ERROR` | Network timeout or Odoo unreachable | Yes | Yes | Gateway may retry once with same idempotency key |
| `ERP_VALIDATION_ERROR` | Odoo rejected (business rule) | No | Yes | Agent surfaces Odoo's message to user |
| `VERIFICATION_FAILED` | Write succeeded but read-back does not match | No | Yes | Agent tells user something went wrong; audit annotated |
| `CONFIRMATION_EXPIRED` | Proposal expired before confirmation | No | Yes | Agent asks user to re-request |
| `CONFIRMATION_HASH_MISMATCH` | Operation hash changed after proposal | No | No | Security alert; agent told "operation cannot proceed" |
| `CONFIRMATION_REPLAY` | Proposal already consumed | No | No | Security alert |
| `IDEMPOTENCY_CONFLICT` | Same idempotency key re-entered while an execution is `pending` (concurrent duplicate) | No | Yes | Agent tells the user the request is already being processed |
| `AMBIGUOUS_OUTCOME` | Write result unknown (timeout after possible Odoo commit) and reconciliation search found no matching record | No | Yes | Agent tells the user the order status is unconfirmed and to check with an admin; audit status `ambiguous`; **never auto-retry-create** |
| `BUSINESS_RULE_VIOLATED` | Gateway business guardrail rejected the call (quantity bounds, `MAX_ORDER_AMOUNT`, line-count cap) | No | Yes | Agent explains the business rule that was violated |
| `MAX_TOOL_CALLS_EXCEEDED` | Agent exceeded bounded tool calls | No | Yes | Agent stops and informs user |
| `LLM_ERROR` | LLM API returned an error | Yes | No | Retry once, then inform user |

**Error response format (returned to agent):**
```json
{
  "success": false,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "User 'readonly_user' does not have permission to create sales orders.",
    "retryable": false,
    "requires_user_action": true
  }
}
```

**Odoo error `debug` field must be stripped before surfacing to the model or user** (verified in research — it contains a full Python traceback).

---

## 9. Logging and Tracing

Every user utterance gets a `trace_id` (UUID v4), which doubles as the `request_id`. Every tool call within that request gets its own `tool_call_id` (UUID v4) and shares the request's `trace_id`. Every audit record carries `trace_id`, `request_id`, and `tool_call_id`; JSON logs carry the same triple, so any audit row correlates 1:1 with its log events.

**Log format (JSON to stdout):**
```json
{
  "timestamp": "2026-09-07T22:30:00Z",
  "level": "INFO",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "component": "gateway",
  "event": "tool_call_started",
  "tool_name": "customer.search",
  "user_id": "sales_user@test",
  "duration_ms": null
}
```

**Events logged:**
- `request_received` (trace_id, user_id)
- `llm_call_started` / `llm_call_completed` (trace_id, model, duration)
- `tool_call_started` / `tool_call_completed` (trace_id, tool_name, duration, result_status)
- `confirmation_proposed` (trace_id, proposal_id, operation_hash)
- `confirmation_executed` (trace_id, proposal_id)
- `verification_started` / `verification_completed` (trace_id, passed)
- `audit_written` (trace_id, audit_id)
- `error` (trace_id, error_code, message)

---

## 10. Latency Measurement

Timestamps are recorded at each boundary. The test harness reports P50/P95 per category.

| Measurement | Start | End |
|---|---|---|
| `llm_latency` | LLM API call sent | LLM response received |
| `tool_latency` | Gateway `handle_tool_call` entered | Gateway returns result |
| `odoo_latency` | Odoo adapter HTTP request sent | Odoo response received |
| `verification_latency` | Read-back request sent | Read-back response received |
| `total_latency` | User input received | Human-readable result returned |

**Targets (from research Section F):**
- P95 total latency (read): < 5 seconds
- P95 total latency (write with confirmation): < 8 seconds (input to proposal display) + (confirmation to result)

---

## 11. Failure Handling

| Failure | Gateway action | Agent action | Audit action |
|---|---|---|---|
| Odoo unreachable | Retry once with same idempotency key → `ERP_CONNECTION_ERROR` | Inform user | Record error |
| Odoo returns validation error | No retry → `ERP_VALIDATION_ERROR` | Surface message | Record error |
| Verification fails after write | No retry → `VERIFICATION_FAILED` | Tell user something went wrong | Record error + annotate `verification_failed=true` |
| Write times out (result unknown) | Reconciliation search by provenance marker (§7) → found: adopt result; not found: `AMBIGUOUS_OUTCOME` | Tell user status is unconfirmed; never silently re-create | Record status `ambiguous` + reconciliation outcome |
| Proposal expired | No retry → `CONFIRMATION_EXPIRED` | Ask user to re-request | Record event |
| Model produces invalid schema | No execution → `SCHEMA_INVALID` | LLM may retry with corrected args (counts toward max) | Record error |
| User has no permission | No execution → `PERMISSION_DENIED` | Inform user | Record denied call |
| LLM API fails | Retry once → `LLM_ERROR` | Inform user | Record error |

---

## 12. Local Development Commands

```bash
# 1. Start Odoo (app + its Postgres)
docker compose up -d

# 2. Wait for Odoo readiness: http://localhost:8069  (first boot takes 1–3 min)
# 3. Create the Odoo test database `poc_test` — deterministic CLI path (Odoo 19):
#      docker compose exec -T odoo odoo -d poc_test --db_host db --db_user odoo \
#        --db_password odoo -i sale_management,stock --stop-after-init
#      docker compose exec -T odoo odoo module force-demo -d poc_test
#    NOTE (Odoo 19 behavior change): CLI-created DBs ship WITHOUT demo data;
#    `module force-demo` is the official installer for demo data.
#    NOTE: run setup commands with `--max-cron-threads=0` where possible —
#    demo loading can fire outbound IAP/SMS cron side effects (verified).
#    The gateway store schema roles note is obsolete — SQLite has no roles;
#    see SECURITY_MODEL.md §8 for the app-supplied-timestamp + hash-input rules.
# 4. Initialize the gateway store schema (SQLite WAL file, idempotent):
python -m poc.db.init   # creates data/poc_gateway.db with WAL mode and all tables

# 5. Create Odoo users + API keys (per verified Odoo docs, master):
#    - Log in as each user → Preferences (top-right avatar) → Account Security → New API Key
#    - Set a description and a duration; the key is displayed ONCE — copy immediately
#    - Keys must be < 3 months duration (Odoo hard limit); for the POC use the max, then rotate
#    Users needed: sales_user@test (Sales/User), readonly_user@test (Sales/Read Only),
#                  no_access_user@test (no Sales groups)

# 6. Set up Python environment
python -m venv .venv
.venv\Scripts\activate        # Windows (PowerShell: .venv\Scripts\Activate.ps1)
# source .venv/bin/activate    # macOS/Linux
pip install -r requirements.txt

# 7. Copy environment template and fill in keys
cp .env.example .env

# 8. Run the POC interactively (Arabic NL via CLI)
python -m poc.main --interactive        # or: python main.py --interactive

# 9. Run the evaluation suite (all 50 cases; auto-confirms writes per test spec)
python -m poc.tests.run_eval --test-cases 03-poc-src/tests/test_cases.json

# 10. Verify audit hash chain integrity
python -m poc.tests.verify_audit --db-path data/poc_gateway.db

# 11. Run unit tests
pytest 03-poc-src/tests/ -v
```

Windows note (this workspace is Windows): use `docker compose` (space, not hyphen) and activate the venv with `.venv\Scripts\activate`. Odoo runs in Linux containers; the POC process runs on the host and reaches Odoo at `http://localhost:8069` via the published port.

---

## 13. Environment Variables

```env
# Odoo (read ONLY by the gateway/adapter module)
ODOO_URL=http://localhost:8069
ODOO_DATABASE=poc_test
ODOO_API_KEY_SALES_USER=your_odoo_api_key_for_sales_user
ODOO_API_KEY_READONLY_USER=your_odoo_api_key_for_readonly_user
ODOO_API_KEY_NO_ACCESS_USER=your_odoo_api_key_for_noaccess_user

# LLM (read ONLY by the agent module) — one provider, pinned
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL_ID=claude-sonnet-4-5        # snapshot pinned at implementation (BFCL V4: Sonnet-4.5 FC 73.24 overall)

# Gateway store (read ONLY by the gateway/db module) — SQLite file path
GATEWAY_DB_PATH=data/poc_gateway.db
AUDIT_DB_PATH=data/poc_gateway.db   # same file; verifier opens read-only

# Gateway behavior
TENANT_ID=poc_tenant_001
CONFIRMATION_EXPIRY_SECONDS=300
MAX_TOOL_CALLS_PER_REQUEST=3
MAX_ORDER_AMOUNT=10000000

# Logging
LOG_LEVEL=INFO
```

**Rules:**
- `.env` is in `.gitignore` (never committed)
- Odoo API keys are per-user (not a single service credential)
- `ANTHROPIC_API_KEY` is the provider's key (not an Odoo credential)
- The agent module reads only `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL_ID` — enforced by a dedicated `config.py` that exposes scoped accessors (`llm_config()` vs `odoo_config()` vs `store_config()`), so `agent.py` has no code path that even loads `ODOO_*` values. Verified by an env-scan unit test.
- The gateway module reads `ODOO_*` and `GATEWAY_DB_*` variables and never the LLM key
- No secret is ever logged, audited, or embedded in a prompt

---

## 14. Odoo Docker Configuration

```yaml
# docker-compose.yml
# odoo:19.0 tag verified on Docker Hub (official image, last pushed 2026-08-20, amd64/arm64)

services:
  odoo:
    image: odoo:19.0
    depends_on:
      - db
    ports:
      - "8069:8069"
    environment:
      - HOST=db
      - USER=odoo
      - PASSWORD=odoo
    volumes:
      - odoo_data:/var/lib/odoo
      - ./odoo-config:/etc/odoo

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=postgres
      - POSTGRES_USER=odoo
      - POSTGRES_PASSWORD=odoo
    volumes:
      - odoo_db_data:/var/lib/postgresql/data

  # Gateway store is a local SQLite file (data/poc_gateway.db) — no container needed

volumes:
  odoo_data:
  odoo_db_data:
  gateway_db_data:
```

**Gateway store:** the local SQLite file `data/poc_gateway.db` holds the three tables `audit_log`, `idempotency_keys`, and `proposals` (schemas in `SECURITY_MODEL.md` §8 and §5), created by `poc.db.init` on first run. WAL mode is set at initialization. Append-only enforcement on `audit_log` is code-discipline-enforced in the POC (no UPDATE/DELETE code paths); production migrates to a database-enforced role.

**Post-startup steps (via Odoo web UI at `http://localhost:8069`):**
1. Create database `poc_test` with demo data enabled
2. Install modules: Sales, Inventory, Product (typically pre-installed with demo data)
3. Create users: `sales_user@test` (Sales/User), `readonly_user@test` (Sales/Read Only), `no_access_user@test` (no Sales groups)
4. Generate API keys **per verified Odoo documentation**: each user logs in → Preferences (avatar menu) → Account Security → New API Key; set description + duration; the key is shown **once**; duration must be ≤ 3 months (Odoo hard limit)
5. Copy API keys to `.env`

---

## IMPLEMENTATION HANDOFF

### Ordered Implementation Sequence

Each step produces a testable, inspectable artifact. Do not proceed to the next step until the current step works.

**Step 1: Odoo Docker Environment**
- Create `docker-compose.yml` (odoo + db per §14)
- Start Odoo 19 Community with its PostgreSQL
- Verify web UI is accessible at `http://localhost:8069`
- Create test database `poc_test` with demo data
- Run `python -m poc.db.init` → creates `data/poc_gateway.db` (WAL mode) with `audit_log`, `idempotency_keys`, `proposals`
- **Test:** Navigate to `http://localhost:8069`, log in, see demo data; open the SQLite file and confirm the three tables exist and `PRAGMA journal_mode` returns `wal`

**Step 2: Odoo User Setup + JSON-2 smoke test**
- Create `sales_user@test` (Sales/User), `readonly_user@test` (Sales/Read Only), `no_access_user@test`
- Generate API keys per §12 step 5 (Preferences → Account Security; shown once; ≤3-month duration)
- Create `.env` with keys
- **Test (raw JSON-2, no POC code):** `POST /json/2/res.partner/search_read` with `sales_user`'s key → 200 with partner JSON. Repeat with `readonly_user`'s key on a create → expect 4xx structured error (`name`, `message` fields; `debug` present but ignored). Also confirm the `X-Odoo-Database` header requirement against this deployment.

**Step 3: Odoo Adapter (`odoo_client.py`)**
- Implement a thin HTTP client for Odoo JSON-2 API (contract in §3)
- Methods: `search_read(model, domain, fields, limit)`, `read(model, ids, fields)`, `create(model, values)`
- Handle bearer key auth, `X-Odoo-Database`, timeouts (default 10 s), and error responses (strip `debug` before any error object leaves the adapter)
- **Test:** `search_read("res.partner", [["name","ilike","Deco"]], ["id","name"])` returns JSON list. Timeout path: point `ODOO_URL` at a blackhole port → adapter raises its timeout error type within 10 s.
- **Test (provenance marker):** create a sale.order with `client_order_ref=<test-key>`, then `search_read` on `client_order_ref = test-key` → exactly one match. This validates the reconciliation mechanism assumed by §7 (if it fails, STOP and record a DECISIONS addendum — do not improvise).

**Step 4: Tool Schemas (`schemas/`)**
- Write 5 JSON schema files (see `TOOL_CONTRACTS.md`)
- Each schema defines: name, version, description, risk_level, readOnly, requiresConfirmation, idempotent, inputSchema, outputSchema, odoo mapping
- **Test:** Load each schema with `jsonschema` and verify it parses; registry startup validation rejects duplicate names and malformed meta-fields

**Step 5: Audit Store (`audit.py`)**
- Implement the append-only audit table with row-level hash chain in SQLite (WAL mode)
- Method: `write_record(audit_data) -> audit_id`
- Method: `verify_chain() -> bool` (opens the file read-only)
- **Test:** Write 3 records, verify chain integrity. Grep test asserts no `UPDATE`/`DELETE` statements against `audit_log` exist anywhere in the codebase (append-only is code-discipline-enforced — classified **demonstrated in POC**). Tamper with a row directly via the `sqlite3` CLI, verify `verify_chain()` detects it.

**Step 6: Authorization (`authz.py`)**
- Implement policy evaluation from `users.yaml` + policy rules
- Method: `check(tool_name, user_id) -> PolicyDecision`
- **Test:** `sales_user@test` + `sales.order.create` → allow. `readonly_user@test` + `sales.order.create` → deny. `readonly_user@test` + `customer.search` → allow. `no_access_user@test` + anything → deny.

**Step 7: Idempotency Store (`idempotency.py`)**
- Implement the SQLite store (§7) with content-addressable key derivation, `BEGIN IMMEDIATE` test-and-set insert, state transitions (`pending/completed/unknown`), and the reconciliation lookup by provenance marker
- Method: `begin(key, request_hash) -> BeginOutcome(new|completed(result)|conflict|unknown)`
- Method: `complete(key, result, external_record_id)` / `mark_unknown(key)`
- Method: `reconcile(key) -> external_record_id | None`
- **Test:** Begin/complete roundtrip returns cached result. Concurrent `begin` on same key → one `new`, one `conflict`. `mark_unknown` then reconcile-with-planted-order → adopted result, zero duplicate orders.

**Step 8: Tool Gateway (`gateway.py`)**
- Implement the full pipeline: validate → authorize → confirm/idempotency → execute → verify → audit → normalize
- Import schemas, audit, authz, idempotency, odoo_client, confirmation
- Do NOT import anything from `agent.py`
- **Test:** `gateway.handle_tool_call("customer.search", {"query":"محمد"}, user_id="sales_user@test", trace_id=uuid4())` returns a structured result. `readonly_user` + `sales.order.create` → `PERMISSION_DENIED`. Both produce audit records.

**Step 9: Confirmation Lifecycle (`confirmation.py`)**
- Implement proposal creation, hash verification, expiry, one-shot state machine (SQLite, per `SECURITY_MODEL.md` §5–6), including re-authorization of the full checklist (user identity, tenant, tool existence, current tool_version, authorization, proposal state, expiry, operation_hash, confirmation binding) at confirmation-execution time
- Methods: `create_proposal(tool_name, args, user_id, trace_id)`, `confirm_and_execute(proposal_id, user_id)`
- **Test:** Create → confirm → executes once. Second confirm attempt → `CONFIRMATION_REPLAY`. Expired proposal → `CONFIRMATION_EXPIRED`. Tamper with stored args → `CONFIRMATION_HASH_MISMATCH`. Two concurrent confirms → exactly one executes.

**Step 10: Agent Runtime (`agent.py`)**
- Implement the tool-calling loop with the LLM (Anthropic Claude Sonnet 4.5, §13)
- Build system prompt with tool catalog; business data is passed as tool results only
- Send Arabic NL to LLM, receive tool call
- Construct `ToolCallRequest`, call gateway; surface `PROPOSAL_REQUIRED` to the CLI for confirmation
- Format result back to Arabic NL; never claim success without a tool result
- **Test:** Input "هاتلي العميل محمد أحمد" → agent calls `customer.search` and returns a human-readable result. Input "هاتلي العملاء اللي عليهم فلوس" (no matching tool semantics) → model answers in NL without forcing a tool call.
- **Import-boundary test (same step):** a scanner test asserts `agent.py` imports neither `odoo_client` nor `audit` nor `confirmation`, and `config.py` never returns `ODOO_*` values through `llm_config()`.

**Step 11: Error Handling (`errors.py`)**
- Implement the full error taxonomy (§8) incl. `AMBIGUOUS_OUTCOME` and `BUSINESS_RULE_VIOLATED`
- Ensure Odoo `debug` fields are stripped; ensure all errors return the structured format
- **Test:** Trigger each error type and verify the structured error is returned to the agent with correct `retryable` and `requires_user_action` flags.

**Step 12: Verification (`verification.py`)**
- Implement post-write read-back verification (§6): partner match, `state='draft'`, lines present
- **Test:** Create a sales order via the gateway, verify read-back passes. Mutate the order in Odoo as admin, re-verify → mismatch detected → `VERIFICATION_FAILED` recorded in audit.

**Step 13: Main Entry Point (`main.py`)**
- CLI that accepts Arabic NL input
- Runs the agent
- Displays structured + human-readable result
- Handles confirmation prompts for write operations (proposal preview → y/n within TTL)
- **Test:** Run interactively, type an Arabic phrase, see the result; decline a confirmation → no order created, audit shows the denial.

**Step 14: Test Harness (`tests/run_eval.py`)**
- Read test cases from JSON
- Execute each through the agent + gateway pipeline (auto-confirm goes **through** `gateway.confirm_and_execute`, never bypassing it)
- Collect metrics per `TEST_PLAN.md` §6
- Output report
- **Test:** Run with 5 test cases, verify report is generated with per-case outcomes + timings.

**Step 15: Full Evaluation**
- Run all 50 test cases (3 passes for read journeys → pass^3)
- Generate report
- Verify audit hash chain (`verify_audit`)
- Verify Odoo state (no unauthorized/duplicate records; `TEST_PLAN.md` §8)
- **Test:** All success criteria from `TEST_PLAN.md` §4 are met (or failures documented per §5).

---

### Implementation Notes

- Language: Python 3.12+ (dependencies: `httpx`, `jsonschema`, `python-dotenv`, `anthropic`, `pyyaml`, `pytest` — nothing else)
- No frameworks. No LangChain. No CrewAI. No FastAPI (not needed for a CLI-based POC).
- SQLite (WAL mode) file `data/poc_gateway.db` for audit + idempotency + proposals — per locked POC scope; Odoo's own Postgres stays untouched
- Odoo 19 Community in Docker (`odoo:19.0` — tag verified)
- Total estimated codebase: 600–900 lines (excluding schemas and test data)
- Each step should take 1–3 hours to implement and test
- The import-boundary scanner, the env-scan test, and the secret-leak scan are first-class test artifacts, not optional hygiene
