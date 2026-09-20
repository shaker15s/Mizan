# TRUST_BOUNDARY — Authority and trust in the POC today

**Generated:** 2026-09-20
**Companion:** `docs/ARCHITECTURE_MAP.md`, `docs/SECURITY_CONTROL_MATRIX.md`

---

## 1. The core principle

> **The LLM is untrusted. The server decides.**

This principle is reflected in the code today but is NOT yet enforced by a process boundary (all components share one OS process in the POC).

## 2. Authority — what the model may never decide

| Decision | Owner today | Owner target |
|---|---|---|
| User identity | Server config (`AgentRuntime.user_id`) | Authenticated session (server) |
| Tenant identity | Server config (`AgentRuntime.tenant_id`) | Authenticated session (server) |
| Role / allowed tools | `users.yaml` → PolicyEngine | ABAC policy engine |
| Tool availability | `ToolRegistry` (server-owned constant) | Versioned, signed registry |
| Argument validity | JSON Schema validator in gateway | Same (typed schema) |
| Approval state | `ConfirmationStore` (+ user click) | Approval Engine |
| Idempotency key | Derived server-side | Same (lease layer added) |
| Execution identity | `execution_id` = server-generated UUIDv4 | Same |
| Verification truth | `verification.py` + ERP read-back | Verification Engine |
| Final ERP truth | Odoo (read-back) | Same |
| Audit integrity | SHA-256 chain in `AuditStore` | Evidence graph + immutable store |

**The model may:** suggest a tool name, suggest arguments, write natural-language Arabic prose, ask clarifying questions.
**The model may NOT:** pick tools outside registry, set identity, set tenant, approve itself, declare success, fabricate an order ID, bypass confirmation, mutate audit state.

---

## 3. Trust boundary diagram (POC today)

```text
  UNTRUSTED ZONE                                    TRUSTED ZONE (server)
  ──────────────                                    ─────────────────────

  Browser JS            ── HTTPS/HTTP ──▶  Web Server (stdlib)
  (any code, user can                                            │
   edit in devtools)                                             │
                                                                 ▼
                                                         AgentRuntime
                                                         (LLM output
                                                          is UNTRUSTED
                                                          input here)
                                                                 │
                                                          validate()
                                                          against registry
                                                                 │
                                                                 ▼
                                                         ToolGateway ◀─── policy.yaml
                                                                 │         (TRUSTED)
                                            ┌────────────────────┼────────────────────┐
                                            ▼                    ▼                    ▼
                                      AuditStore         IdempotencyStore    ConfirmationStore
                                      (append-only,      (content-           (operation_hash
                                       hash-chained)      addressable)        bound to args+ts)
                                            │                    │                    │
                                            └────────────────────┼────────────────────┘
                                                                 │
                                                                 ▼
                                                           OdooClient ──▶ Odoo
                                                           (server-owned
                                                            credentials)


  ──────────────────────────────────────────────────────────────────────────────
  LLM PROVIDER (untrusted responses; validated before use)
     │
     └── llm_client parses tool calls; malformed output → AgentRuntimeError
```

### Important POC caveats (must be addressed for production)

1. **Module isolation is not a security boundary.** In the POC, AgentRuntime, ToolGateway, OdooClient, and the web server all run in the same Python process. A bug that lets model output confuse the runtime can theoretically reach Odoo credentials. The production architecture (plan §40) introduces a connector/secret boundary process.
2. **The web server has no authentication.** Identity comes from `POC_USER_ID` env var. That's fine for a local cockpit demo; it is NOT production.
3. **CORS is `*` and there is no CSP/CSRF protection.** (Flagged §7.F.)
4. **The circuit breaker is global, not per-tenant/connector.** (Flagged §7.I.)
5. **Telemetry can leak env/settings information in dev mode.** (Flagged §7.G.)
6. **Credentials for live LLM/Odoo live in environment variables** loaded into process memory. (Plan §40: secret manager in production.)

---

## 4. Data classification

Every piece of data is classified today; new code must respect these boundaries.

| Data | Trust level | Processed by | Must never reach |
|---|---|---|---|
| Raw user text | UNTRUSTED | AgentRuntime | Policy, audit identity, idempotency key |
| LLM response text | UNTRUSTED | AgentRuntime (composer) | Any trusted store as authoritative data |
| LLM tool call (name/args) | UNTRUSTED | AgentRuntime (validated) | Gateway before validation |
| Server-side user_id/tenant_id | TRUSTED | Config/session | Never modified by LLM or user payload |
| Tool contracts | TRUSTED (read-only constant) | Registry | Model |
| Policy file (`users.yaml`) | TRUSTED | PolicyEngine | LLM prompt (it may see tool NAMES only) |
| Idempotency keys | TRUSTED (server-derived) | IdempotencyStore | Client supply (validated strictly) |
| Operation hash | TRUSTED (server-derived) | ConfirmationStore | Client modification |
| Audit records | TRUSTED (append-only) | AuditStore | Any update/delete |
| Odoo credentials | SECRET | OdooClient only | Prompts, audit, telemetry, frontend, exceptions |
| Odoo response data | UNTRUSTED (external system) | Gateway + Verifier | Treat as any other external input; escape when rendering |
| Frontend-rendered strings (from ERP/LLM/audit) | UNTRUSTED | Browser (must escape) | innerHTML without sanitization |

### Implicit rule

Everything coming back from Odoo is treated as **untrusted external data**, including product names and customer names. ERP data could carry prompt-injection payloads or XSS payloads if rendered unsafely.

---

## 5. Policy Enforcement Points (PEPs) and Policy Decision Points (PDPs)

Today's PDPs (decision points):

| Decision | PDP | PEP (enforcement) |
|---|---|---|
| "May this user call this tool at all?" | `PolicyEngine.evaluate()` against `users.yaml` | `ToolGateway._process()` — deny before any Odoo call |
| "Does this request need confirmation?" | `PolicyEngine` (write/requiresConfirmation → CONFIRMATION_REQUIRED) | `ToolGateway._process_mutating()` — creates proposal, does NOT execute |
| "Is the proposal still valid for approval?" | `ConfirmationStore.approve()` — 9-point re-check | Atomically transitions proposal state in SQLite |
| "Is this a duplicate write?" | `IdempotencyStore.reserve()` | INSERT OR IGNORE under BEGIN IMMEDIATE → CONFLICT/IN_PROGRESS |
| "Did the write actually land in Odoo with the right contents?" | `verify_sales_order_creation()` ERP read-back | `execute_verified()` — blocks success if verification fails; marks AMBIGUOUS |
| "Is this tool call shape valid?" | JSON Schema (jsonschema) | `ToolGateway._validate_arguments()` |
| "Is the tool known and at the right version?" | ToolRegistry | `ToolGateway._process()` |

---

## 6. Data flow for one write, with trust labels

```text
[USER] types "اعمل طلب لأحمد 10 مياه"
   │  UNTRUSTED text
   ▼
[AgentRuntime] builds prompt with UNTRUSTED text and TRUSTED tool defs
   │
   ▼
[LLM Provider] returns UNTRUSTED tool_call {name:"sales.order.create", arguments:{customer_id:42, lines:[{product_id:7,qty:10}]}}
   │  UNTRUSTED
   ▼
[AgentRuntime._validate_tool_call]  ◀── TRUSTED ToolRegistry schema
   │  → version="1.0.0" or rejected
   ▼
[ToolGateway.handle_request]
   ├─ envelope validation (field whitelist, idempotency format)
   ├─ registry lookup (TRUSTED)
   ├─ jsonschema validate
   ├─ PolicyEngine.evaluate(TRUSTED user/tenant from server config, not LLM)
   │      → CONFIRMATION_REQUIRED
   ├─ compute idempotency_key (TRUSTED derivation, ignores any client-supplied key mismatch)
   ├─ IdempotencyStore.reserve → RESERVED
   ├─ ConfirmationStore.create_proposal with TRUSTED operation_hash
   └─ AuditStore.append (TRUSTED record)
   ▼
[FRONTEND] shows signature card (proposal_id, operation_hash, countdown, EDIT/APPROVE/CANCEL)
   │  user click APPROVE
   ▼
[ToolGateway.confirm_and_execute]
   ├─ ConfirmationStore.approve (9-point recheck, TRUSTED)
   ├─ product existence pre-check (TRUSTED read to Odoo)
   ├─ OdooClient.create(sale.order, payload)  ◀── SERVER-HELD credentials, NEVER exposed
   ├─ verify_sales_order_creation (TRUSTED read-back against expected values)
   │    · customer match
   │    · state == draft
   │    · client_order_ref == idempotency_key  (provenance)
   │    · line count + per-line product+qty
   │    · non-negative totals
   ├─ IdempotencyStore.complete (TRUSTED state transition)
   └─ AuditStore.append verification record
   ▼
[AgentRuntime.compose_answer] → Answer with TRUSTED fields only (real order_id from verification)
   ▼
[FRONTEND] renders via textContent / safe DOM — ERP strings are escaped
```

---

## 7. Secrets flow

### Today (POC)
```text
.env / environment  ──▶  settings.py  ──▶  llm_client / odoo_client
                                       (credentials live in process memory)
```
* Sanitized: `AuditStore._sanitize_arguments` drops keys like `api_key`, `password`, `token`, `secret` from audit payloads.
* `IdempotencyStore._validated_result` refuses to persist a result containing forbidden secret keys.
* The web settings endpoint masks secrets in responses.
* Frontend code is served from local disk (no CDN supply chain risk).

### Target (production, per plan §40)
```text
[Agent Runtime process]    ◀── no credentials
        │  authorized connector request (mTLS / signed token)
        ▼
[Connector/Secret Boundary] ◀── short-lived credential from secret manager
        │
        ▼
     [Odoo]
```

---

## 8. Residual risks in the POC (tracked for remediation)

| Risk | Status | Plan ref |
|---|---|---|
| Same-process compromise of gateway by LLM output | Accepted for POC; mitigated by strict schema and registry isolation (no code execution, no eval, no pickling) | §40 |
| No authentication on web API | Accepted for local POC | §52 |
| Wildcard CORS | Accepted for POC | §7.F |
| No CSP headers | Accepted for POC | §7.F, §52 |
| Global circuit breaker | Tracked — must become per-connector/tenant | §7.I |
| No rate limiting | Tracked | §7.F |
| Dev routes (`/api/replay`, slash eval) public | Tracked — must be isolated/disabled in prod | §7.F |
| Audit log in same SQLite file as runtime data | Accepted for POC; production moves to separate/immutable store | §20 |
| No TLS termination in-app | Expected to be behind reverse proxy in any real deploy | N/A (infrastructure) |
| Settings allow runtime reconfiguration without auth | POC-only | §52 |

---

## 9. Invariants callers can rely on

Every code path in the current tree satisfies these (enforced by tests):

1. A successful `GatewayResult` with `verified=True` has an `audit_id` and an `external_record_id` derived from the ERP read-back.
2. No successful write path returns without calling `IdempotencyStore.complete()` or `.fail(AMBIGUOUS)`.
3. Every gateway request is audited exactly once (on success, on deny, even on invalid envelope).
4. `confirm_and_execute` refuses to execute if the proposal has been declined, expired, tampered (hash mismatch), or if the tool version changed.
5. Client-supplied `idempotency_key` that does not match the server-derived value is rejected at envelope validation.
6. No code path issues an Odoo `create` before idempotency reservation.
7. No code path issues an Odoo `create` before confirmation for `requiresConfirmation=True` tools.
8. The model can never call a method on `OdooClient` directly — it only emits `{name, arguments}` that are matched to contracts; the gateway constructs the Odoo payload itself.
