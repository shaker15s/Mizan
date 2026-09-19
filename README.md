<div align="center">

# ⚖️ MIZAN / ميزان

### **قول له تعمل إيه في شغلك، مش تدور على الزرار.**

**ERP ينفذ أوامرك بالعربي — بصلاحيات، وتأكيد، وتدقيق.**
*(An Arabic-first agentic ERP where AI executes real business operations through a deterministic, permission-aware, audited gateway — not a chatbot.)*

[![CI](https://img.shields.io/badge/CI-passing-10b981?style=flat-square)](https://github.com/shaker15s/agent-native-erp/actions)
[![Tests](https://img.shields.io/badge/tests-351_passing-blue?style=flat-square)](03-poc-src/tests/)
[![Live Eval](https://img.shields.io/badge/live_eval-GO_WITH_CONDITIONS-f59e0b?style=flat-square)](02-poc/LIVE_MODEL_EVALUATION_REPORT.md)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](03-poc-src/requirements.txt)
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
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
copy .env.example .env        # fill in keys
python -m poc.db.init
python -m poc.web_server --port 8080   # → http://localhost:8080
```

Run the test suite (336 unit + 15 new, 5 integration auto-skip):

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

> **Want to see it without a live Odoo?** `python -m poc.tests.run_eval --mode deterministic` runs the full pipeline against a seeded fake ERP — zero external dependencies.

---

## ✅ What's Proven (Live Evaluation — 90 executions, Claude Haiku)

| Metric | Result |
|---|---|
| Tool selection (Arabic) | **86.67%** |
| Schema validity | **100%** |
| Unauthorized writes | **0** |
| Duplicate orders | **0** |
| Audit hash chain | **Valid** |
| Prompt injection resistance | **1/1** |

> **Verdict: GO WITH CONDITIONS.** The control plane held. The only gap is orthographic variance — and we fixed it in 2026-09-17 (see below).

---

## 🆕 2026-09-17 Improvements

- **🎯 Arabic normalization fallback ladder** — `"احمد"` finds `"أحمد حسن"` and vice versa (raw → normalized → hamza-variant, max 3 calls, usually 1)
- **⚡ `FORCE_TOOL_CHOICE` opt-in** — eliminates occasional `text_only` hesitation on write requests (Anthropic `{"type":"any"}` / OpenAI `"required"`)
- **🔤 Premium Arabic fonts** — Alexandria (headings/UI) + Readex Pro (data) — Cairo was the generic face of every Arabic site
- **🎙️ Real voice input** — Web Speech API (`ar-EG`), native browser feature, no dependency
- **📋 README + CI** — this file + GitHub Actions on every PR
- **🧹 Housekeeping** — temp files removed, 4 fixes committed

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
- **Tailwind CDN + vanilla JS** — the web cockpit (no build step)
- **Any LLM** — Anthropic or any OpenAI-compatible endpoint (`LLM_PROVIDER`)

---

## 📁 Repository

```
01-spec/          ← PRD (185 sections)
02-poc/           ← Design docs, security model, audit report, live eval
03-poc-src/       ← POC source code (poc/, tests/, web/)
PROJECT_MINDMAP.md ← Full project map
PROJECT_DOSSIER.md ← The complete story — vision, decisions, evidence, roadmap
```

---

## 📄 Docs

- **[PROJECT_DOSSIER.md](PROJECT_DOSSIER.md)** — the complete project document (what, why, how, what's next)
- [PRD (185 sections)](01-spec/agent_native_erp_prd.md) — vision & strategy
- [Technical Design](02-poc/TECHNICAL_DESIGN.md) — implementation architecture
- [Security Model](02-poc/SECURITY_MODEL.md) — threat model & controls
- [Live Evaluation](02-poc/LIVE_MODEL_EVALUATION_REPORT.md) — 90 real executions
- [Project Mind Map](PROJECT_MINDMAP.md) — architecture, modules, git history

---

<div align="center">
Built with 🔴 Egyptian Arabic · ⚖️ Deterministic Safety · 🤖 Agent-Native Architecture
</div>
"# Mizan" 
