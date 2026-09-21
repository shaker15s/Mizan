# Jev (TypeSafe System One) — evaluation for MIZAN

**Date:** 2026-09-21 · **Status:** POC evaluation, synthetic-provider verified
**Related:** `docs/DECISION_LAYER.md` (architecture and operations),
`03-poc-src/data/reports/decision-comparison.md` (run artifacts)

## 1. Question

MIZAN already governs every consequential action: identity, authorization,
policy, confirmation, idempotency, verification, and a hash-chained audit trail
are server-owned and deterministic. A language model proposes; the gateway
decides. The open question this evaluation answers is narrower:

> Can a fast, typed, *second* model — Jev (TypeSafe System One) — add useful
> signal to MIZAN's decisions (which tool, is this ambiguous, is this an
> injection, how risky is this, does the outcome match the claim) **without ever
> becoming an authority**?

## 2. What Jev is (as documented by the vendor, 2026-09)

* One endpoint, one request shape: `POST https://api.typesafe.ai/v1/systemone`
  with `{state, model, questions}`; the response carries `answers` and
  `usage{input_tokens, output_tokens}` (`docs.typesafe.ai/api`).
* Three question types (`docs.typesafe.ai/primitives`):
  **noul** → a probability with no separate confidence;
  **choice** → a choice plus a normalised probability distribution and a
  confidence; **score** → an ordered level plus distribution and confidence.
  One snap judgement per question; questions are independent and can be asked in
  parallel; question ids never leave the caller.
* Confidence is derived from the *shape* of the distribution, not a self-report
  (`docs.typesafe.ai/confidence`) — a low-confidence answer is an honest "I don't
  know", which is exactly what a router or a clarification gate needs.
* Current model `jev-1.13.0` (aliases `jev-latest`/`jev-preview` drift, so a
  pinned versioned id is required once thresholds are tuned); 64k tokens per
  request (32k for state + longest question); published price ≈ $0.042 per 1M
  input tokens with output not billed; vendor-reported latency 70–500 ms.
* The vendor documents the model as strongest in English, and warns about
  *jaggedness* — counting, date comparison and drafting-style tasks are known
  weak spots. MIZAN's traffic is Arabic, so Arabic behaviour is something to
  measure on MIZAN data, not inherit from a benchmark.

## 3. Design consequences taken from those facts

| Vendor fact | Decision taken in MIZAN |
|---|---|
| choice + distribution + confidence | tool routing uses a `choice` question, and the gate requires **both** a confidence floor *and* a top-1/top-2 margin |
| noul has no confidence | ambiguity, injection, claim-match and trace-anomaly use `noul`; the extra `confidence` field some providers add is recorded as *unexpected* and never used as a threshold input |
| confidence is distribution-shaped | "don't act on low confidence" is implemented as *narrowing is skipped*, never as "the tool is wrong" |
| id-style drift and English bias | `decision_spec_version` + pinned model id + threshold fingerprint are recorded with every decision; state is Arabic text with the *server's* tool list, and every threshold is a calibration output, not a vendor default |
| 64k context, 32k state | state is capped (4000 chars of masked utterance, argument *shape* not payload) — the cap is a redaction control first, a budget second |
| latency budget | the client has a per-call timeout and a total per-turn budget; a timeout is a recorded fallback, never a blocked turn |

## 4. Boundary rules (non-negotiable)

1. The decision layer is **signal-only**. It cannot authorize, execute, approve,
   verify, widen permissions, choose a tenant/user, or touch the ERP.
2. **Monotonic safety.** A signal may increase conservatism; it can never reduce
   it. `R4 → R2`, `confirmation_required → none`, or a narrower *authorization*
   set are all impossible by construction — the layer has no such field.
3. **Fail-as-value.** Outage, timeout, rate limit, malformed or over-budget
   answers degrade to "no signal" and the turn continues on the deterministic
   path. A Jev outage can never become a MIZAN outage.
4. **No secrets, no real PII.** State is built by allowlist; credentials,
   tokens, cookies, audit chains, internal prompts and customer records never
   leave the process. Real PII must never be sent to a free or unofficial
   endpoint; the POC profile is synthetic-only.

## 5. What was measured (and why it is labelled synthetic)

The decision layer ships with a deterministic, zero-network **mock provider**
that can be given a scripted quality profile. That is the only provider used in
the numbers committed to this repository, and every artifact says so:

* `oracle` — no faults: proves the mechanism and the harness plumbing;
* `realistic` — confident-wrong routes, mid/low confidence, missed injections and
  ambiguities: the profile used for threshold calibration;
* `adversarial` — a hostile provider: the negative control that must make the
  gates fail. It does: 8 gates red.

Results (144 golden cases; full tables in `docs/DECISION_LAYER.md` §6 and
`03-poc-src/data/reports/decision-comparison.md`):

* shadow and advisory reconcile to the baseline: identical governance counters
  (0 unauthorized writes, 0 duplicates, 100 % audit coverage, chain valid,
  100 % policy enforcement) and 0 failed golden cases on the oracle profile;
* advisory narrowed the offered tool set on 27.45 % of executions, quarantined 18
  injection-shaped turns, and produced 100 % precision/recall on ambiguity,
  injection and security escalation ground truth;
* held-out split (52 cases, never used for tuning): identical verdict, 22.81 %
  narrowing;
* disagreement between the signal and the model's own choice was recorded 0
  times on the oracle profile, 4 on realistic and 27 on adversarial — always as
  a recorded resolution, never silently discarded;
* **cost of the decision call in the synthetic profile is $0** and the measured
  mock latency is p50 ≤ 1.1 ms / p95 ≤ 1.4 ms; a live provider's latency and
  token cost would replace those figures only after a real run.

## 6. What is *not* claimed

* No claim about Jev's accuracy, latency, or Arabic behaviour. Those require a
  live-provider run with a key, on MIZAN's own data, starting in shadow.
* No claim of production readiness: `enforcing` is experimental, and the
  deterministic harness scripts the LLM from the expectation, so a narrowed tool
  set is not re-planned by a real model there.
* No claim that the calibration thresholds (v1.1.0) are tuned for Jev. They are
  tuned for the machinery and are explicitly marked as such
  (`data/decision-calibration.json` → `caveats`).

## 7. Recommendation

Adopt the decision layer as an **optional, off-by-default advisory signal**, with
the following sequence before any production use:

1. Live shadow run on real (approved) traffic; review the disagreement report.
2. Re-calibrate on the calibration split against the live provider and pin the
   model id; validate on the held-out split.
3. Only then consider `advisory`, and treat `enforcing` as a separate,
   evidence-gated step it has not yet earned.
