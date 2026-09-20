# CANONICAL_STATE_MACHINE — One authoritative lifecycle

**Generated:** 2026-09-20
**Implementation:** `03-poc-src/poc/execution/state_machine.py`
**Version:** `state_machine_version() = "1.0.0"` (see module)
**Tests:** `tests/test_execution_state_machine.py` (22 cases, all passing)

This document defines the single source of truth for execution state that
replaces the seven overlapping state concepts identified in
`docs/CURRENT_STATE.md §5`. All other state representations (gateway status,
proposal state, idempotency state, frontend visual state) become *projections*
of this machine via the `project_to_*` adapters.

---

## 1. Design

Every execution has four parallel dimensions:

| Dimension | Purpose |
|---|---|
| `stage`    | Where in the lifecycle the execution is (plan §43 stages). |
| `status`   | User-facing truthful semantics aligned with plan §22. |
| `security` | Authorization/security classification (plan §43 security states). |
| `final`    | Populated ONLY when a terminal stage is reached. |

A state transition is ALWAYS triggered by an explicit `ExecutionEvent`, applied
via `ExecutionState.apply(event, *, actor, reason, evidence_id)`. The machine
is pure-Python, deterministic, and immutable: `apply()` returns a new state
without mutating the previous one.

---

## 2. Stages (plan §43)

### Interaction
* `idle` – no execution in progress
* `listening` – user message received; not yet parsed
* `thinking` – intent parsed; model/rule engine processing
* `streaming` – response streaming (narrative)
* `resolving` – resolving ambiguous references (entities, anaphora)
* `needs_clarification` – waiting for user disambiguation

### Execution pipeline
* `received` – request formally accepted
* `planned` – action plan built
* `policy_checking` – deterministic policy evaluation
* `blocked` – cannot proceed until some condition lifts (approval expired, step-up required, lease lost)
* `awaiting_confirmation` – proposal issued; waiting for user signature
* `authorized` – policy/approval passed; ready to lease
* `lease_acquired` – execution lease held
* `executing` – ERP call in-flight
* `verifying` – post-write read-back verification
* `completed` – verification passed; ready to be finalized

### Failure / recovery
* `failed` – definitive failure (no external mutation or known not-committed)
* `retrying` – transient failure; retry scheduled
* `ambiguous` – possible external mutation; outcome unproven
* `reconciliation_required` – operator/automated reconciliation underway
* `partially_completed` – some side effects happened, not all
* `compensating` – compensating action in-flight
* `compensated` – compensation finished

### Terminal (final)
* `success` – verified success
* `no_op` – informational question, no side effect
* `cancelled` – user cancelled before any side effect
* `rejected` – policy/security rejected
* `unknown` – unable to determine outcome

---

## 3. Statuses (truthful result contract — plan §22)

* `success`
* `pending`
* `blocked`
* `denied`
* `cancelled`
* `failed`
* `ambiguous`
* `reconciliation_required`
* `no_op`
* `unknown`

The `status` dimension is what the user-facing API MUST return. No "تم التنفيذ"
is emitted unless `status == success` and `final == FinalStatus.SUCCESS`.

---

## 4. Security states (plan §43)

* `untrusted` – raw input / model output, not yet screened
* `screened` – input passed schema + screening
* `authorized` – policy/approval passed
* `denied` – policy explicitly denied
* `step_up_required` – MFA / elevated approval required
* `revoked` – approval/policy revoked after initial allow
* `quarantined` – held for manual/security review

---

## 5. Transition table

The table is defined in `CANONICAL_TRANSITIONS` (module) as a mapping of
`(current_stage, event) -> (next_stage, status, security, final)`. The
transitions below summarize the full lifecycle. Each transition records:

* `timestamp`
* `event`
* `from_stage` / `to_stage`
* `status`, `security`, `final`
* `actor` (who/what triggered: `system`, `user:<id>`, `lease`, `policy`, …)
* `reason` (human-readable)
* `evidence_id` (link into evidence graph)
* `trace_id`, `execution_id`, `action_id`

### Happy path (write)

```text
idle
  └─ user_message_received ──▶ listening
      └─ intent_parsed ──▶ thinking
          └─ plan_built ──▶ planned
              └─ security_screened ──▶ policy_checking
                  └─ policy_evaluated ──▶ authorized
                      └─ proposal_issued ──▶ awaiting_confirmation
                          └─ confirmation_approved ──▶ authorized
                              └─ lease_granted ──▶ lease_acquired
                                  └─ execution_started ──▶ executing
                                      └─ verification_started ──▶ verifying
                                          └─ verification_passed ──▶ completed
                                              └─ success_confirmed ──▶ success [FINAL]
```

### Read / informational

```text
... thinking
  └─ plan_built ──▶ planned ──▶ policy_checking (read path goes direct-to-authorized)
  └─ conversation_only ──▶ no_op [FINAL]
```

### Failure paths

```text
executing ── execution_transient_failure ──▶ retrying ── execution_started ──▶ executing
executing ── execution_hard_failure ──▶ failed
executing ── ambiguous_outcome ──▶ ambiguous ── reconciliation_started ──▶ reconciliation_required
  ├─ reconciliation_adopted ──▶ completed ── success_confirmed ──▶ success
  └─ reconciliation_requires_manual ──▶ unknown(FINAL, reconciliation_required)
```

### Approval lifecycle

```text
awaiting_confirmation
  ├─ confirmation_declined ──▶ cancelled [FINAL]
  ├─ confirmation_approved ──▶ authorized
  ├─ proposal_expired ──▶ blocked (security=revoked)
  ├─ proposal_edited ──▶ planned (security=screened)  ← full revalidation
  └─ approval_revoked ──▶ rejected [FINAL]
```

### Security denials

```text
policy_checking ── security_denied ──▶ rejected [FINAL]
policy_checking ── blocked_by_policy ──▶ blocked (security=denied)
policy_checking ── step_up_requested ──▶ blocked (security=step_up_required)
* ── quarantine ──▶ blocked (security=quarantined)
```

### Invalid transitions

Any event not listed in `CANONICAL_TRANSITIONS` for the current stage raises
`InvalidTransition`. Once `is_terminal` is true (final status is set), no
further events are accepted — the execution's lifecycle is closed.

---

## 6. Projections

Adapters map canonical state to existing legacy shapes so existing code keeps
working while we migrate.

| Adapter | Maps to | Used by |
|---|---|---|
| `project_to_gateway_status(state)` | Legacy gateway status strings (`accepted`, `denied`, `confirmation_required`, `in_progress`, `erp_error`, `declined`, `reconciliation_required`) | `gateway.py`, `web_server.py`, `responder.py` (during migration) |
| `project_to_proposal_state(state)` | Legacy `proposals.state` values (`proposed`, `confirmed`, `executing`, `completed`, `failed`) | `confirmation.py` projection (during migration) |

The frontend will switch to rendering from `state.to_public_dict()` directly
(Phase 10), at which point these adapters shrink and eventually disappear.

---

## 7. What this fixes relative to plan §7.A–C

| Gap | How the canonical machine addresses it |
|---|---|
| §7.A State fragmentation | One (stage, status, security, final) tuple replaces seven parallel state enums. Adapters provide backward-compatible projections. |
| §7.B Frontend timer truth | The frontend subscribes to `ExecutionEvent`s and renders `state.stage` directly. Client countdown timers only TICK the proposal expiry; they never move the pipeline to a new stage. Stage progression requires a backend event. |
| §7.C Proposal editing | `PROPOSAL_EDITED` returns to `PLANNED` with `security=SCREENED`, forcing policy, schema, idempotency, and hash to be re-derived (never silently reusing an old approval). |
| §7.J Idempotency TTL vs lease | TTL on idempotency reservation and execution lease are now SEPARATE concepts; lease carries owner/heartbeat/expiry/payload_hash. |
| §7.K Proposal lifecycle | Explicit: proposed → awaiting_confirmation → authorized (post-approve) → executing → … with every transition auditable. |
