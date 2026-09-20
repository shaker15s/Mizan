# MIGRATION_PLAN — Phased path from POC → hardened platform

**Generated:** 2026-09-20
**Baseline:** commit `fb869d6` (477 tests passing, harness 50/50 PASS)
**Strategy:** Strangler fig (plan §100). We never delete working code in one shot;
new machinery lands behind adapters and callers are migrated incrementally.

---

## Status at a glance

| Phase | Description | Status |
|---|---|---|
| 0  | Baseline / evidence | ✓ DONE (docs + evidence manifest + 477 tests green) |
| 1  | Mandatory docs (CURRENT_STATE, ARCHITECTURE_MAP, TRUST_BOUNDARY, CANONICAL_STATE_MACHINE, HARNESS_ARCHITECTURE, SECURITY_CONTROL_MATRIX, TRACEABILITY_MATRIX, MIGRATION_PLAN) | ✓ DONE in this commit |
| 2  | Canonical Execution State Machine | ✓ DONE (`poc/execution/state_machine.py`, 22 tests) |
| 3  | Typed Action Envelope | ✓ DONE (`poc/execution/action.py`) |
| 4  | Proposal Versioning | ◐ Designed; integration into ConfirmationStore pending |
| 5  | Execution Lease | ✓ DONE (`poc/execution/lease.py`, 8 tests); integrate with Gateway pending |
| 6  | Harness 2.0 (144 golden flows + variants + trajectory graders) | ◐ 50/50 existing base; new flows and mutation/replay pending |
| 7  | Policy Engine 2.0 (ABAC + risk) | ◐ Risk engine v1 done; ABAC + multi-factor policy pending |
| 8  | Evidence Graph | ◑ Not started |
| 9  | Production API Security (auth, tenant, CORS/CSP/rate limits, dev-route isolation) | ◑ Not started |
| 10 | Frontend migration (React/TypeScript/state-machine-driven) | ◑ Not started |
| 11 | Production Data Layer (PostgreSQL/Redis when justified) | ◑ Not started |
| 12 | Production deployment hardening (secrets, OTel, runbooks, incidents) | ◑ Not started |
| 13 | Pilot | ◑ Not started |
| 14 | Scale | ◑ Not started |

---

## Phase 0 — Evidence / Baseline (DONE)

**Goal:** canonical repository state + evidence manifest + test baseline.

Deliverables completed:
- `evidence/EVIDENCE_MANIFEST.json` with EV-001 (pytest) and EV-002 (harness deterministic)
- Re-ran full suite: 477 passed, 5 skipped; harness 50/50 PASS.

## Phase 1 — Audit docs (DONE)

All 8 mandatory documents exist and are populated with verified repository
reality (not templates):

- `docs/CURRENT_STATE.md`
- `docs/ARCHITECTURE_MAP.md`
- `docs/TRUST_BOUNDARY.md`
- `docs/CANONICAL_STATE_MACHINE.md`
- `docs/HARNESS_ARCHITECTURE.md`
- `docs/SECURITY_CONTROL_MATRIX.md`
- `docs/TRACEABILITY_MATRIX.md`
- `docs/MIGRATION_PLAN.md` (this file)

## Phase 2 — Canonical Execution State (DONE — module + tests; integration pending)

**Done:**
- `poc/execution/state_machine.py` with full enum-typed states, 60+ transitions,
  immutable `ExecutionState.apply(event)`, transition history, public dict
  projection, and backward-compatible `project_to_gateway_status` /
  `project_to_proposal_state` adapters.
- All four state dimensions per plan §43: stage, status, security, final.
- 22 tests cover happy path, denial, decline, expiry, edit-revalidation,
  transient retry, hard failure, ambiguity, reconciliation (adopt + manual),
  terminal rejection, projections.

**Next:** thread `ExecutionState` through the gateway and agent runtime so
stage events drive the frontend rail instead of client-side timers. Add an
`ExecutionStore` (SQLite) so state is persistent across requests.

**Backward compatibility:** existing `GatewayResult.status` strings and
`proposals.state` values are produced by the projection functions, so
existing web_server / responder / tests continue to work unchanged.

## Phase 3 — Typed Action Envelope (DONE — dataclass; wiring pending)

**Done:**
- `poc/execution/action.py` defines `Actor`, `Versions`, `RiskAssessment`,
  `IdempotencyBinding`, and the `Action` envelope with server-owned identity
  fields. Helper `Action.from_tool_call(...)` constructs Actions from
  validated tool calls.

**Next:** construct an Action in AgentRuntime after schema validation, pass
it through Gateway instead of the raw `ToolGatewayRequest` (while keeping
ToolGatewayRequest available as the serialization boundary for now).

## Phase 4 — Proposal Versioning (next up)

**Plan §11.K:** every proposal edit bumps a server-owned version; approval
binds to a specific version; incompatible changes invalidate approval.

**Steps:**
1. Add `proposal_version INTEGER` column to `proposals` (schema migration in
   `db/init.py` — use ALTER TABLE to preserve existing DBs, like the
   `tool_name`/`tool_version` addition pattern in IdempotencyStore).
2. New proposal → version `1`. Each `amend_proposal` → declines Vn, creates
   V(n+1). Include `(proposal_id, proposal_version)` in `operation_hash`.
3. `approve()` must re-check version matches the latest version the user was
   shown; approval of a stale version returns VERSION_MISMATCH.
4. Frontend always carries the version; UI card displays it.
5. Tests: version increment, stale-version rejection, cross-version approval
   rejection.

## Phase 5 — Execution Lease (DONE — store; gateway integration pending)

**Done:** `poc/execution/lease.py` with SQLite table, acquire/heartbeat/
complete/release/expire_stale, ownership checks, payload hash binding.
8 tests including expiry/supersede.

**Next:** call `LeaseStore.acquire` from `gateway.confirm_and_execute` after
approval passes; start heartbeat during `execute_verified`; on transient
error, release the lease if no write was attempted; on hard failure,
complete; on ambiguous, complete as LEASE_ACTIVE until reconciliation
resolves. This replaces the simple "pending" idempotency TTL for write
ownership while preserving the idempotency key semantic (idempotency =
never duplicate the logical operation; lease = who owns the physical
attempt).

## Phase 6 — Harness 2.0

**Plan §27–31, §83:** Expand 50 → 144 golden flows, add variants, trajectory
graders, mutation mode, replay mode.

**Steps:**
1. Extend scenario schema with all fields listed in plan §27 (expected_state_
   transitions, expected_verification, security_expectations, etc.).
2. Add the 94 additional scenarios across groups B–L (plan §28).
3. Add variant generator (plan §29) for Arabic/Egyptian/MSA/code-switching/
   spelling/noise/retries/failures.
4. Add replay mode: execute scenario → capture records → re-run → diff.
5. Add mutation mode (plan §83): deterministic mutations of arguments/
   identity/tenant/policy/tool/model/ERP/timing; expect safe behavior.
6. Add new graders for trajectory correctness, state transition correctness,
   approval correctness, recovery.
7. Hook into canonical ExecutionState so state-transition expectations are
   asserted directly.

## Phase 7 — Policy Engine 2.0

**Plan §12:** evolve from user→allowed_tools to ABAC over user/role/tenant/
branch/resource/operation/tool/amount/sensitivity/risk/channel/time/velocity/
approval.

**Steps:**
1. Introduce `PolicyDecision(policy_rule_id, policy_version, policy_hash, reason)`
   with versioning/hash (rule IDs exist already; add version/hash).
2. Extend policy schema to support attribute predicates (e.g. sales orders
   with total > X require manager approval).
3. Integrate the deterministic risk engine (`poc/execution/risk.py`) into
   policy: R0 reads → ALLOWED, R1/R2 → CONFIRMATION_REQUIRED, R3 →
   REQUIRE_APPROVAL, R4 → REQUIRE_STEP_UP.
4. Policy result must be an immutable object tied to the Action and stored
   in the evidence chain.
5. Add policy-version hash to audit records.

## Phase 8 — Evidence Graph

**Plan §20–21:** move beyond log rows to typed evidence events linked by
trace/action/execution IDs, with cryptographic linkage.

**Steps:**
1. Define `EvidenceEvent` types: INTENT, PLAN, ENTITY_RESOLUTION,
   POLICY_DECISION, APPROVAL, LEASE, TOOL_CALL, ERP_REQUEST, ERP_RESPONSE,
   VERIFICATION, RECONCILIATION, USER_VISIBLE_CLAIM.
2. New `evidence` table (in gateway DB for POC, separate store later) keyed
   by evidence_id; each event carries parent_event_id, timestamps, actor,
   and a content hash.
3. Emit events from gateway/agent/verification/reconciliation paths.
4. Evidence viewer (Phase 10 frontend) renders the event chain.
5. ADR for evidence chain / crypto selection.

## Phase 9 — Production API Security

**Plan §52, §7.F:** web server hardening.

**Steps:**
1. Authentication layer (session token, secure cookie).
2. Tenant/session context established server-side — client can no longer set
   user_id/tenant_id in request body.
3. Replace wildcard CORS with explicit allowed origin.
4. Add CSP, HSTS, X-Frame-Options, Referrer-Policy, X-Content-Type-Options.
5. Rate-limit per tenant + per IP; request size caps.
6. CSRF tokens for mutating endpoints when cookie-auth is used.
7. Isolate dev/test/routes (`/api/replay`, slash eval) behind a dev-only flag
   that defaults to disabled and refuses to start in production mode.
8. Mask/redact telemetry; split operator telemetry from developer diagnostics.
9. API tests for auth, tenant isolation, CORS/CSRF/CSP headers.

## Phase 10 — Frontend migration

**Plan §44–55:** move cockpit to React+TypeScript with typed API contracts,
explicit state machine, Playwright tests.

**Steps:**
1. Set up a React+TS app (Vite) under `03-poc-src/web/` (or new `web/`
   package) — zero build for the legacy cockpit remains until cutover.
2. Define typed API contracts mirroring `ExecutionState.to_public_dict()`
   and the Action envelope.
3. Build the Execution Rail driven by stage events; remove client-side
   timers that fabricated pipeline state.
4. Build the Confirmation surface (WHAT will change + risk + approval state
   + EDIT/APPROVE/CANCEL) wired to proposal_version.
5. Build Evidence viewer (plan §79).
6. Replace unsafe `innerHTML` paths with React/DOM-safe rendering.
7. Add Playwright harness (golden flows, confirmation, cancel, edit,
   expiry, error, recovery, mobile/tablet/desktop, RTL, accessibility,
   visual regression).
8. Switch over when Playwright parity with existing 55 DOM smoke checks is
   achieved; remove legacy cockpit only after parity.

## Phase 11 — Production Data Layer

**Plan §41:** migrate to PostgreSQL/Redis only when production requirements
(concurrency, multi-instance, row-level isolation) demand it. For now
SQLite/WAL is acceptable.

**Steps:**
1. Measure connection concurrency from pilot.
2. Introduce a repository interface for stores so SQLite can be swapped for
   PostgreSQL without changing domain logic.
3. Add Redis for short-lived state (leases, sessions, rate limiting) when
   multi-instance demands it.
4. Add an outbox/event layer for evidence propagation.
5. Use PostgreSQL RLS as defense in depth (not as the only authz layer).

## Phase 12 — Production deployment hardening

**Plan §40, §42, §106:**

1. Secrets boundary: move ERP credentials out of Agent Runtime process;
   connector mints short-lived credentials; secret manager integration.
2. OpenTelemetry with trace/span propagation (trace_id, execution_id, …);
   redaction hooks to ensure no secret leakage.
3. Operational runbooks: ERP outage, model outage, stuck execution,
   ambiguous mutation, evidence corruption, DB issue, credential rotation,
   tenant incident, security incident, failed deployment, rollback,
   reconciliation.
4. Incident model (plan §107) + post-incident regression test per incident.
5. Backup/restore for SQLite (POC) and PostgreSQL (prod).

## Phase 13–14 — Pilot then Scale

Only after release gates (plan §74) pass AND Phase 9–12 are implemented will
a controlled pilot begin. Scale (multi-tenant rollout, additional ERP
adapters, additional channels) follows pilot evidence.

---

## Per-commit discipline

Per plan §101, §113, every change should:

1. Be a small logical commit.
2. Include unit tests for new behavior.
3. Include harness cases for new workflows.
4. Run full deterministic suite (`pytest` + harness deterministic).
5. Update docs to reflect new behavior.
6. Append to evidence manifest for any evaluation run.

---

## What we will NOT do yet (plan §110)

- Giant multi-agent swarm.
- Universal browser automation.
- 50 ERP adapters.
- Huge tool catalog.
- Event bus / Kafka / Kubernetes everywhere.
- Complex service mesh.
- 3D UI / neon AI aesthetic.
- Fully autonomous money movement.

Engineering sophistication = knowing what NOT to build.
