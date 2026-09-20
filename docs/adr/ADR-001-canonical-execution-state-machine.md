# ADR-001 — Canonical Execution State Machine

**Date:** 2026-09-20
**Status:** Accepted
**Modules:** `poc/execution/state_machine.py`

## Context

The POC had seven parallel state concepts (CURRENT_STATE §5):
Gateway status, proposal state, idempotency state, AgentResult outcome,
Answer status, audit result_status, and frontend visual state. These can
drift: a frontend timer can say "executing" when the backend has already
failed; an approval can appear approved from stale proposal state while
the idempotency store says CONFLICT. Plan §7.A and §43 require one
canonical lifecycle.

## Decision

Introduce a single `ExecutionState` value with four parallel dimensions
(`stage`, `status`, `security`, `final`) and an explicit event-driven
transition table (`CANONICAL_TRANSITIONS`). All other state representations
become projections through adapter functions such as
`project_to_gateway_status` and `project_to_proposal_state`.

The machine is a pure-Python immutable dataclass: `state.apply(event)`
returns a new state and records a structured `ExecutionTransition` in
`state.history`. Terminal states (where `final` is populated) refuse
further events.

## Alternatives considered

1. **Keep the existing enums and document them.** Rejected — the drift
   problem is structural; documentation does not prevent new drift.
2. **Replace all call sites in one giant refactor.** Rejected — violates
   the strangler pattern (plan §100) and would break 442 tests with no
   intermediate green state.
3. **Use a third-party FSM library.** Rejected — adds a dependency for
   ~200 lines of deterministic logic; `pip install` surface area increases
   supply-chain risk for a small benefit (plan §99).

## Consequences

**Positive:**
- One place to inspect for "is the system truthful about its state?"
- Frontend can render deterministically from `state.to_public_dict()` once
  wired in.
- Harness can assert expected state transitions directly.
- Security-related transitions (DENIED, QUARANTINED, REVOKED) are first-class.

**Negative:**
- Adds a new abstraction that existing code must gradually adopt;
  two projection helpers maintain backward compatibility during the
  migration.
- The transition table must be updated when we add recovery flows
  (compensation, multi-level approval); every addition needs tests.

## Security implications

A single state machine makes it possible to assert system-wide invariants
such as "no SEND/EXECUTE transition ever fires before AUTHORIZED" and
"no SUCCESS final without VERIFICATION_PASSED event in history". This
shrinks the trusted code base for the truthfulness contract.

## Migration implications

Existing `GatewayResult.status` and `proposals.state` keep working via
adapters. Callers (gateway, agent runtime, web server, harness) will be
migrated one at a time in subsequent commits, each accompanied by tests
proving the projection is consistent with the new machine.
