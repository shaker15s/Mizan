# Technical Design — index

<div align="center">

**This file is a map, not the document.** The full implementation design lives in
[`02-poc/TECHNICAL_DESIGN.md`](02-poc/TECHNICAL_DESIGN.md) (666 lines, kept in sync
with the code). This page exists so a reader landing at the repo root gets the
invariants first and the prose second.

</div>

---

## The one-sentence version

A language model is allowed to *propose* an ERP operation; only a deterministic
gateway is allowed to *perform* it — so the product's safety claims never depend on
the model behaving.

## Request lifecycle (what actually runs)

```
Arabic utterance
   ↓  poc/agent_runtime.py      assemble prompt + tool contracts, one LLM call
   ↓  poc/tool_contracts.py     closed registry (5 contracts), JSON-Schema validation
   ↓  poc/authz.py              users.yaml policy → allowed | confirmation_required | denied
   ↓  poc/confirmation.py       signed proposal (operation hash, TTL) — no write yet
   ↓  poc/idempotency.py        content-addressable key + fingerprint reservation
   ↓  poc/odoo_client.py        JSON-2 over httpx, circuit breaker, server-owned credentials
   ↓  poc/verification.py       read the record back from Odoo and compare
   ↓  poc/audit_store.py        append-only, SHA-256 chain (previous_hash → own_hash)
   ↓  poc/responder.py          Answer object: headline · KPIs · sections · governance
   ↓  poc/web_server.py          REST + SSE; poc/web/ renders the card
```

Every arrow is one-directional on purpose: the answer is a projection of the executed
result, never a second source of truth.

## Invariants (each one is test-pinned)

| # | Invariant | Pinned by |
|---|---|---|
| 1 | A write never executes without a policy decision **and** a consumed signature | `tests/test_verification.py`, `poc/harness/graders.py::check_security` |
| 2 | The same request twice cannot create two records | `tests/test_phase1_regressions.py`, cases TC-044…046 |
| 3 | The audit chain verifies end-to-end, or the turn is not "accepted" | `tests/test_audit_store.py`, `GET /api/audit` |
| 4 | Numbers in a reply exist in the ERP payload — no invented totals | `graders.py::check_numbers_are_real`, `tests/test_harness.py` |
| 5 | Unknown tools are deleted before execution (fail closed) | `tests/test_tool_contracts.py`, case TC-050 |
| 6 | Credentials live server-side; the model never sees a key | `poc/bootstrap.py`, `tests/test_scenario_security.py` (no secret ever reaches the prompt or the audit row) |
| 7 | The registry is exactly 5 contracts; growth is a deliberate edit | `tests/test_tool_contracts.py` |

## Where to read more

- **Evaluation** — [`03-poc-src/HARNESS.md`](03-poc-src/HARNESS.md): modes, graders,
  failure tags, baselines, gates, exit codes.
- **Security model** — [`02-poc/SECURITY_MODEL.md`](02-poc/SECURITY_MODEL.md) (threats,
  trust boundaries, and the POC's honest caveats).
- **Tool contracts** — [`02-poc/TOOL_CONTRACTS.md`](02-poc/TOOL_CONTRACTS.md).
- **Decisions & audit trail** — [`02-poc/DECISIONS.md`](02-poc/DECISIONS.md),
  [`02-poc/FINAL_AUDIT_REPORT.md`](02-poc/FINAL_AUDIT_REPORT.md),
  [`02-poc/POST_AUDIT_HARDENING_REPORT.md`](02-poc/POST_AUDIT_HARDENING_REPORT.md).
- **Cockpit contract** — `03-poc-src/poc/web_server.py` (endpoints) and
  `03-poc-src/tools/frontend_smoke.mjs` (55 headless UI checks, CI-enforced).
- **What changed recently** — [`CHANGELOG.md`](CHANGELOG.md).

## Known limits (do not oversell a POC)

Single process: module isolation is *not* a security boundary — production needs the
gateway in its own trust domain. The tool registry is frozen at five contracts, so
answer richness comes from projection rather than breadth. Read-only control-plane
mode returns `ready`, not `succeeded`, because an accepted decision whose ERP result
was never populated must not look like an execution (audit F-05).
