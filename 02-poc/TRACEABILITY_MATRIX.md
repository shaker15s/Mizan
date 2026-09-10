# TRACEABILITY MATRIX — Agent-Native ERP POC

**Date:** 10 September 2026 · Companion to `FINAL_AUDIT_REPORT.md` (finding IDs F-xx refer to it).

Legend — Status: **PROVEN** (implementation + test + evidence align) · **PARTIAL** (implemented, evidence incomplete or contradictory) · **UNPROVEN** (designed, not demonstrated) · **GAP** (designed, not implemented).
Evidence keys: **SI** = source inspection · **T** = test (deterministic) · **LO** = live Odoo (`tools/step2_results.json`, adapter-only) · **—** = no evidence.

## 1. Core Hypothesis Chain

| # | Requirement (source) | Implementation | Test | Evidence | Status |
|---|---|---|---|---|---|
| H1 | Arabic NL → agent selects exactly one registry tool (PRD §14, TEST_PLAN §3) | `agent_runtime.process` → `_validate_tool_call` (`agent_runtime.py:140-157,159-206`) | `test_agent_runtime.py:84-121,224-227`, `run_eval.py` live run | SI, T, LIVE | **PROVEN** — 50-case live evaluation (90 executions) on Claude Haiku via FCC proxy: 86.67% tool selection, 100% schema compliance, 0 unauthorized writes (F-15 CLOSED) |
| H2 | Model output validated against server-owned schema before gateway | `jsonschema.validate` runtime (`agent_runtime.py:152-156`) + gateway re-validation (`gateway.py:612-620`) | `test_agent_runtime.py:105-121`, `test_gateway.py:108-131` | SI, T | **PROVEN** |
| H3 | Authorization deterministic, server-side, fail-closed (SECURITY_MODEL §4) | `authz.PolicyEngine.evaluate` (`authz.py:156-219`); YAML policy (`users.yaml`) | `test_authz.py` (30 tests incl. adversarial) | SI, T | **PROVEN** (F-01: policy file untracked) |
| H4 | Mutations require server-created confirmation (SECURITY_MODEL §5) | `_process_mutating` → `create_proposal` (`gateway.py:740-748,786-809`); execute only via `confirm_and_execute` (`gateway.py:447-469`) | `test_scenario_security.py:88-98`, `test_confirmation.py` | SI, T | **PROVEN** |
| H5 | Confirmation binds to exact operation; 9-point re-check at approval (§5.3, §6) | `compute_operation_hash` + `approve()` checklist (`confirmation.py:63-82,284-330`) | `test_confirmation.py:94-118,175-186,269-289` | SI, T | **PROVEN** (F-13: lifecycle stops at `confirmed`) |
| H6 | One logical mutation → at most one ERP mutation (ADR-08, §7) | Content key + `BEGIN IMMEDIATE` test-and-set (`idempotency.py:130-145,297-389`) | `test_idempotency.py:403-438` (16 threads), `test_scenario_golden_path.py:179-203` | SI, T | **PROVEN** at store/gateway level; live level **UNPROVEN** (F-09/F-10 liveness caveats) |
| H7 | Post-write read-back verification before success (ADR-07, §6) | `execute_verified` → `verify_sales_order_creation` (`gateway.py:324-445`, `verification.py:30-136`) | `test_verification.py:99-159,171-235` | SI, T | **PARTIAL** — logic proven vs fakes; F-06 contradicts Odoo semantics; never run against live Odoo through the gateway |
| H8 | 100% audit coverage incl. denials (TEST_PLAN #7) | `_audit` on every request + denial + execution records (`gateway.py:811-876`, `435-437`) | `test_gateway.py:276-282`, `test_scenario_errors.py:104-124` | SI, T | **PARTIAL** — coverage proven; forensics gaps F-08/F-14 |
| H9 | Audit tamper-evidence via hash chain (SECURITY_MODEL §9) | `audit_store.py:134-141,222-247` | `test_audit_store.py:66-163` | SI, T | **PROVEN** (POC-class: code-discipline append-only, per ADR-03) |
| H10 | Truthful result; uncertainty ≠ success (PRD §116) | `_build_payload` success rules (`main.py:17-39`) | `test_agent_runtime.py:230-256` | SI, T | **PARTIAL** — F-04 (`text_only`→success) and F-05 (read accepted→success) leak model prose / non-execution into the success flag |
| H11 | ERP reads return data to model/user (PRD §15 pipeline step 5) | **No executor exists for reads** (`gateway.py:639-647` returns `ready_for_execution`) | none asserts data | SI | **GAP** — F-03 |
| H12 | Per-user Odoo credentials; two-layer authz (SECURITY_MODEL §3-4) | `bootstrap._build_odoo_client` (`bootstrap.py:21-38`) + Odoo native ACL | `test_odoo_client_integration.py:83-100` | SI, LO (403 evidence) | **PROVEN** at adapter level |

## 2. Hostile-Input Vectors (mandate §7)

| Vector | Rejecting code | Test | Status |
|---|---|---|---|
| `{"tool":"arbitrary.execute"}` | `agent_runtime.py:146-147` → `gateway.py:595-602` | `test_scenario_errors.py:127-141` | PROVEN |
| Second tool call in one response | `agent_runtime.py:175-176` | `test_agent_runtime.py:117-121` | PROVEN |
| Extra authority fields in arguments (`verified/tenant_id/user_id/confirmed/skip_confirmation`) | `additionalProperties:false` in all 5 contracts (`tool_contracts.py`) → schema rejection | `test_scenario_security.py:60-71`, `test_gateway.py:116-131,252-266` | PROVEN |
| Caller-supplied idempotency key | runtime passes `None` (`agent_runtime.py:191`); mismatch rejected (`gateway.py:685-691`) | `test_gateway.py:412-432` | PROVEN |
| Tenant/user override via CLI input | identity from constructor/bootstrap only (`bootstrap.py:51-54`) | `test_main_cli.py:206-214` | PROVEN |
| Role/admin spoofing in request | `evaluate()` reads only 4 fields (`authz.py:161-173`) | `test_authz.py:200-253` | PROVEN |
| Argument tampering between proposal and execution | operation_hash re-computation (`confirmation.py:314-316`) | `test_confirmation.py:175-186` | PROVEN |
| Concurrent confirmations | atomic `UPDATE ... WHERE state='proposed'` (`confirmation.py:320-330`) | `test_confirmation.py:269-289`, `test_scenario_security.py:100-130` | PROVEN |
| Prompt injection via business data | data treated as text; write still gated by H3/H4/H5 | TC-050 live-model run on Claude Haiku: data treated as text search query; zero writes triggered | PROVEN |

## 3. Error Taxonomy (TECHNICAL_DESIGN §8)

| Code | Raised by | Test | Status |
|---|---|---|---|
| SCHEMA_INVALID | runtime+gateway validation | `test_error_integration.py:61-77,160-166` | PROVEN |
| TOOL_NOT_FOUND | runtime+gateway unknown tool | `test_error_integration.py:80-94,151-157` | PROVEN |
| TOOL_VERSION_MISMATCH | gateway `:604-610`; approve re-check `confirmation.py:301-302` | `test_gateway.py:100-105`, `test_confirmation.py:189-197` | PROVEN |
| PERMISSION_DENIED | policy deny; Odoo 403 translation | `test_error_integration.py:97-112,180-190`; LO 403 | PROVEN |
| ENTITY_NOT_FOUND | **never raised** | — | GAP (F-11) |
| ERP_CONNECTION_ERROR | adapter timeouts/connection; fallback | `test_verification.py:238-251`, `test_errors.py:185-227` | PROVEN |
| ERP_VALIDATION_ERROR | 422 → `OdooValidationError` | `test_errors.py:210-215`; Odoo-422 mapping **unverified live** | PARTIAL |
| VERIFICATION_FAILED | mismatch outcomes `gateway.py:389` | `test_scenario_errors.py:55-78` | PROVEN |
| CONFIRMATION_EXPIRED / REPLAY / HASH_MISMATCH | approve() paths | `test_confirmation.py:145-163,175-186`; `test_error_integration.py:118-145` | PROVEN |
| IDEMPOTENCY_CONFLICT | reserve conflict/in-progress | `test_idempotency.py:92-111`, `test_gateway.py:150-157` | PROVEN |
| AMBIGUOUS_OUTCOME | `unknown` state retry → `RECONCILIATION_REQUIRED` mapping | `test_verification.py:266-297` | PROVEN (F-10: no reconciliation automation) |
| BUSINESS_RULE_VIOLATED / MAX_TOOL_CALLS_EXCEEDED | **never raised** | — | GAP (F-11, F-15) |
| CONFIRMATION_DECLINED | denial path | `test_main_cli.py:145-160` | PROVEN |
| LLM_ERROR | `LLMProviderError` catch | `test_error_integration.py:169-177` | PROVEN (no retry-once per design) |
| AUDIT_WRITE_FAILED | audit exception → internal code | `test_errors.py:100-107` | PROVEN |

## 4. Design Artifacts vs Reality (mandate §3/§35)

| Design-promised artifact | Exists? | Note |
|---|---|---|
| `agent.py` | renamed | `poc/agent_runtime.py` — benign rename |
| `audit.py` | renamed | `poc/audit_store.py` |
| `schemas/*.json` (5 files) | **no** | contracts embedded in `tool_contracts.py` (equivalent, test-enforced) |
| `config.py` scoped accessors + env-scan test | **no** | bootstrap reads env directly; agent keyword-scan test exists instead — doc stale |
| `tests/test_cases.json` (50 cases) | **no** | F-15 — evaluation never built |
| `tests/run_eval.py`, `tests/verify_audit.py` | **no** | F-15; chain verification exists as `AuditStore.verify_chain()` + tests |
| `requirements.txt` | **no** | F-16 |
| `users.yaml` mirroring doc schema | **drifted** | F-19 — doc's schema rejected by code |
| Gateway as sole `odoo_client` importer | **drifted** | `bootstrap.py:16` imports it (composition root) — F-20 |
| Max 3 tool calls / LLM retry once | **diverged** | single-shot, stricter; undocumented |
| `CONFIRMATION_EXPIRY_SECONDS`, `MAX_*` env vars | **unread** | constants hardcoded; guardrails absent — F-11 |

## 5. Chain-End Verdicts

| Chain link | Status | Missing for PROVEN |
|---|---|---|
| NL → tool selection | PARTIAL | 50-case live-model run (F-15) |
| tool → schema → authz → confirmation | PROVEN | — |
| confirmation → idempotent execution | PARTIAL | live gateway+Odoo run; decline-retry fix (F-09) |
| execution → verification | PARTIAL | F-06 fix + live taxed-product test |
| verification → audit → truthful result | PARTIAL | F-08/F-14 audit fields; F-04/F-05 success-flag fix |

---

## 6. Post-Hardening Chain Verdicts (2026-09-10)

| Link | Status | Evidence |
|---|---|---|
| NL → tool selection | PROVEN (upgraded) | 50-case live evaluation (90 runs) on Claude Haiku via FCC proxy: 86.67% tool selection, 100% schema validity, 0 unauthorized writes, prompt injection resisted (F-15 CLOSED, LIVE_MODEL_EVALUATION_REPORT.md) |
| tool → schema → authz | PROVEN | 317-test suite incl. adversarial policy tests |
| confirmation → idempotent execution | PROVEN (upgraded) | decline/expiry release tests; conflict probe; LIVE: 1 order per logical op, provenance S00048 |
| execution → verification | PROVEN (upgraded) | narrowed contract per §6 (ADR-21a); LIVE verified write through gateway |
| verification → audit → truthful result | PROVEN (upgraded) | proposal_id/hash linkage (F-14); F-07 external_record_id preserved; text_only/no-data never success |
| reads return ERP data | PROVEN (upgraded from GAP) | LIVE customer.search → real record, audited |
