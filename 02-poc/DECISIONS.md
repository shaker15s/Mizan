# Architecture Decision Records

This document records the key architectural decisions made for the Agent-Native ERP POC. Each decision states what was chosen, what alternatives were considered, why the chosen option wins for this POC, and what can change later as the system matures.

For implementation sequencing, see `TECHNICAL_DESIGN.md` § IMPLEMENTATION HANDOFF. This document does not duplicate that sequence.

**Revisions (7 Sept 2026 audit):** ADR-03 confirmed as SQLite (WAL mode) per locked POC scope, ADR-06 corrected (JSON-2 is not JSON-RPC), ADR-08 rewritten (content-addressable keys + provenance-based reconciliation), ADR-12 pinned (Anthropic Claude Sonnet 4.5); ADR-17/ADR-18 added.

---

## ADR-01: Single Python Process

**Decision:** Implement the entire POC (agent loop, tool gateway, authorization, verification, audit, idempotency, logging) as one Python process with no runtime dependencies beyond the Odoo containers (app + its Postgres).

**Alternatives considered:**
- Microservices with separate agent, gateway, and audit services communicating over HTTP or gRPC
- Event-driven architecture with a message broker (Redis, Kafka)
- Serverless functions per tool call

**Why chosen wins for POC:**
- Minimum moving parts means every failure is attributable to POC logic, not inter-service latency, serialization, or deployment issues.
- The trust boundary (Tool Gateway) is a function call, not a network hop. This makes the security review simpler and the attack surface smaller.
- The gateway store is a local SQLite file (WAL mode, `data/poc_gateway.db`) — zero infrastructure, no extra container.
- One process, one log stream, one trace. Debugging 50 Arabic test cases against a live Odoo is straightforward.

**What can change later:**
- The Tool Gateway can be extracted into a standalone service (network-exposed, mTLS-authenticated) once the tool contract and authorization model are stable.
- The audit store can be moved to a dedicated audit database or WORM storage when tamper-resistance requirements grow (research C10).
- The agent loop can become a separate long-running worker if queueing or multi-turn conversations are introduced.

**Trust-boundary caveat (added in architecture correction pass):** The single-process architecture provides engineering/module isolation, NOT a true security boundary. Module-import rules are enforced by code review and automated tests, not by the OS. **Production requirement:** the Agent Runtime and the Tool Gateway MUST run as separate OS processes/containers (distinct trust domains), with the gateway as the sole holder of Odoo credentials and store access. Secrets MUST NOT be accessible to the agent runtime's process environment.

---

## ADR-02: Python 3.12+ as Implementation Language

**Decision:** Implement the POC in Python 3.12 or later, using only the standard library (including `sqlite3`) plus `httpx`, `jsonschema`, `python-dotenv`, `pyyaml`, and the `anthropic` SDK — plus `pytest` for tests. No other dependencies.

**Alternatives considered:**
- Node.js / TypeScript
- Go
- Rust

**Why chosen wins for POC:**
- JSON Schema authoring, tool-call parsing, and LLM SDK integration are idiomatic in Python.
- Pydantic provides runtime JSON Schema validation for tool contracts and test case fixtures.
- Fast iteration matters more than runtime performance for a 50-test evaluation.
- No compiled-binary build steps; `uv` or `pip` handles everything.

**What can change later:**
- TypeScript is a reasonable choice if the team moves to a web frontend and wants shared types.
- Go or Rust is justified only for a production-grade high-throughput gateway service; premature for POC.

---

## ADR-03: SQLite (WAL mode) for Audit, Idempotency, and Proposals

**Decision:** Use a single SQLite file (WAL mode, `data/poc_gateway.db`) for the audit log, idempotency store, and proposal store. **Documented deviation:** the research package (research-report §E) specified PostgreSQL 15+ for these stores; the locked POC scope explicitly names SQLite, and the user's direct scope instruction takes precedence. The properties PostgreSQL would have provided (database-enforced INSERT-only audit role) are reclassified as **production requirements**; the POC achieves append-only via code discipline + hash-chain verification (**demonstrated in POC** class only).

**Alternatives considered:**
- PostgreSQL container (the research package's recommendation; rejected for the POC: extra container, port, and DSN management for a single-process, single-writer workload; the INSERT-only role control it enables is a production concern)
- Flat JSONL append-only file
- No persistent idempotency (in-memory only)

**Why chosen wins for POC:**
- Zero infrastructure: no extra container, no port, no DSN management.
- WAL mode provides concurrent reads + single-writer serialization, sufficient for the POC's single-process model.
- Append-only audit is code-discipline-enforced (no UPDATE/DELETE paths in `audit.py`); production migrates to database-enforced roles.
- The audit hash chain is verified by a standalone function reading the same file.
- `BEGIN IMMEDIATE` provides the write-lock serialization required for idempotency and confirmation state machines.

**What can change later:**
- Write-once audit storage (object storage with object-lock, or a dedicated immutable ledger) for tamper resistance beyond hash-chain verification.
- Separate audit database when volumes grow (research C10).

---

## ADR-04: Per-User Odoo API Keys (Not a Bot Service Account)

**Decision:** The Tool Gateway authenticates to Odoo JSON-2 using the real Odoo user's API key (not a shared bot account). Authorization is therefore enforced twice: once by the gateway's YAML policy, once by Odoo's own record rules and access rights.

**Alternatives considered:**
- Single bot/service account with elevated permissions; gateway decides everything
- OAuth token delegation (not supported by Odoo JSON-2 for third-party acting-on-behalf-of)
- Per-user OAuth via Odoo's standard web session (not designed for headless API)

**Why chosen wins for POC:**
- Odoo JSON-2 supports per-user bearer API keys (max 3-month lifetime, max 10 keys per user). This is the documented, supported pattern.
- Defense in depth: even if the gateway authorization policy has a bug, Odoo's own access rights prevent unauthorized actions.
- The audit trail in Odoo itself records the correct user identity, not a generic bot.
- No OAuth delegation flow exists in Odoo JSON-2, so per-user keys are the only viable approach.

**What can change later:**
- For multi-tenant production, a token-vault or secret-manager approach stores per-user keys more securely than environment variables.
- If Odoo adds OAuth or OIDC client-credentials with user delegation, migrate to that pattern.

---

## ADR-05: Custom Minimal Agent Loop (No Framework)

**Decision:** Implement the agent loop as a plain Python function: send tool definitions to the LLM, parse the tool-call response, validate parameters against the tool contract, call the Tool Gateway, and loop up to 3 iterations.

**Alternatives considered:**
- LangChain / LangGraph
- CrewAI
- Semantic Kernel
- OpenAI Agents SDK
- Vercel AI SDK

**Why chosen wins for POC:**
- The loop is approximately 80–150 lines of Python. A framework adds dependency weight, abstraction layers, and debugging difficulty that this POC cannot afford.
- Framework abstractions hide the tool-call → gateway call → response flow, making it harder to instrument latency, classify errors, and verify audit completeness.
- The POC has exactly 5 tools, single-turn input, and max 3 iterations. No framework feature is needed.
- Research confirmed that τ-bench-level multi-turn reliability (< 25% pass^8) is far from solved; simplifying to single-turn + max 3 tool calls is deliberate scope control.

**What can change later:**
- If the tool count exceeds ~15 or multi-turn conversation state becomes important, a lightweight framework (e.g. OpenAI Agents SDK) may be justified.
- A tool-registry abstraction can be preserved regardless of framework choice, so migration cost is moderate.

---

## ADR-06: Thin Odoo JSON-2 Adapter (No CLI-Anything in the Runtime)

**Decision:** Write a small, direct Odoo JSON-2 adapter module inside the POC process that constructs plain-JSON HTTP requests for each of the 5 tool contracts and calls `POST /json/2/{model}/{method}`.

**Alternatives considered:**
- Use CLI-Anything as a tool-calling intermediary
- Use the official `odoo-client-lib-python` package
- Use MCP as the internal tool-calling protocol
- Build a full ORM abstraction layer

**Why chosen wins for POC:**
- The 5 tools map to exactly 5 Odoo JSON-2 calls. No abstraction is needed.
- Direct adapter keeps the tool contract, parameter mapping, and error handling visible in one place.
- CLI-Anything is a separate research track, not a POC runtime dependency. Research verified its catalog contains **no Odoo or ERPNext harness at all** (the PRD's claim to the contrary is contradicted), so there is nothing to integrate even if we wanted to.
- MCP adds a protocol layer (stdio/HTTP transport, JSON-RPC handshake) that is unnecessary for a single-process POC. MCP may be added as a compatibility layer later.
- **Terminology correction (audit):** Odoo's "JSON-2" API is *not* JSON-RPC 2.0 — the body is a plain JSON object of named arguments and responses are the method's JSON return value or a structured error object. The adapter reflects this accurately.

**What can change later:**
- The adapter can be replaced by an MCP server that wraps the same tool contracts, making the tools available to any MCP-compatible client.
- CLI-Anything can be evaluated as an alternative transport after the POC proves the core hypothesis.
- If tool count grows, a generic adapter with configurable endpoint mapping may be justified.

---

## ADR-07: Read-Back Verification After Write Operations

**Decision:** After every successful `sales.order.create`, the Tool Gateway immediately calls `sales.order.get` with the returned `id` and verifies that the created record matches the requested parameters (customer_id, product_id, quantity, and computed amount_total). Only after read-back succeeds does the gateway return success to the agent.

**Alternatives considered:**
- Trust the JSON-2 create response alone (no verification)
- Subscribe to Odoo's bus/webhook events for async verification
- Periodic background reconciliation job
- Return response without verification; mark verification as a future feature

**Why chosen wins for POC:**
- Read-back is synchronous, deterministic, and requires no additional infrastructure.
- It directly proves the "Verification" link in the core hypothesis chain.
- If the create response says `id: 42` but the record was actually miswritten (e.g. wrong product due to a gateway bug), read-back catches it immediately.
- Odoo JSON-2 has no native idempotency and no per-call cross-request transaction guarantee; a follow-up read is the simplest way to confirm committed state.

**Scope and limits (added in architecture correction pass):** Read-back verifies that the returned record exists and that the compared fields match at read-back time. It does NOT prove transactional atomicity across the create and read-back calls — **each Odoo JSON-2 request runs in its own SQL transaction** (per official Odoo 19 documentation). The create commits when its request returns; the read-back is a separate transaction. The design does not pretend these are one atomic unit.

**What can change later:**
- Webhook or event-bus verification for async workflows.
- Snapshot-based reconciliation with checksum comparison for higher confidence.
- Digital signature on create response for non-repudiation.

---

## ADR-08: Gateway-Side Idempotency with Content-Addressable Keys and Provenance-Based Reconciliation

**Decision:** The Tool Gateway maintains its own idempotency store (SQLite WAL mode, `data/poc_gateway.db`). Every `sales.order.create` is assigned a **content-derived idempotency key**: `SHA256(tenant_id || user_id || tool_name || canonical_json(arguments))[:32]` — not a random UUID. The create carries the key in `sale.order.client_order_ref` as a provenance marker. On ambiguous outcomes (Odoo may have committed but the response was lost), the gateway reconciles by searching for that marker before any re-execution. States: `pending → completed | unknown`. The gateway also mints a distinct **execution correlation ID** (`execution_id`, UUID v4) per physical attempt — carried in audit and logs for traceability; it plays no role in deduplication.

**Alternatives considered:**
- Rely on Odoo's native idempotency (verified: not available in JSON-2 API)
- Random UUID v4 keys (Stripe-style) — rejected: with random keys, the user's natural retry after an ambiguous failure generates a *new* key and the gateway happily creates a duplicate order. The gateway is both key issuer and retry handler, so the key must be a function of intent.
- Use Odoo's `context` field to pass a marker and deduplicate via a custom Odoo module — rejected for POC: requires deploying server-side code into the customer ERP.
- Database unique constraint on a customer field (e.g. email) to prevent duplicate customers — wrong granularity for orders.
- No idempotency; accept duplicate risk in POC — rejected: "duplicate orders from retry = 0" is a POC success criterion.

**Why chosen wins for POC:**
- A deterministic key makes "did my order go through?" a pure cache lookup: identical retry → same key → `completed` state returns the stored result without touching Odoo.
- The provenance marker (`client_order_ref`) closes the ambiguous-outcome gap that a local store alone cannot: if the response is lost after Odoo committed, the reconciliation search adopts the existing order and the retry becomes a no-op — zero duplicates without any Odoo server code.
- The atomic test-and-set insert (`BEGIN IMMEDIATE` + `INSERT OR IGNORE`; first writer wins under the SQLite write lock) also serializes concurrent duplicates: the second caller never executes and receives `IDEMPOTENCY_CONFLICT`.
- Same-key semantics: same key + same request → replay the stored result (never execute twice); same key + different request → `IDEMPOTENCY_CONFLICT`; concurrent same-key → exactly one execution.
- The evaluation harness uses the same deterministic derivation (or an explicit `idempotency_key` field in the test case), making retry tests reproducible.
- Transport-agnostic: works regardless of which ERP sits underneath.

**Known trade-off:** two genuinely identical write requests (same user, same args, same tool) collide by design; the user changes quantity to disambiguate. Documented, acceptable for the POC.

**What can change later:**
- If Odoo adds native idempotency to JSON-2, the gateway store becomes a cache rather than the source of truth.
- For multi-instance production, the store migrates to a shared database with row-level locking (PostgreSQL).
- A client-supplied dedup salt can replace the content-collision trade-off when a real UI exists.

---

## ADR-09: SHA-256 Hash Chain for Audit Integrity

**Decision:** Every audit record includes `prev_hash` and `own_hash` where `own_hash = SHA-256(prev_hash || record_json_without_hash_fields)`. The genesis record uses a fixed seed string. A separate verification function walks the chain and confirms no record was removed, reordered, or modified.

**Alternatives considered:**
- WORM storage (write-once-read-many filesystem or object storage with object-lock)
- Append-only log with Merkle tree (periodic anchoring to external trust anchor)
- Third-party audit service (e.g. immudb, Amazon QLDB)
- Database-level write protection (INSERT-only role — a production requirement, not a POC one)

**Why chosen wins for POC:**
- Zero infrastructure cost beyond the existing SQLite file; the hash chain is computed in-process and stored alongside all gateway state.
- Detection of tampering (modification, deletion, or insertion) is verifiable by a standalone function with no network access.
- The chain proves integrity of sequence and content, not identity of the writer; for POC, a single process writer makes that distinction less important.
- No external dependency or separate storage is needed.

**What can change later:**
- Anchor the chain head to external storage (blockchain, timestamping authority, or remote object store) for stronger non-repudiation.
- Merkle tree for efficient partial verification in high-volume audit.
- Move to an immutable database or append-only log service.

---

## ADR-10: YAML Policy File for Authorization (No OPA)

**Decision:** Authorization rules are defined in a YAML file loaded at process start. The file maps `odoo_username` → { allowed_tools: [...], confirmation_required: [...], denied_models: [...] }. The Tool Gateway checks this policy before every tool call.

**Alternatives considered:**
- Open Policy Agent (OPA) with Rego policies
- Database-stored policy with UI for editing
- Hardcoded Python authorization logic
- Odoo's own group/permission system only (no gateway layer)

**Why chosen wins for POC:**
- The POC has 3 users and 5 tools. A YAML file is the simplest representation that a human can read, review, and modify.
- OPA adds a network dependency (sidecar or embedded server), Rego syntax learning curve, and operational overhead disproportionate to a 5-tool POC.
- Hardcoded logic is less inspectable than YAML for non-developer stakeholders.
- YAML preserves the ability to switch to OPA, Casbin, or a database-backed policy engine by replacing the loader only.

**What can change later:**
- Migrate to OPA or a policy-as-code platform when the number of users, tools, or tenants grows beyond what a YAML file can manage.
- Add per-tool field-level restrictions (e.g. sales_user can read total but not margin).
- Move policy to a database for dynamic updates without restart.

---

## ADR-11: CLI-Only Interface (No Web UI)

**Decision:** The POC is invoked via a Python CLI command (`python -m agent_native_erp`) that accepts an Arabic natural-language string, runs the agent loop, and prints a JSON result to stdout. No HTTP server, no browser, no API endpoint.

**Alternatives considered:**
- Local HTTP API (FastAPI or Flask) for programmatic testing
- Simple web UI (Streamlit or plain HTML) for human review
- Interactive REPL loop for manual testing

**Why chosen wins for POC:**
- The test harness calls the CLI programmatically. No HTTP overhead.
- The 50-test dataset is a JSON file; each test case is one CLI invocation with its input string, expected tool, expected parameters, and expected outcome.
- No frontend scope in POC. Building UI introduces browser rendering, CORS, and session complexity irrelevant to the hypothesis.
- stdout JSON makes the output machine-readable for the test harness and human-readable for debugging.

**What can change later:**
- Wrap the same agent loop in a FastAPI endpoint for an HTTP API surface.
- Build a Streamlit or web UI that calls the same loop.
- Add an MCP server layer that exposes the same 5 tools over MCP.

---

## ADR-12: Anthropic Claude Sonnet 4.5 as the Single LLM Provider

**Decision:** Use Anthropic Claude Sonnet 4.5 (native tool calling) as the single provider for the POC, pinned via `ANTHROPIC_MODEL_ID=claude-sonnet-4-5`. No model abstraction interface is built.

**Alternatives considered:**
- OpenAI GPT-4o / GPT-5 family
- Model-agnostic abstraction layer (e.g. LiteLLM, custom interface)
- Local open-source model (Llama, Mistral) via Ollama
- Multiple providers for A/B comparison in POC

**Why chosen wins for POC:**
- Verified benchmark position: BFCL V4 snapshot puts Claude-Sonnet-4.5 (FC) at 73.24 overall — second only to Claude-Opus-4.5 (77.47) — with the strongest tier of multi-turn performance (64.95) at materially lower cost and latency than Opus.
- External precedent: Microsoft's own D365 ERP MCP documentation names Claude Sonnet 4.5 as its recommended orchestration model (GPT-5 Chat fallback) — the closest published analog to this POC's use case.
- Anthropic's official tool-use guidance ("extremely detailed descriptions … by far the most important factor in tool performance") directly informs the contract style used here.
- The core hypothesis is about the architecture (agent → gateway → ERP), not about model portability; an abstraction layer adds an interface to maintain without POC benefit.

**What can change later:**
- Add a second provider for comparison after the POC proves the architecture.
- If model abstraction is needed, a simple provider interface (send messages, parse tool calls) can be added without restructuring the agent loop.
- Re-pin the model id at implementation time; the snapshot is a variable, not an architecture.

---

## ADR-13: Egyptian Arabic Dialect Test Set (Not MSA)

**Decision:** The 50-test dataset uses Egyptian Arabic (مصري) dialect with code-switching for product names and model numbers. MSA (Modern Standard Arabic) is not tested separately in this POC.

**Alternatives considered:**
- Modern Standard Arabic (فصحى) only
- Mix of MSA and Egyptian dialect in the test set
- English input only
- Parallel MSA + Egyptian test pairs to measure the gap

**Why chosen wins for POC:**
- The product targets Egyptian users; MSA is not how they speak.
- Research (arXiv 2601.05101) showed a −5–10% accuracy drop for Arabic tool calling. Testing Egyptian dialect directly measures the realistic worst case.
- Code-switching (e.g. "اروني السعر بتاع الديل اوتريل") is representative of real usage.
- Mixing MSA and Egyptian would blur the signal; the POC should measure the harder case.

**What can change later:**
- Add MSA test cases for a broader market.
- Test multiple Arabic dialects (Levantine, Gulf) if the product expands.
- Benchmark Egyptian dialect accuracy against MSA to quantify the gap and inform prompt engineering.

---

## ADR-14: 50 Test Cases (Not 200)

**Decision:** The evaluation dataset contains exactly 50 test cases across 9 categories, as defined in `TEST_PLAN.md`.

**Alternatives considered:**
- 200+ test cases for statistical significance
- 20 test cases for a quick smoke test
- Iterative: start with 10, expand based on findings

**Why chosen wins for POC:**
- 50 cases provide enough signal per category (5–15 cases each) to identify systematic failures, not just noise.
- 200 cases would require more test-authoring effort than the POC timeline allows, and the marginal information gain per additional case diminishes sharply.
- 10 cases would not cover 9 failure modes (authorization, ambiguity, idempotency, ERP errors, prompt injection).
- The category structure is extensible; more cases can be added per category later without changing the harness.

**What can change later:**
- Expand to 200+ for production readiness evaluation.
- Add cross-category adversarial combinations (e.g. prompt injection inside an ambiguous entity search).

---

## ADR-15: 5-Minute Confirmation Expiry

**Decision:** `sales.order.create` proposals expire after 5 minutes if not confirmed. The confirmation lifecycle is: agent proposes → gateway creates proposal with `proposal_id`, `operation_hash`, and `expires_at` → user confirms within 5 minutes → gateway executes → audit. After expiry, the proposal is rejected with `CONFIRMATION_EXPIRED`.

**Alternatives considered:**
- 30-second expiry (too short for a human to review)
- 15-minute expiry (allows more replay window)
- 1-hour expiry (too long for a real-time ERP action)
- No expiry (proposal remains valid until explicitly cancelled)

**Why chosen wins for POC:**
- 5 minutes balances human review time with security: a stolen or replayed proposal_id has a bounded window.
- For a CLI-based POC, the human runs the confirm command manually; 5 minutes is a comfortable interaction window.
- Shorter (30 seconds) risks test failures due to human latency; longer (1 hour) weakens the replay defense without POC benefit.
- The exact value is configurable; 5 minutes is the POC default.

**What can change later:**
- Adjust based on production UX (e.g. 60 seconds for chat-based real-time, 15 minutes for email approval).
- Add explicit cancel action before expiry.
- Add notification (email, Slack) when a proposal is created and again when it expires unconfirmed.

---

## ADR-17: Defer Arabic Orthographic Normalization

**Decision:** The POC does not normalize Arabic orthography (alef variants أ/إ/ا, taa marbuta ة/ه, alif maqsura ى/ي) before search. The evaluation dataset uses entity names whose spelling matches the Odoo demo data exactly.

**Alternatives considered:**
- Normalize the query (and/or indexed fields) in the adapter before building the `ilike` domain
- Use Odoo's `unaccent`-style trigram indexing
- Accept mismatch noise and measure it

**Why chosen wins for POC:**
- Normalization is a data-layer concern that would silently change what the tool contract means; the POC must measure the tool-calling chain, not text normalization.
- Demo data is fixed and small; matching orthography keeps eval results interpretable.
- The gap is real and known — it is documented in `TOOL_CONTRACTS.md` rather than hidden.

**What can change later:**
- Pre-pilot: add normalization to the adapter's search domain construction (a localized, versioned change).
- Evaluate Postgres trigram/`pg_bigm`-style indexing for Arabic fuzzy matching with real tenant data.

---

## ADR-18: No LLM-Facing `limit` Parameter; Adapter-Side Hard Cap

**Decision:** `customer.search` and `product.search` expose no `limit` argument to the LLM. The adapter applies a server-side cap (`limit_cap: 20` in the contract, default 10 rows returned).

**Alternatives considered:**
- Expose `limit` in `inputSchema` (min 1, max 20)
- Return all matches (no cap)

**Why chosen wins for POC:**
- Every schema parameter is an LLM error surface; `limit` adds argument-generation risk with zero eval value (no test case needs a non-default limit).
- A server-side cap bounds prompt size and Odoo load deterministically.
- If the model needs "more results", it says so in NL; the UX answer (refine the query) is better than a bigger page.

**What can change later:**
- Expose `limit` when a real UI exists and pagination matters; contract version bump, evaluated against the regression suite.

---

## ADR-16: MCP as Optional Compatibility Layer (Not Internal Dependency)

**Decision:** The POC does not use MCP internally. The 5 tool contracts are defined in the POC's own format. After the POC succeeds, an MCP server wrapper can expose those same contracts to any MCP-compatible client.

**Alternatives considered:**
- MCP-first: define tools as MCP servers from day one
- Use MCP as the internal tool-calling protocol between agent and gateway
- Skip MCP entirely and never mention it

**Why chosen wins for POC:**
- MCP adds a protocol layer (stdio or HTTP transport, JSON-RPC handshake, session management) that is unnecessary overhead for a single-process POC.
- The tool contract format (JSON Schema for input, structured error codes, authorization metadata) can be translated to MCP tool definitions mechanically later.
- The POC proves the hypothesis (NL → agent → tool → authz → ERP → verify → audit → result). MCP transport does not affect any link in that chain.
- Research showed that Odoo JSON-2 has no MCP bridge; building one is an implementation task, not a research question.

**What can change later:**
- Build an MCP server that wraps the same 5 tool contracts, making the tools available to Claude, ChatGPT, or any MCP client.
- If the product becomes multi-agent or integrates external tools, MCP becomes the natural interop layer.

---

## Summary of Build vs. Buy vs. Wrap

| Component | Decision | Rationale |
|-----------|----------|-----------|
| Agent loop | **Build** | Minimal, inspectable, no framework dependency |
| Tool Gateway | **Build** | Core trust boundary; must be controlled |
| Tool contracts | **Build** | 5 tools, well-defined JSON Schema |
| Authorization policy | **Build** (YAML file) | Simple enough to hand-author |
| Odoo adapter | **Build** (thin) | Direct JSON-2 calls, no library needed |
| Verification | **Build** (read-back) | Deterministic, no extra infrastructure |
| Idempotency store | **Build** (SQLite WAL mode) | Odoo has no native support |
| Audit log + hash chain | **Build** (SQLite + SHA-256) | No third-party needed for POC; code-discipline-enforced append-only |
| LLM provider | **Reuse** (SDK) | Anthropic SDK (ADR-12) |
| Odoo 19 Community | **Wrap** (Docker) | Official image, no fork |
| MCP | **Defer** | Optional compatibility layer post-POC |
| CLI-Anything | **Evaluate separately** | Not a POC runtime dependency |
| Evaluation harness | **Build** (Python CLI) | Test dataset is JSON; harness is a loop |
| Observability | **Build** (JSON stdout logging) | Structured logs, no external service |

---

## Post-Review Amendments (Architecture Correction Pass)

These corrections were applied across all five POC documents during the final architecture review. They tighten the design without expanding scope.

| # | Amendment | Where | Rationale |
|---|---|---|---|
| 1 | **Trust-boundary clarification:** single-process = module isolation, NOT a security boundary. Production requirement added: agent and gateway MUST be separate OS/process/container trust domains; secrets MUST NOT be accessible to the agent runtime. | ADR-01, TECHNICAL_DESIGN §2, SECURITY_MODEL §1–2 | Prevents overclaiming POC security. |
| 2 | **operation_hash extended** to bind `tool_name, tool_version, canonicalized_arguments, user_id, tenant_id, created_at`. Canonical JSON serialization specified precisely (UTF-8, key-sorted lexicographically, no whitespace, arrays order-preserving, numbers shortest round-trip). `tool_version` is now a semantic version string in the contract, registry, proposal, operation_hash, and audit record. | TOOL_CONTRACTS (all 5 tools + registry), SECURITY_MODEL §5–6, TECHNICAL_DESIGN §5a | A silently modified tool contract could otherwise execute a previously approved proposal against changed behavior. |
| 3 | **Re-authorization checklist** (9 checks) at confirmation-execution time: user identity, tenant, tool existence, current tool_version, authorization, proposal state, expiry, operation_hash, confirmation binding. The proposal MUST NOT execute based only on creation-time state. | SECURITY_MODEL §5.3, TOOL_CONTRACTS (authorization policy), TECHNICAL_DESIGN §5a | Closes the TOCTOU gap between proposal creation and execution. |
| 4 | **Idempotency split** into execution correlation ID (UUID v4, traceability) and idempotency key (deterministic content-derived, dedup). Same-key semantics defined: same request → replay result; different request → `IDEMPOTENCY_CONFLICT`; concurrent → exactly one execution via `BEGIN IMMEDIATE` + `INSERT OR IGNORE` insert-first test-and-set under the SQLite write lock. | ADR-08, TECHNICAL_DESIGN §7, SECURITY_MODEL §7, TEST_PLAN §2 (TC-044) | Prevents duplicate execution under retry and concurrency; separates correlation from dedup. |
| 5 | **Gateway store = SQLite (WAL mode, `data/poc_gateway.db`)** across all five documents, per locked POC scope. | ADR-03, ADR-08, TECHNICAL_DESIGN §3/§7/§12–14, SECURITY_MODEL §7–8 | Internal consistency + zero infrastructure. |
| 6 | **Entity-resolution split** (A–E: direct ID, exact name, partial name, ambiguous name, nonexistent) in the test plan. Ambiguous rules: model must NOT invent an ID, MUST search, MUST request disambiguation, `sales.order.create` MUST NOT execute. | TEST_PLAN §2 (new subsection), §3 | Makes entity-resolution accuracy measurable and prevents hallucinated writes. |
| 7 | **Verification semantics clarified:** what read-back proves and what it does NOT prove. Odoo JSON-2 transaction warning added: each JSON-2 request runs in its own SQL transaction; create → read-back is NOT one atomic transaction. | TECHNICAL_DESIGN §6, ADR-07 | Prevents a false sense of transactional safety. |
| 8 | **Security-claim classification** (demonstrated in POC / defense-in-depth / production requirement) introduced. The POC is explicitly NOT "production secure." | SECURITY_MODEL §1, TECHNICAL_DESIGN §2 | Prevents overstating security posture. |

*Deviation note (amendment 5): the research package (research-report §E) specified PostgreSQL 15+ for the gateway stores. The locked POC scope explicitly names SQLite, and the user's direct scope instruction takes precedence over the research package. The deviation is documented in ADR-03; the database-enforced INSERT-only audit role that PostgreSQL would have provided is reclassified as a **production requirement**, and the POC's append-only guarantee is classified as **demonstrated in POC** (code discipline + hash-chain verification) — not as a security boundary.*

---

## IMPLEMENTATION HANDOFF

The exact ordered implementation sequence (15 steps, each independently testable) is defined in `TECHNICAL_DESIGN.md` § IMPLEMENTATION HANDOFF. Do not duplicate it here. Refer to that section for:

1. Odoo Docker environment setup (odoo + db)
2. Tool contract JSON Schema definitions
3. Thin Odoo JSON-2 adapter
4. Tool registry loading
5. Authorization policy loading
6. Agent loop (no framework)
7. Tool Gateway (trust boundary)
8. Verification (read-back after write)
9. Idempotency store (SQLite WAL mode)
10. Audit log with hash chain (SQLite WAL mode)
11. Logging and tracing (JSON stdout)
12. CLI entry point
13. Test harness
14. 50-test dataset
15. Evaluation run and report
