# Agent-Native ERP — Architecture Challenges

**Date:** 7 September 2026 (second pass — evidence upgraded to primary-source verified; see `source-index.md`)
**Scope:** Research only. No implementation. No MVP. No UI. No database.

---

This document identifies challenges in the *composition* of the PRD's proposed architecture. It is distinct from `technical-risks.md`, which covers discrete risk items. Here, the focus is on architectural seams where individually proven components must interact and where the composition itself is the hard problem.

**New in this pass:** C11 (verified absence of cross-call transactions in Odoo JSON-2) and C12 (MCP protocol-version strategy), both arising from primary-source verification.

---

## C1 — The Chain Is Only as Strong as Its Weakest Composition Point

The PRD's core hypothesis is a linear pipeline:

```
NL → Agent → Tool Selection → Permission Check → Deterministic ERP Action → Verification → Audit Log → Human-readable Result
```

Each step is individually achievable. The composition problem is that each step has its own failure modes and its own trust boundaries, and the PRD treats the pipeline as if failures compose additively rather than multiplicatively.

**The challenge:** If tool selection is 95% accurate, schema validation 99%, permission enforcement 99.9%, and the ERP action succeeds 99% of the time, compound success is roughly 0.95 × 0.99 × 0.999 × 0.99 ≈ 93.2%. The PRD sets per-link thresholds (§45) but no compound end-to-end metric. **Verified benchmark context** `[V]`: BFCL V4's snapshot shows top models' Overall ~72–77 with Multi-Turn systematically below single-turn (Claude-Opus-4-5: 73.76 vs 88.58; GPT-5.2: 45.81 vs 81.85); τ-bench reports <50% task success and pass^8 <25% (retail) for strong agents on multi-turn tool-agent-user tasks. These numbers say the chain's probabilistic links are *worse in composition* than in isolation.

**What the PRD gets right:** each step must be deterministic except NL in/NLG out; the LLM must not enforce its own boundaries.

**What the PRD does not address:** no compound success metric, no per-step error budget, no mechanism attributing which step failed when the chain fails. The evaluation design should instrument every link and report chain-level metrics (and a pass^k-style consistency metric), not per-link averages.

---

## C2 — Permission Propagation Identity Flow

The most under-specified architectural seam in the PRD.

The PRD says (§20): "Respect Odoo's actual access rights and record rules rather than recreating a weaker permission layer outside the ERP." Correct policy — but the architecture does not show how identity flows from the platform to Odoo.

**The identity chain problem:**

```
Platform User → Platform Auth (JWT) → Agent Runtime → Tool Gateway → Adapter → Odoo API
```

At the Odoo boundary the request must carry *Odoo* identity. **Verified facts that now shape this seam** `[V]`:

1. Odoo JSON-2 authenticates with a **per-user bearer API key**; every operation is validated against the calling user's access rights, record rules, and field access.
2. There is **no OAuth on-behalf-of flow** for JSON-2 — the seam is a credential *mapping* problem (platform user → Odoo user → API key), not a token-exchange problem.
3. Odoo's documented best practice for automation is a **dedicated bot user** whose access-log attribution is the bot itself: "No user is impersonalized" — i.e., Odoo-side per-user audit does not exist under the bot-user pattern.

**Alternative: shared service account.** One Odoo credential with broad permissions; all authorization enforced by the platform. Simpler, but creates a single trust boundary — if the gateway has a bug or is compromised via prompt injection, Odoo's authorization provides no defense-in-depth.

**The PRD gap:** it states the policy (respect Odoo authorization) but does not architect the mechanism, and does not acknowledge that Odoo's own recommended pattern (bot user) *conflicts* with PRD §98. The POC must choose explicitly:

| Option | Defense-in-depth | Odoo-side audit | Ops cost |
|---|---|---|---|
| A. Per-user API keys | Odoo record rules enforce per-user scope | Per-user | Key lifecycle for every user (max 3-month lifetime, ≤10 programmatic keys/user default) `[V]` |
| B. Scoped bot user(s) | Gateway becomes sole trust boundary | Bot-attributed | One credential set per tenant |

Verified security anchors: OWASP LLM06 "complete mediation" and LLM01 least-privilege mitigations; MCP's confused-deputy and token-passthrough prohibitions `[V]`. Whichever option is chosen, the gateway must re-check authorization deterministically and never rely on model output.

---

## C3 — Verification Mechanism Design

The PRD's core hypothesis includes "Verification" as a distinct step, but never defines it architecturally.

**What verification must answer:** after the ERP reports success for a write, did the business state actually change to the expected state?

**Options (now evidence-annotated)** `[V]`:

| Approach | Mechanism | Latency cost | Confidence | Race risk |
|---|---|---|---|---|
| Read-back | GET immediately after write | +1 round trip | High for checked fields | Yes — Odoo docs warn each JSON-2 call is its own transaction; concurrent modification is explicitly flagged as dangerous for reservations/payments |
| Event subscription | Webhook/event listener | Near-zero extra | High if events reliable | Lower; **webhook support undocumented for this API era** `[U]` |
| Response parsing | Trust Odoo success body | Zero extra | Medium (return shape is method-specific, not a documented verification contract) | None (synchronous) |
| Invariant check | Verify expected field values post-write | +1 round trip | High for checked fields only | Yes |

**The architectural consequence:** read-back requires every write tool to have a paired read (or a generic read); event subscription requires queue infrastructure; response parsing couples the platform to Odoo return shapes that may vary by version.

**What the POC must decide:** minimum viable verification (recommendation: response parsing + spot-check read-back on `sales.order.create`). **What the PRD does not address:** the verification-failure path — compensate? annotate audit? alert? This is completely unspecified and must be designed before any pilot.

---

## C4 — Confirmation Binding and Replay Prevention

The PRD (§118) identifies `proposal_id`, `operation_hash`, `expires_at` — necessary but not sufficient.

**The lifecycle problem:**

```
User intent → Agent proposes → Policy: confirm → Proposal stored (immutable) →
User approves → Hash re-verified → Execute (atomically consume proposal) → Audit
```

**Failure points:**
1. **Proposal storage:** must be immutable; if DB-compromised, proposals can change between display and confirmation.
2. **Hash re-verification at execution:** recompute operation hash at execution time; any drift (bug/race/attack) must fail the execution.
3. **Confirmer identity:** PRD does not state that only the proposing user (or an explicit approver) may confirm.
4. **Expiry:** checked at execution time; expired-between-confirm-and-execute case must be defined.
5. **Double-execution:** two concurrent confirmations for one `proposal_id` → exactly-once execution requires an atomic state machine (proposed → confirmed → executing → completed/failed).

**The architectural challenge:** the proposal is a stateful entity with its own lifecycle; the PRD assigns it to neither the agent runtime nor the tool gateway. This is a missing component. Verified context: τ-bench's pass^8 <25% shows LLM flows cannot be trusted to preserve proposal integrity implicitly `[V]`; the confirmation guarantees must be deterministic code.

---

## C5 — Multi-Tenant Composition with External ERP

The PRD states tenant identity must be server-side (§97) and the LLM must never choose the tenant. It does not show how tenant isolation composes with external ERP connections.

**The composition problem:**

```
Platform (tenant A) → Tool Gateway (tenant A) → Adapter (tenant A) → Odoo (tenant A's instance)
```

If tenant A's adapter is misconfigured to point at tenant B's Odoo instance, the entire isolation model fails. The adapter **destination** (host + `X-Odoo-Database` header `[V]`) must be tenant-bound and validated at startup, not per-request from mutable config.

**What must be enforced:**
1. Adapter instances are created only inside a tenant context.
2. Adapter destination URL/database is tenant configuration, validated at startup.
3. Adapter credentials are scoped to that tenant's Odoo instance only.
4. Gateway validates tenant context against the adapter tenant before every call.

**Verified incumbent asymmetry** `[V]`: Odoo = one database, many companies (`company_id` + `ir.rule`; multi-company on Online Standard requires Custom plan); Frappe/ERPNext = **site-per-database**, multi-company only within a site. Tenant-mapping and permission-sync strategies must therefore be designed per ERP, not assumed uniform.

**PRD gaps:** no negative-tests requirement for adapter misrouting; no statement that one adapter instance must never serve two tenants; no credential-storage model (secret manager) beyond §33's `credential_reference`.

---

## C6 — Tool Catalog Governance

The PRD's canonical tool contract (§17: name, risk, readOnly, destructive, idempotent, requiresConfirmation, inputSchema, outputSchema) is good. Governance is missing.

**Governance questions:**
1. **Who creates tools?** Platform team, tenant, or adapter? Different trust implications. (MCP's rule — "clients MUST consider tool annotations to be untrusted unless they come from trusted servers" `[V]` — implies a signed/curated platform catalog.)
2. **Version compatibility:** schema changes can break conversations/workflows; PRD mentions versioning (§107) but not a backward-compatibility policy.
3. **Description quality as code:** Anthropic's official guidance — "Provide extremely detailed descriptions. This is by far the most important factor in tool performance." `[V]` — makes descriptions a reliability-critical, reviewed artifact, not documentation.
4. **Discovery/classifier evaluation:** PRD §28 proposes intent→tool-subset retrieval but does not specify how the classifier itself is evaluated or what happens on misclassification.
5. **Cross-ERP normalization depth:** how much normalization lives in the adapter vs. the contract is undecided (see C9).

---

## C7 — Conversation State and Entity Resolution

The PRD lists conversation state (§15) but does not specify cross-turn entity resolution.

```
Turn 1: اعمل طلب بيع لمحمد أحمد
Turn 2: خليه 30 قطعة بدل 20
```

Turn 2's خليه refers to the draft order proposed in Turn 1. The system must maintain: the proposed order (structured), the reference resolution, and the distinction between proposed (unexecuted) and executed orders.

**Architectural challenge:** entity resolution needs a structured, tenant-scoped, expiring state store — not raw chat history. **Verified benchmark context** `[V]`: multi-turn is precisely where agent reliability collapses (BFCL multi-turn tens of points below single-turn; τ-bench pass^k <25%) — so multi-turn support materially raises POC risk.

**POC decision required:** single-turn only for the POC (recommended), explicitly declared; if multi-turn is included, define the draft-state format and the modify-draft vs. create-new disambiguation rule.

---

## C8 — Error Taxonomy and Agent Recovery

The PRD's structured errors (§23: code, message, retryable, requiresUserAction) are a good start; the recovery mapping is missing.

| Category | Example | Deterministic agent action |
|---|---|---|
| Schema invalid | Model produces invalid arguments | Reject pre-execution; return schema error to model |
| Permission denied | User lacks permission | Return to user; never retry |
| ERP validation error | Odoo rejects write (required field) | Structured error; model may retry once with corrected args |
| ERP connection error | Timeout | Retry **with same idempotency key** only for idempotent-safe ops |
| Ambiguous outcome | Committed but response lost | Reconciliation path (C from R3); otherwise manual review |
| Verification failure | Write succeeded, state check failed | Alert + audit annotation; never silent |
| Tool not found | Hallucinated tool name | Return catalog hint |
| Rate limit | Too many calls | Backoff |
| Confirmation timeout | No user response | Expire proposal; return to user |

**The architectural challenge:** the error→action mapping must be deterministic code, not model judgment. Note: Odoo JSON-2 error bodies include a full Python traceback in `debug` `[V]` — the adapter must strip/contain `debug` before errors reach the model or user (both an information-leak and a prompt-injection surface).

---

## C9 — Adapter Abstraction Layer vs. Per-ERP Reality

The PRD's adapter abstraction (Odoo/ERPNext/native drivers) is sound but hides semantic divergence. **Now verified concretely** `[V]`:

| Dimension | Odoo | ERPNext/Frappe |
|---|---|---|
| Tenancy | One DB, many companies (`company_id`+`ir.rule`) | Site-per-database |
| Auth | Per-user bearer API key (max 3-month life) | Token `api_key:api_secret`, password, or OAuth Bearer |
| API surface | Explicit model/method endpoints; named args only | Auto-generated REST for all DocTypes (`/api/resource`) |
| Hidden behavior | Business workflows via `action_*` methods; per-call transactions | Server Scripts + permission query conditions execute logic *outside* the API schema |
| Permission model | ACL/record rules/field groups | Roles × DocTypes × permlevels × stages + User Permissions |

**The architectural challenge:** "create a sales order" is not one canonical action — required fields, identification (partner_id vs. customer code), validation timing, and error semantics differ. If the contract is too abstract, tool selection accuracy suffers; too ERP-specific, it does not generalize. Server Scripts mean ERPNext behavior cannot be fully inferred from schema — adapters need documented behavioral contracts, not just field maps.

**POC decision:** Odoo only; declare the abstraction as design intent. Do not build the ERPNext adapter until Odoo is proven (consistent with PRD's own trap list).

---

## C10 — Where Does the Audit Log Live?

The PRD lists audit fields (§24) but does not architect storage and protection.

| Approach | Complexity | Tamper resistance | Query |
|---|---|---|---|
| Same DB, separate table | Low | Low | Full SQL |
| Same DB, append-only table | Low | Medium | Full SQL |
| Separate audit database | Medium | Medium | Separate connection |
| Hash-chained table | Medium | High (tamper-evident) | Full SQL + verification |
| External WORM | High | Very high | Limited |

**Now verified context** `[V]`: neither Odoo (chatter notes) nor ERPNext (version/audit-trail views) provides native tamper-evident audit — the platform's audit layer is the only integrity anchor. Also note the MCP token-passthrough lesson: if the platform merely forwards user credentials, downstream logs attribute actions to the user rather than the platform+agent chain, collapsing accountability. Option A (per-user keys) preserves richer downstream attribution precisely because actions execute as mapped users.

**POC:** same-DB append-only table with per-row hash chain is sufficient; production requires separation. PRD also does not distinguish platform audit vs. ERP-internal audit — both should exist with different purposes.

---

## C11 — No Cross-Call Transactions in Odoo JSON-2 (NEW, verified)

**Verified:** "All calls to the JSON-2 endpoint run in their own SQL transaction… Using the JSON-2 API, it is not possible to chain multiple calls inside a single transaction… The solution is to always call a single method that performs all the related operations in a single transaction" (Odoo docs; `action_*` business methods and `search_read` cited as the pattern) `[V]`.

**Architectural consequence:** any multi-step business operation ("create order, then confirm it") is a **saga across two independent transactions**, not a transactional unit. The PRD's §105 ("do not hold model reasoning inside DB transactions") is consistent, but the stronger, verified constraint is that *the ERP cannot provide multi-call atomicity at all* through this API. Therefore:
- Tools should map to single `action_*`-style ERP methods wherever possible (e.g., prefer one method that creates+validates, not create-then-update sequences).
- Where multi-step is unavoidable, the gateway needs outbox-style persistence of intended steps + compensation logic (Saga/Outbox patterns `[V]`).
- The "plan → validate → confirm → execute short deterministic transaction" model must be per-call, with the idempotency/ambiguity machinery of R3 attached to each call.

---

## C12 — MCP Protocol Version Strategy (NEW, verified)

**Verified:** MCP's current protocol version is **2026-07-28**; 2025-11-25 remains fully published as the prior generation `[V]`. The PRD (§5, §25, §120, §183) references 2025-11-25 as current without versioning strategy.

**Architectural consequence:** the MCP integration boundary must be version-aware: negotiate protocol version at connection, pin the spec version per release, and track the 2025-11-25 → 2026-07-28 diff before building the adapter (diff not yet performed — tracked in `source-index.md` follow-ups). Security-relevant MUSTs read this session (tool annotations untrusted; explicit user consent; per-client consent for proxies; no token passthrough) are from the 2025-11-25 text and must be re-checked against 2026-07-28 wording before being load-bearing.

---

## Summary of Compositional Gaps

| # | Challenge | PRD Status | POC Impact |
|---|---|---|---|
| C1 | Compound success measurement | Not addressed | Define end-to-end metric + pass^k consistency |
| C2 | Permission identity flow | Policy stated; mechanism absent; bot-user conflict unacknowledged | Choose per-user keys vs. scoped bot user |
| C3 | Verification mechanism | Mentioned, not defined | Response parsing + spot read-back; define failure path |
| C4 | Confirmation lifecycle | Fields listed; state machine absent | Design proposal state machine |
| C5 | Multi-tenant + external ERP | Isolation stated; composition absent | Tenant-bind adapter destination; asymmetric models per ERP |
| C6 | Tool catalog governance | Schema defined; governance absent | Curated signed catalog; descriptions as code |
| C7 | Entity resolution across turns | Component listed; design absent | Single-turn POC; declare explicitly |
| C8 | Error taxonomy & recovery | Fields listed; mapping absent | Deterministic error→action map; strip `debug` |
| C9 | Adapter abstraction vs. ERP reality | Tension unacknowledged | Odoo-only POC; per-ERP behavioral contracts |
| C10 | Audit storage architecture | Fields listed; storage absent | Append-only + hash chain; platform vs. ERP audit split |
| C11 | No cross-call transactions (verified) | Not acknowledged | Tools = single ERP methods; saga/outbox for multi-step |
| C12 | MCP version strategy | Single version assumed | Version negotiation; re-verify MUSTs on 2026-07-28 |
