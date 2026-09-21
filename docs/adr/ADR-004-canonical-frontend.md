# ADR-004 — One canonical frontend, one dependency-free fallback

**Date:** 2026-09-21
**Status:** Accepted
**Modules:** `03-poc-src/frontend/` (canonical), `03-poc-src/poc/web/` (fallback)

## Context

Plan §40 requires exactly one canonical cockpit. The repository currently
carries three front-end trees, which is the fragmentation the plan calls out:

| Tree | What it is | How it is served |
|---|---|---|
| `03-poc-src/frontend/` | React 18 + TypeScript + Vite cockpit, inside the POC root | served when `frontend/dist/index.html` exists (`web_server.WEB_DIR`) |
| `frontend/` (repo root) | an earlier React cockpit with a components split | not served by anything |
| `03-poc-src/poc/web/` | dependency-free vanilla ES-module cockpit | served as the fallback, and covered by `tools/frontend_smoke.mjs` |

Serving two React trees guarantees drift (two API clients, two state models),
and building the React tree here is not reproducible today: there is no
`package-lock.json`, so `npm ci` cannot run and the artifact cannot be rebuilt
deterministically in CI.

## Decision

1. **`03-poc-src/frontend/` is the canonical product cockpit.** It is the tree the
   server already prefers, and the only one that lives inside the POC root
   boundary.
2. **`03-poc-src/poc/web/` is the supported fallback** for environments that have
   no Node toolchain: the zero-dependency server keeps serving it, and
   `tools/frontend_smoke.mjs` keeps it honest. It is frozen feature-wise.
3. **`frontend/` at the repository root is superseded.** No new work goes there;
   it is kept only until the canonical tree reaches feature parity, then deleted
   in a single cleanup commit.
4. To make the canonical tree buildable in CI, a follow-up must commit a
   `package-lock.json` and add `npm ci && npm run build` to the workflow; until
   that exists, the canonical tree is developed locally and the deterministic CI
   path uses the fallback plus the Python-side smoke tests.

## Consequences

* One place to add product UI (canonical), one guaranteed-working surface for
  environments without Node (fallback), and no third tree accumulating changes.
* The decision-layer health surface (`GET /api/decision`) is server-side and
  therefore available to either cockpit; surfacing it in the canonical UI is
  tracked as a next priority in `docs/DECISION_LAYER_DELIVERY.md` §16.
* Deleting the root `frontend/` is deferred rather than done blind, because the
  canonical tree cannot yet be rebuilt byte-for-byte here.
