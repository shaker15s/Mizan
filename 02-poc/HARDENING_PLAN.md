# HARDENING PLAN — Agent-Native ERP POC

**Date:** 10 September 2026 · Companion to `FINAL_AUDIT_REPORT.md` (F-xx = finding IDs there).
No speculative features. Each item: issue → why it matters → fix → complexity → architecture impact.

Priority buckets: **NOW** (do before anything else) · **BEFORE DEMO** (before showing this to anyone) · **BEFORE EXTERNAL REVIEW** (before another team audits it) · **PRODUCTION ONLY** (explicitly out of POC scope; listed so nobody "fixes" them prematurely).

---

## NOW

### N-1 · Commit the repository (F-01, F-02)
- **Issue:** The entire implementation — all `poc/*.py`, `tests/*.py`, `pytest.ini`, and critically `users.yaml` (the authorization policy) — is untracked; docs are modified. One baseline commit exists.
- **Why:** A fresh clone cannot construct `PolicyEngine` (crashes at import in 30 authz tests) or the gateway. All security evidence is unreproducible; the work is one accident away from deletion.
- **Fix:** `git add` the implementation + `users.yaml` + `requirements.txt` (N-2) as logical commits. Remove scratch files (`.tmp.patch`, `.tmp_py.txt`, `patch.txt`, `02-poc/.patch.tmp`, root `TECHNICAL_DESIGN.md` duplicate, `data/tmp*.db`) or gitignore them.
- **Complexity:** Trivial (≤30 min). **Architecture impact:** none.

### N-2 · Add `requirements.txt` (F-16)
- **Issue:** Dependencies (httpx, jsonschema, python-dotenv, anthropic, pyyaml, pytest — ADR-02) declared nowhere installable; TECHNICAL_DESIGN §12 references a file that doesn't exist.
- **Why:** Reproducibility; the audit environment itself could not run the suite partly for this class of reason.
- **Fix:** Create `03-poc-src/requirements.txt` pinned to the currently working versions (`pip freeze` on the machine where the suite last ran green).
- **Complexity:** Trivial. **Architecture impact:** none.

### N-3 · Release idempotency reservations on decline/expiry (F-09)
- **Issue:** Reserve happens before the proposal is decided; `decline()` and `CONFIRMATION_EXPIRED` never release the `pending` row; the key is poisoned forever — the identical order can never be reissued.
- **Why:** This breaks the documented decline→retry journey in normal use. It is the worst liveness defect in the POC.
- **Fix (minimal):** Add `IdempotencyStore.release_pending(key, request_fingerprint, tool_name, tool_version, tenant_id, user_id, execution_id)` that `DELETE`s the row only when `state='pending'` AND the full binding matches (same guard style as `complete()`). Call it from `record_confirmation_denial` and from the `EXPIRED` branch of approval handling in the gateway. Regression tests: decline→re-request→`confirmation_required` again; expired→re-request→same.
- **Complexity:** Small (~40 lines + 2 tests). **Architecture impact:** none — release is only legal from `pending` with zero ERP side effects.

## BEFORE DEMO

### D-1 · Fix verification's amount comparison (F-06)
- **Issue:** `execute_verified` computes `expected_amount_total = Σ list_price × qty` and `verification.py` demands exact Decimal equality with Odoo's tax/pricelist-inclusive `amount_total` — a correct order fails on live Odoo for any taxed product. Contradicts TECHNICAL_DESIGN §6 ("present and non-negative").
- **Why:** The demo's happy path will visibly fail with `VERIFICATION_FAILED` on correct orders.
- **Fix (POC-minimal, no accounting engine):** Follow TECHNICAL_DESIGN §6 exactly — verify existence, `state='draft'`, partner, per-line product/quantity, provenance, and `amount_total` presence + non-negativity; drop the exact-total equality (keep the computation only as an optional logged sanity delta). Add one **live-Odoo** test creating an order for a taxed demo product.
- **Complexity:** Small. **Architecture impact:** none (verification stays deterministic; the doc already specifies the weaker check).

### D-2 · Preserve the created order ID on verification failure (F-07)
- **Issue:** On `VERIFICATION_FAILED` the created `sale.order` id is discarded — neither the idempotency record nor the audit row records it.
- **Why:** The most dangerous failure mode (order exists, unverified) currently leaves no artifact pointing at the order.
- **Fix:** Pass `created_ids[0]` into `_verification_failure` → `external_record_id`; it flows into `complete()` and the audit row automatically. One regression test asserting `external_record_id` is set on the mismatch path.
- **Complexity:** Trivial. **Architecture impact:** none.

### D-3 · Fix audit timestamp corruption on execution rows (F-08)
- **Issue:** `_verification_audit_record` writes `verification["status"]`/`["reason"]` into `start_time`/`end_time`.
- **Why:** Violates SECURITY_MODEL §8 clock discipline; verification timing forensics are unrecoverable; trivially visible to any reviewer who opens the DB.
- **Fix:** `_utc_now()` for both; the status/reason already live in `result_status`/`error_code`. Add an assertion to the verification tests that the columns parse as ISO-8601.
- **Complexity:** Trivial. **Architecture impact:** none.

### D-4 · Stop reporting non-executed work as success (F-04, F-05)
- **Issue:** `text_only` model prose → `success=true`; read `accepted` (nothing executed, `result=None`) → `success=true`.
- **Why:** Undercuts the "server owns final truth" claim in front of exactly the audience a demo targets.
- **Fix:** In `main._build_payload`: `text_only` → `success=None`; require `gateway_result.result is not None` (or a distinct `not_executed` status) before read-`accepted` maps to success. Update the two affected CLI tests.
- **Complexity:** Trivial. **Architecture impact:** none.

### D-5 · Fix interactive-mode gateway rebinding (F-12)
- **Issue:** `bind_confirmed_execution` raises on the second confirmation within one gateway instance; `--interactive` reuses one instance; the second write fails misleadingly *after* its proposal was consumed.
- **Why:** The interactive demo path breaks on the second order.
- **Fix (minimal):** Make the binding per-call — thread `tenant_id`/`user_id` through `execute_verified(...)` parameters instead of instance state, or reset the binding after `execute_verified` completes. Keep the ownership check (reservation `execution_id` must match). Regression test: two sequential confirmed writes on one gateway both succeed.
- **Complexity:** Small. **Architecture impact:** none (removes an accidental one-shot restriction; the real guard is the reservation ownership check, which stays).

## BEFORE EXTERNAL REVIEW

### X-1 · Record `proposal_id`/`operation_hash` in proposal-path audit rows (F-14)
`gateway._audit` should include them when `context.requires_confirmation` / a proposal exists. Trivial; closes the "what was proposed, under which hash" forensic hole.

### X-2 · Complete the proposal lifecycle or shrink the doc (F-13)
Either set `executed_at` and transition `confirmed→completed/failed` in `execute_verified`/failure paths, or amend SECURITY_MODEL §5.1 to the actually-implemented lifecycle (`proposed→confirmed` terminal, `proposed→failed` on decline). Do not leave doc and code telling different stories. Small either way.

### X-3 · Implement or explicitly defer reconciliation (F-10)
Minimum honest state: keep the `RECONCILIATION_REQUIRED` behavior but document in DECISIONS that automated reconciliation (§7 step 6) is deferred and ambiguous keys require manual adoption. Better: implement the ~30-line `search_read("sale.order", [["client_order_ref","=",key]])` adopt path in the gateway on the `unknown` branch. Small.

### X-4 · Reconcile `users.yaml` with TOOL_CONTRACTS.md (F-19)
Update the doc's Authorization Policy section to the implemented schema (`tenant_id`, `denied_tools` with documented precedence) — the code's schema is the better design (tenant binding belongs server-side; odoo key mapping lives in bootstrap). Doc-only.

### X-5 · Build and run the 50-case evaluation (F-15)
The single biggest credibility gap. `test_cases.json` per TEST_PLAN §2 + a `run_eval.py` harness through `AgentRuntime` (auto-confirm via `gateway.confirm_and_execute`, never bypassing), recording tool-selection/argument accuracy, entity-resolution A–E, the injection case, and pass^3 on read families. Deterministic parts (schema/authz/idempotency expectations) can run with `FakeLLMClient`; the model track runs separately with the pinned model id and is reported separately (mandate §32). Medium complexity; no architecture change.

### X-6 · Add real latency instrumentation (F-18)
Timestamp the boundaries TECHNICAL_DESIGN §10 names (llm/tool/odoo/verify) and put them in the audit rows + eval report. Small.

### X-7 · Tighten shallow tests (test-of-tests, mandate §29)
`len(odoo.create_calls) <= 1` → `== 1`; make scanner-test source paths cwd-independent; add decline→retry, interactive-second-write, and audit-timestamp regression tests (these three double as the N-3/D-4/D-5 regression suite).

### X-8 · Guardrails decision (F-11)
Either implement the two cheap guards (quantity upper bound from an env var; customer/product existence pre-check returning `ENTITY_NOT_FOUND`) or amend TOOL_CONTRACTS to record them as deferred with Odoo as the documented backstop. Pick one story; do not leave the codes defined-but-unreachable silently. Small.

## PRODUCTION ONLY (do not build now)

| Item | Why it is production-only |
|---|---|
| Process/container separation of agent runtime and gateway; secrets out of agent env (TECHNICAL_DESIGN §2 production requirement) | Already classified in the docs; module isolation is a legitimate POC posture |
| Database-enforced append-only audit role / WORM or separate audit store | ADR-03 explicitly reclassified as production requirement |
| Secret manager + ≤3-month API-key rotation workflow (SECURITY_MODEL §3) | POC uses 3 per-user keys in `.env`, by design |
| Reconciliation/TTL for crash-orphaned `pending` rows (general case beyond N-3) | Needs an operational story, not POC code |
| Multi-tenant isolation tests, record rules, ≥2 tenants (SECURITY_MODEL §14) | Single-tenant locked scope |
| Policy hot-reload / file-integrity monitoring for `users.yaml` | Static load is correct for POC |
| Tax/pricelist-aware expected-total model (beyond D-1) | Requires an accounting design, not a patch |
| Multi-turn agent loop with tool-call budget (design's max-3) | Single-shot is the stricter, deliberate POC simplification |

## Execution Order

1. N-1 + N-2 (commit + requirements) — unblocks everything, including running the suite at all.
2. Run the deterministic suite fresh; record the baseline result (the audit could not).
3. N-3, D-1…D-5 with their regression tests; re-run suite; re-run the live Odoo write-through.
4. X-1…X-8 before any external eyes.
5. Only then: the 50-case eval (X-5) to close the hypothesis loop.

---

## Completion Status (2026-09-10 reconciliation pass)

| Item | Status |
|---|---|
| N-1 commit repo (F-01/F-02) | DONE (8291ada + 5 follow-up commits) |
| N-2 requirements.txt (F-16) | DONE (741da72) |
| N-3 release on decline/expiry (F-09) | DONE + regression tests |
| D-1 verification amount (F-06) | DONE per ADR-21a + live taxed-product evidence |
| D-2 preserve created id (F-07) | DONE (ADR-21b) + live evidence (order S00048) |
| D-3 audit timestamps (F-08) | DONE (earlier pass) |
| D-4 truthfulness (F-04/F-05) | DONE (earlier pass) |
| D-5 interactive rebinding (F-12) | DONE (earlier pass) |
| X-1 proposal audit linkage (F-14) | DONE |
| X-2 proposal lifecycle (F-13) | DOC PATH: classified POC LIMITATION (ADR-21c) |
| X-3 reconciliation (F-10) | DOC PATH: POC LIMITATION (ADR-21c) |
| X-4 users.yaml doc drift (F-19) | DONE (TOOL_CONTRACTS rewritten) |
| X-5 50-case eval (F-15) | DONE deterministic (90/90; model metrics N/A per ADR-21d); live-model run pending ANTHROPIC_API_KEY |
| X-6 latency instrumentation (F-18) | PARTIAL: harness-level P50/P95 done; audit-row boundaries deferred (ADR-21c) |
| X-7 tighten tests | DONE: ==1 concurrency assertions, cwd-proof paths, decline-retry/interactive/timestamp regressions |
| X-8 guardrails (F-11) | PARTIAL: ENTITY_NOT_FOUND pre-create check implemented; caps deferred (ADR-21c) |
