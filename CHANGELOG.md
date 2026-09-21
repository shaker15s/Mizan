# Changelog

All notable changes to Mizan. Dates are Egyptian; the format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)-ish, and every entry lists
the command that proves it.

## [2026-09-21] — decision signal on the cockpit, hot-apply, CI mock gates

### Added
- Compact, secret-free `decision` field on every chat payload (`authority: signal_only`).
- `/api/health` and `/api/telemetry` chips for the decision layer (never an approval).
- Hot-apply of `decision.*` settings rebuilds the router without touching identity or the gateway.
- Vanilla cockpit: governance footer special-cases the decision dict, statusbar chip, stage labels.
- Canonical React (`03-poc-src/frontend/`): `GET /api/decision` + per-turn signal chip.
- CI after pytest: artifact `--check`, harness doctor, mock advisory then mock enforcing (`--no-repeat`).

### Fixed
- `build_decision_router_from_settings` called an unbound `build_router` name.
- Narrative attach dropped `decision=` on the intermediate `AgentResult`.
- Governance markdown no longer stringifies nested objects as `[object Object]`.

### Proof
```
$ cd 03-poc-src && python -m pytest tests/ -q
594 passed, 5 skipped
$ python scripts/build_decision_artifacts.py --check
[artifacts] up to date
$ python -m poc.harness run --decision-provider mock --jev-mode advisory --jev-profile oracle --no-repeat --format json --quiet
144/144 PASS · exit 0
$ python -m poc.harness run --decision-provider mock --jev-mode enforcing --jev-profile oracle --no-repeat --format json --quiet
144/144 PASS · exit 0
```
No live Jev numbers are claimed.

## [2026-09-20] — phases 6–9: execution store, policy 2.0, evidence graph, API hardening

### Added
- **ExecutionStore** (`poc/execution/store.py`) — persistence for canonical state with event log replay; per-execution row + append-only events.
- **Policy Engine 2.0** (`poc/policy_engine2.py`) — attribute-based decisions with `policy_version` + `policy_hash`, R0→allow, R1→allow, R2→self-confirm, R3→manager approval, R4→MFA/step-up. Backward-compatible with the legacy engine.
- **Evidence event graph** (`poc/evidence.py`) — typed append-only evidence events (INTENT, PLAN, ENTITY_RESOLUTION, POLICY_DECISION, APPROVAL, LEASE, TOOL_CALL, ERP_REQUEST, ERP_RESPONSE, VERIFICATION, RECONCILIATION, USER_VISIBLE_CLAIM, STATE_TRANSITION) with SHA-256 hash chain (separate from audit_log). Provides `verify_chain()` and `for_execution()`.
- **Web security middleware** (`poc/web_security.py`) and hardening:
    - Strict CSP/HSTS/X-Frame-Options/X-Content-Type-Options/Referrer-Policy/Permissions-Policy applied to all responses.
    - CORS is same-origin by default; dev mode reflects the origin; `MIZAN_ALLOWED_ORIGINS` allows explicit origins.
    - Dev routes (`/api/test/*`, `/api/replay`, `/api/dev/*`, `/api/eval/*`, `/api/debug/*`) return 404 in production mode unless `MIZAN_DEV=1`.
    - Request body size cap (default 512 KB).
    - In-memory sliding-window rate limiter (default 120 req/min) with `X-RateLimit-*` headers and 429 responses.
    - `Server: mizan` fingerprint removal.
- **New tests:** `test_execution_store.py`, `test_policy_engine2.py`, `test_evidence_store.py`, `test_web_security.py` (13 tests).

### Proof
```
$ PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
494 passed, 5 skipped   (was 481 → +13 new; 0 regressions)
$ PYTHONPATH=. .venv/bin/python -m poc.harness --mode deterministic --no-repeat
50/50 PASS · all safety gates green
```

## [2026-09-20] — phases 1–5: canonical state, typed action, proposal versioning, execution lease, risk

### Added
- **Mandatory architectural documentation** (`docs/`) — `CURRENT_STATE.md`,
  `ARCHITECTURE_MAP.md`, `TRUST_BOUNDARY.md`, `CANONICAL_STATE_MACHINE.md`,
  `HARNESS_ARCHITECTURE.md`, `SECURITY_CONTROL_MATRIX.md`, `TRACEABILITY_MATRIX.md`,
  `MIGRATION_PLAN.md`, and `docs/adr/ADR-001..003`. All populated with verified
  repository reality, not templates.
- **Canonical Execution State Machine** (`poc/execution/state_machine.py`) — ONE
  authoritative lifecycle (stage/status/security/final), enum-typed, event-driven,
  immutable transitions with history. 60+ transitions cover happy path,
  clarification, policy, approval, lease, execution, verification, retry,
  ambiguity, reconciliation, compensation, quarantine, and terminals. Backward
  compatibility via `project_to_gateway_status()` and `project_to_proposal_state()`.
- **Typed Action Envelope** (`poc/execution/action.py`) — frozen dataclass
  carrying actor (server-owned), versions, risk, idempotency binding; never
  overwritten by model output.
- **Proposal Versioning** (Phase 4) — `proposal_version` INTEGER column,
  `superseded_by` pointer, `ConfirmationStore.create_successor_proposal()`
  atomically marks old version failed/superseded and issues a new version with
  a fresh operation_hash; prior approvals on superseded proposals are rejected.
  `AgentRuntime.amend_proposal()` now uses successor creation.
- **Execution Lease** (`poc/execution/lease.py`) — lease_id/owner/heartbeat/
  payload_hash, acquire/heartbeat/complete/release/expire_stale, owner-gated
  transitions, expired leases are superseded not double-reserved.
- **Deterministic Risk Engine v1** (`poc/execution/risk.py`) — R0–R4 based on
  readOnly/destructive/quantity thresholds; explains factors (precursor to
  Policy 2.0 / ABAC).
- **Evidence manifest** (`evidence/EVIDENCE_MANIFEST.json`) — every test/harness
  run attributable to commit, Python version, OS, env, tool/policy/prompt/
  harness versions, pass/fail/skip, artifacts, result hash.
- **New tests:** `test_execution_state_machine.py` (22), `test_execution_lease.py` (8),
  `test_risk_engine.py` (4), `test_proposal_versioning.py` (4).

### Changed
- **`poc/db/init.py`** — idempotent schema migrations: adds `proposal_version`
  and `superseded_by` to `proposals` and creates the `execution_leases` table
  on existing databases (no DROP, no data loss).
- **`poc/confirmation.py`** — `Proposal` dataclass carries `proposal_version`
  and `superseded_by`; `create_proposal` writes version=1; new successor method.

### Proof
```
$ PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
481 passed, 5 skipped   (previously 442 passed, 5 skipped; +39 new tests, 0 regressions)
$ PYTHONPATH=. .venv/bin/python -m poc.harness --mode deterministic --no-repeat
50/50 PASS, all safety gates green
```

See `evidence/EVIDENCE_MANIFEST.json` EV-003, EV-004.

## [2026-09-19] — the integrated pass: answer quality, eval harness v2, cockpit rebuild

### Added
- **Eval harness v2** (`poc/harness/`, `HARNESS.md`) — rebuilt from scratch: hermetic
  per-case ERP + gateway stores, faceted case selection, deterministic graders with
  stable failure tags, percentile latency, `pass^k`, category/user/outcome slices,
  console/JSON/Markdown/RTL-HTML reports, baseline diffing, threshold gates with CI
  exit codes, and parallel execution (`--jobs 8`).
  Proof: `python -m poc.harness --mode deterministic --no-repeat` → `50/50 · verdict PASS · exit 0`.
- **Structured answer cards** (`poc/answer.py`, `poc/responder.py`) — every turn compiles
  into one `Answer` (headline, KPIs, typed sections: table / fields / list / steps /
  bars, notices, next steps, governance footer). Markdown is a lossless projection of
  the same object, so chat text and the card can never disagree.
- **Offline rule engine** (`poc/simulated_llm.py`) — Arabic-first intent extraction
  (fold-based matching, dative handling, Arabic-Indic digits, explicit-ID-only writes,
  gibberish rejection). `--mode simulated` now scores real model metrics: **100 / 100 / 100**
  for tool selection / parameter accuracy / outcome accuracy across all 50 cases.
- **Cockpit redesign** (`poc/web/`) — four vanilla ES modules (`app.js`, `ui.js`,
  `markdown.js`, `styles.css`), no CDN, no build step: answer cards, in-thread
  signature card with a live countdown + quantity amend stepper, streaming pipeline
  stepper, slash-command palette, ⌘K command palette, settings/integrations/audit/tools
  rail, light-dark theme, print styles. RTL-first with logical properties and LTR
  isolates for IDs and codes.
- **Settings panel that controls everything** — the UI renders the server's own
  `SettingsStore` manifest (36 keys, typed, validated, masked secrets, per-key reset),
  so a new setting appears in the cockpit with no frontend change.
- **Truthfulness graders** — `check_numbers_are_real` (`unsupported_claim`) and
  `reasoning_leak` gates; the responder now refuses to invent ERP numbers.
- **Dev tools** — `tools/mock_odoo.py` (deterministic JSON-2 ERP double sharing the
  harness seeds, so the cockpit demos the real gateway path without Odoo) and
  `tools/frontend_smoke.mjs` (55 headless checks over the live DOM: stream → card →
  sign → amend → execute → audit).
- **Tests** — `tests/test_harness.py` (36), `tests/test_simulated_intent.py` (32) and
  `tests/test_settings.py` (23) added; suite is now **442 passed, 5 skipped**.

### Fixed
- **SSE turns never ended** — the cockpit streams over HTTP/1.1 without chunked
  framing, so the response body had no terminator and clients hung waiting (the send
  button stayed busy forever). The stream handler now declares and honours
  `Connection: close`, and the client stops at the `done` frame and falls back to the
  buffered endpoint only when nothing had started executing (never re-posting a write
  that may have committed).
- **Fabricated totals on created orders** — `sales.order.create` answers with IDs only;
  the card used to render `0 ج.م` for unit price and line total. Price columns now
  appear only when the ERP actually returned prices, with a notice explaining where
  the money is read from instead. Same treatment for the idempotency-replay card.
- **Executed writes carried no audit provenance** — `execute_verified` appended the
  audit block and dropped the receipt; the response and card now carry `audit_id` and
  the `verify-*` request id, so a signed write is traceable from the UI in one click.
- **Demo replay rendered a nonsense card** — `compose_answer` had no mapping for
  `conflict`/`in_progress` without a gateway result and fell through to "no usable model
  reply". Both outcomes now render the correct governance card.
- **Confirmation copy was hard-coded to 5 minutes** — the TTL sentence now derives from
  the proposal's own timestamps, so `governance.confirm_ttl_seconds` and the UI agree.
- **Old `run_eval.py` path bug** — the compatibility shim resolves `SRC_ROOT` two
  levels up, so `--report` / `--test-cases` defaults land where they used to.
- **Static asset whitelist** — new modules are served with the same ETag/gzip/CSP
  treatment as `app.js`, and `_serve_static` enforces web-root containment on its own
  (defence in depth, not just the router's).
- Repo hygiene: removed a committed `.patch.tmp` and a stray root `poc/tests/` stub;
  `data/settings.json` (operator overlay, may hold keys) and `data/reports/` are now
  git-ignored; the empty root `TECHNICAL_DESIGN.md` became a pointer instead of a
  dead file; README links no longer point at a non-existent dossier.

### Changed
- Answer richness comes from projection, not surface area: the tool registry stays
  closed at 5 contracts (pinned by `tests/test_tool_contracts.py` and
  `tests/test_web_server.py::test_api_tools_registry`).
- The cockpit dropped Tailwind-CDN + Google Fonts for a self-contained stylesheet with
  an Arabic-metric type scale (16–18px body, 1.75 leading, zero letter-spacing, tabular
  numerals, dual-script stacks) so it renders correctly offline and in RTL by default.
- **CI — queued, not merged.** `.github/workflows/ci.yml` gains `harness doctor`, a gated
  deterministic run (a gate failure exits 1 → the PR is blocked), the simulated mode, a report
  artifact, and a `cockpit-ui` job that boots `tools/mock_odoo.py` + the real server and runs
  `node tools/frontend_smoke.mjs`. It could not be pushed from this environment: GitHub refuses
  any App-authored commit that touches `.github/workflows/**` without the `workflows`
  permission (the contents API returns 403 too). The file is intact in commit `497f76d` — apply
  it with `git checkout 497f76d -- .github/workflows/ci.yml` once the App is granted that
  permission (or commit it as a human, which is enough on its own). Until then, run the two
  gates locally:
  ```bash
  cd 03-poc-src && PYTHONPATH=. python -m poc.harness --mode deterministic --no-repeat
  PYTHONPATH=. python -m poc.harness doctor
  ```

## [2026-09-17] — normalization ladder, FORCE_TOOL_CHOICE, Arabic fonts, voice input

See `README.md` ("2026-09-17 Improvements") and `02-poc/POST_AUDIT_HARDENING_REPORT.md`.

## [2026-09-15] — hardening audit follow-through

`02-poc/FINAL_AUDIT_REPORT.md`, `02-poc/HARDENING_PLAN.md`: fail-closed gateway,
idempotency lifecycle, hash-chained audit, confirmation store.

## [2026-09-20] — phase 10: React/TypeScript frontend scaffold

### Added
- **`frontend/`** — Vite + React 18 + TypeScript cockpit replacement for the legacy
  `poc/web/` HTML/JS UI. Strict CSP (self-only) baked into `index.html`; same-origin
  `/api` proxy to `http://127.0.0.1:8765` in dev, RTL Arabic-first layout.
- Components: `ChatPanel`, `TelemetryStrip`, `ToolsPanel`, `AuditTrail`.
- Typed API client (`src/api.ts`) and shared DTOs (`src/types.ts`) that track the
  Python gateway contract exactly — the client never invents IDs or success state.
- `npm run build` → `dist/` produces ~150 KB JS / 5.5 KB CSS gzipped; `tsc --noEmit`
  passes cleanly.

### Proof
```
$ cd frontend && npm run build
✓ 36 modules transformed · dist/index.html 0.89 KB · built in 971 ms
```
Backend unchanged: 494 tests passed, harness 50/50 PASS.

## [2026-09-20] — phase 5 complete: execution lease wired into gateway

### Added
- **Lease lifecycle inside `confirm_and_execute` / `execute_verified`:**
    - Acquires a `LeaseStore` lease atomically before any ERP call, owner-tagged
      with `gateway:<hostname>:<pid>`.
    - Prevents double-execution when another concurrent worker already holds an
      active lease for the same idempotency key (returns 409/IN_PROGRESS, Odoo
      `create` is never invoked).
    - Transitions the canonical state machine through
      CONFIRMATION_APPROVED → LEASE_GRANTED → EXECUTION_STARTED →
      VERIFICATION_STARTED → VERIFICATION_PASSED → SUCCESS_CONFIRMED
      (or *_FAILURE / AMBIGUOUS_OUTCOME on error).
    - Completes/releases/expires the lease on every exit path (success, hard
      failure, retryable-before-write, ambiguous-after-write).
- **Additional evidence events** on the confirmed-mutation path: APPROVAL, LEASE,
  TOOL_CALL, ERP_REQUEST (product pre-check + sale.order.create), ERP_RESPONSE,
  VERIFICATION, RECONCILIATION, USER_VISIBLE_CLAIM. Hash chain remains valid
  (496 tests verify chain integrity end-to-end).
- **Execution ID alignment:** `_process_mutating` now rebases the in-memory
  `ExecutionState` onto the authoritative execution_id allocated by
  `IdempotencyStore.reserve()` so evidence/leases/audit share one UUID.
- **New test `tests/test_lease_in_gateway.py`** (2 tests): end-to-end lease
  acquisition + evidence coverage, plus concurrent-lease duplicate-write block.

### Proof
```
pytest tests/ -q       → 496 passed, 5 skipped (was 494 → +2)
harness deterministic  → 50/50 PASS · 0 unauthorized · 0 duplicate · chain valid
```
