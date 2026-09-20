# ADR-002 — Typed Action Envelope

**Date:** 2026-09-20
**Status:** Accepted
**Modules:** `poc/execution/action.py`

## Context

Plan §10 requires a typed internal action representation carrying action_id,
execution_id, trace_id, actor, intent, entities, operation, arguments, risk,
policy_context, approval, idempotency, and versions. Model-generated values
must never override server-owned identity/policy fields.

## Decision

Introduce a frozen `Action` dataclass plus supporting value types:
`Actor` (user_id/tenant_id/branch_id/role), `Versions`, `RiskAssessment`,
`IdempotencyBinding`. Add a constructor helper `Action.from_tool_call(...)`
for the common post-validation path.

The Action type is *not* wired directly into Gateway in this phase; Gateway
still accepts `ToolGatewayRequest` during the strangler migration. The Action
is constructed by AgentRuntime after validation so that Phase 6+ (policy 2.0,
evidence graph) can consume it.

## Alternatives considered

1. **Continue passing dicts around.** Rejected — no type safety, typos become
   runtime bugs, and identity fields can be silently added/overwritten.
2. **Adopt a full CQRS/command library.** Rejected — plan §99 (dependency
   policy): the dataclass approach covers what we need with stdlib only.

## Consequences

Callers can now pass a single immutable Action object through policy, risk,
approval, lease, execution, and evidence. Identity is frozen at construction.
