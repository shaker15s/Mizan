# Agent-Native ERP — Technical Risks

**Date:** 7 September 2026 (second pass — all external claims re-verified against primary sources; see `source-index.md`)
**Scope:** Research only. No implementation. No MVP. No UI. No database.
**Evidence labels:** `[V]` = primary source retrieved and read during this research session. `[A]` = assessed from prior research, not independently re-verified this session. `[U]` = uncertain; must be validated before being treated as fact.

---

## Risk Classification

- **Critical** — Must be addressed before any production deployment
- **High** — Must be addressed before external pilots
- **Medium** — Must be addressed before scaling
- **Low** — Known trade-off; acceptable for POC

**Material changes in this pass:**

1. R1/R3/R10/R14 upgraded from assumptions to verified findings after reading the Odoo External JSON-2 API documentation in full `[V]`.
2. R5 materially updated: a first dedicated Arabic tool-calling benchmark now exists (arXiv 2601.05101, Jan 2026) — Arabic prompts degrade tool-calling accuracy by an average of 5–10% `[V]`. The "no Arabic benchmark" framing of the first pass is outdated.
3. R6 strengthened: neither Odoo nor ERPNext documents a native tamper-evident audit trail `[V]` — the platform must own audit integrity.
4. New risk R15 (Odoo Online plan gating of the external API) added — a verified commercial blocker the PRD does not mention.
5. PRD correction recorded: the PRD's claim that CLI-Anything's catalog "lists Odoo (Community) and ERPNext" is **contradicted** by the current CLI-Anything Live Catalog `[V]`; GitHub issue #194 is an open contributor sign-up, not shipped functionality.

---

## R1 — Permission Propagation to External ERP (Critical)

**The risk:** The platform must enforce its own authorization model *and* propagate identity to the external ERP so that Odoo's own access rights and record rules also apply. If the platform uses a shared service credential with broad permissions, Odoo's authorization is bypassed and the tool gateway becomes the sole trust boundary.

**Why this is critical (verified):**
- OWASP LLM06:2025 (Excessive Agency) mitigation #7 "Complete mediation": "Implement authorization in downstream systems rather than relying on an LLM to decide if an action is allowed or not." `[V]`
- OWASP LLM01:2025 mitigation #4: provide the application its own API tokens and "restrict the model's access privileges to the minimum necessary." `[V]`
- MCP security best practices explicitly name the confused-deputy risk for proxy servers holding downstream credentials, and forbid token passthrough ("MCP servers MUST NOT accept any tokens that were not explicitly issued for the MCP server"). `[V]`

**What is now verified about Odoo's actual authorization surface `[V]` (Odoo master docs, External JSON-2 API):**
- "The JSON-2 API uses the standard security models of Odoo. All operations are validated against the access rights, record rules and field accesses of the user." Odoo enforces model-level access rights (`ir.model.access`: per-group CRUD), record rules (`ir.rule`: domain filters, global rules intersect, group rules union), and field-level access on **every API call**. `[V]` (security reference, 19.0)
- Authentication is a **per-user bearer API key**. There is **no OAuth on-behalf-of flow** documented for JSON-2 — the earlier "OBO token pattern" hypothesis is resolved: the mechanism is per-user API keys, not token exchange.
- Odoo **officially recommends dedicated "bot users"** for extended automated usage: minimum required permissions, empty password, and — notably — *"The Access log fields use the bot account. No user is impersonalized."*

**The verified tension the PRD does not acknowledge:** Odoo's own best practice (bot user, no impersonation) pulls in the **opposite direction** from PRD §98 ("use the actual connected user/role credentials"). Two real options:

| Option | Mechanism | Defense-in-depth | Odoo-native audit |
|---|---|---|---|
| A. Per-user API keys | Platform user → mapped Odoo user → that user's API key | Strong: Odoo record rules enforce per-user scope | Per-user |
| B. Scoped bot user(s) | One (or few) scoped bot accounts per tenant; platform policy engine does all authorization | Weak: bot permission scope is the ceiling; gateway bug = bypass | Bot account, explicitly *not* impersonated |

**What must be built:** the identity mapping (platform user → Odoo user → API key), key lifecycle management (see R14), a permissions mapping matrix, and negative tests proving Odoo's authorization rejects unauthorized operations even if the platform's policy engine has a bug.

**What is unproven:** whether Option A is operationally sustainable at scale (R14); whether the platform policy engine and Odoo authorization compose without double-deny conflicts; whether Odoo API-key `scope` values can restrict anything finer than RPC access (the documented scope model is coarse; row-level scope comes from the owning user's access rights/record rules).

---

## R2 — Verification of Mutations (Critical)

**The risk:** After executing a write, how does the system *know* the state changed correctly? A 200 response does not prove business correctness. The PRD's core hypothesis includes "Verification" but never defines the mechanism.

**What is now verified `[V]:**
- Odoo JSON-2 returns the method's return value serialized as JSON on success (e.g., `create` returns the new record id) and a structured JSON error object on failure (`name`, `message`, `arguments`, `context`, `debug` — note `debug` carries a full Python traceback and must be stripped before surfacing to models/users).
- Odoo's docs warn that each JSON-2 call runs in its **own SQL transaction** and that consecutive calls cannot be chained into one transaction — concurrent modification is "especially dangerous when performing operations related to reservations, payments, and such." A read-back can therefore race with other writers **by design**.

**Options:**
1. **Response parsing** — zero extra round trip; confidence depends on the method's return shape (not contractually documented as a verification surface).
2. **Read-back** — +1 round trip; high confidence for checked fields; races possible (Odoo's own docs warn about concurrency).
3. **Event subscription/webhooks** — requires extra infrastructure; JSON-2-era webhook support is not documented on the API page read `[U]`.
4. **Invariant check** — verify specific expected fields post-write.

**POC minimum:** response parsing + spot-check read-back on `sales.order.create` key fields. **The failure path (verification fails → annotate audit → alert → compensate?) is completely unspecified in the PRD and must be designed.**

---

## R3 — Idempotency Across a Boundary (Critical)

**The risk:** The platform generates an idempotency key, but the mutation happens inside Odoo. A naive retry can create a duplicate order.

**What is now verified `[V]:`
- The full Odoo External JSON-2 API page contains **no idempotency mechanism** — no `Idempotency-Key` header, no dedup semantics, no replay protection.
- Stripe's pattern is the verified reference: save the resulting status code and body of the first request per key (including errors); subsequent requests with the same key return the same result; keys pruned after ≥24h; all POST requests accept idempotency keys; parameter mismatch → `idempotency_error`; concurrent same-key → 409 Conflict; V4 UUID recommended. `[V]` (docs.stripe.com)
- Saga pattern (microservices.io): compensating transactions must be idempotent; no ACID isolation across saga steps. Transactional outbox: messages guaranteed sent iff the local transaction commits; consumers must be idempotent. `[V]`

**What must be built:**
1. Local idempotency store scoped `(tenant_id, user_id, idempotency_key)` recording request hash + result.
2. Check-before-execute; cached result on retry; record after execute.
3. **The ambiguous-outcome gap:** request reaches Odoo and commits, response is lost → the platform cannot know whether the write happened. Mitigation candidates (all unproven, to be validated in PRD experiment E3): pre-retry reconciliation query using a platform-supplied reference field on the order `[U]`; conservative manual-review queue for ambiguous mutations.
4. Never blindly retry irreversible actions (consistent with PRD §77 and the Saga literature).

---

## R4 — Prompt Injection Through Business Data (Critical)

**The risk:** Customer names, product descriptions, invoice notes flowing through the agent context can carry injection payloads; the model may follow them instead of user intent.

**What is verified `[V]` (OWASP Top 10 for LLM Applications 2025):**
- LLM01:2025 = Prompt Injection (top risk); LLM06:2025 = Excessive Agency. The two are coupled: indirect injection is a trigger path for excessive agency.
- OWASP's own position: *"it is unclear if there are fool-proof methods of prevention for prompt injection"*; research shows RAG/fine-tuning "do not fully mitigate prompt injection vulnerabilities." → **No complete defense exists; defense-in-depth is mandatory.**
- Prescribed mitigations relevant here: constrain model behavior; validate output format; least privilege (application holds its own API tokens); human approval for high-impact actions; isolate external content; adversarial testing.
- MCP treats tool annotations as untrusted unless from trusted servers ("clients MUST consider tool annotations to be untrusted unless they come from trusted servers") and requires explicit user consent before tool invocation — directly relevant to tool-poisoning defenses. `[V]`

**What the PRD does not specify (still open):** how business data is delimited from instructions; whether a secondary validation pass re-checks chosen tool/arguments against user intent; concrete adversarial payload coverage (PRD experiment E6 needs a defined corpus).

---

## R5 — Arabic NL-to-Tool Accuracy (High)

**The risk:** The PRD's targets (98% tool selection, 99.5% parameter validity) are engineering goals without domain evidence.

**What is verified `[V]:**
- **First dedicated Arabic benchmark exists:** arXiv 2601.05101 "Arabic Prompts with English Tools: A Benchmark" (submitted 2026-01-08; IEEE BigData 2025 LLMs4All workshop) — tool-calling accuracy **drops by an average of 5–10% when users interact in Arabic**, regardless of whether tool descriptions are Arabic or English. Caveats: built on translated BFCL with limited human review; a workshop paper, not an adopted industry benchmark.
- **BFCL V4** (last updated 2026-04-12) has no Arabic category. Snapshot: top model Claude-Opus-4-5 (FC) Overall 77.47 (Agentic 84.5, Multi-Turn 73.76, Live single-turn 88.58); GPT-5.2 (FC) 55.87 with Multi-Turn 45.81 vs Live 81.85 — a consistent, measured pattern that multi-turn/agentic reliability is materially below single-turn.
- **τ-bench** (arXiv 2406.12045): "even state-of-the-art function calling agents (like gpt-4o) succeed on <50% of the tasks, and are quite inconsistent (pass^8 <25% in retail)." README leaderboard snapshot: retail pass^1 0.692 (claude-3-5-sonnet) / 0.604 (gpt-4o); airline 0.460 / 0.420.

**Implication:** the 5–10% Arabic penalty is real but modest; the far bigger threat to the PRD's targets is the **multi-turn/agentic gap** (tens of points, and pass^k consistency collapse). ERP journeys are exactly the multi-turn, policy-following shape τ-bench measures. Treat 98% as a hypothesis; adopt a pass^k-style repeated-trial metric in the evaluation design; build a **native** (not translated) Arabic eval set — including Egyptian dialect and Arabic/English code-switching ("هات الSO بتاع محمد") — to avoid the translated-benchmark limitation the Arabic benchmark itself acknowledges.

---

## R6 — Audit Integrity (High)

**The risk:** If the audit log lives in the application database and is mutable, the trust layer collapses.

**What is verified `[V]:`
- FDA 21 CFR Part 11 §11.10(e) is the established design standard: "secure, computer-generated, time-stamped audit trails to independently record the date and time of operator entries and actions that create, modify, or delete electronic records. Record changes shall not obscure previously recorded information." §11.10(g) adds authority checks. (Regulatory analog, not a legal requirement for this product.)
- **Neither Odoo nor ERPNext documents a native tamper-evident audit trail.** Odoo's chatter logs timestamped change notes (ordinary mutable records); ERPNext offers document versioning and an "Audit Trail" that "can be used to view at most 5 previously amended versions of a submittable doctype" — change-log/compare level, not append-only. (Reported as "not found in official docs" — absence of evidence, carefully stated.)

**What must be built:** append-only audit store with hash chaining; audit writes separated from the application DB; audit access itself audited; platform identity preserved downstream (per MCP token-passthrough lesson: pure credential forwarding destroys downstream accountability). POC: same-DB append-only table + per-row hash chain is acceptable; production needs architectural separation.

---

## R7 — Multi-Tenant Isolation (High)

**The risk:** A tenant-filtering bug leaks data across tenants.

**What is verified `[V]:`
- **Odoo:** "In Odoo, multiple companies can be configured under one database" — multi-company = `company_id` field + `ir.rule` record rules (example `domain_force`: `['|', ('company_id','=',False), ('company_id','in', company_ids)]`). Enabling multi-company on Odoo Online Standard triggers a Custom-plan upgrade.
- **ERPNext/Frappe:** "Frappe is a multitenant platform and each tenant is called a site. **A site has its own database.**" Multi-company exists only *within* a site.
- `[A]` (carried from first pass): PostgreSQL RLS is a safety net, not a substitute for application-level filtering; restricted DB role required; `SET LOCAL app.tenant_id` per transaction.

**Composition risk (see architecture-challenges C5):** tenant isolation must bind the **adapter destination** (which Odoo instance/database — host + `X-Odoo-Database` header) to the tenant, not only the gateway policy. Note the two incumbent models are *fundamentally different* (Odoo: one DB, many companies; Frappe: many DBs) — tenant-mapping and permission-sync strategies must be designed separately per ERP.

---

## R8 — Tool Description Quality and Model Compatibility (Medium)

**The risk:** Tool catalogs tuned for one model degrade on another.

**What is verified `[V]:**
- Anthropic's official guidance: "Provide extremely detailed descriptions. This is by far the most important factor in tool performance." (with additional advice: merge related operations, namespace by service name, return high-signal data).
- BFCL V4 includes an explicit Format Sensitivity category and publishes per-model variance across FC vs Prompt modes — the same model can differ by tens of points (Claude-Opus-4-5: 77.47 FC vs 33.47 Prompt overall).

**What must be done:** test the same catalog with ≥2 providers and both calling modes; treat tool descriptions as versioned code (consistent with PRD §156/§158); description quality is a first-class engineering investment, not an afterthought.

---

## R9 — Confirmation Replay and Binding (Medium)

**Unchanged from first pass.** The proposal state machine (proposed → confirmed → executing → completed/failed), hash re-verification at execution, same-user confirmation, and short expiry remain unaddressed beyond field names (PRD §118). τ-bench's pass^8 <25% finding is a standing reminder that LLM-driven flows are too unreliable for these guarantees to depend on model behavior `[V]`.

---

## R10 — Odoo API Version Lock (Medium)

**What is now verified `[V]` (Odoo master docs, deprecation section):**
- XML-RPC and JSON-RPC at `/xmlrpc`, `/xmlrpc/2`, `/jsonrpc` are deprecated.
- The **db service was already removed in Odoo 20 (fall 2026)** and Online 19.1 (winter 2025) — earlier than the PRD's single-date framing.
- The **common and object services are scheduled for removal in Odoo 22 (fall 2028)** and Online 21.1 (winter 2027). The 19.0 external-RPC page carries the same removal notice and names JSON-2 as replacement.
- JSON-2 (new in 19.0) replaces the object service: named arguments only, bearer API-key auth, per-call transactions.

**Implication:** PRD §148's direction is correct; its timeline is incomplete. POC targets Odoo 19+ JSON-2 only; older deployments need a legacy-RPC adapter later — a scaling decision, not a POC blocker.

---

## R11 — Cost and Latency of Arabic NL Processing (Medium — upgraded from Low)

**What is verified `[V]:**
- BFCL V4 publishes per-model latency: frontier models' single tool-call benchmarks show mean ~2–15s and P95 up to ~33s (Claude-Opus-4-5: mean 3.13s / P95 7.56s; Gemini-3-Pro-Preview: P95 32.73s). These are benchmark round trips, not full ERP journeys.
- τ-bench tasks are long multi-turn interactions with nontrivial cost per resolved task.

**Implication:** the PRD's P95 targets (§45: <2.5s read / <4s write, "excluding model generation variability") exclude the component that will dominate real UX latency. Restate as end-to-end budgets with model turns itemized (PRD §76 sketches the right decomposition — make it the measurement norm).

---

## R12 — Malicious Tool Arguments (High)

**The risk:** Schema-valid arguments that pass JSON validation but are semantically wrong or malicious (`customer_id: 99999`, `quantity: -50`, inflated amounts).

**What is verified `[V]:** JSON Schema validates structure, not business semantics; OWASP LLM06 warns agents can perform unintended actions; Odoo's own docs warn about concurrent-transaction hazards for reservations/payments — implying business-level validation cannot be skipped at the gateway.

**What must be built:** business-level validation in the gateway (range checks, entity-existence checks against the tenant's Odoo instance, financial-threshold sanity checks) in addition to schema validation. Layered with Odoo's own validation (which runs server-side under the calling user's rights) — but the gateway must not rely on Odoo catching everything after the action is already proposed and confirmed.

---

## R13 — Data Leakage Through LLM Context (High)

**The risk:** Business data (customer names, prices, financial figures) flowing through the LLM prompt may be logged by the provider, retained in history, or leaked in errors.

**What must be built (unchanged):** data classification for LLM-bound fields; structured context instead of free-text dumps; conversation retention/deletion policy; user disclosure; enterprise zero-retention tiers for production. **Unproven:** whether redaction degrades Arabic tool-selection accuracy — measure in the eval set.

---

## R14 — Secrets Management and Per-User Key Lifecycle (Medium)

**The risk:** Odoo API credentials must be stored, rotated, and revoked. If leaked, they grant direct API access to the tenant instance.

**What is now verified `[V]` (Odoo API-key documentation):**
- Keys are per-user bearer tokens; manual creation requires a description + duration; **maximum key lifetime is 3 months** ("it is not possible to create keys that last for more than three months. This means that long lasting keys must be rotated at least once every three months."); keys are displayed once at creation; revocation via `res.users.apikeys.revoke()`.
- Programmatic generation/revocation exists (`res.users.apikeys.generate()`), default-limited to **10 keys per user** (HTTP 422 beyond), default-restricted to Settings administrators (system parameter `base.enable_programmatic_api_keys` relaxes this). Odoo documents rotation best practices and recommends "dedicated API keys per service or integration."
- CLI-Anything's own SECURITY.md stores API keys in **plaintext config files** with 0o600 permissions `[V]` — a reminder that the accelerator's credential hygiene is below enterprise ERP requirements and must not be inherited.

**Implication:** Option A (R1) implies a key-vault + rotation scheduler + break-glass revocation subsystem per tenant. POC: 1–3 manually created test keys suffice; automation is a pre-pilot requirement.

---

## R15 — Odoo Online Plan Gating of the External API (Medium — NEW, verified)

**The risk (commercial):** "Access to data via the external API is only available on **Custom** Odoo pricing plans. Access to the external API is not available on **One App Free** or **Standard** plans." `[V]` (Odoo docs). The same gating applies to enabling multi-company on Standard (triggers Custom upgrade) `[V]`.

**Implication:** SMB tenants on Odoo Online Standard — plausibly a large share of the target ICP — **cannot grant API access at their plan level**. The adapter market splits by deployment: self-hosted Odoo has no such plan gate but skews to larger/more mature customers. The PRD never mentions this constraint.

**Required action:** add a qualification question to pilot interviews ("which Odoo plan/deployment?"), add it to the compatibility matrix, and treat it as an explicit filter for the first 5–10 pilots.

---

## Summary Table

| # | Risk | Severity | Evidence status | PRD coverage |
|---|---|---|---|---|
| R1 | Permission propagation / confused deputy | Critical | `[V]` Odoo + OWASP + MCP | Policy stated; mechanism absent; bot-user tension unacknowledged |
| R2 | Mutation verification undefined | Critical | `[V]` Odoo transaction/response semantics | Gap |
| R3 | Cross-boundary idempotency | Critical | `[V]` Odoo (none) + Stripe pattern + Saga/Outbox | Required; ambiguous-outcome gap unaddressed |
| R4 | Prompt injection via business data | Critical | `[V]` OWASP LLM01/06 + MCP tool-annotation rule | Threat identified; delimiters/secondary checks unspecified |
| R5 | Arabic + multi-turn accuracy unproven | High | `[V]` Arabic benchmark (−5–10%), BFCL V4, τ-bench | Targets assumed |
| R6 | Audit integrity | High | `[V]` FDA §11.10(e); `[V]` no native tamper-evidence in Odoo/ERPNext | Fields listed; storage unspecified |
| R7 | Multi-tenant isolation composition | High | `[V]` Odoo one-DB / Frappe site-per-DB + `[A]` RLS | Stated; adapter-binding unspecified |
| R8 | Tool-description portability | Medium | `[V]` Anthropic guidance + BFCL format sensitivity | Unaddressed |
| R9 | Confirmation binding/replay | Medium | Design gap | Fields listed; state machine absent |
| R10 | Odoo API version lock | Medium | `[V]` full deprecation timeline | Direction right; timeline incomplete |
| R11 | Latency/cost realism | Medium | `[V]` BFCL latency + τ-bench | Targets exclude dominant model latency |
| R12 | Malicious tool arguments | High | `[V]` schema ≠ semantics | Partially addressed (§159 invariants) |
| R13 | LLM-context data leakage | High | Design gap | Unaddressed |
| R14 | Secrets + key lifecycle | Medium | `[V]` Odoo key constraints; CLI-Anything plaintext keys | §33 gestures; undersized |
| R15 | Odoo Online plan gating | Medium | `[V]` Odoo docs | **Missing entirely** |
