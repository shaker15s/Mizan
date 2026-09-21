# TRACEABILITY_MATRIX — Requirement → Design → Implementation → Test → Evidence

**Generated:** 2026-09-20
**Format:** Requirement ID (per plan section) → Design module → Implementation file(s) → Test file(s) → Evidence.

> Major requirements are tracked here; this file is updated as work progresses.

---

## Authority & safety invariants (plan §2, §6)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-AUTH-01 | LLM is not an authority | Fail-closed gateway | `poc/gateway.py` | `test_gateway.py`, `test_scenario_security.py` | EV-002 `unauthorized_writes=0` |
| R-AUTH-02 | Server decides identity/tenant/policy | PolicyEngine + server-owned runtime construction | `poc/authz.py`, `poc/bootstrap.py`, `poc/agent_runtime.py` | `test_authz.py` | EV-001 |
| R-AUTH-03 | Model output is untrusted input | Registry validation + JSON Schema | `poc/agent_runtime._validate_tool_call`, `poc/gateway._validate_arguments` | `test_tool_choice.py`, `test_tool_contracts.py` | EV-001 |
| R-AUTH-04 | No fabricated success claims | Verification-gated status; truthful responder | `poc/verification.py`, `poc/responder.py` | `test_verification.py`, harness truthfulness grader | EV-002 `unsupported_claims` check |
| R-AUTH-05 | No fabricated external IDs | Real order_id from Odoo only; no fake fallback | `poc/gateway._verification_success` | `test_gateway.py`, harness | EV-002 `duplicate_orders=0` |
| R-AUTH-06 | Unknown = unknown; pending = pending | Explicit statuses + no over-claiming | `poc/errors.py`, `poc/responder.py` | `test_errors.py`, `test_error_integration.py` | EV-001 |

## Tool contracts (plan §24)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-TOOL-01 | Closed server-owned registry | Static contracts in code | `poc/tool_contracts.py` | `test_tool_contracts.py` | EV-001 |
| R-TOOL-02 | Versioned contracts | `TOOL_VERSION` + per-contract semantic version | `poc/tool_contracts`, `poc/authz.RULE_TOOL_VERSION_MISMATCH` | `test_authz.py::test_tool_version_mismatch` | EV-001 |
| R-TOOL-03 | JSON Schema input validation | `jsonschema.validate` per call | `poc/gateway._validate_arguments` | `test_gateway.py::test_invalid_arguments_*` | EV-001 |
| R-TOOL-04 | No arbitrary ERP RPC | Tool→model/method mapping hardcoded | `poc/gateway._execute_read`, `poc/gateway._build_odoo_payload` | `test_gateway.py` | EV-001 |

## Policy (plan §12)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-POL-01 | Deterministic policy decisions | YAML-loaded allowlist | `poc/authz.py` | `test_authz.py` | EV-001 |
| R-POL-02 | Fail closed on ambiguity | Default deny | `PolicyEngine.evaluate` returns `denied` on malformed request | `test_authz.py::test_*_denied` | EV-001 |
| R-POL-03 | Writes require confirmation | `CONFIRMATION_REQUIRED` for readOnly=False | `PolicyEngine.evaluate` | `test_authz.py::test_writer_gets_confirmation_required` | EV-001 |

## Confirmation (plan §14)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-CONF-01 | Operation hash binds proposal | SHA-256(tool+ver+args+user+tenant+created_at) | `poc/confirmation.compute_operation_hash` | `test_confirmation.py::test_hash_mismatch_*` | EV-001 |
| R-CONF-02 | 9-point recheck at approve | identity/tenant/tool/version/policy/state/expiry/hash | `ConfirmationStore.approve` | `test_confirmation.py` | EV-001 |
| R-CONF-03 | Proposal expiry | TTL (default 300s) enforced | `ConfirmationStore` + `confirm_ttl_seconds` | `test_confirmation.py::test_expired_*` | EV-001 |
| R-CONF-04 | Edit invalidates prior approval | `amend_proposal` declines old + creates new | `poc/agent_runtime.amend_proposal` | `test_agent_runtime.py` amend cases | EV-001 |

## Idempotency (plan §16)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-IDEM-01 | Content-addressable keys | sha256(tenant+user+tool+canonical_args)[:32] | `poc/idempotency.compute_idempotency_key` | `test_idempotency.py` | EV-001 |
| R-IDEM-02 | execution_id distinct from idempotency_key | UUID v4 per attempt | `IdempotencyStore.reserve` | `test_idempotency.py` | EV-001 |
| R-IDEM-03 | Duplicate detection | INSERT OR IGNORE → CONFLICT/IN_PROGRESS/REPLAYED | `IdempotencyStore.reserve` | `test_idempotency.py::test_*`, harness TC-044/045/046 | EV-002 `idempotency_conflict_detected=yes` |
| R-IDEM-04 | Ambiguous outcome blocks re-execution | STATE_UNKNOWN → RECONCILIATION_REQUIRED | `IdempotencyStore.fail(AMBIGUOUS)` | `test_gateway.py::test_*_ambiguous` | EV-002 |

## Audit (plan §20)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-AUD-01 | Append-only | No UPDATE/DELETE against audit_log in code | `poc/audit_store.py`, `poc/db/init.py` | `test_audit_store.py::test_*_append_*`; grep-verified | EV-002 `audit_coverage=100%` |
| R-AUD-02 | SHA-256 hash chain | GENESIS=`0*64`, each row chained to previous | `AuditStore.compute_row_hash` | `test_audit_store.py::test_chain_integrity` | EV-002 `audit_chain_valid=yes` |
| R-AUD-03 | Argument sanitization allowlist | Drops unknown/credential fields | `_sanitize_arguments` | `test_audit_store.py::test_sanitize_*` | EV-001 |
| R-AUD-04 | Every request audited | `_audit` called from every `_process` path | `ToolGateway._audit` | harness audit_coverage grader | EV-002 `audit_coverage=100%` |

## Verification (plan §19)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-VER-01 | Post-write read-back | Read `sale.order` + lines and compare | `poc/verification.verify_sales_order_creation` | `test_verification.py` | EV-001 |
| R-VER-02 | Provenance via client_order_ref | Compare against idempotency_key | `verify_sales_order_creation` | `test_verification.py::test_*_ref*` | EV-001 |
| R-VER-03 | ERP owns financial totals | Only check totals exist and non-negative; no local tax math | `verify_sales_order_creation` (amount_total checks) | `test_verification.py` | EV-001 |

## Error taxonomy (plan §14.N)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-ERR-01 | Canonical structured errors | `StructuredError(code, retryable, requires_user_action, category, message)` | `poc/errors.py` | `test_errors.py`, `test_error_integration.py` | EV-001 |
| R-ERR-02 | Translate Odoo exceptions via MRO | `translate_odoo_exception` | `poc/errors.py` | `test_error_integration.py` | EV-001 |
| R-ERR-03 | No stack traces in responses | Web handler maps to StructuredError only | `poc/web_server.py` | `test_web_server.py` | EV-001 |

## Canonical state machine (plan §43 — Phase 2)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-CSM-01 | One authoritative state | `ExecutionState(stage, status, security, final)` | `poc/execution/state_machine.py` | `tests/test_execution_state_machine.py` | New in this milestone |
| R-CSM-02 | Event-driven transitions only | `ExecutionState.apply(event)` with explicit transition table | `poc/execution/state_machine.py:CANONICAL_TRANSITIONS` | same tests | New |
| R-CSM-03 | Terminal states are final | `is_terminal` blocks further transitions | `ExecutionState.apply` | same tests | New |
| R-CSM-04 | Transition history preserved | `history: tuple[ExecutionTransition, ...]` | `ExecutionState` | same tests | New |
| R-CSM-05 | Backward-compatible projections | `project_to_gateway_status`, `project_to_proposal_state` | `state_machine.py` | same tests | New |

## Typed Action Envelope (plan §10 — Phase 3)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-ACT-01 | Typed action with identity/risk/versions | `Action` dataclass with `Actor`, `Versions`, `RiskAssessment`, `IdempotencyBinding` | `poc/execution/action.py` | Implicit via imports; future integration tests will cover wiring | New module; integration TBD |
| R-ACT-02 | Server-owned actor | `Actor.user_id/tenant_id/branch_id/role` never set from model output | `action.py` (frozen dataclass) | Construction tests to add | New |

## Execution Lease (plan §15 — Phase 5)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-LEASE-01 | Lease separate from idempotency | `ExecutionLease(lease_id, execution_id, owner, issued_at, heartbeat_at, expires_at, payload_hash, state)` | `poc/execution/lease.py` | `tests/test_execution_lease.py` | New module |
| R-LEASE-02 | Heartbeat & expiry | `LeaseStore.heartbeat()`, `expire_stale()` | `lease.py` | same tests | New |
| R-LEASE-03 | Owner-gated transitions | `LeaseNotOwnedError` on wrong owner | `LeaseStore.complete/release/heartbeat` | same tests | New |
| R-LEASE-04 | Payload hash binding | `_payload_hash(arguments)` stored with lease | `LeaseStore.acquire` | same tests | New |

## Risk engine (plan §13 — Phase 7 precursor)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-RISK-01 | Deterministic classification | R0–R4 based on readOnly/destructive/quantity/factors | `poc/execution/risk.py` | `tests/test_risk_engine.py` | New |
| R-RISK-02 | Explainable factors | `RiskAssessment.factors: tuple[str, ...]` | `risk.assess_risk` | same tests | New |

## Arabic normalization (plan §86)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-AR-01 | Orthographic fold (hamza/taa/yaa) | Normalization + query variant ladder | `poc/normalization.py`, `poc/gateway._query_variants` | `test_normalization_gateway.py` | EV-002 |
| R-AR-02 | Egyptian colloquial friendly | Egyptian dialect prompts + response style | `poc/prompts.py`, `poc/responder.py` | Live eval report 02-poc/LIVE_MODEL_EVALUATION_REPORT.md | Live eval |

## Harness (plan §25–33)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-HAR-01 | Deterministic hermetic runs | Per-case temp env + seeded ERP + FakeLLM | `poc/harness/environment.py`, `runner.py` | `test_harness.py` | EV-002 50/50 PASS |
| R-HAR-02 | Safety gates block merge | Thresholds + exit code | `metrics.py`, CLI exit codes | `test_harness.py::test_thresholds_*` | EV-002 `verdict: PASS · exit 0` |
| R-HAR-03 | Trajectory & answer graders | Independent graders per dimension | `graders.py` | same | EV-002 |

## Frontend security (plan §52, §53)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-WEB-01 | Arabic-first RTL | CSS with RTL logical properties, LTR isolates | `web/styles.css` | `tools/frontend_smoke.mjs` | EV-001 (smoke) |
| R-WEB-02 | No XSS via safe DOM | `textContent` / safe renderer | `web/ui.js`, `web/markdown.js` | Full audit pending (see SECURITY_CONTROL_MATRIX #20) | ~ partial |
| R-WEB-03 | Backend-driven state (no fake pipelines) | Stage events from AgentRuntime feed UI | `web/app.js` SSE → stages | Manual test | ~ partial (timer audit pending) |

## Decision intelligence — Jev (plan §2, §5–§27, §30–§36, §42–§49)

| ID | Requirement | Design | Implementation | Tests | Evidence |
|---|---|---|---|---|---|
| R-DEC-01 | Jev is a signal, never an authority | `authority: signal_only` on every payload; no write/authorize field exists | `poc/decision/router.py` (`health`, `post_run_review`), `poc/agent_runtime.py` | `tests/test_decision_layer.py::TestRouter`, `TestRuntimeIntegration` | EV-006; baseline/advisory governance parity |
| R-DEC-02 | Provider-neutral layer, no SDK in the runtime | `DecisionClientProtocol` + thin httpx client | `poc/decision/protocol.py`, `jev_client.py`, `mock_client.py` | `tests/test_decision_layer.py::TestMockProvider` | EV-006 |
| R-DEC-03 | Tool routing via a `choice` question | Choice with probabilities + confidence, dual gate (confidence **and** margin) | `poc/decision/questions.py`, `policy.compute_route_plan` | `TestMonotonicSafety`, harness `decision_layer.decision.tool_selection_accuracy` | EV-006 (advisory 100 % on oracle, held-out 100 %) |
| R-DEC-04 | Ambiguity via `noul` | Allowlisted state + signal threshold | `poc/decision/questions.py`, `thresholds.ambiguity_trigger` | harness ambiguity P/R gates | EV-006 (100/100 oracle; 33.33 recall on the adversarial negative control) |
| R-DEC-05 | Injection signal is additive only | Noul escalator; can raise, never lower | `policy.compute_escalation` | `TestMonotonicSafety.test_injection_signal_may_only_raise_escalation` | EV-006 (18 TP, 0 FP on oracle) |
| R-DEC-06 | Semantic risk may never downgrade deterministic risk | `effective_risk_level` raise-only + opt-in bump | `poc/decision/policy.py` | `TestMonotonicSafety.test_semantic_risk_can_never_downgrade_a_deterministic_level` | EV-006 `escalation_lowered_deterministic_risk = 0` |
| R-DEC-07 | Monotonic safety overall | Escalation ladder, narrowing only advisory/enforcing, escalation cancels narrowing | `policy.py`, `router.screen` | `test_actives_escalation…`, `TestRuntimeIntegration` | EV-006 safety block (all zeros) |
| R-DEC-08 | Fail-as-value degradation | `DecisionResult.error`, `fallback=True`, timeouts/budgets | `jev_client.py`, `router.screen` | `TestRouter.test_provider_outage_degrades_to_a_usable_outcome`, `TestRuntimeIntegration.test_provider_outage_is_not_a_mizan_outage` | EV-006 (`provider_failure_did_not_block`) |
| R-DEC-09 | Redaction by allowlist; no secrets/PII | `DecisionStateBuilder` + `mask_pii` + forbidden literals | `poc/decision/redaction.py`, `questions.build_*_state` | `TestRedaction` | EV-006 |
| R-DEC-10 | Versioned questions + state hash | spec `1.0.0`, `compute_state_hash` over spec+model+state | `models.py`, `questions.py` | `TestParsing.test_state_hash_*` | EV-006 (`threshold_fingerprint`, `state_hash` per decision) |
| R-DEC-11 | Evidence-graph events | `DECISION_REQUEST/RESPONSE/ROUTING/ESCALATION/DISAGREEMENT` in the existing chain | `evidence.py`, `router._evidence` | `tests/test_decision_layer.py` + evidence tests | EV-006 |
| R-DEC-12 | Disagreement recorded, never overwritten | `Disagreement` with a stable resolution string | `policy.compare_with_llm`, `agent_runtime._record_disagreement` | `TestRouter.test_disagreement_recording_*` | EV-006 (0 oracle / 2 realistic / 27 adversarial, all with resolutions) |
| R-DEC-13 | Offline deterministic mode is non-negotiable | Mock provider, scripted profiles, zero network | `mock_client.py`, `profiles.py` | `tests/test_harness_decision.py` | EV-006; CI runs it on every push |
| R-DEC-14 | Never silently enabled; never silently disarmed | Two-axis opt-in; `decision_layer.enabled` gate + stderr warning when armed and refused | `settings.py`, `harness/cli.py`, `harness/runner.py` | `test_an_armed_layer_that_cannot_be_built_is_a_failure_not_a_silent_pass` | EV-006 |
| R-DEC-15 | Honest metrics; no fabricated numbers | Real runs only; synthetic labelled `synthetic: true`; MISSING never zero | `harness/decisions.py`, `scripts/compare_decision_reports.py` | `tests/test_harness_decision.py` | EV-006 + `data/reports/decision-comparison.md` |
| R-DEC-16 | Calibration on a split, validation on held-out | Grid sweep, conservative tie-break, split doc | `scripts/calibrate_decision.py`, `tests/decision_split.json` | `test_calibration_artifact_matches_the_shipped_thresholds` | EV-006 (0.85/0.50, held-out PASS) |
