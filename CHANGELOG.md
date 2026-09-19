# Changelog

All notable changes to Mizan. Dates are Egyptian; the format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)-ish, and every entry lists
the command that proves it.

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
- CI runs the gated deterministic harness and the headless cockpit smoke alongside
  pytest.

## [2026-09-17] — normalization ladder, FORCE_TOOL_CHOICE, Arabic fonts, voice input

See `README.md` ("2026-09-17 Improvements") and `02-poc/POST_AUDIT_HARDENING_REPORT.md`.

## [2026-09-15] — hardening audit follow-through

`02-poc/FINAL_AUDIT_REPORT.md`, `02-poc/HARDENING_PLAN.md`: fail-closed gateway,
idempotency lifecycle, hash-chained audit, confirmation store.
