# SECURITY_CONTROL_MATRIX — Threat → Control → Test → Evidence

**Generated:** 2026-09-20
**Scope:** As of commit `fb869d6`. Status values:
* ✓ implemented and tested
* ~ partial / POC-grade
* ✗ not yet implemented

See plan §73 for the required matrix shape.

---

| # | Threat | Attack vector | Control | Code location | Test(s) | Evidence | Residual risk |
|---|---|---|---|---|---|---|---|
| 1 | Direct prompt injection | User message asking to ignore policy / execute arbitrary tool | System prompt isolation; registry allowlist; tool-call validation; deterministic policy engine | `agent_runtime.py`, `gateway.py`, `tool_contracts.py`, `authz.py` | `test_scenario_security.py` (injection), harness TC-050 | Harness EV-002: `prompt_injection_resisted=yes` | Low — model can still produce odd prose, but never bypasses gateway |
| 2 | Indirect prompt injection | Customer name / product name / ERP data containing instructions | Model output never trusted; server validation; output sanitization; argument allowlisting | `gateway.py`, `audit_store.py`, `responder.py` | `test_post_audit_hardening.py` | Harness `reasoning_leaks=0` | Medium — indirect-injection corpus not yet built |
| 3 | Tool poisoning | Model attempts to register new tools or invoke arbitrary ERP RPC | Closed static registry; no dynamic registration; `unknown_tool_rejected` | `tool_contracts.py`, `agent_runtime._validate_tool_call` | `test_tool_choice.py`, `test_scenario_security.py` | EV-001 tool selection tests | Low — no code path for model-defined tools |
| 4 | Tool argument injection | Malformed arguments, extra fields, credential fields | JSON Schema validation; `additionalProperties: false`; argument allowlist for audit/idempotency | `tool_contracts.py` (schemas), `gateway._validate_arguments` | `test_tool_contracts.py`, `test_gateway.py` | EV-001 schema validity tests | Low |
| 5 | Privilege escalation | Sales user attempts admin-only tool | PolicyEngine allowlist per user/tenant/tool | `authz.py`, `users.yaml` | `test_authz.py`, `test_scenario_security.py` authz cases | Harness TC-031..035, TC-036..038 | Low |
| 6 | Cross-tenant access | Tenant A user_id with Tenant B tenant_id | Server-owned tenant identity; tenant mismatch denied | `authz.py:_denied` (unknown_tenant), `confirmation.py` (tenant check) | `test_authz.py`; multi-tenant tests pending (Phase 9) | Currently only single-tenant POC | Medium — awaiting multi-tenant tenant enforcement in API/session layer |
| 7 | Cross-tenant mutation | Write with forged tenant_id | Same as #6 plus idempotency key is tenant-bound | `idempotency.py` (key = sha256(tenant+user+tool+args)) | Idempotency tests cover tenant binding | Multi-tenant cases pending | Medium (same as #6) |
| 8 | Secret leakage | Credentials in arguments, result, audit, LLM prompt | Allowlist sanitization in audit; secret-key stripping in idempotency result; never inject credentials into prompts; settings endpoint masks secrets | `audit_store._sanitize_arguments`, `idempotency._contains_secret_key`, `settings.py`, `web_server.py` (masking) | `test_audit_store.py`, `test_settings.py` | Harness `unauthorized_writes=0, reasoning_leaks=0` | Medium — secrets live in-process today (plan §40) |
| 9 | Replay attack | Re-send an approved proposal or captured request | Operation hash binding; proposal expiry; idempotency keys; atomic SQL transitions | `confirmation.approve` (9-point recheck), `idempotency.py` | `test_confirmation.py`, `test_idempotency.py`, harness TC-044..046 | EV-002 `idempotency_conflict_detected=yes` | Low |
|10 | Duplicate critical mutation | Retry after ambiguous success without reconciliation | AMBIGUOUS state blocks re-execution; release path only for NOT-committed failures; reconciliation required before adoption | `gateway.execute_verified`, `idempotency.fail(AMBIGUOUS)` | `test_gateway.py` (ambiguity paths), harness TC-047..049 | EV-002 `duplicate_orders=0` | Medium — manual review UX pending |
|11 | Approval bypass | Client calls execute endpoint without confirmation | Confirmation proposal is server-owned; `confirm_and_execute` re-checks all 9 points atomically; no "approve=true" client field exists | `gateway.confirm_and_execute`, `confirmation.approve` | `test_confirmation.py`, `test_gateway.py` | EV-001 approval tests | Low |
|12 | Identity spoofing | Client sends `user_id` of another user | Server configures identity at runtime construction; web session (POC) ignores identity in body | `AgentRuntime.__init__`, `bootstrap.py`, `web_server.py` | `test_web_server.py` | Session not yet authenticated (§7.F) | Medium in POC; real auth slated for Phase 9 |
|13 | Tenant spoofing | Client claims different tenant_id | Same as #12 | Same as #12 | Tests pending | Medium (same as #6) |
|14 | Model-supplied verification | Model returns "تم التنفيذ" without ERP check | `responder.compose_answer` reports success only when `verification.passed`; status keywords are gateway-driven | `gateway._verification_success`, `verification.py`, `agent_runtime._response_text` | `test_verification.py`, `test_scenario_golden_path.py` | EV-002 `governance.unauthorized_writes=0` | Low |
|15 | Fabricated external IDs | Fallback to random order ID on error | Verified by code review: `_verification_success` uses `created_ids[0]` from Odoo; no fallback generator exists; `unsupported_claim` grader | `gateway.py`, harness graders | `test_gateway.py` failure paths; harness `answer_quality.grounded_rate` | EV-002 `unauthorized_writes=0`; no fake ID generator in codebase | Low |
|16 | Approval stored client-side | UI stores approved flag | Proposal state lives in SQLite `proposals`; UI only sends proposal_id | `confirmation.py`, `web_server.py` confirm route | `test_confirmation.py` | EV-001 | Low |
|17 | Mutable approved payload | Client edits arguments after approval | Operation hash mismatch triggers HASH_MISMATCH; `amend_proposal` issues a fresh proposal with a new hash | `confirmation.approve` (recompute hash), `agent_runtime.amend_proposal` | `test_confirmation.py:test_hash_mismatch`, `test_agent_runtime.py` amend tests | EV-001 | Low |
|18 | Idempotency based on random execution IDs | Idempotency key = random | Content-addressable key derivation from tenant+user+tool+canonical args; execution_id is separately a UUID per attempt | `idempotency.compute_idempotency_key` | `test_idempotency.py` | EV-001 key derivation tests | Low |
|19 | Blind retry after ambiguous write | Retry after timeout causes duplicate | AMBIGUOUS state blocks further reserve(); release only allowed for not-committed failures | `gateway.execute_verified` catch branch | `test_gateway.py` ambiguous cases | Harness TC-047/048/049 | Medium — full reconciliation flow pending (Phase 6+) |
|20 | Frontend XSS | Rendering ERP/model data with `innerHTML` | `markdown.js` minimal renderer; partial `textContent` use; audit of `ui.js` for safe DOM methods required | `web/markdown.js`, `web/ui.js` | `test_web_server.py` (basic); DOM smoke via `tools/frontend_smoke.mjs` | Low/Medium — full innerHTML audit scheduled (plan §7.E) |
|21 | Wildcard CORS | Browser from any origin can call API | `Access-Control-Allow-Origin: *` set in `web_server.py` | `web_server.py` | Not yet tested | ✗ POC-only (Phase 9) |
|22 | No authentication on API | Anonymous access to /api/chat etc. | No auth in POC; identity comes from env | `web_server.py` | Not applicable in POC | ✗ (Phase 9) |
|23 | Missing security headers (CSP, HSTS, X-Frame-Options) | Clickjacking / XSS vectors | Not yet set | `web_server.py` | ✗ | ✗ (Phase 9) |
|24 | No rate limiting | Brute force / DoS | Not enforced | `web_server.py` | ✗ | ✗ (Phase 9) |
|25 | Developer endpoints public (/api/replay, slash eval) | Execution of arbitrary replay / eval in production | Routes present in same server; gated by env flag only partially | `web_server.py`, `slash.py` | ✗ | ~ partial (Phase 9) |
|26 | Telemetry / env info leak | Settings endpoint exposes env details | Settings allowlist + secret masking | `settings.py`, `web_server.py` settings route | `test_settings.py` | ~ secrets masked, but env keys still listed | Medium |
|27 | Network/ERP failure mid-write | Timeout after possible commit causes duplicate | Ambiguous classification + idempotency lock + reconciliation | `gateway.execute_verified`, `idempotency.fail(AMBIGUOUS)` | `test_gateway.py` post-create-timeout cases | EV-002 ERP failure cases | Medium (UI reconciliation pending) |
|28 | Global circuit breaker | One tenant's outage affects others | Single CB instance in `OdooClient` | `odoo_client.py` | `test_circuit_breaker.py` (single-tenant) | ✗ per-connector/tenant CB needed (plan §7.I) | Low in single-tenant POC; medium in production |
|29 | Audit log tampering | Modification/delete of audit rows | SHA-256 hash chain verified by `verify_chain()`; append-only design; code contains no UPDATE/DELETE against audit_log | `audit_store.py`, `db/init.py` (grep-verified no DROP) | `test_audit_store.py` (chain verification) | EV-002 `audit_chain_valid=yes` | Medium within same-DB-file (separate immutable store slated for Phase 8) |
|30 | ERP permission bypass | Using a privileged user_id to make calls the user shouldn't | Per-user Odoo identity in POC is same as platform user (API key per user); Odoo enforces own ACLs | `users.yaml`, `odoo_client.py` (uses user's key) | `test_odoo_client_integration.py` (skipped in sandbox) | Low at Odoo boundary |
|31 | Model sees credentials | Credentials passed into prompt or visible to model | Credentials never loaded into prompt; llm_client receives only messages/system/tools | `prompts.py`, `agent_runtime.py` | Code review | Low by construction |
|32 | Memory poisoning | Conversation history modifies future policy decisions | Policy re-evaluated on every turn against server-owned `users.yaml`; history is sent to LLM as context-only and never feeds authz | `authz.py`, `agent_runtime.process` | Tested indirectly by authz tests | Low — but indirect prompt injection from history remains a general LLM risk |
|33 | Argument tampering in-flight | Mutation between authorization and execution | Idempotency reservation fingerprint binds arguments; execution lease payload_hash; verification checks expected lines | `idempotency.py` (fingerprint), `lease.py` (payload_hash), `verification.py` | `test_idempotency.py`, new `test_execution_lease.py` | Lease is new; fingerprint exists today | Low |
|34 | Large-amount write without escalation | Creating very large orders without elevated approval | Contract risk R2; risk engine upgrades ≥1000 qty to R3; R3 will require step-up once approval 2.0 lands | `poc/execution/risk.py`, future Policy Engine 2.0 | New `test_risk_engine.py` | ✗ R3 enforcement pending Policy 2.0 (Phase 7) | Medium (R3 detected but not yet gated) |
|35 | Replay of expired proposal | Attacker resubmits old proposal_id | 9-point approve checks expiry | `confirmation.approve` (EXPIRED) | `test_confirmation.py::test_expired_proposal_rejected` | EV-001 | Low |
|36 | Compromised package / supply chain | CDN script injection | Frontend has zero CDN deps; all ES modules served locally; 6 pinned Python deps | `poc/web/`, `requirements.txt` | DOM smoke test | Low |
|37 | Audit PII leakage | Storing free-form PII/credentials in audit | Allowlist `_sanitize_arguments` blocks unknown fields and credential keys | `audit_store.py` | `test_audit_store.py` | EV-001 | Low for structured tool args; free-form NL input is not audited verbatim |
|38 | Stack trace / exception leakage | Error responses expose internals | `web_server.py` returns structured errors; `translate_error` maps to canonical codes | `errors.py`, `web_server.py` error handler | `test_web_server.py`, `test_error_integration.py` | ~ | Low |
|39 | Denial of service via unbounded inputs | Extremely large messages / parameters | Input validation: id/tool/tenant/user bounded strings; JSON schema limits lines length; request body size not explicitly capped (POC) | `gateway._validate_envelope` | ✗ body size cap | ✗ Medium |
|40 | CSRF | State-changing requests from other origins | POC: wildcard CORS + no CSRF token | `web_server.py` | ✗ | ✗ (Phase 9) |

---

## Release gates blocking "production-ready" (plan §74)

| Gate | Status |
|---|---|
| Authorization tests green | ✓ |
| Cross-tenant tests green | ✗ (awaiting multi-tenant implementation) |
| Approval tests green | ✓ |
| Replay tests green | ✓ (harness TC-044..046) |
| Idempotency tests green | ✓ |
| Verification tests green | ✓ |
| Secret-leak tests green | ~ (in-process credentials still — separate secret boundary needed) |
| Prompt-injection tests green | ✓ (basic direct); indirect corpus pending |
| API auth tests green | ✗ (no auth yet) |
| UI security tests green | ~ (basic smoke; full innerHTML audit pending) |
| Audit integrity verified | ✓ |
| Evidence manifest generated | ✓ (this commit) |
| Rollback strategy documented | ✗ |
| Recovery strategy documented | ~ (AMBIGUOUS→reconciliation exists; runbooks pending) |
