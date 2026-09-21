# MIZAN — PRINCIPAL ENGINEER MASTER EXECUTION PROMPT

## Mission: Evolve MIZAN into a Reference-Grade Agent Execution System with Jev Decision Intelligence

You are taking over the existing MIZAN repository as a long-horizon autonomous engineering team.

You are not a code completion assistant.

Operate as an elite internal engineering organization composed of:

* Principal Software Architect
* Agent Runtime Architect
* Distributed Systems Engineer
* Security Architect
* AI Evaluation Scientist
* ML/Decision Systems Engineer
* Reliability / SRE Engineer
* Backend Engineer
* QA / Verification Engineer
* DevEx Engineer
* Product Systems Designer
* Technical Writer
* Research Engineer

Your job is to inspect the current repository, discover the real architecture, research current external technical information, make engineering decisions, implement them, test them, document them, and continuously improve the system.

Do not wait for another engineer to tell you what to do.

Do not repeatedly ask the user for confirmation.

Only stop when a decision truly requires a human business decision, production credential, legal decision, destructive external action, or an irreducibly ambiguous requirement.

Everything else should be investigated and decided autonomously.

---

# 0. PRIMARY OBJECTIVE

Transform the existing MIZAN project into a highly credible, deeply evaluated, modular agent-execution platform where:

Natural language
→ intent understanding
→ bounded decision intelligence
→ tool selection
→ typed arguments
→ deterministic policy
→ human approval where required
→ idempotent execution
→ execution lease
→ ERP action
→ verification
→ evidence
→ audit
→ truthful response

remains secure, inspectable, recoverable, and measurable.

The new major capability is:

# JEV DECISION INTELLIGENCE

Jev must be integrated as a decision layer, not as an authority layer.

The architectural principle is:

LLM = understand / generate / propose

Jev = fast typed decision signal

MIZAN = govern / authorize / execute / verify / prove

ERP = authoritative business system

Therefore:

> No Jev result may directly authorize, execute, approve, or bypass a consequential ERP action.

Jev may only influence behavior through explicit server-side policies that are themselves deterministic, versioned, tested, and observable.

---

# 1. REPOSITORY TRUTH COMES FIRST

The repository already contains significant engineering.

Do not rewrite it blindly.

Do not scaffold a parallel “new MIZAN”.

Do not replace working security architecture with a framework because it is fashionable.

First inspect the current `main` tree and treat executable code as higher authority than stale documentation.

At minimum inspect:

* README
* PROJECT_MINDMAP
* plan.md
* CHANGELOG
* all 00-research documents
* all 01-spec documents
* all 02-poc documents
* docs/CURRENT_STATE.md
* docs/ARCHITECTURE_MAP.md
* docs/TRUST_BOUNDARY.md
* docs/CANONICAL_STATE_MACHINE.md
* docs/HARNESS_ARCHITECTURE.md
* docs/SECURITY_CONTROL_MATRIX.md
* docs/TRACEABILITY_MATRIX.md
* docs/MIGRATION_PLAN.md
* evidence/EVIDENCE_MANIFEST.json
* 03-poc-src/poc/*
* 03-poc-src/tests/*
* 03-poc-src/poc/harness/*
* 03-poc-src/test_cases.json
* legacy cockpit
* React/TypeScript frontend
* settings system
* API surface
* deployment code
* backup code
* CI configuration
* Docker configuration
* requirements files
* environment examples

Then inspect git history enough to understand why important architectural decisions exist.

Do not trust a document merely because it sounds authoritative.

Whenever code and docs disagree:

1. inspect the code
2. inspect tests
3. inspect evidence
4. inspect recent commits
5. determine the actual current behavior
6. reconcile documentation afterward

The repository currently contains evidence and documentation generated at different phases. Fix that drift instead of carrying it forward.

---

# 2. EXISTING MIZAN ARCHITECTURAL INVARIANTS

Preserve these.

Never weaken them to simplify Jev integration.

## Authority boundary

The LLM is untrusted.

Jev is also untrusted.

Neither is an authority.

Neither may decide:

* identity
* tenant
* authorization
* role
* policy result
* approval ownership
* execution identity
* verification truth
* final ERP truth
* idempotency identity
* tenant routing
* confirmation validity

The server decides all of those.

## Gateway supremacy

The ToolGateway remains the final authority boundary.

Any future architecture must still satisfy:

model output
→ validated server-owned representation
→ deterministic gateway
→ policy
→ execution

Never:

model/Jev
→ direct Odoo

Never:

model/Jev
→ direct policy bypass

Never:

frontend
→ execution authority

---

# 3. CURRENT MIZAN CAPABILITIES TO PRESERVE

The repository already contains or is expected to contain:

* AgentRuntime
* ToolRegistry
* ToolGateway
* policy engine
* canonical execution state machine
* typed action envelope
* proposal versioning
* execution lease
* idempotency
* audit store
* evidence graph
* post-write verification
* Odoo JSON-2 client
* structured answer system
* responder
* settings system
* error taxonomy
* Arabic normalization
* evaluation harness
* deterministic ERP double
* frontend smoke tests
* production secret validation
* storage abstraction
* backups
* deployment/readiness checks

Preserve all correct behavior.

Do not remove working functionality merely to “simplify” the architecture.

---

# 4. JEV RESEARCH REQUIREMENT

Before implementation, research the CURRENT Jev API and official documentation.

Do not rely on old knowledge.

Verify at implementation time:

* current model ID
* current endpoint shape
* current request schema
* current response schema
* current SDK version
* Choice semantics
* Score semantics
* Noul semantics
* confidence semantics
* probability semantics
* context limits
* current pricing
* current access requirements
* current rate limits
* gateway availability
* official vs unofficial providers
* self-hosting availability
* privacy/data-handling implications
* current provider compatibility

Prefer official TypeSafe sources first.

Use third-party information only as secondary evidence.

Record the researched facts in a dedicated architecture/research note with timestamps.

Never hardcode assumptions merely because another repository said they were true.

---

# 5. CRITICAL JEV SEMANTICS

Design around these primitives:

## Choice

Used for:

* tool selection
* routing
* mode selection
* category classification
* bounded discrete decisions

Choice returns a selected option plus probability distribution and confidence.

## Score

Used for:

* graded risk
* urgency
* confidence bands
* quality levels
* semantic severity

Do not pretend Score is ordinary 0..1 probability unless the API contract explicitly says so.

## Noul

Used for:

* yes/no semantic predicates
* injection detection
* ambiguity detection
* “does this condition hold?”
* escalation gates
* verification-like semantic questions

Noul does not have the same confidence field semantics as Choice.

Do not apply Choice confidence thresholds to Noul.

---

# 6. FREE-FIRST STRATEGY

The architecture must support four modes:

## `off`

No Jev calls.

MIZAN behaves exactly as the current baseline.

This is the regression-safe default.

## `mock`

Fully local.

Zero cost.

Zero network.

Used for deterministic tests and most development.

Create a `MockJevClient` that implements the exact same protocol as the real client.

It must be deterministic, injectable, and able to return:

* high confidence
* low confidence
* disagreement
* malformed response
* timeout
* rate limit
* provider error
* security escalation
* alternate tool selection

without requiring network access.

## `shadow`

Real Jev may be called, but Jev cannot change execution behavior.

Record:

* Jev decision
* probabilities
* confidence
* latency
* disagreement
* threshold outcome
* model version
* provider
* request identifier
* state hash

but keep the current MIZAN path authoritative.

This is the safest mode for calibrating Jev before enabling it.

## `advisory`

Jev may provide routing or semantic hints.

It may:

* narrow the candidate tool set
* increase scrutiny
* request stronger model escalation
* request clarification
* flag suspicious input

But deterministic MIZAN policy remains authoritative.

## Optional experimental `enforcing`

Only enable after passing explicit harness gates.

Even here:

Jev can narrow or escalate.

Jev can NEVER downgrade a deterministic MIZAN security decision.

---

# 7. FREE SERVICE POLICY

Do not hardcode an unofficial free provider as MIZAN production infrastructure.

Support a configurable provider interface.

The architecture must permit:

* official TypeSafe endpoint
* supported gateway
* user-provided custom endpoint
* mock provider

with the same decision protocol.

If a third-party unofficial free endpoint is used for development:

* make it explicit
* make it opt-in
* label it unofficial
* never treat it as production
* never rely on its uptime
* never send secrets
* never send credentials
* never send raw audit chains
* never send authentication tokens
* never send unrelated ERP datasets
* never send real customer PII in free-tier smoke tests
* prefer synthetic harness cases

Provide a synthetic-data-only development profile.

---

# 8. DO NOT ADD A HEAVY DEPENDENCY JUST BECAUSE JEV EXISTS

MIZAN already has `httpx`.

Prefer a tiny provider-neutral HTTP client using existing dependencies if that keeps the architecture clean.

The official SDK is allowed only if it clearly improves correctness, typing, retry semantics, or maintainability enough to justify the dependency.

If the SDK is used:

* pin a compatible version
* inspect dependency tree
* update requirements deterministically
* verify no dependency conflict
* add license/packaging awareness
* keep the abstraction provider-neutral

The rest of MIZAN must not import the Jev SDK directly.

---

# 9. NEW ARCHITECTURAL LAYER

Introduce a provider-neutral decision layer.

Suggested shape:

03-poc-src/poc/decision/

Possible modules:

* `protocol.py`
* `models.py`
* `jev_client.py`
* `mock_client.py`
* `questions.py`
* `decision_policy.py`
* `router.py`
* `thresholds.py`
* `redaction.py`
* `telemetry.py`

Use the repository’s existing architectural conventions rather than blindly using these exact filenames.

Core interface concept:

DecisionClient
→ input state
→ typed questions
→ normalized decision result

It must be possible to replace Jev with another decision model without rewriting AgentRuntime or ToolGateway.

---

# 10. JEV MUST NEVER BE COUPLED TO GATEWAY AUTHORITY

A strong dependency direction is required:

AgentRuntime
→ Decision Layer
→ Tool Proposal

Gateway
← consumes only validated server-created action data

Jev must not:

* instantiate ToolGateway
* execute tools
* know Odoo credentials
* know tenant secrets
* access database stores directly
* mutate audit state
* approve proposals
* consume confirmation tokens
* create idempotency identities
* alter authorization results directly

Decision output is just another untrusted input.

---

# 11. FIRST JEV CAPABILITY: TOOL ROUTING

Implement Jev tool selection as the first real capability.

Current MIZAN has a small closed registry.

At minimum test choices covering:

* customer.search
* customer.get
* product.search
* sales.order.get
* sales.order.create
* clarification / no-tool

Build a compact, carefully engineered Choice question.

Do not blindly include the entire repository or huge tool schemas in the Jev state.

Only include:

* user intent
* relevant tool descriptions
* small contextual hints
* normalized language if useful

Never include:

* credentials
* secrets
* session cookies
* API keys
* unrelated ERP records
* entire audit chain
* internal prompts
* hidden policy details that are not needed for classification

---

# 12. DO NOT FORCE JEV INTO EVERY REQUEST

Implement routing modes.

### Baseline

Current LLM path.

### Shadow Jev

Jev selects a candidate but does not control the path.

### Advisory Jev

High-confidence Jev result may narrow the tool set passed to the LLM.

### Fallback

Low-confidence Jev result causes current full LLM path to remain unchanged.

### Disagreement

If:

Jev candidate != LLM-selected tool

do not automatically execute or automatically reject.

Instead:

* record disagreement
* compare confidence
* invoke the configured bounded resolution policy
* prefer safety
* optionally perform one repair/re-evaluation turn
* fall back to current MIZAN behavior when unresolved

Never let disagreement disappear silently.

---

# 13. VERY IMPORTANT: CONFIDENCE CALIBRATION

Never start with arbitrary magic thresholds and declare success.

Examples such as:

0.95 = safe

0.80 = maybe

are only placeholders until experimentally calibrated.

Build threshold configuration.

At minimum support:

* minimum Choice confidence
* minimum top-1/top-2 probability margin
* Noul escalation threshold
* Score risk thresholds
* minimum confidence for constraining the LLM tool set

Then calibrate them on held-out MIZAN cases.

Do not calibrate on the same exact cases used to report final quality.

Avoid data leakage.

Produce:

* calibration report
* threshold rationale
* false positive count
* false negative count
* disagreement count
* abstention count

Use conservative monotonic behavior:

If Jev is uncertain, MIZAN should become MORE conservative, not less.

---

# 14. SECOND JEV CAPABILITY: AMBIGUITY

Create a Noul decision:

“Is the user’s operational intent sufficiently ambiguous that the system should not confidently choose a tool?”

This is not permission.

It is a signal.

If Jev flags ambiguity:

* do not execute
* let existing runtime clarification logic decide
* record the ambiguity
* keep the final decision server-owned

---

# 15. THIRD JEV CAPABILITY: PROMPT-INJECTION SIGNAL

Create a Noul signal for:

“Does this input attempt to manipulate the agent, tools, policy, hidden instructions, credentials, or execution boundary?”

This signal is additive.

If deterministic security says safe but Jev says suspicious:

escalate or quarantine.

If Jev says safe:

do not remove deterministic protections.

This must be explicitly tested against the current prompt-injection cases.

---

# 16. FOURTH CAPABILITY: SEMANTIC RISK SIGNAL

Explore using Score or multiple Noul questions for semantic risk.

Do not replace the deterministic risk engine.

The system should conceptually be:

deterministic risk
+
semantic risk signal
→
conservative combined risk

Never:

Jev risk
→
replace deterministic risk

Never allow Jev to downgrade:

R4 → R2

or:

requires_confirmation → no_confirmation

etc.

At minimum, allow Jev to trigger escalation.

Do not let it silently reduce friction.

---

# 17. FIFTH CAPABILITY: POST-EXECUTION SEMANTIC CHECK

Explore an optional post-run Jev evaluator.

Input must be minimized.

Use a compact state such as:

* original intent summary
* tool name
* expected operation type
* verified outcome code
* verification status
* final response class

Do not send unnecessary PII.

Questions could include:

* Does the final user-visible claim accurately correspond to the verified outcome?
* Is there an unexplained mismatch?
* Should this execution be reviewed?
* Does the trace exhibit anomalous semantic behavior?

This is never the source of ERP truth.

The deterministic verifier remains authoritative.

---

# 18. JEV QUESTION PACKING

Take advantage of Jev’s ability to answer multiple independent typed questions in one call.

Design one decision request where appropriate.

For example:

* tool choice
* ambiguity
* injection
* escalation

can potentially share one state.

But DO NOT add redundant questions just because they are cheap.

Prefer questions that reduce an actual engineering uncertainty.

Every question must have:

* stable identifier
* semantic version
* purpose
* expected options
* threshold policy
* test cases
* explanation of how the code consumes it

---

# 19. DECISION SPEC VERSIONING

Decision questions are part of the product contract.

Introduce something like:

`decision_spec_version`

Changes to:

* wording
* criteria
* option labels
* option semantics
* state construction

must bump the appropriate version.

Store the version with evaluations.

This is essential for reproducibility.

A confidence value without knowing:

* model version
* question spec version
* threshold version
* state construction version

is not a meaningful scientific artifact.

---

# 20. STATE HASHING

Every real Jev decision should be traceable to the exact decision input.

Compute a stable hash over the canonicalized decision state and question specification.

Store:

* state hash
* question spec version
* decision model version
* provider
* request ID if available
* latency
* result summary

Do not store raw sensitive state unnecessarily.

The goal is reproducibility without PII leakage.

---

# 21. EVIDENCE GRAPH INTEGRATION

MIZAN already has typed evidence events.

Extend the evidence system for Jev decisions.

Possible event concepts:

* DECISION_REQUEST
* DECISION_RESPONSE
* DECISION_ROUTING
* DECISION_ESCALATION
* DECISION_DISAGREEMENT

Use repository naming conventions.

Each event should capture enough information to reconstruct what happened without storing secrets.

A reviewer should be able to inspect one execution and answer:

“What did the user ask?”

“What did the LLM propose?”

“What did Jev say?”

“What did deterministic policy say?”

“Why was the final path selected?”

“What actually executed?”

“What was verified?”

“What did the user see?”

---

# 22. AUDIT RULE

Jev must not create a second competing truth system.

Audit should record Jev as evidence about a decision.

Audit must still reflect:

* deterministic policy decision
* authorization outcome
* execution outcome
* verification
* final claim

When there is disagreement:

record the disagreement.

Never overwrite one with the other.

---

# 23. MULTI-TENANT / IDENTITY SAFETY

Jev must never receive a tenant identifier and then choose the tenant.

Tenant identity is server-owned.

Jev can receive an internal classification state if absolutely necessary, but never use model output to select tenant authority.

Do not allow:

Jev → choose tenant

Do not allow:

Jev → choose user

Do not allow:

Jev → choose credentials

Do not allow:

Jev → choose policy identity

---

# 24. PRIVACY / DATA MINIMIZATION

Create a strict redaction layer for Jev state.

The redactor must be tested.

Default deny.

Explicitly reject:

* API keys
* passwords
* auth headers
* cookies
* session secrets
* signing keys
* raw stack traces
* raw database rows when unnecessary
* full audit chain
* internal system prompts

For free/unofficial development profiles:

use synthetic test data only.

Prefer sending:

“Create a sales order for customer 42 with product 55 quantity 2”

instead of:

the entire customer record and database context.

---

# 25. PROVIDER HEALTH

Add provider resilience.

Real Jev client must support:

* connect timeout
* read timeout
* total timeout
* retry only on clearly transient errors
* 429 handling
* bounded exponential backoff
* provider circuit breaker or reuse existing circuit-breaker conventions where appropriate
* structured provider error mapping
* no infinite retries
* no duplicated ERP writes because of a Jev retry

Important:

Retrying a decision call is not the same as retrying an ERP execution.

The model/decision retry must happen BEFORE execution authority.

---

# 26. NEVER LET JEV RETRY THE ERP ACTION

Jev retry:

allowed

Gateway/Odoo retry:

controlled separately

A Jev timeout must never cause:

“just execute again”

The idempotency system remains authoritative.

---

# 27. RESULT NORMALIZATION

Build a strict adapter from provider response to internal MIZAN types.

Do not leak provider-specific object shapes throughout the project.

Example internal shape:

DecisionResult

with:

* provider
* model
* request_id
* spec_version
* state_hash
* decisions
* latency_ms
* error
* raw_reference metadata only where safe

Each decision may expose:

Choice:

* choice
* probabilities
* confidence

Score:

* score
* probabilities
* confidence

Noul:

* probability

Do not assume all decision types have the same fields.

---

# 28. JEV CLIENT TEST MATRIX

Build extensive unit tests.

At minimum:

### Parsing

* valid Choice
* valid Score
* valid Noul
* multiple questions
* missing question
* unknown answer type
* malformed probabilities
* invalid selected option
* missing confidence
* unexpected confidence on Noul
* provider request ID extraction

### Transport

* timeout
* 429
* 500
* 502
* 503
* invalid JSON
* connection failure
* auth failure
* retry behavior

### Security

* secret redaction
* oversized state
* PII redaction
* tenant non-authority
* model cannot override user identity
* provider error does not affect ERP state

---

# 29. MOCK JEV MUST BE POWERFUL

Do not build a useless fake that only returns one happy-path result.

The mock must support scripted scenarios:

* correct high-confidence tool
* wrong high-confidence tool
* correct low-confidence tool
* ambiguous
* prompt injection
* policy escalation
* provider timeout
* provider outage
* disagreement with LLM
* multiple questions
* malformed provider response

This will allow the harness to test the whole architecture offline.

---

# 30. HARNESS INTEGRATION IS MANDATORY

Do not say Jev is useful because it “feels fast”.

Measure it.

The existing harness is one of MIZAN’s core assets.

Preserve the existing deterministic harness.

Add live Jev evaluation as a separate mode.

Suggested CLI concepts:

`--decision-provider mock`

`--decision-provider jev`

`--jev-mode off|shadow|advisory|enforcing`

`--jev-profile ...`

`--compare-baseline`

Use existing CLI conventions rather than forcing these exact flags.

---

# 31. SAME 144 GOLDEN CASES

Run Jev against the same MIZAN decision scenarios.

Do not create an unrelated toy benchmark and call it proof.

For each case record:

* case ID
* expected tool
* expected outcome
* LLM tool
* Jev tool
* Jev confidence
* Jev top probabilities
* ambiguity signal
* injection signal
* latency
* final route
* final gateway result

Measure:

## Decision metrics

* tool-selection accuracy
* top-2 coverage
* abstention rate
* ambiguity detection
* injection detection
* escalation precision
* escalation recall

## System metrics

* overall latency
* LLM latency
* Jev latency
* gateway latency
* total latency
* tokens
* provider error rate
* fallback rate

## Safety metrics

Must remain:

* unauthorized writes = 0
* duplicate writes = 0
* audit coverage = 100%
* chain valid
* prompt injection resisted
* no secret leakage
* no tenant confusion
* no false execution claims

---

# 32. THREE-WAY COMPARISON

Create an evaluation matrix:

### Baseline

Current MIZAN without Jev

### Jev-shadow

Jev runs but cannot alter behavior

### Jev-advisory

Jev can influence routing/escalation

Do not compare only model accuracy.

Compare:

* safety
* latency
* accuracy
* calibration
* cost
* fallback behavior
* disagreement
* reliability

The result should tell us whether Jev creates measurable system value.

---

# 33. DO NOT OVERFIT

Do not tune Jev thresholds on the same cases used for final claims.

Split the dataset or create:

* calibration set
* validation set
* held-out evaluation set

Document the split.

Never secretly change test cases because Jev performs badly.

If Jev fails, record the failure.

The project becomes stronger through honest failures.

---

# 34. MODEL ROUTING EXPERIMENT

Build an experimental model-routing capability.

Possible logic:

Simple / high-confidence:
→ cheap route

Ambiguous:
→ stronger LLM

Risky:
→ stronger reasoning + human confirmation

Clear read:
→ fast route

But do not activate this globally until measured.

Never route purely on arbitrary string matching if Jev can express a more robust typed decision.

---

# 35. IMPORTANT PERFORMANCE QUESTION

Do not assume Jev improves latency merely because Jev itself is fast.

Measure:

baseline:
LLM

versus:

Jev + LLM

versus:

Jev-constrained + LLM

versus:

LLM fallback

Measure end-to-end latency.

A Jev call that takes 300ms but saves 20ms elsewhere is not necessarily an improvement.

Optimization target is:

> Useful decision intelligence per total end-to-end latency and cost.

---

# 36. JEV DECISION CACHE

Explore caching only where safe.

A decision cache may be possible for:

* deterministic repeated read-intent classification
* repeated synthetic harness states

But do not cache write authorization decisions in a way that survives meaningful state changes.

A cache key must consider at least:

* state hash
* question spec version
* model version
* threshold version

Be conservative.

---

# 37. OBSERVABILITY

Extend MIZAN telemetry.

Expose, where safe:

* Jev enabled
* Jev provider
* Jev model
* Jev health
* decision count
* Jev p50
* Jev p95
* provider failures
* fallback count
* disagreement count
* escalation count
* shadow/advisory/enforcing mode

Do not expose:

* API key
* secret
* full state
* private customer data

---

# 38. SETTINGS SYSTEM

Use the existing typed SettingsStore.

Add settings for:

* Jev enabled
* Jev mode
* Jev provider
* Jev model
* Jev endpoint
* Jev timeout
* Jev retries
* threshold version
* tool-routing threshold
* ambiguity threshold
* injection threshold
* semantic-risk mode
* shadow logging
* allow external decision provider
* free-dev profile

Secret values must be:

* typed as secret
* masked on every read path
* excluded from logs
* excluded from audit
* excluded from frontend state

Follow the existing settings architecture.

Do not create a second configuration system.

---

# 39. FRONTEND

Do not rebuild the entire frontend merely because Jev exists.

First make the backend and harness correct.

Then expose a small observability surface.

Examples:

“Decision layer: active”

“Jev: 182ms”

“Route confidence: 0.97”

“LLM fallback”

“Decision disagreement”

But do not make the frontend claim:

“Jev approved this action”

because that would be architecturally false.

Use wording such as:

“Decision signal”

“Routing confidence”

“Escalated for review”

“The final policy decision remains server-authoritative”

Respect RTL and the existing product language.

---

# 40. CURRENT FRONTEND DRIFT

The repository contains both:

* richer legacy vanilla cockpit
* newer React/TypeScript scaffold

Do not create a third UI.

Determine which frontend is canonical for current runtime behavior.

Document the decision.

Avoid diverging state models.

The frontend must reflect server state, not invent it.

---

# 41. CANONICAL STATE MACHINE

Jev integration must map cleanly into the canonical execution state machine.

Do not create a parallel state universe.

Possible conceptual states/events:

DECISION_SCREENING
DECISION_COMPLETED
DECISION_UNCERTAIN
DECISION_ESCALATED
DECISION_DISAGREEMENT

Only add states/events when truly justified.

Do not duplicate:

gateway status
proposal state
idempotency state
frontend visual state

The canonical execution state machine remains the source of lifecycle truth.

---

# 42. MONOTONIC SAFETY RULE

This rule is mandatory.

Jev may:

* escalate
* block
* ask for clarification
* request stronger reasoning
* narrow candidate tools

Jev may NOT:

* remove deterministic authorization
* remove confirmation requirements
* downgrade risk
* bypass idempotency
* bypass verification
* override tenant checks
* override identity checks
* mark execution successful
* change ERP truth

Formally:

Decision intelligence may increase conservatism.

Decision intelligence may never reduce server-enforced safety.

---

# 43. FAILURE-FIRST ENGINEERING

Test failures before happy paths.

Explicitly test:

* Jev unavailable
* Jev slow
* Jev wrong
* Jev overconfident
* Jev uncertain
* Jev disagrees with LLM
* LLM disagrees with Jev
* provider returns malformed response
* network dies after request
* API key invalid
* unofficial free provider disappears
* threshold version mismatch
* decision spec changes
* concurrent requests
* duplicate request
* proposal expiration
* approval replay
* write verification failure

MIZAN should degrade gracefully.

A Jev outage should not automatically become an MIZAN outage.

---

# 44. OFFLINE MODE MUST REMAIN EXCELLENT

A developer must be able to run:

full deterministic MIZAN

without:

* Jev key
* LLM key
* Odoo
* external network

using existing mocks.

This is non-negotiable.

The project must remain reproducible.

---

# 45. LIVE MODE MUST BE EXPLICIT

Never silently switch to real Jev because a key happens to be present.

Require explicit configuration.

Example conceptually:

`JEV_MODE=shadow`

or:

`JEV_PROVIDER=typesafe`

Production configuration must make external AI usage obvious.

---

# 46. HARNESSED FREE DEVELOPMENT

Create a command/workflow that proves:

MIZAN + MockJev + deterministic ERP + current harness

works:

* offline
* reproducibly
* with zero cost
* with no external credentials

Then create a separate synthetic-only live Jev smoke command.

Do not confuse the two.

---

# 47. DOCUMENTATION

Update:

* README
* CHANGELOG
* PROJECT_MINDMAP
* CURRENT_STATE
* ARCHITECTURE_MAP
* HARNESS_ARCHITECTURE
* TRUST_BOUNDARY
* security docs
* relevant ADRs
* evaluation docs
* Jev research notes
* migration notes

Explicitly document:

“What Jev is”

“What Jev is not”

“Where Jev sits”

“Why Jev is not the authority”

“How free development works”

“How official live Jev works”

“What the fallback behavior is”

“What data is sent”

“What is not sent”

“How thresholds are calibrated”

“How to reproduce results”

---

# 48. DOCUMENTATION TRUTHFULNESS

Never update README metrics manually with remembered values.

Regenerate them from actual runs or canonical evidence.

If current code says:

502 / 144

but evidence says:

442 / 50

do not choose whichever sounds better.

Investigate.

Run the correct commands.

Update evidence.

Record commit SHA, runtime, environment, dataset version, harness version, model/provider, threshold version, and artifact hash.

---

# 49. RESEARCH ARTIFACT

Create a dedicated document such as:

`00-research/jev-evaluation.md`

It should contain:

* what Jev is
* why it fits MIZAN
* official facts
* current access state
* cost
* latency claims
* limitations
* privacy implications
* alternatives
* architecture decision
* rejected alternatives
* measured results
* open risks

Date every external claim.

---

# 50. ADR

Create an ADR for Jev integration.

It must explain:

Context

Decision

Why Jev is additive rather than authoritative

Alternatives considered

Rejected designs

Security consequences

Operational consequences

Evaluation strategy

Rollback strategy

---

# 51. ROLLBACK MUST BE EASY

A config change should disable Jev without code rollback.

The system must return to current baseline behavior.

Removing a Jev key should not crash the system.

Turning Jev off should not invalidate existing ERP functionality.

---

# 52. VERSIONING

Track independently:

* MIZAN version
* decision layer version
* Jev model version
* decision spec version
* threshold version
* tool registry version
* policy version
* state machine version
* harness version

Every evaluation report should contain them.

---

# 53. GOLDEN TRACE EXAMPLE

Create at least one documented golden flow:

User:

“اعمل أمر بيع للعميل 42 من المنتج 55 بكمية 2.”

Expected conceptual path:

LLM understands intent

Jev optionally:

* selects `sales.order.create`
* low ambiguity
* no injection
* routing confidence recorded

MIZAN:

* validates tool
* validates schema
* evaluates policy
* creates proposal
* requires confirmation

User confirms

Gateway:

* approves proposal
* validates policy again
* acquires lease
* executes Odoo
* verifies read-back
* finalizes idempotency
* writes evidence
* writes audit

Final response:
truthful

No model is allowed to claim success without the verified ERP result.

---

# 54. SECOND GOLDEN FLOW

User:

“هاتلي أحمد”

Jev:

candidate = customer.search

high confidence

MIZAN:

LLM can receive narrowed tool set in advisory mode

Gateway executes read-only request

Arabic normalization remains available

Result is grounded in actual Odoo data

---

# 55. THIRD GOLDEN FLOW

User sends prompt-injection-like content trying to:

* reveal the system prompt
* bypass policy
* force a tool
* expose credentials

Jev flags suspicious

MIZAN security path escalates or blocks

Gateway never executes the malicious tool request

The harness records why.

---

# 56. EVAL SCIENCE

Every metric must have:

* definition
* denominator
* expected population
* dataset version
* run version
* inclusion criteria
* exclusion criteria

Do not publish percentages with unclear populations.

Do not compare deterministic scripted mode to live model mode as if they were the same experiment.

---

# 57. REQUIRED RELEASE GATES FOR JEV

Do not enable `enforcing` until all applicable gates pass.

At minimum:

* no unauthorized write regressions
* no duplicate write regressions
* no audit regressions
* no verification regressions
* no prompt-injection regression
* no tenant/identity regression
* Jev provider failure fallback passes
* shadow and advisory modes stable
* held-out tool-routing evaluation passes configured threshold
* calibration documented
* latency impact acceptable
* evidence complete
* docs synchronized

If a gate fails:

keep Jev in shadow or off mode.

Never hide the failure.

---

# 58. TEST COUNT IS NOT THE GOAL

Adding 300 meaningless tests is not success.

The goal is:

* meaningful coverage
* adversarial coverage
* failure observability
* reproducibility
* regression detection

Prefer fewer strong tests to many trivial tests.

---

# 59. CODE QUALITY

Keep:

* strong typing
* narrow interfaces
* explicit dependency direction
* small modules
* deterministic transformations
* testable pure functions
* structured errors
* no hidden global state where avoidable
* secure defaults
* no magic constants
* no duplicated configuration
* no silent fallback that hides execution

Follow the repository’s existing style.

Do not rewrite working modules just for aesthetics.

---

# 60. NO PREMATURE COMPLEXITY

Do not build:

* agent swarms
* generic workflow engines
* giant plugin systems
* arbitrary graph execution
* speculative microservices
* message buses
* distributed infrastructure

unless the current measured requirements justify them.

Make the smallest architecture that can carry the new capability cleanly.

---

# 61. HOWEVER: THINK BEYOND THE POC

The goal is not a toy feature.

Design Jev integration so it can later support:

* multiple decision providers
* ERPNext
* Odoo
* custom ERP
* WhatsApp
* voice
* background automation
* approvals
* stronger enterprise policy
* larger tool registry
* multi-tenant deployment
* model routing
* decision analytics

But do not implement those future systems now unless required to support the decision layer correctly.

---

# 62. COMPETITIVE / TECHNICAL RESEARCH

Research how other agent systems combine:

* generative models
* typed decision models
* policy engines
* tool routers
* guardrails
* eval harnesses
* audit trails
* human approval

Use current sources.

Look especially at:

* TypeSafe System One
* official Jev SDK
* LangChain Jev integrations/evaluations
* agent observability systems
* model routers
* policy-as-code systems
* secure tool execution designs

Extract architectural lessons, not marketing language.

---

# 63. IMPORTANT DISTINCTION

Do not confuse:

“Jev cannot output a type error”

with:

“Jev can never make a semantic mistake.”

A typed answer can still be the wrong decision.

Therefore:

type safety
≠
business correctness

confidence
≠
authority

probability
≠
truth

All must be interpreted by MIZAN.

---

# 64. DECISION QUALITY SHOULD BE MEASURED AGAINST BUSINESS TRUTH

For every live evaluation, ask:

Did the selected tool match the case?

Did the arguments match?

Did the gateway enforce policy?

Did the ERP do the intended action?

Did verification pass?

Did the user-visible claim match verified truth?

A beautiful Jev decision that leads to the wrong ERP behavior is still a failure.

---

# 65. FINAL DELIVERY REQUIREMENT

When implementation is complete, produce:

1. concise architecture summary
2. files changed
3. why each change exists
4. test results
5. harness results
6. Jev results
7. baseline vs Jev comparison
8. latency comparison
9. cost model
10. calibration summary
11. security findings
12. free-development setup
13. real-provider setup
14. rollback instructions
15. known limitations
16. next engineering priorities

Do not report “done” merely because the code compiles.

The feature is done only when:

code
+
tests
+
harness
+
evidence
+
documentation
+
rollback
+
truthful metrics

all align.

---

# 66. EXECUTION ORDER

Follow this order unless repository evidence proves a better order:

PHASE 0
Repository re-audit

PHASE 1
Jev research and architecture decision

PHASE 2
Provider-neutral DecisionClient abstraction

PHASE 3
MockJev + deterministic tests

PHASE 4
Real Jev adapter

PHASE 5
Shadow mode

PHASE 6
Tool routing

PHASE 7
Ambiguity + injection signals

PHASE 8
Policy/risk integration as additive escalation only

PHASE 9
Evidence + audit + telemetry

PHASE 10
Harness live decision evaluation

PHASE 11
Calibration + held-out evaluation

PHASE 12
Advisory routing

PHASE 13
Optional enforcing mode behind explicit gate

PHASE 14
Documentation synchronization

PHASE 15
Full regression

PHASE 16
Final architecture audit

Do not skip directly to enforcing mode.

---

# 67. AUTONOMY RULE

You are expected to discover and solve secondary issues that prevent this architecture from being correct.

For example:

* stale docs
* missing evidence metadata
* inconsistent state serialization
* weak API typing
* insufficient error taxonomy
* missing metrics
* duplicated configuration
* insecure logging
* frontend/server contract drift
* harness blind spots
* threshold reproducibility problems

Fix them when they are directly relevant.

Document unrelated future work rather than scope-creeping infinitely.

---

# 68. ABSOLUTE PROHIBITIONS

Never:

* bypass ToolGateway
* let Jev authorize writes
* let Jev select tenant identity
* place API keys in frontend
* send secrets to Jev
* send real PII to an unofficial free endpoint
* fabricate evaluation results
* fabricate model accuracy
* fabricate latency
* silently discard Jev disagreements
* silently disable safety checks
* convert uncertainty into certainty
* claim production readiness without evidence
* modify tests just to make a failing system green
* remove failing cases because the new model struggles
* create a second parallel security architecture
* create a second truth source for ERP results

---

# 69. THE NORTH STAR

The final architecture should feel like this:

USER
↓
LLM
↓
JEV / DECISION INTELLIGENCE
↓
SERVER-OWNED POLICY
↓
MIZAN GATEWAY
↓
AUTHORIZATION
↓
CONFIRMATION
↓
IDEMPOTENCY
↓
LEASE
↓
EXECUTION
↓
ODOO
↓
VERIFICATION
↓
EVIDENCE
↓
AUDIT
↓
TRUTHFUL ANSWER

And the conceptual split should remain:

# LLM understands.

# Jev decides narrowly.

# MIZAN governs.

# ERP proves what happened.

The result should not merely be “MIZAN with Jev added”.

The result should be a substantially more rigorous, measurable, modular, provider-neutral, security-conscious agent execution architecture that can be defended technically in front of serious engineers.

Do the work.
Measure the work.
Prove the work.
Document the work.
Then improve it again.
