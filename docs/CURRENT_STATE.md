# CURRENT_STATE — MIZAN POC as of commit `69a1c52` (+ cockpit/CI follow-through)

**Generated:** 2026-09-21
**Commit:** `69a1c52` (PR #3 on `main`) plus the cockpit/API/CI follow-through on this branch
**Baseline evidence:** `/evidence/EVIDENCE_MANIFEST.json` → EV-001 … EV-007

> Numbers below were re-verified on 2026-09-21 against the working tree. Where an
> older figure appears later in the document, the tables in §1 and §6 are the
> ones to trust.

This document describes **what is actually in the repository today** — not the target architecture. It is verified against the code on disk.

---

## 1. Scope of the codebase

```text
00-research/           Background research (competitive, risks, architecture challenges)
01-spec/               Product requirements document (PRD)
02-poc/                Design docs, security model, audit reports, hardening notes
03-poc-src/poc/        POC runtime (Python, zero frameworks)
03-poc-src/tests/      587 passing tests (5 live-Odoo tests auto-skip) + 144-case dataset
03-poc-src/poc/harness Evaluation harness (deterministic / simulated / live modes)
03-poc-src/poc/decision Decision layer (Jev) — provider-neutral signal, never an authority
03-poc-src/poc/web/    Vanilla-ES web cockpit (4 files, no build, no CDN)
docs/                  Architectural documentation (this document and siblings)
evidence/              Canonical evidence manifest
```

### Language / dependencies
* Python **3.11+** (tested on 3.11.2)
* SQLite (WAL mode) — single embedded database at `03-poc-src/data/poc_gateway.db`
* 6 pinned Python deps: `anthropic`, `httpx`, `jsonschema`, `python-dotenv`, `pyyaml`, `pytest`
* Zero web frameworks (vanilla `http.server`-based server at `poc/web_server.py`)
* Frontend: zero build, vanilla ES modules in `poc/web/`

---

## 2. Modules (verified against source)

| Module | File | LOC | Responsibility |
|---|---|---|---|
| DB init | `poc/db/init.py` | 164 | Idempotent SQLite schema (`audit_log`, `idempotency_keys`, `proposals`), WAL mode. |
| Tool registry | `poc/tool_contracts.py` | 314 | Static versioned contracts for **5 tools** (customer.search, customer.get, product.search, sales.order.create, sales.order.get). |
| Authz / policy | `poc/authz.py` | 218 | YAML-loaded user→{allowed,denied}_tools policy; decisions: `allowed`/`denied`/`confirmation_required`. |
| Errors | `poc/errors.py` | 412 | Canonical error taxonomy + translation from internal codes and Odoo exceptions into `StructuredError` (code, retryable, requires_user_action, category, message_ar). |
| Idempotency store | `poc/idempotency.py` | 700 | Content-addressable 32-hex keys; SHA-256 fingerprints; states: `pending`/`completed`/`unknown`; lease timeout=300s; renewal on expiry; reconciliation outcome. |
| Confirmation store | `poc/confirmation.py` | 355 | Proposal lifecycle: `proposed`→`confirmed`/`failed`; 9-point re-check on approve (identity, tenant, tool version, policy, expiry, operation_hash). |
| Audit store | `poc/audit_store.py` | 318 | Append-only log with SHA-256 hash chain (GENESIS=`0*64`); allowlist argument sanitization; chain verification. |
| Verification | `poc/verification.py` | 146 | Post-write read-back verification for sales-order creation (partner, state=draft, provenance via `client_order_ref`, line count, per-line product+qty, non-negative totals). |
| Gateway | `poc/gateway.py` | 1251 | Orchestration boundary: envelope validation → schema → policy → idempotency → confirmation → (for reads) Odoo execution → audit. For writes: returns proposal for signature; `confirm_and_execute()` does the ERP mutation + verification + idempotency completion. |
| LLM client | `poc/llm_client.py` | 607 | Provider-neutral client with Anthropic + OpenAI-compatible adapters; structured tool-call parsing; streaming support. |
| Simulated LLM | `poc/simulated_llm.py` | 508 | Rule-engine fallback used by deterministic/simulated harness modes (Arabic keyword → tool call). |
| Normalization | `poc/normalization.py` | 15 | Arabic orthographic fold (hamza, taa marbuta, yaa/alif maqsura). |
| Prompts | `poc/prompts.py` | 164 | System and composer prompts (Arabic/Egyptian). |
| Agent runtime | `poc/agent_runtime.py` | 826 | LLM→gateway orchestrator: prompt build, tool validation, bounded repair turn, history management, stage events, `confirm()`/`decline()`/`amend_proposal()`. |
| Answer composer | `poc/answer.py` | 348 | Structured `Answer` type (headline, KPIs, typed sections, governance, notices). |
| Responder | `poc/responder.py` | 783 | Compose Arabic answers from gateway results (status cards, signature cards, error cards). |
| Sessions | `poc/sessions.py` | 236 | In-memory session store (conversation history, pending proposal). |
| Odoo client | `poc/odoo_client.py` | 271 | Odoo 19 JSON-2 client with JSON-RPC, global circuit breaker, structured exceptions. |
| Settings | `poc/settings.py` | 550 | Typed runtime settings with allowlist; secrets masked; hot-reconfigure. |
| Slash commands | `poc/slash.py` | 264 | `/` command support (settings, audit, eval, replay, etc.). |
| Web server | `poc/web_server.py` | 921 | Stdlib HTTP server with SSE streaming; routes: `/api/chat`, `/api/confirm`, `/api/decline`, `/api/amend`, `/api/audit`, `/api/settings`, `/api/health`, `/api/replay` (dev only). |
| CLI bootstrap | `poc/bootstrap.py` | 122 | Wires registry/policy/stores/gateway/runtime/web. |
| CLI | `poc/main.py` | 203 | CLI entry point. |

### Frontend (`poc/web/`)
* `index.html` — Cockpit shell
* `styles.css` — Arabic-first RTL design system (typography, spacing, cards)
* `app.js` — Entry, SSE streaming, keyboard shortcut / command palette
* `ui.js` — Render pipeline (message list, cards, signature surface, settings panel, audit view)
* `markdown.js` — Minimal safe markdown renderer (no HTML injection)

### Harness (`poc/harness/`)
* `cli.py`, `runner.py`, `cases.py` — Scenario loading, hermetic per-case DB/ERP, execution
* `graders.py`, `metrics.py`, `records.py` — Trajectory grading + metric aggregation
* `render/` — Console / markdown / HTML / JSON report renderers
* `environment.py` — Hermetic working directories per case

---

## 3. Tools currently registered

Tool registry version `1.0.0`. Five tools:

| Tool | Read-only | Risk | Confirmation | Odoo model | Method |
|---|---|---|---|---|---|
| `customer.search` | ✓ | R0 | — | `res.partner` | `search_read` |
| `customer.get` | ✓ | R0 | — | `res.partner` | `read` |
| `product.search` | ✓ | R0 | — | `product.product` | `search_read` |
| `sales.order.get` | ✓ | R0 | — | `sale.order` | `read` |
| `sales.order.create` | ✗ | R2 | required | `sale.order` | `create` (draft) |

Every tool contract carries: name, version, description, risk_level, readOnly, destructive, requiresConfirmation, idempotent, inputSchema (JSON Schema), outputSchema, odoo mapping.

---

## 4. Current execution flow (verified against code)

### Read path (`customer.search`, etc.)

```text
AgentRuntime.process(user_input)
  → build prompt + tool defs
  → LLM.chat(messages, tools)
  → _validate_tool_call against registry
  → ToolGateway.handle_request(ToolGatewayRequest, odoo_client)
      → envelope validation
      → registry lookup + version match
      → jsonschema.validate(args)
      → PolicyEngine.evaluate({user_id, tenant_id, tool_name, tool_version})
      → contract is readOnly + ALLOWED → _execute_read
          → odoo_client.search_read / read
          → Arabic-orthography query variants on empty search
          → _shape_read_result
      → audit.append(gateway outcome)
  → compose_answer(gateway_result)
  → AgentResult(...)
```

### Write path (`sales.order.create`)

```text
AgentRuntime.process
  → LLM → validate → ToolGateway.handle_request
      → ... validation, policy (→ CONFIRMATION_REQUIRED)
      → compute_idempotency_key (tenant+user+tool+canonical_args → sha256[:32])
      → idempotency_store.reserve(...) → RESERVED
      → confirmation_store.create_proposal(...) (operation_hash = sha256(tool+ver+args+user+tenant+ts))
      → audit.append with proposal_id+operation_hash
      → GatewayResult(status=confirmation_required, proposal=...)
  → cockpit shows signature card (countdown timer, EDIT/APPROVE/CANCEL)
[...user clicks APPROVE...]
AgentRuntime.confirm(proposal_id)
  → gateway.confirm_and_execute(proposal_id, user, tenant, odoo_client)
      → confirmation_store.approve(proposal_id)
          9-point check: identity, tenant, tool exists, version match,
          policy re-evaluated, state==proposed, not expired,
          operation_hash recomputes to stored value
      → execute_verified(key, execution_id, args, odoo, tenant, user)
          → pre-check: product existence read
          → odoo_client.create("sale.order", payload)
          → verify_sales_order_creation(read-back: partner, state=draft,
              client_order_ref==idempotency_key, lines, non-negative totals)
          → idempotency_store.complete(...) OR fail(AMBIGUOUS) on post-write errors
          → audit.append(verification record)
```

---

## 5. State that exists today (and where it lives)

There is **no single canonical state machine** today. State is split across several stores:

| Concept | Store | States |
|---|---|---|
| Gateway outcome | `GatewayResult.status` | `accepted`, `denied`, `validation_error`, `confirmation_required`, `replay`, `declined`, `conflict`, `in_progress`, `reconciliation_required`, `erp_error` |
| Idempotency record | `IdempotencyStore` → `idempotency_keys.state` | `pending`, `completed`, `unknown` |
| Proposal | `ConfirmationStore` → `proposals.state` | `proposed`, `confirmed`, `executing`, `completed`, `failed` |
| Agent outcome | `AgentResult.outcome` | `tool_call`, `text_only`, `unknown_tool_rejected`, `malformed_tool_call`, `invalid_arguments`, `multiple_tool_calls`, `confirmed_execution`, `confirmation_declined`, `confirmation_amended`, `llm_error`, `erp_error` |
| Answer status | `Answer.status` (derived) | `signature_required`, `completed_read`, `completed_write`, `error`, `denied`, `pending`, `text_only`, etc. |
| Audit result | `audit_log.result_status` | `success`, `error`, `pending`, `denied` |
| Session / conversation | `SessionStore` (in-memory) | tracks active proposal + history, no formal state enum |
| Frontend visual | JS timers + CSS classes | progress rail driven by `stages[]` events AND client-side countdown timers |

**This is the fragmentation called out in §7.A of the plan.**

---

## 6. Test baseline (verified)

Re-verified 2026-09-21 (EV-006):

```text
pytest:        594 passed, 0 failed, 5 skipped (live-Odoo integration)
harness det:   144/144 PASS (dataset v2.0)
decision:      baseline/shadow/advisory/heldout/enforcing PASS (synthetic provider)
               realistic (negative control) GATE FAIL · adversarial (negative control) FAIL
doctor:        17 checks ok · 1 warning · 0 blockers
```

The two red decision runs are intentional negative controls: a deliberate
mid-quality and hostile provider must fail the gates, otherwise the gates would
be decorative. See `docs/DECISION_LAYER.md` and
`03-poc-src/data/reports/decision-comparison.md`.

Safety metrics (deterministic harness), all green:

* unauthorized writes = **0**
* duplicate orders = **0**
* audit coverage = **100%**
* audit chain valid = **yes**
* idempotency conflict detected = **yes (3/3)**
* prompt injection resisted = **1/1**
* structured answer rate = **100%**
* reasoning leaks = **0**
* read p95 ≈ **4 ms**, write p95 ≈ **14 ms**

---

## 7. Security controls currently in place

| Control | Location | Status |
|---|---|---|
| Server-owned tool registry | `tool_contracts.py` | ✓ strict closed set |
| Schema validation (JSON Schema) | `gateway.py` → `jsonschema.validate` | ✓ fail-closed |
| Deterministic authz (YAML) | `authz.py` | ✓ user/tenant/tool allowlist |
| Human-in-the-loop for writes | `confirmation.py` | ✓ 9-point re-check at approve time |
| Operation hash binding | `confirmation.py:compute_operation_hash` | ✓ SHA-256 over tool+ver+args+user+tenant+created_at |
| Content-addressable idempotency | `idempotency.py` | ✓ key = sha256[:32](tenant+user+tool+canonical_args) |
| SHA-256 audit hash chain | `audit_store.py:compute_row_hash` | ✓ append-only, GENESIS=`0*64` |
| Argument allowlist in audit | `audit_store.py:_sanitize_arguments` | ✓ drops non-allowlisted keys (anti-credential leak) |
| Post-write read-back verification | `verification.py` | ✓ strict identity/lines/provenance |
| Structured error taxonomy | `errors.py` | ✓ canonical codes + retry/user-action flags |
| Arabic orthography normalization | `normalization.py` + gateway `_query_variants` | ✓ hamza/taa/yaa folding (2-variant bound) |
| Proposal amendment (re-hash + re-authz) | `agent_runtime.amend_proposal` | ✓ declines old + creates new proposal |
| Secret key stripping (idempotency result) | `idempotency.py:_contains_secret_key` | ✓ blocks api_key/password/token/… |
| Simulated/rule LLM (determinism) | `simulated_llm.py` | ✓ offline reproducible runs |

---

## 8. Known gaps actually present in the code

Verified against source, these plan §7 items are real in today's tree:

| # | Gap | Evidence |
|---|---|---|
| A | Execution state fragmentation | §5 above: 7 parallel state concepts |
| B | Frontend uses timers for pipeline | `poc/web/ui.js` drives the rail via stage events but also uses its own TTL countdown; the rail shows "executing" via stage event but "Approved" visual can drift. |
| C | Proposal editing goes through `amend_proposal` server-side | ✓ already server-validated + creates new hash (mostly addressed but no explicit version numbering) |
| D | **No fake ERP IDs found** — verified `gateway._verification_success` returns the real `created_ids[0]` from Odoo, never a random fallback. | ✓ Addressed |
| E | HTML injection surface — `markdown.js` is minimal but there are `innerHTML` uses in `ui.js` that need audit. | ⚠ needs audit |
| F | Dev HTTP surface — web_server has wildcard CORS (`Access-Control-Allow-Origin: *`), no authentication, `/api/replay`, `/api/test/` reachable. | ⚠ POC-only |
| G | Telemetry — settings panel exposes env info; dev flags may leak provider URLs. | ⚠ POC-only |
| H | Voice capability — browser speech/SpeechRecognition present behind feature flag. | ⚠ behind flag |
| I | Circuit breaker is global in `odoo_client.py` — one breaker for all users/tenants. | ⚠ global |
| J | Idempotency TTL — reservations auto-renew at 300s; no explicit execution lease separate from reservation. | ⚠ no lease abstraction |
| K | Proposal lifecycle — states exist but are not one canonical machine (shared with #A). | ⚠ |
| L | Verification — financial totals are checked for non-negative existence, not recomputed; ERP-owner principle respected. | ✓ correct approach |
| M | Reconciliation — `AMBIGUOUS` state exists in idempotency but adoption/re-execution/manual-review flows are not surfaced to UI. | ⚠ partial |
| N | Error taxonomy — exists (StructuredError) with canonical codes; not all paths have Arabic messages. | ⚠ partial |

---

## 9. What this means for the migration

The POC is solid as a deterministic single-process control-plane: all of registry, schema, authz, idempotency, confirmation, verification, and audit are already implemented and tested.

The next milestones are:

1. **Canonical Execution State Machine** (Phase 2) — collapse the 7 parallel state concepts into one authoritative state machine with explicit transitions; expose events so the frontend renders truth, not timers.
2. **Typed Action Envelope** (Phase 3) — introduce `Action` dataclass carrying trace/action/execution ids, actor, intent, entities, arguments, versions.
3. **Proposal Versioning** (Phase 4) — explicit `proposal_version`, versioned approval invalidation.
4. **Execution Lease** (Phase 5) — separate lease from idempotency reservation with heartbeat + expiry.
5. Then continue through policy 2.0, evidence graph, API hardening, etc.

We will preserve backward compatibility during the migration (strangler pattern — plan §100). Existing tests and the 144-case harness dataset must continue to pass.

---

## 10. Decision layer (Jev) — added since the previous revision

A provider-neutral decision layer lives in `03-poc-src/poc/decision/` (13 modules)
and is wired into the runtime, the web server, and the harness:

* **Default off.** `decision.provider=off` + `decision.mode=off` keeps the system
  identical to the un-integrated baseline; no environment variable can enable it.
* **Four modes:** `off`, `shadow` (record only), `advisory` (may narrow/escalate),
  `enforcing` (experimental, stricter gates). See `docs/DECISION_LAYER.md`.
* **Signal, never authority.** The layer cannot authorize, execute, approve or
  verify; the gateway remains the final boundary, and escalation is raise-only.
* **Evidence:** `DECISION_REQUEST/RESPONSE/ROUTING/ESCALATION/DISAGREEMENT` events
  are appended to the existing hash-chained evidence log.
* **Evaluation:** the same 144 golden cases, three synthetic provider profiles,
  a three-way comparison artifact, and per-mode release gates
  (`decision_any` / `decision_shadow` / `decision_advisory` / `decision_enforcing`).
* **Public surface:** every chat payload carries a compact `decision` view with
  `authority: signal_only`. `/api/health` and `/api/telemetry` expose a secret-free
  chip. Settings that touch `decision.*` hot-swap the router without touching
  identity or the gateway. Vanilla and canonical React cockpits render the chip
  as a warning/info badge, never as an approval.
* **CI:** after pytest, mock advisory then mock enforcing harness runs
  (`--no-repeat`) plus `build_decision_artifacts.py --check`. No live Jev figures
  are published until a real-key shadow + recalibrate + held-out.
