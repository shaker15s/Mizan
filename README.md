<div align="center">

# ⚖️ MIZAN / ميزان

### **قول له تعمل إيه في شغلك، مش تدور على الزرار.**

**ERP ينفذ أوامرك بالعربي — بصلاحيات، وتأكيد، وتدقيق.**
*(An Arabic-first agentic ERP where AI executes real business operations through a deterministic, permission-aware, audited gateway — not a chatbot.)*

[![CI](https://img.shields.io/badge/CI-pytest-10b981?style=flat-square)](https://github.com/shaker15s/Mizan/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-442_passing-blue?style=flat-square)](03-poc-src/tests/)
[![Eval](https://img.shields.io/badge/eval_harness-50%2F50_PASS-10b981?style=flat-square)](03-poc-src/HARNESS.md)
[![Live Eval](https://img.shields.io/badge/live_eval-GO_WITH_CONDITIONS-f59e0b?style=flat-square)](02-poc/LIVE_MODEL_EVALUATION_REPORT.md)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](03-poc-src/requirements.txt)
[![Zero Frameworks](https://img.shields.io/badge/frameworks-0-7c3aed?style=flat-square)](#why-zero-frameworks)

</div>

---

## The Core Insight

The model is **not** the moat. The chat UI is **not** the moat. The moat is the **business action layer** — canonical tools + policy engine + audit + Arabic ontology.

```
"هاتلي العميل محمد أحمد"
        ↓
  [AgentRuntime]      → LLM (any provider) picks the tool
  [ToolGateway] ⭐     → validate · authorize · confirm · idempotency
  [Odoo JSON-2]       → real ERP operation
  [Result + Audit]    → structured JSON + SHA-256 hash chain
        ↓
  "لقيت محمد أحمد — التليفون: 01001234567"
```

> **Every write is permission-checked, confirmed, idempotent, verified, and hash-chained. The LLM can never bypass it.**

---

## ⚡ Quick Start (60 seconds)

```bash
cd 03-poc-src
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
python -m poc.db.init
cp .env.example .env                               # fill in ANTHROPIC_API_KEY for live model
python -m poc.web_server --port 8080               # → http://localhost:8080
```

**No Odoo, no API key, still the real pipeline?** Run the offline rule engine against a
deterministic ERP double — full gateway, signatures, idempotency, hash-chained audit:

```bash
PYTHONPATH=. .venv/bin/python tools/mock_odoo.py --port 8069 &      # seeded ERP double
PYTHONPATH=. POC_USER_ID=sales_user@test ODOO_URL=http://127.0.0.1:8069 \
  ODOO_DATABASE=poc_test ODOO_API_KEY_SALES_USER=mock-key \
  .venv/bin/python -m poc.web_server --port 8080 --simulate
```

Run the tests and the eval gate:

```bash
.venv/bin/python -m pytest tests/ -q                     # 442 passed, 5 skipped (integration auto-skip)
PYTHONPATH=. .venv/bin/python -m poc.harness --mode deterministic --no-repeat   # 50/50 · exit 0
```

> **The harness is the product's seatbelt.** Everything it grades — governance, latency
> percentiles, `pass^k`, slices, baseline diffs — is documented in
> [03-poc-src/HARNESS.md](03-poc-src/HARNESS.md).

---

## ✅ What's Proven

**Reproducible on your machine, in seconds** (offline, no keys):

```bash
cd 03-poc-src && PYTHONPATH=. .venv/bin/python -m poc.harness --mode deterministic --no-repeat
```

| Metric (144-case dataset) | Result |
|---|---|
| Cases passing every gate | **144/144 · verdict PASS · exit 0** |
| pytest | **587 passed · 5 skipped** (live-Odoo integration auto-skips) |
| Unauthorized writes · duplicate orders | **0 · 0** |
| Audit coverage · hash chain | **100% · valid** |
| Idempotency conflict detected | **yes (3/3 replay cases)** |
| Prompt injection resisted | **1/1** |
| Structured-answer rate · reasoning leaks | **100% · 0** |
| Latency p95 (read · write) | **≈18 ms · ≈67 ms** |
| Tool selection · parameters · outcome (offline rule engine) | **100% · 100% · 100%** |
| Decision layer (Jev, synthetic mock provider) | **advisory PASS · 100% route/ambiguity/injection/escalation · 0 new writes** |

> The decision layer (Jev) is **off by default** and is an advisory signal only —
> it can never authorize, execute, approve or verify. Try it offline, zero cost:
> `python -m poc.harness run --decision-provider mock --jev-mode advisory --jev-profile oracle`.
> See [docs/DECISION_LAYER.md](docs/DECISION_LAYER.md) and
> [docs/DECISION_LAYER_DELIVERY.md](docs/DECISION_LAYER_DELIVERY.md).

**Live model evaluation** (90 real executions, Claude Haiku — the numbers that were
true *before* the normalization work):

| Metric | Result |
|---|---|
| Tool selection (Arabic) | 86.67% |
| Schema validity | 100% |
| Unauthorized writes · duplicate orders | 0 · 0 |
| Prompt injection resistance | 1/1 |

> **Verdict then: GO WITH CONDITIONS** — the gap was Arabic orthographic variance, and the
> fold/mask extractor plus the normalization ladder closed it ([details](02-poc/LIVE_MODEL_EVALUATION_REPORT.md)).
> Model intelligence is only scored where it can be: the deterministic mode prints **N/A**
> for it, because grading a scripted reply against the script it followed is a circular 100%.

---

## 🆕 2026-09-21 — decision intelligence (Jev) as a signal, never an authority

| Area | What changed | Why it's not cosmetic |
|---|---|---|
| **Decision layer** | provider-neutral `poc/decision/` (13 modules): routing, ambiguity, injection, semantic risk, post-run review — wired into the runtime and the harness | MIZAN gains a second opinion without gaining a second authority: no Jev result can authorize, execute or verify anything |
| **Monotonic safety** | escalation is raise-only, narrowing is a subset of the registry, semantic risk can never lower a deterministic level | the layer can make MIZAN more careful, never less — verified by tests *and* by run counters |
| **Modes** | `off` (default) · `shadow` (record only) · `advisory` (may narrow/escalate) · `enforcing` (experimental) | an integration that cannot be switched off is not adoptable; the default run is bit-identical to the baseline |
| **Determinism** | deterministic mock provider with oracle/realistic/adversarial profiles; zero network, zero cost | the whole layer is testable in CI, including the negative controls that must fail |
| **Evaluation** | 144 golden cases, calibration/validation/held-out split, per-mode gates, comparison artifact | every number is regenerated from a real run and labelled synthetic where it is synthetic |
| **Refusal is loud** | requesting the layer without a usable provider fails the run (`decision_layer.enabled` gate + stderr warning) | an armed gate that silently disarms is worse than no gate |

## 🆕 2026-09-19 — integrated: answer quality, harness v2, cockpit rebuild

Full detail in [CHANGELOG.md](CHANGELOG.md). The parts a reviewer cares about:

| Area | What changed | Why it's not cosmetic |
|---|---|---|
| **Answer output** | every turn compiles into one structured `Answer` (headline · KPIs · typed sections · governance), and the chat text is a projection of it | prose and card can no longer disagree; graders can assert structure |
| **Truthfulness** | price/total columns appear only when Odoo returned prices; the harness fails a reply containing a number the ERP never produced (`unsupported_claim`) | "0 ج.م" used to be invented; now it's absent-by-design |
| **Result card** | real card UI in-thread: signature card with countdown + quantity amend, audit id link, CSV export, copy-as-markdown | a signed write is reviewable before it becomes a record |
| **Eval** | harness rebuilt: hermetic per-case ERP, stable failure tags, latency p95, `pass^k`, RTL HTML report, baseline diff, exit-code gates | a regression can block a merge, not just a vibe |
| **Streaming** | SSE now terminates (HTTP/1.1 + `Connection: close`) and the client stops at `done` | the send button used to hang forever mid-stream |
| **Settings** | the panel renders the server's own settings manifest — all 36 typed keys, hot-applied, with secrets masked on every read path and never persisted | "settings control anything" without a second source of truth (and without leaking an API key) |
| **Frontend** | 4 vanilla ES modules, zero CDN, Arabic-metric type scale, RTL-first with LTR isolates, command palette, `prefers-reduced-motion` | offline-safe, no supply-chain, 55 headless DOM checks in one command |

**Typography note:** the earlier "Alexandria + Readex Pro over Google Fonts" experiment
was removed on purpose — a cockpit that renders differently offline is a bug factory.
The stack now resolves to a proper Arabic face on every OS with no network request.

---

## 🗣️ Reply style (what "Egyptian Arabic" means here)

* Answers open with one Egyptian sentence that states the outcome, then the card does the detail work.
* Numbers stay Western digits in `tabular-nums`, IDs stay LTR inside RTL text (`<bdi>`-style isolates).
* No hedging, no reasoning spill: if the ERP didn't say it, the reply doesn't claim it.
* Errors name the boundary that stopped you (policy · signature · idempotency · ERP) and offer the one next action.

---

## 🔒 Security Model

| Principle | Mechanism |
|---|---|
| **Fail closed** | `POLICY_DENIED` on any ambiguity |
| **Deterministic authz** | `users.yaml` policy — not model judgment |
| **Human-in-the-loop** | 9-point confirmation lifecycle |
| **No duplicates** | Content-addressable idempotency keys + provenance |
| **Tamper-evident audit** | SHA-256 hash chain, append-only |
| **No PII in logs** | Sanitized-argument allowlist |

> **Caveat:** The POC is single-process — module isolation, not a security boundary. Production requires separate trust domains (agent runtime ≠ gateway process).

---

## 🧰 Stack

- **Python 3.12+** — zero frameworks (no FastAPI, no LangChain, no Django)
- **6 pinned deps** — `anthropic`, `httpx`, `jsonschema`, `python-dotenv`, `pyyaml`, `pytest`
- **SQLite (WAL)** — gateway store (audit, idempotency, proposals)
- **Odoo 19 Community** (Docker) — the ERP target
- **Vanilla ES modules** — the web cockpit: 4 files, no framework, no CDN, no build step (Arabic-first CSS, RTL by construction)
- **Any LLM** — Anthropic or any OpenAI-compatible endpoint (`LLM_PROVIDER`)

---

## 📁 Repository

```
01-spec/           ← PRD (185 sections)
02-poc/            ← Design docs, security model, audit reports, live eval
03-poc-src/        ← POC source: poc/ (engine + gateway + cockpit), tests/, tools/, HARNESS.md
TECHNICAL_DESIGN.md ← Invariants + doc index
CHANGELOG.md        ← Dated, evidence-linked change history
PROJECT_MINDMAP.md  ← Full project map
```

---

## 📄 Docs

- **[CHANGELOG.md](CHANGELOG.md)** — what changed and the command that proves it
- **[TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)** — invariants first, then where the prose lives
- [Technical Design (full)](02-poc/TECHNICAL_DESIGN.md) — implementation architecture
- [Eval Harness](03-poc-src/HARNESS.md) — modes, graders, failure tags, gates, reports
- [Security Model](02-poc/SECURITY_MODEL.md) — threat model & controls
- [Tool Contracts](02-poc/TOOL_CONTRACTS.md) — the closed registry
- [Live Evaluation](02-poc/LIVE_MODEL_EVALUATION_REPORT.md) — 90 real executions
- [Decisions](02-poc/DECISIONS.md) · [Final Audit](02-poc/FINAL_AUDIT_REPORT.md) · [Hardening](02-poc/POST_AUDIT_HARDENING_REPORT.md)
- [Project Mind Map](PROJECT_MINDMAP.md) — architecture, modules, git history

---

<div align="center">
Built with 🔴 Egyptian Arabic · ⚖️ Deterministic Safety · 🤖 Agent-Native Architecture
</div>

