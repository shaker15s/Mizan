# FINAL AUDIT REPORT — Agent-Native ERP POC

**Auditor:** Independent final audit (multi-agent sweep + direct static review)
**Date:** 10 September 2026
**Scope:** `01-spec/`, `02-poc/`, `03-poc-src/` as they exist in the working tree on 2026-09-10.
**Method:** Full read of all 13 production modules, all 6 authoritative documents, ~18 test files, git state, and prior-run artifacts (`.pytest_cache`, `tools/step2_results.json`, `STEP15_EVALUATION_REPORT.md`). A 12-area multi-agent evidence sweep with adversarial verification was executed; 2 of 12 area reports completed and are incorporated (authorization; authority/LLM-trust). **Python/pytest could not be executed in the audit environment** (no interpreter on PATH; shell policy blocks it) — all dynamic claims rest on committed prior-run artifacts, not on a fresh audit-time run.

---

## 1. Executive Summary

The POC's **control plane is genuinely authoritative and defensively correct**: the LLM cannot choose tools, versions, identity, tenant, authorization, confirmation, idempotency keys, or verification outcomes. Every hostile model vector tested is rejected by named code. The audit hash chain, the fail-closed authorization engine, and the confirmation 9-point re-authorization checklist are correctly implemented and tested.

However, the POC **does not yet prove the end-to-end hypothesis it claims**, for four structural reasons:

1. **Read tools never execute.** The gateway returns `accepted / ready_for_execution` for `customer.search` and friends without ever contacting Odoo, and the CLI reports `success=true` with `result=null`. Half of the hypothesis chain (NL → tool → ERP read → structured result) is unimplemented.
2. **The live-model evaluation (50 Arabic cases) was never built or run.** No `test_cases.json`, no `run_eval.py`. Tool-selection accuracy, Arabic accuracy, and pass^3 are unevaluable. The first link of the chain — Arabic NL → correct tool selection — is **unproven**.
3. **Verification will likely false-fail on live Odoo.** `expected_amount_total` is computed locally as `Σ list_price × qty` and compared by exact Decimal equality to Odoo's tax-inclusive `amount_total`. Any taxed or pricelisted product → guaranteed `VERIFICATION_FAILED` on a *correct* order. This also contradicts TECHNICAL_DESIGN §6, which specifies only "present and non-negative".
4. **The repository is not reproducible as committed.** `users.yaml` (the entire authorization policy) is untracked; all `poc/*.py` and `tests/*.py` are untracked (one baseline commit exists); `requirements.txt` does not exist. A fresh clone cannot run the gateway or the test suite.

There is **no evidence of any security-critical defect**: no path was found by which the LLM, the CLI input, or the model output can produce an unauthorized, unconfirmed, duplicated, or falsely-verified ERP mutation. The defects found are liveness, truthfulness-of-presentation, audit-forensics, and reproducibility defects.

## 2. Verdict Summary

| Question | Verdict |
|---|---|
| Is the architecture coherent? | **YES** (with the read-path gap noted) |
| Is the server genuinely authoritative? | **YES** |
| Can the LLM or CLI bypass the control plane? | **NO** |
| Can unauthorized or unconfirmed ERP mutations occur? | **NO** (two-layer defense; live 403 evidence exists) |
| Can one logical mutation create multiple ERP records? | **NO** (proven at store/gateway level; live only at adapter level) |
| Can wrong ERP state be reported as verified? | **No false-PASS found**; false-FAIL likely on live (F-06) |
| Can uncertainty be reported as success? | **YES — in two bounded presentation paths** (F-04, F-05) |
| Can a reviewer reconstruct what happened? | **PARTIAL** (chain solid; verification rows corrupted; proposal↔execution link missing) |
| Can another engineer reproduce the evidence? | **NO as the tree currently stands** (fixable in minutes) |
| Does the POC demonstrate the Agent-Native ERP hypothesis? | **PARTIALLY** — control plane yes; NL→ERP loop only for writes, against fakes; live-model link absent |

## 3. Evidence Inventory

| Evidence | Type | Status |
|---|---|---|
| Full source read of `poc/*.py` (13 modules) | PROVEN BY SOURCE INSPECTION | Complete |
| All 6 design docs read in full | PROVEN BY SOURCE INSPECTION | Complete |
| ~18 test files read | PROVEN BY SOURCE INSPECTION | Complete |
| `tools/step2_results.json` | PROVEN BY LIVE ODOO (pre-POC, adapter-only) | Committed artifact |
| `.pytest_cache/v/cache/*` | Prior-run trace | Present, not independently re-executable by auditor |
| `STEP15_EVALUATION_REPORT.md` | Prior-run claim document | Read; claims not independently re-executable |
| Fresh pytest run at audit time | — | **NOT POSSIBLE in audit environment** |
| Live Odoo at audit time | — | **LIVE ODOO VALIDATION NOT AVAILABLE** (instance not running) |
| Live model evaluation | — | **NOT PERFORMED / NOT AVAILABLE** (no eval harness exists) |
| Multi-agent sweep (12 areas) | Partial | 2/12 area reports completed before workflow interruption; incorporated |

## 4. Findings

Severity: **CRITICAL** = invalidates a security/truthfulness guarantee the POC claims to prove. **HIGH** = materially weakens credibility or contradicts an authoritative doc.

### F-01 · HIGH · Reproducibility · `users.yaml` untracked
- **Evidence:** `git status: ?? 03-poc-src/users.yaml`; `poc/authz.py:19` (DEFAULT_POLICY_PATH); `tests/test_authz.py:22` (module-level `PolicyEngine()`).
- **Impact:** A fresh clone cannot construct the gateway or collect 30 authz tests; all security evidence is unreproducible from the repo.
- **Fix:** `git add 03-poc-src/users.yaml`. Five minutes.

### F-02 · HIGH · Implementation never committed
- **Evidence:** `git log` shows only baseline `fccebb2`; `git status` shows every `poc/*.py`, `tests/*.py`, `pytest.ini`, `users.yaml` untracked; `02-poc/*.md` modified.
- **Impact:** The entire STEP 2–14 implementation exists only as uncommitted working-tree state; a single accident destroys it; reviewers cannot diff.
- **Fix:** Commit the implementation as one or more logical commits.

### F-03 · HIGH · Read tools never execute (hypothesis chain half-implemented)
- **Evidence:** `poc/gateway.py:639-647` — readOnly → `ACCEPTED / ready_for_execution`, no Odoo call, and no executor for reads exists anywhere in the tree; `poc/main.py:12,23` maps `accepted → success=True` with `result=None`; `tests/test_scenario_golden_path.py:153-176` asserts only `status == "accepted"`.
- **Impact:** `customer.search/get`, `product.search`, `sales.order.get` return no ERP data to the model or the user, ever. TECHNICAL_DESIGN §5 step 5 ("Call Odoo adapter") is unimplemented for reads. The 50-case `read_happy`/`read_product` families are unexecutable by construction.
- **Classification:** Design-vs-implementation gap; safe direction (no false data), but the read half of the core hypothesis is not demonstrated.

### F-04 · MEDIUM · Truthfulness: `text_only` model prose sets `success=True`
- **Evidence:** `poc/agent_runtime.py:172-173` (text passes through, `gateway_result=None`); `poc/main.py:13,20,23` (`text_only` ∈ `_NON_ERROR_OUTCOMES` → `success=True`). (Sweep-confirmed.)
- **Impact:** A hallucinating or compromised model saying "تم إنشاء الطلب بنجاح" with no tool call is surfaced as `success=true`. No ERP mutation occurs (bounded), but model prose — not server truth — sets the success flag, contradicting the "server owns final truth" claim.
- **Fix:** `success=None` (or `False`) for `text_only`, or require non-null `gateway_result` before `success=True`.

### F-05 · MEDIUM · Truthfulness: read `accepted` reported as `success=true` with no data
- **Evidence:** `poc/main.py:12,23`; `poc/gateway.py:642-647`; result is `None`.
- **Impact:** CLI JSON says the read succeeded while nothing was executed. Related to F-03; separate because it is a presentation-layer claim, not a missing executor.

### F-06 · HIGH · Verification total check contradicts Odoo financial semantics
- **Evidence:** `poc/gateway.py:343-363` (expected = Σ `list_price × qty` via product read); `poc/verification.py:70-78` (exact `Decimal` equality vs `amount_total`); TECHNICAL_DESIGN §6 specifies only "amount_total is present and non-negative"; TOOL_CONTRACTS states "Prices, taxes, and totals are computed by Odoo".
- **Impact:** For any product with taxes, pricelist, or discount — i.e., typical Odoo demo data — a **correct** order fails verification → `VERIFICATION_FAILED`, user told something went wrong while a correct order exists. Fail-closed (never false success), but it breaks the live write golden path and makes verification unreliable in the false-negative direction. The fakes hide this (`FakeOdooClient.amount_total = 50.0 × 2` exactly).
- **Fix (POC-appropriate):** Align with TECHNICAL_DESIGN §6 — verify presence/non-negativity + per-line product/quantity/provenance; drop or tolerance-band the exact total comparison until a documented tax/pricelist model exists. Add one live-Odoo test with a taxed product before relying on it.

### F-07 · HIGH · Verification failure discards the created order ID
- **Evidence:** `poc/gateway.py:234-240` (`_verification_failure` → `external_record_id=None`); `created_ids[0]` used only on the success path (`gateway.py:387`); `complete(..., external_record_id=verification["external_record_id"])` at `gateway.py:421-434`; audit record same.
- **Impact:** In the most dangerous failure mode (order created in Odoo, read-back mismatch), **no gateway artifact records which Odoo order was created**. Recovery is only possible by manual search on `client_order_ref = idempotency_key`. Forensically weak exactly where it matters most.
- **Fix:** Thread `created_ids[0]` into the failure outcome and persist it in both the idempotency record and the audit row.

### F-08 · MEDIUM · Audit timestamp corruption on verification rows
- **Evidence:** `poc/gateway.py:286-287` — `"start_time": verification["status"]`, `"end_time": verification["reason"]` (status/reason strings in timestamp columns), violating SECURITY_MODEL §8 clock discipline.
- **Impact:** Every execution-phase audit row has non-timestamp values in `start_time`/`end_time`; verification timing is unrecoverable; time-range queries silently miss these rows. Chain integrity unaffected (the hash covers what is stored).
- **Fix:** `_utc_now()` for both fields; move status/reason to their own columns.

### F-09 · HIGH · Declined/abandoned confirmations permanently poison the idempotency key
- **Evidence:** Reserve happens in `gateway._process_mutating` (`gateway.py:699-707`) **before** the proposal is decided; `ConfirmationStore.decline` (`confirmation.py:215-282`) and expiry never release the row; `IdempotencyStore` has no release/GC method; a `pending` row with matching fingerprint yields `IN_PROGRESS` forever (`idempotency.py:362-368`).
- **Impact:** A user who declines a confirmation — or abandons it, or whose process crashes after reserve — can **never** reissue that identical order: every retry returns `IDEMPOTENCY_IN_PROGRESS` until manual DB surgery. One logical operation, zero mutations, permanently dead. Liveness defect; no duplication risk.
- **Fix (POC-minimal):** On decline, and on `CONFIRMATION_EXPIRED` at approval, release the pending reservation (delete the row where `state='pending'` and `execution_id` matches); document crash-residue as a production-reconciliation requirement.

### F-10 · MEDIUM · Reconciliation path unimplemented
- **Evidence:** Design §7 step 6 / ADR-08 mandate searching `sale.order` by `client_order_ref` on ambiguous outcomes; `IdempotencyStore.complete(..., reconciliation=ADOPTED)` exists (`idempotency.py:437-446`) but **no code anywhere performs the reconciliation search** (grep: only the step-2 smoke test sets `client_order_ref`). Gateway returns `RECONCILIATION_REQUIRED` → `AMBIGUOUS_OUTCOME` and stops (`errors.py:261`).
- **Impact:** After a timeout following a possible commit, the key is stuck at `unknown` forever; the documented "retry becomes a no-op" guarantee does not exist. Fail-safe (never re-executes), but the design claim is unimplemented.

### F-11 · MEDIUM · Documented business guardrails unimplemented
- **Evidence:** TOOL_CONTRACTS § sales.order.create mandates gateway pre-checks (customer/product existence, `MAX_LINE_QUANTITY`, `MAX_ORDER_AMOUNT`); grep shows `MAX_ORDER_AMOUNT` only in `.env.example`; `ENTITY_NOT_FOUND`/`BUSINESS_RULE_VIOLATED`/`MAX_TOOL_CALLS_EXCEEDED` are defined but never raised (`errors.py:36,45,46`; self-acknowledged in `STEP15_EVALUATION_REPORT.md:141-156`). Product existence is indirectly checked at execution (price read, `gateway.py:343-358`); customer existence never is.
- **Impact:** A nonexistent `customer_id` reaches Odoo and fails there (defense-in-depth holds — live 403/AccessError evidence exists), but the canonical error will not be `ENTITY_NOT_FOUND`, and the amount/quantity caps do not exist at all.

### F-12 · MEDIUM · Interactive CLI broken after the first confirmed write
- **Evidence:** `ToolGateway.bind_confirmed_execution` (`gateway.py:192-209`) raises on any second bind; `confirm_and_execute` binds (`gateway.py:458-463`); `main --interactive` reuses one gateway for the session (`bootstrap.build_runtime` → `main._run_interactive`).
- **Impact:** Second confirmed write in one interactive session → `ValueError` → caught in `agent_runtime.confirm` → misleading `erp_error` ("مشكلة في الاتصال") **after** the proposal was already consumed (`state=confirmed`, never executed). State corruption from normal interactive use. One-shot CLI mode is unaffected.

### F-13 · MEDIUM · Proposal lifecycle stops at `confirmed`
- **Evidence:** Design state machine `proposed→confirmed→executing→completed/failed` (SECURITY_MODEL §5.1); `approve()` transitions to `confirmed` (`confirmation.py:320-330`); nothing ever sets `executed_at` or transitions further (grep: no `UPDATE proposals` outside approve/decline).
- **Impact:** Replay protection still holds (`state != proposed`), but `executed_at` is always NULL and decline reuses the `failed` state — the design's lifecycle is partially implemented and post-execution proposal state is unrecoverable from the DB.

### F-14 · MEDIUM · `confirmation_required` audit rows omit `proposal_id`/`operation_hash`
- **Evidence:** `gateway._audit` (`gateway.py:811-849`) never writes `proposal_id`/`operation_hash`, including for the row that records the proposal being created; only the denial path (`gateway.py:497-519`) populates them.
- **Impact:** The audit cannot answer "what was proposed, and under which hash" for the normal proposal path — a direct hit on the forensics question the audit mandate asks.

### F-15 · MEDIUM · The 50-case Arabic evaluation does not exist
- **Evidence:** No `tests/test_cases.json`, no `run_eval.py` anywhere; TEST_PLAN defines them as the primary instrument; `STEP15_EVALUATION_REPORT.md` documents unit/scenario testing only.
- **Impact:** Tool-selection accuracy (≥90%), Arabic accuracy, entity-resolution behaviors A–E, injection resistance, pass^3 — all **unevaluable**. The NL→tool-selection link of the hypothesis is unproven; `FakeLLMClient` tests prove plumbing, not model behavior. (Live-model evaluation is optional per mandate §32 — but its absence must be stated, and it is.)

### F-16 · MEDIUM · `requirements.txt` missing
- **Evidence:** TECHNICAL_DESIGN §12 step 6 references it; the file does not exist; dependencies (httpx, jsonschema, python-dotenv, anthropic, pyyaml, pytest per ADR-02) are declared nowhere installable.
- **Impact:** Combined with F-01/F-02, a fresh clone cannot reproduce the environment.

### F-17 · LOW · Error-translation fallback is semantically inconsistent
- **Evidence:** `errors.py:337-347` — unknown internal codes map to `code=ERP_CONNECTION_ERROR` with `retryable=False`, contradicting the taxonomy entry (`retryable=True`); approval `not_found` reaches the same fallback (`confirmation.py:288` + `gateway._approval_failure`).
- **Impact:** "Proposal not found" surfaces as an ERP connection error. Misleading, never unsafe.

### F-18 · LOW · No latency measurement anywhere
- **Evidence:** `gateway._audit` writes `start_time == end_time` always; no boundary timestamps exist despite TECHNICAL_DESIGN §9-10 defining five measurement points; TEST_PLAN §6 latencies are unmeasurable.
- **Impact:** Latency criteria (16-17) unevaluable — consistent with F-15.

### F-19 · LOW · Policy schema drift: `users.yaml` vs TOOL_CONTRACTS.md
- **Evidence:** TOOL_CONTRACTS.md:375-404 defines `odoo_user`/`odoo_api_key_env` per user; `users.yaml` uses `tenant_id`/`denied_tools` and omits the odoo fields; `authz._validate_policy` (`authz.py:87-97`) **rejects** the doc's schema outright.
- **Impact:** The doc cannot be used to reconstruct the policy; the doc's example is dead config. `denied_tools` precedence over `allowed_tools` is undocumented (`authz.py:195-198`). (Sweep-confirmed.)

### F-20 · LOW · Import-boundary tests are shallow
- **Evidence:** `test_scenario_security.py:133-155` scans only direct imports of 5 modules via substring/AST; `poc/main.py` passes while `poc/bootstrap.py` (its import) is the module that imports `odoo_client`; TECHNICAL_DESIGN §2 claims "gateway.py is the ONLY module that imports odoo_client.py" — bootstrap violates the letter of it (composition root; benign in fact, but both the doc and the test overstate the guarantee).

### F-21 · LOW · Repository hygiene
- **Evidence:** Scratch files at root: `.tmp.patch`, `.tmp_py.txt`, `02-poc/.patch.tmp`, `patch.txt`; a stray root-level `TECHNICAL_DESIGN.md` duplicating `02-poc/` (stale-duplicate risk); `03-poc-src/data/tmp*.db` (gitignored but present); `tools/step2_results.json` embeds full Odoo server tracebacks (paths, no secrets) — acceptable for a local POC, flag before any external sharing.

### F-22 · OBSERVATION · Confirmed clean (negative findings with evidence)
- **Authority model:** tool name/version resolved from registry (`agent_runtime.py:144-148`, re-checked `gateway.py:595-610`); arguments schema-validated with `additionalProperties:false` at both runtime and gateway; identity/tenant injected by bootstrap from env (`bootstrap.py:51-54`), never from model output; runtime always passes `idempotency_key=None` (`agent_runtime.py:191`), gateway rejects mismatched caller keys (`gateway.py:685-691`); multiple tool calls rejected (`agent_runtime.py:175-176`); verification expectations derived server-side (`gateway.py:343-389`, `verification.py`).
- **Authorization fail-closed:** all seven scenarios correct (`authz.py:156-219`; tests `test_authz.py:69-238`); TOCTOU mitigated by policy re-evaluation inside `approve()` (`confirmation.py:303-307`). (Sweep-confirmed.)
- **Confirmation integrity:** 9-point checklist enforced; atomic single-winner `UPDATE ... WHERE state='proposed'` (`confirmation.py:320-330`); hash re-computation from stored arguments detects tampering (test at `test_confirmation.py:175-186`).
- **Idempotency semantics:** test-and-set insert under `BEGIN IMMEDIATE`; 16-thread concurrency test shows exactly one owner (`test_idempotency.py:403-438`); cross-tenant/user isolation proven (`test_idempotency.py:149-177`).
- **Audit chain:** hash input excludes exactly `audit_id`/`previous_hash`/`own_hash`; genesis = 64 zeros; tamper tests cover modify/delete/reorder (`test_audit_store.py:99-163`); append-only discipline holds (no UPDATE/DELETE paths in production code; grep-verified).
- **Live Odoo (adapter layer only, pre-gateway):** `step2_results.json` — real `search_read` 200s, real create (order S00026) with `client_order_ref` provenance round-trip, real 403 `AccessError` denials for readonly and no-access users. Genuine live evidence — but for the adapter alone, not the gateway chain.
- **Secrets:** `.env` gitignored with placeholders in `.env.example`; no `sk-ant-` or key-shaped values in tracked files; Odoo `debug` tracebacks stripped by `odoo_client._strip_debug_fields` before any error object leaves the adapter.

## 5. Test-Suite Assessment

Strengths: scenario tests exercise real public boundaries (`AgentRuntime → ToolGateway → ConfirmationStore/IdempotencyStore → FakeOdoo`); concurrency uses barriers (16-thread idempotency owner test; 3-thread confirmation race); negative/security tests assert canonical error codes and wire shapes; import-scanner and secret-leak tests exist as first-class artifacts.

Weaknesses (mutation-thinking):
- The 3-thread confirmation race asserts `len(odoo.create_calls) <= 1` — weaker than the true invariant `== 1`.
- The fakes' `amount_total` exactly equals `list_price × qty`, structurally hiding F-06.
- Several tests read `Path("poc/gateway.py")` relative to cwd — pass only when pytest runs from `03-poc-src` (pytest.ini's directory); fragile under different invocations.
- No test covers decline-then-retry (would catch F-09), interactive second write (F-12), or audit timestamp shape (F-08) — each is a TEST GAP matching an implementation gap.
- `readonly_user`'s re-authorization revocation path is exercised only via identity mismatch (`test_confirmation.py:215-219`), not via an actual policy change.

## 6. Scorecard

| Area | Status | Evidence type | Gap severity | Finding |
|---|---|---|---|---|
| Architecture | PARTIAL | Source inspection | HIGH | F-03 (read path unexecuted) |
| Authority model | PASS | Source + tests + sweep | — | Clean (F-22) |
| Tool system | PASS | Source + tests | LOW | 5 tools, closed registry, no arbitrary RPC surface |
| Schema validation | PASS | Source + tests | — | `additionalProperties:false` everywhere; fail-closed |
| Authorization | PASS | Source + tests + sweep | HIGH (repo) | Fail-closed correct; F-01 policy untracked |
| Confirmation | PARTIAL | Source + tests | MEDIUM | Integrity solid; F-09 poisoning, F-13 lifecycle |
| Idempotency | PARTIAL | Source + tests | MEDIUM | No duplicates; F-09/F-10 liveness gaps |
| Execution | PARTIAL | Source | HIGH | Writes only; F-06/F-07 verification defects |
| Verification | PARTIAL | Source | HIGH | F-06 (false-fail vs Odoo), F-07 (lost order id) |
| Truthfulness | PARTIAL | Source | MEDIUM | F-04, F-05 presentation leaks |
| Error model | PARTIAL | Source + tests | LOW/MEDIUM | F-11, F-17; 3 codes defined-only |
| Audit | PARTIAL | Source + tests | MEDIUM | Chain solid; F-08, F-14 forensics gaps |
| Identity | PASS | Source + tests | — | Server-injected; no escalation channel |
| Tenant isolation | PARTIAL | Tests (store level) | — | Single-tenant by design; cross-tenant store isolation proven; cross-tenant E2E untestable per SECURITY_MODEL §14 |
| CLI | PARTIAL | Source + tests | MEDIUM | F-04/F-05/F-12 |
| Secrets | PASS | Source + gitignore | LOW | No leakage found; step2 tracebacks noted (F-21) |
| Odoo integration | PARTIAL | Live (adapter only) | MEDIUM | Adapter proven live pre-gateway; gateway+Odoo never proven together |
| Reliability | PARTIAL | Source | HIGH | F-09/F-10/F-12 liveness |
| Concurrency | PASS | Tests | LOW | Exactly-one-winner proven; weak assertion noted |
| Testing | PARTIAL | Source + cache | MEDIUM | Solid boundaries; F-15 harness absent; fake-realism gap |
| Documentation | PARTIAL | Source comparison | MEDIUM | F-19 drift; root duplicate; STEP15 claims not independently re-executable |
| Reproducibility | FAIL | git state | HIGH | F-01/F-02/F-16 — clone is dead on arrival |

## 7. Red-Team Results (15 guarantees)

| # | Guarantee | Result | Where |
|---|---|---|---|
| 1 | Unauthorized mutation | BLOCKED | `authz` fail-closed + Odoo 403 (live evidence) |
| 2 | Mutation without confirmation | BLOCKED | `requiresConfirmation` server-side; execution only via `confirm_and_execute` |
| 3 | Duplicate ERP mutation | BLOCKED (store/gateway level) | test-and-set; 16-thread proof; live level unproven |
| 4 | Wrong-tenant mutation | BLOCKED | tenant in policy + key derivation + store PK |
| 5 | Wrong-user attribution | BLOCKED | `approve()` identity check; audit `user_id` server-side |
| 6 | False verification | BLOCKED | verifier is deterministic; no model channel |
| 7 | False success | **PARTIAL** | F-04/F-05 presentation paths (no ERP effect) |
| 8 | Audit forgery | BLOCKED (app-level) | hash chain + append-only discipline; filesystem attacker out of scope per §9 limitation |
| 9 | Secret leakage | BLOCKED | env-only, gitignored, debug-stripped; no prompt/audit/log path |
| 10 | Unknown tool execution | BLOCKED | registry KeyError at runtime and gateway |
| 11 | Arbitrary ERP method execution | BLOCKED | no raw-RPC surface; `test_gateway_has_no_arbitrary_odoo_execution_surface` |
| 12 | Confirmation replay | BLOCKED | atomic state transition + tests |
| 13 | Confirmation tampering | BLOCKED | operation_hash re-computation + tamper test |
| 14 | Ambiguous outcome misreporting | BLOCKED | `unknown` state → `AMBIGUOUS_OUTCOME`, never re-executes (F-10 liveness caveat) |
| 15 | CLI bypass | BLOCKED | CLI has no identity/tenant/credential surface; main passes import scan |

## 8. POC Limitations (inherent, documented)

Single process = module isolation, not a security boundary (docs already classify this). Single tenant. SQLite store shared with data (a filesystem attacker defeats the chain — documented §9 limitation). Per-user API keys with manual rotation. No multi-turn, no destructive tools, max-1-tool-call (stricter than design's 3). Egyptian-dialect-only evaluation defined but not executed. No reconciliation automation. No latency measurement.

## 9. Change Policy Compliance

No production code was modified by this audit. No tests were added. The three deliverables (`FINAL_AUDIT_REPORT.md`, `TRACEABILITY_MATRIX.md`, `HARDENING_PLAN.md`) are the only files created. Rationale for not fixing inline: every HIGH fix touches security-relevant machinery (idempotency release semantics, verification comparison, gateway binding), and the audit environment could not execute the suite to prove no regression; per mandate §40/§47, fixes require reproduction + full-suite green, which is only possible on a machine where Python runs. All fixes are precisely specified in `HARDENING_PLAN.md`.

## 10. Final Verdicts

- **Hypothesis verdict: PARTIALLY PROVEN.** The requirements → architecture → implementation → tests links are strong and internally consistent. The chain breaks at runtime evidence: no live model evaluation (F-15), no read execution (F-03), no gateway+Odoo live run (adapter-only live evidence), and verification correctness on live Odoo is doubtful (F-06).
- **Production verdict: NOT PRODUCTION READY — POC ONLY.**
- **Single most important next step:** Make the tree reproducible and the loop closable: commit everything (F-01/F-02), add `requirements.txt` (F-16), run the deterministic suite, then run ONE live Odoo write through the gateway end-to-end (it will surface F-06 immediately), then fix F-06/F-07/F-09 and re-run. Only then build the 50-case eval (F-15) to close the hypothesis loop.

---

## 11. Reconciliation Addendum (2026-09-10 · post-hardening verification pass)

Every finding was re-verified against the current tree with executable
evidence (Python 3.12.10, live Odoo 19 via docker). Classification per the
reconciliation mandate: STALE = was true at audit time, already fixed;
FIXED = reproduced, then fixed in this pass; POC LIMITATION = real but
out of POC scope by design; ACCEPTED RISK = real, safe direction, documented.

| ID | Original claim | Status | Evidence / regression test |
|---|---|---|---|
| F-01 | users.yaml untracked | **STALE (fixed)** | tracked (`git ls-files`); 30 authz tests green |
| F-02 | implementation uncommitted | **STALE (fixed)** | commits 8291ada..HEAD; tree clean after this pass |
| F-03 | reads never execute | **STALE (fixed)** | `_execute_read` executes via registry odoo metadata; LIVE: `customer.search("Acme")` → Acme Corporation, audited, chain valid |
| F-04 | text_only → success=true | **FIXED** | `success=None` for text_only (main.py); suite green |
| F-05 | read accepted, no data, success=true | **FIXED** | success requires non-null result payload |
| F-06 | exact total check contradicts Odoo | **FIXED (ADR-21a)** | verifier: presence+non-negative only; strict identity/lines/qty/provenance; LIVE taxed product verified (order S00048); `missing_amount`+negative-total tests |
| F-07 | created order id discarded on failure | **FIXED (ADR-21b)** | `external_record_id` threaded into failure path + idempotency record; LIVE: order 48 recorded |
| F-08 | timestamps corrupted | **STALE (fixed earlier pass)** | `_utc_now()` in verification audit rows |
| F-09 | decline/expiry poison idempotency key | **FIXED** | release on decline AND on expired approval; decline→re-propose test |
| F-10 | reconciliation unimplemented | **POC LIMITATION (ADR-21c)** | ambiguous keys stay unknown; provenance key preserved; store-level adopt path exists |
| F-11 | guardrails documented-only | **PARTIAL → documented (ADR-21c)** | product-existence pre-check now raises ENTITY_NOT_FOUND (was unreachable); caps deferred to Odoo backstop |
| F-12 | interactive breaks after 2nd write | **STALE (fixed earlier pass)** | per-call identity; two-confirmed-writes regression test |
| F-13 | lifecycle stops at confirmed | **POC LIMITATION (ADR-21c)** | replay protection independent of lifecycle state |
| F-14 | proposal audit rows missing proposal_id/hash | **FIXED** | `_audit` writes them from `result.proposal` |
| F-15 | 50-case eval missing | **FIXED (deterministic) / POC LIMITATION (live model)** | harness + 50 cases + verify_audit; model metrics N/A in deterministic (ADR-21d); live requires ANTHROPIC_API_KEY |
| F-16 | requirements.txt missing | **STALE (fixed)** | exists, pinned |
| F-17 | fallback semantic inconsistency | **ACCEPTED RISK (ADR-21e)** | conservative, never unsafe |
| F-18 | no latency measurement | **PARTIAL (ADR-21c)** | eval harness measures P50/P95 + llm/confirm split; audit rows single-point |
| F-19 | policy doc schema drift | **FIXED (doc)** | TOOL_CONTRACTS §Authorization Policy rewritten to implemented schema; deny-precedence documented |
| F-20 | import-boundary overstatement | **FIXED (doc)** | TECHNICAL_DESIGN §2 names bootstrap as deliberate composition-root exception |
| F-21 | repo hygiene | **STALE (fixed)** | scratch files removed; tmp DBs gitignored |

Additional defects found by the adversarial code review and fixed in this
pass (each with a remove-the-fix-fails regression test): pre-create retryable
failure poisoned the key as ambiguous (now released); Odoo 422 rejection
poisoned the key (now released); create-timeout ambiguity guarded against
over-release (stays unknown); read path ignored non-allow policy decisions
(now fail-closed `POLICY_DENIED`); audit hash payload mis-normalized bools;
deterministic eval metrics were circular (now N/A); duplicate-orders metric
was hardcoded 0 (now computed from provenance refs); p95 returned 0.0 for
empty sets (now null); verify_audit secret scan strengthened.

**Post-hardening evidence:** 317 passed / 5 skipped (deterministic, run twice
identically) · 5/5 live-Odoo integration · live gateway read + confirmed
write (order S00048, verified, chain valid) · eval 90/90 executions, control
plane all green, model metrics N/A.

**Amended verdicts:** Hypothesis: **PARTIALLY PROVEN** (control plane proven;
live Arabic model evaluation remains the open link). Production: **NOT
PRODUCTION READY — POC ONLY.**
