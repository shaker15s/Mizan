# ADR-003 — Execution Lease separate from Idempotency

**Date:** 2026-09-20
**Status:** Accepted
**Modules:** `poc/execution/lease.py`

## Context

Plan §15 and §7.J require separating idempotency reservation (same semantic
op must not run twice) from execution lease (a particular physical attempt
owns the right to execute, with heartbeat/expiry and payload_hash). The POC
previously piggybacked a 300-second TTL on the pending idempotency record,
which prevents duplicate reservations but conflates two concepts:

* Two different physical workers attempting the same logical op must not both
  proceed — ownership matters.
* Reservation expiry must not be the same thing as execution timeout; retries
  require renewing ownership, not freeing a semantic key.

## Decision

Add `ExecutionLease` and `LeaseStore` (SQLite-backed, same DB file for the
POC) with:

* `lease_id`, `execution_id`, `idempotency_key`, `owner`, `payload_hash`,
  `approval_id`, `action_id`, `issued_at`, `heartbeat_at`, `expires_at`,
  `state` (active/expired/released/completed).
* `acquire` issues a lease for an idempotency_key; existing non-expired
  active leases raise `LeaseAlreadyHeldError`; expired active leases are
  marked expired and superseded.
* `heartbeat` renews `heartbeat_at`/`expires_at`; owner-gated.
* `complete` / `release` transition state; owner-gated.
* `expire_stale()` sweeps expired active leases.

Lease is NOT permission and NOT idempotency — it only identifies ownership
of an execution attempt.

## Alternatives considered

1. **Add owner/heartbeat columns directly to `idempotency_keys`.** Rejected —
   the idempotency table's semantics (semantic de-duplication) differ from
   lease semantics (execution ownership); a failed lease can be released
   without destroying the semantic record. A lease can also expire and be
   renewed by another worker without creating a new semantic execution.
2. **Use Redis / distributed lock.** Rejected — premature (plan §41, §99);
   SQLite suffices for POC and single-process dev; the interface is small
   enough to swap later.

## Consequences

Gateway can now distinguish "no one owns this op" from "someone else is
executing this op," which removes the current auto-renewal heuristic and
enables correct lease/heartbeat semantics before multi-instance or long-
running workflow support.
