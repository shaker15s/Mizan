# Decision Intelligence (Jev) — final delivery report

**Date:** 2026-09-21 · **Commit:** `6b10c12` + uncommitted decision-layer work
**Companion documents:** `docs/DECISION_LAYER.md` (architecture/ops),
`00-research/jev-evaluation.md` (evaluation), `docs/TRACEABILITY_MATRIX.md`
(R-DEC-01…16), `evidence/EVIDENCE_MANIFEST.json` (EV-006).

This report answers the sixteen required items in order. Every number below is
taken from a committed run artifact; nothing is estimated, and every synthetic
result is labelled as synthetic.

---

## 1. Architecture summary

MIZAN keeps four separate roles. The LLM understands, drafts, and proposes. The
**decision layer** adds a fast, typed second opinion — which tool, is this
ambiguous, does this look like an injection, how risky, does the outcome match
the claim. The **MIZAN server** owns identity, tenant, authorization, policy,
approval, verification and idempotency. The **ToolGateway** is the final
authority boundary and the **ERP** holds business truth.

```
turn ─▶ decision screen (optional, fail-as-value)
        ├─ evidence: DECISION_REQUEST → DECISION_RESPONSE → DECISION_ROUTING/ESCALATION
        ├─ route plan: narrow the offered tools (advisory/enforcing only, subset of the registry)
        └─ escalation: none < watch < step_up < clarify < quarantine (raise-only)
      ─▶ LLM proposes a tool call ─▶ gateway: policy → risk → confirmation → execute → verify
      ─▶ disagreement recorded (never silently discarded) ─▶ outcome
```

Safety properties that hold by construction: the layer has no field that could
authorize, execute, approve, verify, widen permissions or pick identity;
escalation is monotonic; narrowing is a subset; provider failures become "no
signal". Thirteen modules under `03-poc-src/poc/decision/`, wired once in
`poc/bootstrap.py` and once in the harness.

## 2. Files changed

Tracked files modified (21, +1126/−47 as of this report):

| File | Why |
|---|---|
| `poc/settings.py` | 28 typed `decision.*` settings (provider, mode, model, thresholds, redaction caps) — one configuration system, no env-only switches |
| `poc/agent_runtime.py` | screens a turn, applies route/escalation, records disagreement, exposes `DECISION_*` evidence; 10 early-return paths carry the decision so it is never lost |
| `poc/bootstrap.py` | `build_decision_router_from_settings`; `build_runtime(..., decision_router=None)`; one shared `EvidenceStore` for gateway + router |
| `poc/web_server.py` | read-only `GET /api/decision` health (secret-free, never 500s) |
| `poc/evidence.py` | 5 new `EvidenceType` members |
| `poc/execution/state_machine.py` | state-machine version 1.1.0 for the quarantined/clarification outcomes |
| `poc/responder.py` | truthful user-facing copy for decision outcomes |
| `poc/harness/{cli,runner,records,metrics,graders,report}.py` | decision flags, per-execution decision record, decision metrics, honest graders, per-mode gates, decision criteria |
| `poc/harness/render/{console,markdown}.py` | decision section labelled "signal only / not a Jev measurement" |
| `tests/eval_thresholds.json` | `decision_any` / `decision_shadow` / `decision_advisory` / `decision_enforcing` gate sections |
| `.github/workflows/ci.yml` | offline mock-provider runs on every push — see the note below |
| `03-poc-src/.env.example` | documented opt-in block (off by default; free-dev and real-provider recipes) |
| `docs/CURRENT_STATE.md`, `docs/TRACEABILITY_MATRIX.md`, `evidence/EVIDENCE_MANIFEST.json` | refreshed truth + R-DEC rows + EV-006 |

New files:

| File | Why |
|---|---|
| `poc/decision/{protocol,models,questions,redaction,thresholds,jev_client,mock_client,profiles,policy,router,telemetry,cache,__init__}.py` | the layer itself (~4.4k LOC): provider-neutral contract, versioned questions, allowlist redaction, calibrated thresholds, thin httpx client, deterministic mock provider, escalation/route/disagreement policy, orchestration |
| `poc/harness/decisions.py` | decision evaluation: §31 per-case record, decision/system/safety metrics, split filtering, run provenance |
| `scripts/build_decision_artifacts.py` | deterministic generator (`--check`) of the split + scenario documents |
| `scripts/calibrate_decision.py` | threshold sweep with conservative tie-break |
| `scripts/compare_decision_reports.py` | baseline vs shadow vs advisory comparison artifact |
| `tests/decision_split.json`, `tests/decision_scenarios.json` | 64/28/52 stratified split; 144 scripted cases × 3 profiles |
| `tests/test_decision_layer.py` (66), `tests/test_harness_decision.py` (19) | contract/safety/integration tests; harness honesty tests |
| `docs/DECISION_LAYER.md`, `00-research/jev-evaluation.md`, this report | documentation that would let another engineer operate and audit the layer |

## 3. Why each change exists

Grouped by the plan sections they satisfy: boundary and invariants (no authority
field, raise-only escalation, gateway untouched); provider neutrality (protocol +
hand-rolled httpx client instead of the vendor SDK, whose `typesafe-sdk` pulls an
incompatible `httpx2` transport); determinism (mock provider, scripted profiles,
zero network); observable truth (evidence events, decision metrics, disagreement
records); honesty (synthetic labelling, N/A never zero, armed-refusal gate);
operability (settings, health endpoint, CI, rollback).

## 4. Test results

```text
cd 03-poc-src && .venv/bin/python -m pytest tests/ -q
587 passed, 5 skipped in 25.93s
```

The 5 skips are the live-Odoo integration tests (auto-skip without an ERP
instance). New coverage: 66 decision-layer tests (parsing, mock provider,
monotonic safety, redaction, cache, router failure paths, artifacts, runtime
integration) and 19 harness tests (metric honesty, gate sections, rendering,
synthetic labelling, a real 4-case mock run, and the armed-refusal test).

> **CI.** `.github/workflows/ci.yml` now runs, after pytest: artifact `--check`,
> `harness doctor`, then mock advisory and mock enforcing (`--no-repeat --format json --quiet`).
> These are machinery gates (one pass per golden case), not a 3× stability
> measurement, and they are deterministic / offline / zero-cost. Pushing the
> workflow file still requires the GitHub `workflows` permission; if the App
> cannot update `.github/workflows/**`, apply the same YAML as a human.
>
> Frontend provenance (health chip + per-turn `authority: signal_only` badge) is
> now on both cockpits: vanilla `poc/web/` and canonical `03-poc-src/frontend/`.

## 5. Harness results

```text
baseline (decision off)          144/144 PASS · 0 failing · exit 0
shadow (oracle)                  144/144 PASS · exit 0
advisory (oracle)                144/144 PASS · exit 0
advisory (held-out split)        144/144 PASS · exit 0   (52 cases scored)
enforcing (oracle, experimental) 144/144 PASS · exit 0   (20 gates, 0 failing)
realistic (negative control)     GATE FAIL · exit 3      (narrowed_expected_tool_removed = 2)
adversarial (negative control)   FAIL · exit 1           (5 case failures, 8 gates red)
doctor: 17 checks ok · 1 warning (no provider env) · 0 blockers
```

The red runs are deliberate negative controls and are *not* relaxed: a
mid-quality provider fails one gate, a hostile provider fails eight.

## 6. Jev results

**None are claimed.** No live-provider run has been executed, so this report
contains no Jev accuracy, latency, or Arabic-behaviour figure. What is measured
is the machinery against scripted providers of known quality, and the artifacts
say so (`synthetic: true`, plus a "NOT a Jev measurement" banner in the console
and markdown renderers). The vendor's own documentation (English-strongest,
known jaggedness on counting/date/drafting) is recorded in
`00-research/jev-evaluation.md` and drove the design — it is not a substitute for
measurement.

## 7. Baseline vs Jev comparison

From `03-poc-src/data/reports/decision-comparison.{json,md}` (synthetic mock):

| run | verdict | route acc | abstention prec | ambiguity P/R | injection P/R | esc security P/R | disagreement | narrowing |
|---|---|---|---|---|---|---|---|---|
| baseline | PASS | — | — | — | — | — | — | — |
| shadow | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | 0 % |
| advisory | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | 27.45 % |
| advisory @held-out | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | 22.81 % |
| enforcing | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | 27.45 % |
| realistic (control) | GATE FAIL | 95.65 % | 100 % | 100/100 | 100/100 | 100/100 | 2 | 31.30 % |
| adversarial (control) | FAIL | 61.05 % | 94.54 % | 100/33.33 | 100/72.22 | 100/68.42 | 27 | 26.47 % |

Governance parity holds in every run — unauthorized writes 0, duplicate orders 0,
audit coverage 100 %, chain valid, policy enforcement 100 % — and the decision
path's own safety counters are zero apart from intended quarantines
(18 oracle / 6 held-out / 8 realistic / 13 adversarial).

## 8. Latency comparison

| run | read p95 | write p95 | decision p50 | decision p95 | decision max |
|---|---|---|---|---|---|
| baseline | 18.30 ms | 67.10 ms | — | — | — |
| shadow | 20.10 ms | 62.10 ms | 1.00 ms | 1.30 ms | 1.70 ms |
| advisory | 22.30 ms | 70.40 ms | 1.00 ms | 1.40 ms | 2.70 ms |
| enforcing | 20.50 ms | 69.40 ms | 1.00 ms | 1.30 ms | 2.00 ms |

These are **mock-provider** numbers: they measure the layer's own overhead
(≤ 2.7 ms worst case in-process), not a network call. The vendor documents
70–500 ms for the real endpoint; a live shadow run is what would put a real
figure in this table.

## 9. Cost model

* Offline (mock) runs: **$0**. No key, no network, no tokens — this is what CI
  runs and what a developer uses.
* Live runs: one request per screened turn. At the vendor's published
  ≈ $0.042 per 1M input tokens with output unbilled, a screened turn costs
  ≈ $0.00003–$0.0001 at MIZAN's state sizes (utterance + 5 tool descriptions,
  well under the 32k state budget). The harness records measured
  `decision.tokens` per run so a live cost is derived from real usage rather
  than this arithmetic.
* Cost controls already in code: an allowlisted, capped state; a per-call timeout
  and total per-turn budget; retries capped at 1; a read-routing-only cache
  (default off, never for writes or enforcing results); downgrade-to-no-signal on
  any failure.

## 10. Calibration summary

`DecisionThresholds` v1.1.0, calibrated on the **calibration** split (64 cases)
with the synthetic `realistic` profile over a 4×3 grid
(`scripts/calibrate_decision.py`, artifact `data/decision-calibration.json`):

* 12/12 candidates produced zero failed golden cases;
* narrowing was 37.31 % for confidence 0.75–0.85 and 35.82 % at 0.90, so 0.90
  bought nothing;
* the tie-break rule (safe → useful → most conservative) therefore shipped
  **route_min_confidence 0.85, route_min_margin 0.50**, with ambiguity 0.60,
  injection 0.90/0.70, risk escalate 3.0.
* The **validation** split (28 cases) then ran PASS — 100 % route/abstention,
  100 % ambiguity/injection/escalation, safety counters all zero, 4 quarantines.
* The **held-out** split (52 cases) also ran PASS with 22.81 % narrowing,
  confirming the thresholds did not overfit the split they were chosen on.
* Every report records the thresholds' `threshold_fingerprint` so a run can never
  be quoted against the wrong threshold set.

## 11. Security findings

* **No new authority surface.** The layer's payloads are explicitly
  `authority: signal_only`; the gateway, policy engine, confirmation flow and
  verification are untouched, and the baseline run is bit-identical in behaviour.
* **Monotonic safety verified by tests and by run counters**: zero unauthorized
  writes, zero duplicate orders, zero `escalation_lowered_deterministic_risk`,
  and `narrowed_expected_tool_removed = 0` on every non-negative-control run.
* **Redaction is allowlist-first and fails closed**: unknown state keys raise,
  forbidden literals raise at build time, PII shapes (emails, 12+ digit numbers,
  IBAN-like) are masked before anything leaves the process. Health and telemetry
  payloads were checked to contain no key material.
* **No silent disarm.** Requesting the layer without a usable provider now warns
  on stderr and fails the new `decision_layer.enabled` gate (verified: a
  keyless `typesafe` run exits 3 with the refusal reason recorded).
* **Residual risks:** the decision layer increases the number of components on the
  hot path (mitigated by fail-as-value and budgets); `enforcing` is experimental
  and gated more strictly than it is trusted; the mock provider is a test double
  and must never be mistaken for evidence about a real provider.

## 12. Free-development setup

```bash
cd 03-poc-src && .venv/bin/python -m poc.harness run \
    --decision-provider mock --jev-mode advisory --jev-profile oracle
```

No key, no network, no cost. `shadow` records without acting; `--jev-profile
adversarial` is the deliberate red run used to prove the gates bite. The typed
settings panel exposes the same switches (`decision.provider`, `decision.mode`).

## 13. Real-provider setup

1. `DECISION_PROVIDER=typesafe`, `DECISION_MODE=shadow`,
   `TYPESAFE_API_KEY=…`, `DECISION_MODEL=jev-1.13.0` (pin a versioned id — an
   alias can drift under a tuned threshold set).
2. Nothing else changes: no code path, no dependency (the client is ~1 KB of
   httpx, not the vendor SDK).
3. Start in shadow, read the disagreement report, then re-calibrate on the
   calibration split with the live provider, validate on held-out, and only then
   consider `advisory`. Synthetic or explicitly approved data only — real PII
   must never be sent to a free or unofficial endpoint.

## 14. Rollback instructions

* **Full:** set `DECISION_PROVIDER=off` (env or settings panel) and restart. The
  router is not constructed; behaviour is the pre-integration baseline.
* **Partial:** `DECISION_MODE=shadow` keeps recording but removes every
  behavioural effect.
* **Emergency:** `decision.mode=off` alone is enough to silence the layer even
  with a provider configured (both axes must opt in) — no deploy required.
* No data migration is involved: the layer owns no tables; its events are rows in
  the existing hash-chained evidence log and can be ignored by any consumer.

## 15. Known limitations

1. No live-provider measurement (see §6 and §8). Arabic behaviour on Jev is
   unmeasured; the vendor documents English as strongest.
2. Deterministic mode scripts the LLM from the expectation, so narrowed tool sets
   are not re-planned by a real model; live-mode shadow/advisory must be measured
   before any routing-quality claim.
3. `enforcing` is experimental and not recommended for production pilots.
4. The threshold set is *machinery-calibrated*; it is explicitly marked as not
   tuned for Jev.
5. The cache covers read-routing only and is off by default; write authorization
   is never cached.
6. Post-execution semantic review is opt-in, can only add a "needs review" notice,
   and is never a source of ERP truth.
7. The harness's own decision metrics depend on scripted ground truth; they are
   only as good as the scenario document, which is why the negative-control
   profiles exist and are kept red on purpose.

## 16. Next engineering priorities

1. Live shadow run (approved data), then disagreement review and a live
   calibration on the calibration split, validated on held-out.
2. Arabic question-design study against the vendor's documented weak spots
   (counting/date/drafting kept out of question design).
3. Frontend surfacing of decision provenance (health panel + per-turn badges)
   behind the existing read-only API, keeping the "signal only" label.
4. Promote `enforcing` only after its stricter gates pass on live data across
   repeated runs.
5. Optional: semantic post-run review enabled by policy for write-heavy tenants,
   with the anomaly counters wired into existing monitoring.
