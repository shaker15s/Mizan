# POST-AUDIT HARDENING REPORT — Agent-Native ERP POC

**Date:** 10 September 2026 · Companion to `FINAL_AUDIT_REPORT.md` (F-xx = its finding IDs).
**Environment:** Python 3.14.2 · pytest · live Odoo 19 (local Docker, database `poc_test`) — **LIVE ODOO VALIDATION WAS AVAILABLE AND USED.**

> **Coordination disclosure:** during this hardening pass a second working session was committing fixes to the same repository (commits `8291ada`→`393a7ff`: implementation checkpoint, phase 0 hygiene, phase 1 correctness, phase 2 read execution, phase 3 eval harness). This pass re-derived the current state, re-reproduced every finding against the post-commit code, and applied only what remained open. Working-tree changes from this pass are **uncommitted** (`poc/gateway.py`, `poc/main.py`, `tests/test_post_audit_hardening.py`); the user should commit them once, to avoid a two-writer race.

---

## 1. Findings Reproduced

| ID | Original severity | Reproduced (pre-fix, against committed code) | Evidence | Root cause |
|---|---|---|---|---|
| F-03 | HIGH | **NO — already fixed** by the parallel session (commit ae57cc8) | `poc/gateway.py` `_execute_read` + `_shape_read_result`; runtime passes a client for read-only tools | Reads had no executor |
| F-04 | MEDIUM | **YES** | `text_only` payload → `success=true` | `_NON_ERROR_OUTCOMES` membership granted success without any server evidence |
| F-05 | MEDIUM | **YES** | accepted read, `result=None` → `success=true` | `_SUCCESS_STATUSES` membership alone set success |
| F-06 | HIGH | **PARTIALLY** — the exact-total bug was already fixed (B5: verifier compares `amount_untaxed`); a first-fix attempt that dropped the amount check entirely was **reverted** as weaker than B5 | taxed-order scenario, harness + `test_phase1_regressions.py::test_amount_verification_accepts_taxed_total` | Original code compared Σ list_price×qty to tax-inclusive `amount_total` |
| F-07 | HIGH | **YES** | verification failure → `external_record_id=None` in idempotency + audit | `_verification_failure` never received the created id |
| F-09 | HIGH | **PARTIALLY** — decline release already fixed (B3); **expiry path still poisoned the key** | expired proposal → confirm → `denied`; identical retry → `in_progress` forever | `approve()` EXPIRED path never released the reservation |
| F-12 | MEDIUM | **NO — already fixed** (per-call binding, commit phase 1) | two sequential confirms on one gateway both execute | `bind_confirmed_execution` one-shot raise (removed) |
| F-14 | MEDIUM | **YES** | `confirmation_required` audit row had `proposal_id=NULL` | `_audit` never wrote the proposal linkage |

## 2. P0 Fixes Applied (this pass)

| Fix | Files | Invariant restored | Regression test |
|---|---|---|---|
| **F-04/F-05 truthfulness**: `text_only` → `success=None`; accepted → success only when a server-executed `result` exists (else `false`) | `poc/main.py` `_build_payload` | Success flag is set only by server-side execution evidence, never by model prose or a non-executed decision | `test_post_audit_hardening.py::test_text_only_model_response_is_not_success`, `::test_accepted_read_without_result_is_not_success` |
| **F-07 provenance**: `_verification_failure` accepts and persists the created order id; the audit's verify-row now carries `external_record_id=<sale.order id>` | `poc/gateway.py` | A mutation that happened is never anonymous — even when verification fails | `::test_verification_failure_retains_created_order_id` |
| **F-09/expiry release**: on `CONFIRMATION_EXPIRED` the pending reservation is released (same guard pattern as decline) so the identical request can re-propose | `poc/gateway.py` `confirm_and_execute` | Declined/expired confirmations cannot permanently deadlock a logical operation | `::test_expired_confirmation_releases_reservation_for_retry` |
| **F-14 proposal linkage**: proposal-path audit rows record `proposal_id` + `operation_hash` | `poc/gateway.py` `_audit` | Audit answers "what was proposed, under which hash" | `::test_proposal_audit_row_links_proposal_and_hash` |
| **F-06 guard**: kept the B5 untaxed-reference verification (restored `expected_amount_total` after reverting a weaker drop-the-check attempt); gateway comment documents the semantics | `poc/gateway.py` | Money verification stays deterministic and tax-safe | `::test_taxed_correct_order_passes_verification` (+ existing B5 test) |

Already-fixed-by-parallel-session (verified, not reimplemented): **F-03** read execution in the gateway (`ae57cc8`), **F-08** audit timestamps, **F-12** per-call binding, **F-09/decline** release (`aaf86cc`).

## 3. P1 Findings

- **F-16** requirements.txt — FIXED (parallel session, commit 741da72; pinned ranges verified present).
- **F-15** 50-case eval — PARTIALLY FIXED: harness + `test_cases.json` now exist (phase 3 commit `6944a8b`) with deterministic FakeLLM mode; **the live-model track has not been run** (correctly deferred per mandate §17).
- **F-10** reconciliation automation, **F-11** guardrails/`ENTITY_NOT_FOUND` gateway pre-checks, **F-13** proposal lifecycle completion, **F-18** latency instrumentation, **F-19** TOOL_CONTRACTS policy-section drift, **F-21** baseline-committed junk files — **NOT FIXED** (not hypothesis-critical; unchanged classification from the audit).

## 4. Read Path — LIVE PROVEN

`Arabic input → AgentRuntime → registry validation → ToolGateway (schema→authz) → OdooJSON2Client → real Odoo 19 → shaped result`, live run: `customer.search` → Acme Corporation (count=1); `product.search "Desk"` → 15 products (limit-capped); `customer.get` → full record. All returned `accepted` with real ERP data; audit rows appended; hash chain valid.

## 5. Write Path — LIVE PROVEN

`sales.order.create` → gateway reserves idempotency key → `confirmation_required` (server proposal) → `confirm_and_execute` (9-point re-check) → real Odoo create → **live read-back verification passed** → `accepted` with `verified=true`. Order **S00067 / id 46**, total 862.5 (taxed — the untaxed-reference verification held). Probe order left as **draft** with `client_order_ref=45473b2c096d45e2c8ffa13a568c446a`.

## 6. Truthfulness

| Previously possible false-success path | Now |
|---|---|
| Model says "تم التنفيذ بنجاح" with no tool call → `success=true` | `success=None`, `result=None` (F-04) |
| Accepted read with nothing executed → `success=true` | `success=false` (F-05) |
| Replay of a stored error → `success=true` | `success=false` (parallel-session B4 fix, retained) |
| LLM `verified=true` / authority fields in arguments | Unchanged — rejected by `additionalProperties:false` at runtime and gateway (re-tested green) |

Mutation success still requires live read-back verification (`verified=true` set only by the gateway verifier).

## 7. Verification Semantics — LIVE ODOO EVIDENCE

Verifier contract: provenance (`client_order_ref` == idempotency key), partner, `state='draft'`, `amount_total` present + non-negative, **amount reference = `amount_untaxed`** vs Σ list_price×qty (taxes/pricelists are Odoo-owned — B5), per-line product + exact Decimal quantity, line-count equality. Live proof: real create → real read-back → `verified=true` on a taxed order (§5). Unit tests still prove the verifier rejects wrong customer/state/amount/provenance/product/quantity.

## 8. Idempotency — LIVE + TEST PROVEN

- Live: identical completed request → `replay` of order 46; exactly one Odoo order (provenance search = 1 match).
- Decline → identical re-request → **new valid proposal** (`test_..._allows_identical_retry`).
- Expiry → identical re-request → **new valid proposal** (`test_..._releases_reservation_for_retry`).
- Release is legal only from `state='pending'` with matching execution_id — completed/unknown rows are untouchable; conflict/replay semantics untouched (16-thread owner test still green).

## 9. Provenance / Reconciliation

Every create carries `client_order_ref = idempotency_key` (live: exactly 1 Odoo match). On verification failure the created order id is now persisted in the audit verify-row (F-07) and the key reconciles as `unknown`→`AMBIGUOUS_OUTCOME` (never auto-re-executes). Automated reconciliation search remains deferred (F-10) — but a failed mutation is now fully identifiable: execution_id + idempotency_key + external_record_id + operation_hash.

## 10. Security (re-run)

All adversarial suites green in the 314: LLM cannot choose tools/versions/identity/tenant/idempotency keys; unknown-tool and arbitrary-RPC surfaces absent; tenant override denied; confirmation replay/tamper blocked; concurrent confirms exactly-one-winner; secret-leak scans pass; readonly denial live (`POLICY_DENIED` before Odoo, plus Odoo's own 403 from the adapter suite). CLI import-boundary and no-secret-output tests pass.

## 11. Tests

| Suite | Result |
|---|---|
| Focused regression (`test_post_audit_hardening.py`, 8 tests) | **8 passed** |
| Full deterministic suite, fresh process, **run 3×** | **314 passed, 5 skipped** each run |
| Live Odoo adapter suite (`test_odoo_client_integration.py`) | **5 passed** |
| Live end-to-end gateway probe (one-shot script, deleted after) | **10/10 passed** |
| Skipped | 5 (live-marker tests superseded by the dedicated live run) |
| Failed | **0** |
| Eval harness (`poc/tests/run_eval.py`) | Not run this pass (mandate §17) |

## 12. Remaining Findings (updated classification)

FIXED: F-01, F-02, F-03, F-04, F-05, F-06 (B5 semantics), F-07, F-08, F-09, F-12, F-14, F-16.
PARTIALLY FIXED: F-15 (harness exists; live-model track pending by design).
NOT FIXED (accepted for POC / deferred): F-10 (reconciliation automation), F-11 (gateway business pre-checks; Odoo backstop live-proven), F-13 (proposal lifecycle tail), F-17 (fallback code semantics), F-18 (latency instrumentation), F-19 (TOOL_CONTRACTS policy-doc drift), F-20 (shallow import scans), F-21 (baseline junk files).
FALSE POSITIVE: none — every audit finding was re-derived against code before action.

## 13. Updated Evidence Classification

- Dual-path (read+write) NL→tool→gateway→ERP→result: **PROVEN LIVE ODOO** (was NOT PROVEN at audit time).
- No unauthorized/unconfirmed/duplicate mutations: **PROVEN LIVE ODOO + PROVEN BY TEST**.
- Verification correctness incl. taxes: **PROVEN LIVE ODOO** (taxed order verified).
- Truthfulness of the success flag: **PROVEN BY TEST** (F-04/F-05 regressions).
- Provenance of failed mutations: **PROVEN BY TEST**; end-to-end reconciliation search: NOT PROVEN (deferred).
- Arabic NL→tool accuracy with a real model: **NOT PROVEN** (eval harness ready; live-model track deferred per mandate §17).
- Repository reproducibility: **PROVEN BY SOURCE** (7 commits, requirements.txt pinned, users.yaml committed).

## 14. Updated Hypothesis Verdict

**PARTIALLY PROVEN** — upgraded materially: the *architecture* hypothesis (untrusted LLM → server-authoritative gateway → safe, verified, audited ERP mutation — and now real ERP reads) is **proven live end-to-end**. The single unproven link is model-behavioral: Arabic tool-selection accuracy on the live model, which the mandate explicitly defers (§17) until after structural soundness — which this pass established.

## 15. Production Verdict

**NOT PRODUCTION READY — POC ONLY.**
