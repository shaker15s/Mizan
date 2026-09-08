# Agent-Native ERP — Competitive Landscape

**Date:** 7 September 2026 (second pass — vendor claims re-verified against official documentation; GA/preview/marketing status explicitly separated; see `source-index.md`)

---

## 1. Major Vendors Already Shipping Agentic ERP Capabilities

### Microsoft Dynamics 365

**What they ship (verified from official documentation):**
- **Dynamics 365 ERP MCP server**: "a dynamic framework for agents to perform data operations and access the business logic of finance and operations apps" — without custom code, connectors, or APIs `[V]`.
- An earlier **static** server (13 fixed tools, built on the Dataverse connector framework) is **retired** "due to limitations in the server's scale and extensibility" `[V]`.
- **Public preview** status announced 2025-11-11 `[V]`; MCP for analytics announced as a follow-up.
- Security model: agent access "matches the agent's security role and environment context" — the MCP server inherits Dynamics 365 role-based security `[V]`.
- **Orchestration-model guidance (verbatim):** "The recommended model for agents using the Dynamics 365 ERP MCP server is **Claude Sonnet 4.5**… If Claude Sonnet 4.5 isn't available in your environment, use **GPT-5 (Chat)**." `[V]` — with the note that Claude is externally hosted and requires tenant-admin approval.

**Implication for this project:** Microsoft is already exposing ERP operations through MCP with security-role inheritance — this validates the technical approach and simultaneously narrows the "we have an MCP server for ERP" differentiation window to zero. The explicit third-party model recommendation also legitimizes model routing as an architectural pattern.

### Oracle Fusion

**What they ship (verified from official documentation):**
- Prebuilt AI agents across ERP domains: Budget Adjustment Assistant (26C), Cash Processing Agent, Payables Agent for Invoice Ingestion/Compliance/Control (26A–26C), Payments Agent, Ledger Agent, Customer Billing Agent, Expenses Agent; SCM adds Autonomous Sourcing Assistant, Supplier Portal Advisor, Sourcing Command Center `[V]`.
- **AI Agent Studio**: design-time environment to "create, configure, validate, and deploy GenAI features and AI agents", including agent-team templates `[V]`.
- **invokeAsync API**: external programmatic access to agent teams ("Initiates an asynchronous request to the AI agent team…"), authorized by the caller's assigned role via OAuth 2.0 bearer `[V]`.

**Implication:** Oracle ships agentic capabilities at portfolio scale with role-based external access. A startup cannot match this breadth; the differentiator must be cross-ERP scope, SMB economics, and Arabic-first UX.

### SAP

**What they ship (verified from official pages; GA vs. preview explicitly separated):**
- "Joule Agents are AI agents with business process expertise that automate workflows at scale" — finance, spend, supply-chain agents; grounded by SAP Knowledge Graph + Business Data Cloud `[V]`.
- SAP Connect (2025-10-09): **14 new Joule Agents** across finance/HR/procurement/supply chain; some agents GA-planned Q1 2026; Joule Studio GA from December 2025; deep research in beta `[V]`.
- Availability "spans general release, early access, and planned releases across 2026" `[V]` — productivity figures (e.g., 20–30% financial-close improvement) are **stated targets, not measured results**.

**Implication:** treat SAP claims as roadmap-weighted. Their Knowledge Graph grounding is a genuine owned differentiator.

### Odoo

**What they ship (verified from official documentation):**
- Odoo 19 native **AI agents**: System Prompt + Topics (Instructions + Tools) + Sources; LLM options limited to ChatGPT/Gemini `[V]`.
- **Hard capability boundary:** without configured Topics, an agent is "only able to provide information, not complete tasks or make changes to the database" — write execution requires custom agent/tool development `[V]`.
- Positioning: product-internal assistant, not an open agent platform.

**Implication:** Odoo is the most relevant competitor *and* the first integration target. Its native AI does not currently constitute a general agentic execution layer (no cross-system scope, closed model set, no public tool-contract layer), which preserves room for an external execution layer — but Odoo iterates fast and ships in-product, so the window must be validated against real pilot demand, not assumed.

### CLI-Anything / CLI-Hub (open-source ecosystem)

**What it is (verified from repository):**
- HKUDS project, "Making ALL Software Agent-Native": 7-phase AI pipeline converting applications (demo targets: GIMP, Blender, LibreOffice — **desktop apps**) into agent-usable CLIs with REPL, `--json`, undo/redo `[V]`. 49,105 stars / 4,549 forks / created 2026-03-08 / Apache-2.0 `[V]` (GitHub API).
- CLI-Hub: package manager/registry for generated harnesses (`pip install cli-anything-hub`) `[V]`; Live Catalog lists 103+ CLIs.
- **Material correction to the PRD:** the current Live Catalog and capability matrices contain **no Odoo, no ERPNext, no ERP category at all** `[V]`. GitHub issue #194 ("[Contributor Sign-Up]", open) *proposes* an Odoo harness — it is a volunteer sign-up, not shipped functionality `[V]`.
- Security posture: its own SECURITY.md documents plaintext API keys in config files (0o600) and a threat model aimed at prompt-injected agents passing crafted arguments `[V]` — far below enterprise ERP requirements.

**Implication:** CLI-Anything is a legitimate *pattern reference* (agent-native CLI conventions, subprocess allowlists) and possibly a code-generation accelerator, but it is **not an ERP integration layer**, and the PRD's "Odoo harness exists in the ecosystem" premise is contradicted by current evidence. PRD experiment E9 (direct API vs. CLI harness) remains worth running, but the default POC path is the direct JSON-2 adapter.

---

## 2. Where the Gap Remains (verified reasoning)

1. **Cross-system orchestration.** All major vendors build agents inside their own ecosystem. No vendor positions its agent as a layer operating *across* multiple ERP systems.
2. **Arabic-first UX.** No vendor positions Arabic as first-class for agentic ERP. The first Arabic tool-calling benchmark only appeared in January 2026 (workshop-grade) — the space is genuinely early `[V]`.
3. **SMB affordability.** Oracle/SAP/Microsoft target enterprise pricing; Odoo is SMB-accessible but its native AI is a product assistant, not a governed execution layer.
4. **Permission-aware tool calling over *external* ERPs.** Microsoft's MCP server inherits Dynamics security because it *is* Dynamics. No vendor demonstrates a safe external agent operating a *different* vendor's ERP with full permission propagation — precisely this project's core hypothesis.
5. **SMB-grade trust layer.** Enterprise vendors assume enterprise governance; SMBs need simpler but still tamper-evident permission/audit/approval mechanisms. (Note: incumbent ERPs' native audit is change-log level only `[V]` — a trust layer above them is not redundant.)

---

## 3. Competitive Threats

### Near-term (0–12 months)

| Threat | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Odoo ships stronger native agentic execution (DB writes without custom tools) | Medium | High | Differentiate on Arabic UX, cross-system scope, SMB pricing, governance |
| D365 ERP MCP reaches GA | Medium | Low | Different market (SMB vs. enterprise) |
| MCP protocol shifts again (2026-07-28 is current) | High | Low | Version negotiation; pin per release |

### Medium-term (12–36 months)

| Threat | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Horizontal agent platforms add ERP tooling | Medium | High | Own the business action layer + eval data, not the framework |
| Oracle/SAP build cross-system orchestration | Low | High | SMB affordability + Arabic ontology |
| Odoo partners/acquires an AI layer | Low | High | Deep evaluation-data moat; adapter portability |
| Arabic tool-calling benchmarks/leadership emerge and commoditize the eval angle | Medium | Medium | Native (non-translated) Egyptian-dialect eval set as moat |

---

## 4. Positioning Recommendation

Based on verified evidence, the strongest positioning is not:

> "We are an AI layer for Odoo."

It is:

> "We are the AI execution layer for business operations — starting with Odoo, expanding to any ERP."

Position pillars, each anchored to a verified gap:
1. **ERP-agnostic** — vendors' agents are ecosystem-bound `[V]`.
2. **Arabic-first** — no incumbent leads there; benchmark space is embryonic `[V]`.
3. **SMB-affordable** — enterprise pricing leaves the segment open.
4. **Trust-layer-first** — permission/audit/approval above ERPs whose native audit is change-log level `[V]`.

The moat is not the model. The moat is the compounding combination: Arabic business ontology + native evaluation data + cross-ERP integration knowledge + policy/governance engine + audit infrastructure.
