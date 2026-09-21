# Decision Layer (Jev) — architecture, operations, and evidence

> **Status: POC-verified on synthetic providers. Not production-ready, and not a
> claim about Jev.** Every number in this document comes from a committed run
> artifact under `03-poc-src/data/reports/`; nothing here is estimated.

## 1. What this is (and what it is not)

MIZAN is the authority. The decision layer is a **signal**, never an authority:
it can make MIZAN *more* careful and can narrow the option space handed to the
language model, and it can never authorize, execute, approve, verify, widen
permissions, choose identity, or bypass a single server-side check.

| Layer | Owns |
|---|---|
| LLM | understanding, drafting, proposing a tool call |
| **Decision layer (Jev)** | **a fast, typed, second opinion: which tool, is this ambiguous, does this look like an injection, how risky does this read, does the outcome match the claim** |
| MIZAN server | identity, tenant, authorization, policy, approval, verification, idempotency |
| ToolGateway | the final authority boundary — every write still traverses policy → risk → confirmation → execution → verification |
| ERP (Odoo) | business truth |

Two rules make that safe in practice:

* **Monotonic safety.** A decision signal may raise escalation, never lower it.
  `R4 → R2` is impossible; a write that requires confirmation still requires it;
  a narrowed tool set can only ever be a *subset* of the server-owned registry.
* **Fail-as-value.** A provider outage, timeout, rate limit, malformed answer, or
  budget overrun degrades to "no signal" — the turn proceeds on the existing
  deterministic path. A Jev outage can never become a MIZAN outage.

## 2. Modes

| `--jev-mode` | Effect | Gate section |
|---|---|---|
| `off` (default) | not built, not called; identical to the un-integrated baseline | — |
| `shadow` | provider is called, the signal is recorded (evidence + report), **nothing changes** | `decision_shadow` |
| `advisory` | may narrow the offered tool set, may escalate, may ask for clarification; deterministic policy still decides what runs | `decision_advisory` |
| `enforcing` (experimental) | like advisory, with the stricter gate set; still cannot authorize anything | `decision_enforcing` |

Selection is explicit and two-axis: `decision.provider` chooses the client,
`decision.mode` chooses whether it may act. Both default to `off`, and a key
sitting in the environment never switches either one on.

## 3. Code map

```
03-poc-src/poc/decision/
  protocol.py     DecisionClientProtocol (decide(state, questions) -> DecisionResult)
  models.py       QuestionType, DecisionMode, error codes, Choice/Score/Noul answers,
                  canonical JSON, compute_state_hash (spec + model + state → sha256[:32])
  questions.py    versioned question sets (routing, risk, post-run) + allowlisted state builders
  redaction.py    RedactionPolicy, DecisionStateBuilder (allowlist + default deny), mask_pii
  thresholds.py   DecisionThresholds — every number the code branches on (v1.1.0)
  jev_client.py   thin httpx client for POST /v1/systemone (no SDK dependency)
  mock_client.py  deterministic synthetic provider + scripted scenarios/faults
  profiles.py     mock profiles (oracle/realistic/adversarial), scenario document, split loader
  policy.py       escalation ladder, route plan, disagreement classification
  router.py       DecisionRouter: one screening pass per turn, evidence, telemetry, cache
  telemetry.py    counters + snapshots (secret-free)
  cache.py        read-routing only, default off, key = state+spec+model+thresholds+questions
```

Nothing outside `poc/decision/` imports a provider SDK, and only
`poc/bootstrap.py` constructs the router.

## 4. Questions and state

Three question types, one judgement each, answered in parallel: **choice** (tool
routing, with probabilities and confidence), **noul** (ambiguity, injection,
claim-match, trace-anomaly — a probability with no separate confidence), and
**score** (semantic risk, ordered levels 2–10). Six question ids are versioned as
decision spec `1.0.0` (hash `399dff96e3e07c77` in the report provenance).

State is built by allowlist, never by dumping a record: `build_routing_state`
sends the utterance (PII-masked, 4000-char cap), the language/channel hints, and
the *server's* tool list with read-only flags. `build_risk_state` sends the
argument *shape* (keys/types), not the payload. Never sent: credentials, cookies,
tokens, the audit chain, internal prompts, tenant secrets, unrelated ERP records,
or anything whose key is not on the allowlist.

## 5. Thresholds and calibration

`DecisionThresholds` v1.1.0 carries every branching number, and every report
records its `threshold_fingerprint`:

| threshold | value | meaning |
|---|---|---|
| `route_min_confidence` | 0.85 | below this the signal may not narrow |
| `route_min_margin` | 0.50 | top-1 minus top-2 must clear this too |
| `ambiguity_trigger` | 0.60 | at/above → ambiguity flagged |
| `injection_quarantine` / `injection_flag` | 0.90 / 0.70 | quarantine / watch bands |
| `risk_escalate_level` | 3.0 | at/above → escalation (watch band = −0.5) |

Calibration is a sweep, not a guess: `scripts/calibrate_decision.py` runs a grid
over a split and ranks candidates by *(no failed golden cases, then most useful
narrowing, then the most conservative threshold)*. On the **calibration** split
with the synthetic `realistic` profile, confidence 0.85 and 0.90 were equally
safe with 37.31 % vs 35.82 % narrowing, so the more conservative 0.85 was kept
(`data/decision-calibration.json`, `data/reports/decision-calibration.md`).

**This calibrates the machinery, not Jev.** A real-provider calibration run on
the calibration split is a prerequisite before quoting any routing threshold as
tuned; the held-out split is the only source of cited numbers.

## 6. Evaluation

Same golden dataset as everything else — 144 cases, no toy benchmark. Every
execution records: case id/category, expected tool/outcome, the LLM's tool, the
Jev tool, confidence, top probabilities, top-two, ambiguity/injection/semantic
risk answers, decision latency, final route strategy, candidate tools, gateway
status, escalation level, disagreement resolution, fallback/error code.

* dataset split: `tests/decision_split.json` — 64 calibration / 28 validation / 52
  held-out, stratified so every category appears in all three;
* synthetic provider profiles: `tests/decision_scenarios.json` — oracle (no
  faults), realistic (confident-wrong, mid/low confidence, missed injections),
  adversarial (negative control);
* generator: `scripts/build_decision_artifacts.py` (deterministic, `--check`);
* comparison: `scripts/compare_decision_reports.py` →
  `data/reports/decision-comparison.{json,md}`.

### Results (synthetic provider — machinery check, not a Jev claim)

| run | verdict | route acc | abstention prec | ambiguity P/R | injection P/R | esc security P/R | disagreement | narrowing | quarantines |
|---|---|---|---|---|---|---|---|---|---|
| baseline (off) | PASS | — | — | — | — | — | — | — | — |
| shadow | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | 0 % | 0 |
| advisory | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | 27.45 % | 18 |
| advisory @validation | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | — | 4 |
| advisory @held-out | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | 22.81 % | 6 |
| enforcing | PASS | 100 % | 100 % | 100/100 | 100/100 | 100/100 | 0 | 27.45 % | 18 |
| realistic (negative control) | GATE FAIL | 95.65 % | 100 % | 100/100 | 100/100 | 100/100 | 2 | 31.30 % | 8 |
| adversarial (negative control) | FAIL | 61.05 % | 94.54 % | 100/33.33 | 100/72.22 | 100/68.42 | 27 | 26.47 % | 13 |

Governance parity holds in **every** run: unauthorized writes 0, duplicate orders
0, audit coverage 100 %, audit chain valid, policy enforcement 100 %. The
decision path added no unauthorized write, no duplicate, and never lowered a
deterministic risk level. Mock decision latency: p50 ≤ 1.1 ms, p95 ≤ 1.4 ms.

The two negative controls are the point of the exercise: with a worse provider
the gates go red (realistic fails on
`narrowed_expected_tool_removed = 2`; adversarial fails 8 gates), which proves
the harness can see a bad signal rather than flattering it.

## 7. Running it

```bash
cd 03-poc-src

# offline, deterministic, zero cost (the CI path)
python -m poc.harness run --decision-provider mock --jev-mode advisory --jev-profile oracle
python -m poc.harness run --decision-provider mock --jev-mode shadow   --jev-profile oracle
python -m poc.harness run --decision-provider mock --jev-mode advisory --jev-profile adversarial   # expect red

# calibration sweep (writes data/decision-calibration.json)
python scripts/calibrate_decision.py --profile realistic --split calibration

# comparison artifact (baseline vs shadow vs advisory …)
python scripts/compare_decision_reports.py
```

### Free development setup

`DECISION_PROVIDER=mock` needs no key, no network, and no account: it is the
synthetic provider built into the repository. That is the intended way to
develop against the decision layer, and the way CI runs it.

### Real provider setup

1. Set `DECISION_PROVIDER=typesafe`, `DECISION_MODE=shadow`, `TYPESAFE_API_KEY`,
   and pin `DECISION_MODEL` to a versioned id (`jev-1.13.0`) — an alias can drift
   under a tuned threshold set.
2. Run the golden set in shadow and read the disagreement report before enabling
   advisory. Real-provider runs are the only runs whose numbers may be quoted
   about Jev.
3. Only synthetic or explicitly-approved data: the POC profile always masks PII
   before anything leaves the process, and **real PII must never be sent to a
   free or unofficial endpoint**.

## 8. Enabling/disabling, and rollback

* **Ship-safe default:** `decision.provider=off`, `decision.mode=off` — nothing
  is built, nothing is called, and the baseline run is bit-for-bit the old
  behaviour (proved by the `baseline` run and the off-mode integration tests).
* **Instant rollback:** set `DECISION_PROVIDER=off` (settings panel or env) and
  restart. The router is not constructed; no code path changes.
* **Partial rollback:** `DECISION_MODE=shadow` keeps the call and the recording
  but removes every behavioural effect — useful when a provider misbehaves
  mid-pilot. `--no-gates` is for deliberate stress profiles only and must never
  be used to green a release gate.
* Nothing to migrate: the layer owns no tables of its own; its events live in the
  existing hash-chained evidence log and can be ignored by any consumer.

## 9. Known limitations (honest list)

1. **No live-provider measurement yet.** Every number above is synthetic. The
   vendor documents Jev as strongest in English; MIZAN's traffic is Arabic, so
   Arabic calibration on our data is the first real task, not an assumption.
2. **Deterministic harness caveat.** In deterministic mode the LLM is scripted
   from the expectation, so a narrowed tool set is not re-planned by a real
   model. Shadow/advisory must be re-measured in `--mode live` before any claim
   about routing quality in production.
3. **`enforcing` is experimental** and gated more strictly than it is trusted.
4. **Cache is read-routing only, off by default**; write authorization is never
   cached, and enforcing results are never cached.
5. **Semantic post-run review is opt-in** and can only add a "needs review"
   notice; it cannot alter verified ERP truth.
6. **Cost model** is per screened turn at the provider's token price; the harness
   records input/output tokens per run so cost is derived from measured usage.
