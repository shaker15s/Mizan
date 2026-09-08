# Agent-Native ERP — Source Index

**Date:** 7 September 2026 (second pass — all `[V]` entries were retrieved and read during this research session, by the captain and/or four independent verification subagents; every listed URL was actually fetched unless marked otherwise)

---

## Labels

- **[V]** Verified — content was accessed and read during this research session.
- **[A]** Assessed — referenced by a verified source or by prior research; reasonable confidence but not independently re-verified this session.
- **[U]** Uncertain — not confirmed; treat with caution; validation task defined.

---

## 1. MCP (Model Context Protocol)

| Source | URL | Label | Notes |
|---|---|---|---|
| MCP Specification 2025-11-25 | https://modelcontextprotocol.io/specification/2025-11-25 | [V] | Full page read. Hosts/Clients/Servers, JSON-RPC 2.0, Security & Trust principles ("Hosts must obtain explicit user consent before invoking any tool"). **Not the latest version — current is 2026-07-28** (versioning page read). |
| MCP Architecture | https://modelcontextprotocol.io/specification/2025-11-25/architecture | [V] | Client↔server 1:1 sessions, capability negotiation. |
| MCP Tools | https://modelcontextprotocol.io/specification/2025-11-25/server/tools | [V] | `tools/list`/`tools/call`; `inputSchema` (JSON Schema 2020-12), optional `outputSchema`, `structuredContent`; "clients MUST consider tool annotations to be untrusted unless they come from trusted servers"; human-in-the-loop "SHOULD always be" able to deny tool invocations. |
| MCP Authorization | https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization | [V] | "Authorization servers MUST implement OAuth 2.1" (references OAuth 2.1 IETF draft, RFC 8414/7591/9728/8707). |
| MCP Security Best Practices | https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices | [V] | Confused-deputy (proxy servers MUST implement per-client consent); token passthrough forbidden ("MCP servers MUST NOT accept any tokens that were not explicitly issued for the MCP server"); SSRF; session hijacking; scope minimization. |
| MCP current version | https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning.md | [V] | "The **current** protocol version is 2026-07-28." PRD references 2025-11-25 as current — outdated. |

## 2. Odoo

| Source | URL | Label | Notes |
|---|---|---|---|
| External JSON-2 API (master) | https://www.odoo.com/documentation/master/developer/reference/external_api.html | [V] | Full page read. "New in version 19.0"; `/json/2/<model>/<method>`; bearer per-user API keys; programmatic key generate/revoke (default 10 keys/user; max 3-month lifetime); dedicated bot users recommended ("No user is impersonalized"); **external API only on Custom pricing plans** (not One App Free/Standard); each call = own SQL transaction, calls cannot be chained; JSON-2 validates all operations against access rights/record rules/field access; deprecation: db service removed in Odoo 20 (fall 2026)/Online 19.1 (winter 2025); common+object services removal scheduled Odoo 22 (fall 2028)/Online 21.1 (winter 2027). |
| External RPC API (19.0) | https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html | [V] | Exists (200); Danger notice: removal Odoo 22 (fall 2028)/Online 21.1 (winter 2027); JSON-2 named as replacement. |
| Security reference (19.0) | https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html | [V] | `ir.model.access` (per-group CRUD, union across groups); `ir.rule` record rules (global rules intersect, group rules union); field-level access via `groups` (restricted fields removed from views; access error on read/write). |
| Multi-company user guide (19.0) | https://www.odoo.com/documentation/19.0/applications/general/companies/multi_company.html | [V] | "In Odoo, multiple companies can be configured under one database." Enabling multi-company on Standard plan triggers Custom upgrade. |
| Company how-to (developer) | https://www.odoo.com/documentation/19.0/developer/howtos/company.html | [V] | `company_id`, company-dependent fields, `check_company`; example record rule `['|', ('company_id','=',False), ('company_id','in', company_ids)]`. |
| Odoo 19 AI Agents | https://www.odoo.com/documentation/19.0/applications/productivity/ai/agents.html | [V] | Agents = System Prompt + Topics (Instructions+Tools) + Sources; LLM limited to ChatGPT/Gemini; without Topics an agent is "only able to provide information, not complete tasks or make changes to the database"; product-internal assistant, not an open agent framework. |
| Odoo 19 AI overview | https://www.odoo.com/documentation/19.0/applications/productivity/ai.html | [A] | Companion overview page. |
| Odoo 17 data-access tutorial | https://www.odoo.com/documentation/17.0/developer/tutorials/restrict_data_access.html | [A] | Referenced by prior research; security model unchanged in 19.0 docs read this session. |
| Odoo chatter (audit trail level) | https://www.odoo.com/documentation/19.0/applications/productivity/discuss/chatter.html | [V] (partial) | Timestamped change notes in chatter; no tamper-evidence/append-only claims found. |

## 3. ERPNext / Frappe

| Source | URL | Label | Notes |
|---|---|---|---|
| Frappe REST API | https://docs.frappe.io/framework/user/en/api/rest | [V] | "generates REST API for all of your DocTypes out of the box"; `/api/resource/:doctype` CRUD + `/api/method`; auth: token (`token api_key:api_secret`), password/cookie, OAuth Bearer. |
| ERPNext Role-Based Permissions | https://docs.frappe.io/erpnext/permissions | [V] | Roles × DocTypes × Permission Levels (0–9) × document stages; User Permissions; full permission-type table incl. Share; v16 Data Masking. |
| Role and Role Profile | https://docs.frappe.io/erpnext/role-and-role-profile | [V] | "Role Profiles act as a template to store and select multiple roles." |
| Permission Query Conditions | https://docs.frappe.io/framework/get_query | [V] (partial) | "Applies conditions defined via Hooks or Server Scripts"; permlevel-1 fields "silently removed" from results. |
| Frappe Sites (multi-tenancy) | https://docs.frappe.io/framework/user/en/basics/sites | [V] | "Frappe is a multitenant platform and each tenant is called a site. A site has its own database."; `bench new-site`. |
| Frappe Server Scripts | https://docs.frappe.io/framework/user/en/desk/scripting/server-script | [V] (partial) | Existence + `server_script_enabled` confirmed. |
| Frappe Hooks | https://docs.frappe.io/framework/user/en/python-api/hooks | [V] (partial) | Existence confirmed. |
| Frappe Audit Trail | https://docs.frappe.io/framework/user/en/audit-trail | [V] | "Audit Trail can be used to view at most 5 previously amended versions of a submittable doctype" — version-compare level, not append-only/tamper-evident. |
| ERPNext Document Versioning | https://docs.frappe.io/erpnext/document-versioning | [V] (partial) | Track changes → Version Log. |
| ERPNext company setup / concepts | https://docs.frappe.io/erpnext/company-setup , https://docs.frappe.io/erpnext/concepts-and-terms | [V] (partial) | Multi-company within a site (search-summary-level evidence only). |
| Frappe Framework API overview | https://frappeframework.com/docs/user/en/api | [A] | Redirects to docs.frappe.io equivalent. |

## 4. CLI-Anything / CLI-Hub

| Source | URL | Label | Notes |
|---|---|---|---|
| HKUDS/CLI-Anything repository metadata | https://api.github.com/repos/HKUDS/CLI-Anything | [V] | Authoritative JSON: stars 49,105; forks 4,549; open issues 85; created 2026-03-08; Apache-2.0; default branch `main`. |
| CLI-Anything README | https://github.com/HKUDS/CLI-Anything | [V] (partial) | "CLI-Anything: Making ALL Software Agent-Native"; 7-phase AI pipeline (Analyze→Design→Implement CLI w/ REPL+JSON+undo/redo→tests→docs→publish); demo targets are desktop apps (GIMP, Blender, LibreOffice…); 2,461 tests; arXiv:2606.03854. |
| CLI-Anything SECURITY.md | https://github.com/HKUDS/CLI-Anything | [V] | Threat model = prompt-injected agent passing crafted arguments to backends (subprocess arg allowlists, escaping); **credentials stored in plaintext config files (0o600)**. No enterprise/ERP security claims. |
| CLI-Hub README | https://github.com/HKUDS/CLI-Anything/blob/main/cli-hub/README.md | [V] | "cli-hub — Package manager for CLI-Anything"; `pip install cli-anything-hub`. |
| CLI-Anything Live Catalog | https://reeceyang.sgp1.cdn.digitaloceanspaces.com/SKILL.md | [V] | 103+ CLIs (79 harness + 24 public), last-updated 2026-06-19. **No Odoo, no ERPNext, no ERP category** (Finance = Firefly III only). |
| Capability matrix registry | https://github.com/HKUDS/CLI-Anything/blob/main/matrix_registry.json | [V] | Full-text search "odoo|erpnext": zero hits (updated 2026-06-11). |
| GitHub issue #194 | https://api.github.com/repos/HKUDS/CLI-Anything/issues/194 | [V] | Title "[Contributor Sign-Up]", author yaseryy, **state: open**, created 2026-04-07. Proposes Odoo harness (CRM/Invoicing/Accounting/HR/Inventory). A sign-up, not a shipped harness. |
| PRD claims about catalog | (PRD §4.4/§184) | — | PRD's "the public catalog explicitly lists Odoo (Community) and ERPNext" is **CONTRADICTED** by the current Live Catalog and matrix registry `[V]`. |

## 5. Tool Calling / Agent Evaluation

| Source | URL | Label | Notes |
|---|---|---|---|
| BFCL V4 leaderboard | https://gorilla.cs.berkeley.edu/leaderboard.html | [V] | Last updated 2026-04-12. Categories: Agentic (Web Search, Memory), Multi-Turn, Single-Turn (Non-live/Live AST), Hallucination (Relevance/Irrelevance), Format Sensitivity, Latency. Snapshot: Claude-Opus-4-5 (FC) Overall 77.47 / Multi-Turn 73.76 / Live 88.58 / FC-vs-Prompt gap 77.47→33.47; GPT-5.2 (FC) 55.87, Multi-Turn 45.81 vs Live 81.85; P95 latency up to ~33s. No Arabic category. |
| BFCL V3 multi-turn blog | https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html | [V] | Multi-turn failure-mode analysis (e.g., "failure to perform implicit actions"); state+response dual verification. |
| τ-bench paper | https://arxiv.org/abs/2406.12045 | [V] | Abstract: "even state-of-the-art function calling agents (like gpt-4o) succeed on <50% of the tasks, and are quite inconsistent (pass^8 <25% in retail)." pass^k reliability metric; DB-state comparison. |
| τ-bench leaderboard (README) | https://raw.githubusercontent.com/sierra-research/tau-bench/main/README.md | [V] | Retail pass^1: claude-3-5-sonnet 0.692, gpt-4o 0.604; Airline: 0.460 / 0.420. README notes tasks are outdated; successor τ²/τ³-bench (banking, voice). |
| Arabic tool-calling benchmark | https://arxiv.org/abs/2601.05101 | [V] | "Arabic Prompts with English Tools: A Benchmark" (submitted 2026-01-08; IEEE BigData 2025 LLMs4All workshop). Claims first dedicated Arabic tool-calling benchmark; Arabic prompts degrade tool-calling accuracy by **5–10% on average** regardless of tool-description language. Built on translated BFCL; workshop paper, not an industry benchmark. |
| Arabic LLM tool-calling data strategies | https://aclanthology.org/2025.arabicnlp-main.28.pdf | [V] (partial) | ACL ArabicNLP 2025 — also notes absence of standardized Arabic tool-calling benchmarks. |
| Anthropic tool use overview | https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview | [V] | "Tool use (also called function calling) lets Claude call functions that you define…". |
| Anthropic define-tools guidance | https://docs.claude.com/en/docs/agents-and-tools/tool-use/define-tools | [V] | "Provide extremely detailed descriptions. This is by far the most important factor in tool performance." |
| OpenAI function calling | https://platform.openai.com/docs/guides/function-calling | [V] (partial) | Page exists ("Function calling (also known as tool calling)…"); description-quality wording not captured — treat specific OpenAI phrasing as [U]. |
| BFCL GitHub | https://github.com/ShishirPatil/gorilla | [A] | Project repo hosting BFCL. |

## 6. Security & Authorization

| Source | URL | Label | Notes |
|---|---|---|---|
| OWASP Top 10 for LLM Applications 2025 | https://genai.owasp.org/llm-top-10/ | [V] | Full list read: LLM01 Prompt Injection, LLM02 Sensitive Info Disclosure, LLM03 Supply Chain, LLM04 Data & Model Poisoning, LLM05 Improper Output Handling, LLM06 Excessive Agency, LLM07 System Prompt Leakage, LLM08 Vector & Embedding Weaknesses, LLM09 Misinformation, LLM10 Unbounded Consumption. 2023-24 version also linked (not read). |
| OWASP LLM01:2025 detail | https://genai.owasp.org/llmrisk/llm01-prompt-injection/ | [V] | "it is unclear if there are fool-proof methods of prevention for prompt injection"; RAG/fine-tuning "do not fully mitigate"; 7 mitigations incl. least privilege ("Provide the application with its own API tokens…"), human approval for high-impact actions, external-content isolation. |
| OWASP LLM06:2025 detail | https://genai.owasp.org/llmrisk/llm062025-excessive-agency/ | [V] | Mitigation #7 "Complete mediation": "Implement authorization in downstream systems rather than relying on an LLM…". |
| OAuth 2.1 (IETF draft) | https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/ | [V] | draft-ietf-oauth-v2-1-16, active WG draft (updated 2026-09-02), milestone Dec 2026 → IESG. "This specification replaces and obsoletes the OAuth 2.0 Authorization Framework described in RFC 6749 and the Bearer Token Usage in RFC 6750." **Not yet an RFC — cite as draft.** |
| OAuth 2.0 (RFC 6749) | https://datatracker.ietf.org/doc/html/rfc6749 | [A] | Foundation reference. |
| OAuth Token Exchange (RFC 8693) | https://datatracker.ietf.org/doc/html/rfc8693 | [A] | Relevant to OBO patterns generally (not applicable to Odoo JSON-2, which uses API keys). |
| OAuth 2.0 Security BCP (RFC 9700) | https://datatracker.ietf.org/doc/html/rfc9700 | [V] (referenced) | Cited by the MCP security best-practices page. |

## 7. Reliability / Transactions / Compliance

| Source | URL | Label | Notes |
|---|---|---|---|
| Stripe idempotent requests | https://docs.stripe.com/api/idempotent_requests | [V] | Save result (incl. errors) per key; replay on retry; keys pruned after ≥24h; "All POST requests accept idempotency keys"; `idempotency_error` on parameter mismatch; 409 on concurrent same-key; V4 UUIDs recommended. |
| Saga pattern | https://microservices.io/patterns/data/saga.html | [V] | "A saga is a sequence of local transactions… compensating transactions that undo the changes…" (Chris Richardson). |
| Transactional Outbox | https://microservices.io/patterns/data/transactional-outbox.html | [V] | Message stored in-transaction; "Messages are guaranteed to be sent if and only if the database transaction commits"; consumers must be idempotent. |
| FDA 21 CFR Part 11 §11.10 | https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11/subpart-B/section-11.10 | [V] | §11.10(e): "secure, computer-generated, time-stamped audit trails… Record changes shall not obscure previously recorded information." (eCFR: authoritative but unofficial.) |
| NIST AI RMF 1.0 | https://www.nist.gov/itl/ai-risk-management-framework | [V] | Released 2023-01-26; voluntary; four functions Govern/Map/Measure/Manage; revision in progress per White House AI Action Plan; GenAI profile NIST AI 600-1 (2024-07-26). |
| PostgreSQL RLS | https://www.postgresql.org/docs/current/ddl-rowsecurity.html | [A] | Safety-net framing; `SET LOCAL` transaction-scoped setting; superuser bypass. |
| Azure multi-tenant guidance | https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/overview | [A] | Shared-table / schema-per-tenant / database-per-tenant patterns. |
| NSA/CISA AI deployment guidance | https://media.defense.gov/2024/Apr/15/2003439257/-1/-1/0/CSI-DEPLOYING-AI-SYSTEMS-SECURELY.PDF | [A] | Referenced by prior research; not re-read this session. |

## 8. Competitive Landscape

| Source | URL | Label | Notes |
|---|---|---|---|
| Dynamics 365 ERP MCP server | https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/copilot/copilot-mcp | [V] (partial) | "The Dynamics 365 ERP MCP server provides a dynamic framework for agents to perform data operations and access the business logic of finance and operations apps"; earlier static server (13 tools) retired "due to limitations in the server's scale and extensibility". |
| Build agents with D365 MCP | https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/copilot/build-agent-mcp | [V] | Access "matches the agent's security role and environment context"; recommended orchestration model: **"Claude Sonnet 4.5"** with fallback **"GPT-5 (Chat)"** (verbatim, 2026-03-11 update); Claude non-Azure-hosted requires tenant-admin approval. |
| D365 ERP MCP announcement | https://www.microsoft.com/en-us/dynamics-365/blog/it-professional/2025/11/11/dynamics-365-erp-model-context-protocol/ | [V] (partial) | "The dynamic Dynamics 365 ERP MCP server is now in public preview!" |
| D365 AI overview | https://learn.microsoft.com/en-us/dynamics365/copilot/ai-get-started | [A] | Companion overview. |
| Oracle Fusion AI ERP features | https://docs.oracle.com/en/cloud/saas/fusion-ai/aiafl/ai-erp.html | [V] (partial) | Prebuilt ERP agents: Budget Adjustment Assistant, Cash Processing Agent, Payables Agent for Invoice Ingestion/Compliance/Control (26A–26C), Payments Agent, Ledger Agent, Customer Billing Agent, Expenses Agent, etc. |
| Oracle Fusion AI SCM features | https://docs.oracle.com/en/cloud/saas/fusion-ai/aiafl/ai-scm.html | [V] (partial) | Autonomous Sourcing Assistant, Supplier Portal Advisor, Sourcing Command Center, etc. |
| Oracle AI Agent Studio | https://docs.oracle.com/en/cloud/saas/readiness/common/25c/common25c/25C-common-wn-f38824.htm | [V] (partial) | "design-time environment… create, configure, validate, and deploy GenAI features and AI agents"; agent-team templates. |
| Oracle invokeAsync API | https://docs.oracle.com/en/cloud/saas/applications-common/26a/farca/op-orchestrator-agent-v2-workflowcode-invokeasync-post.html | [V] (partial) | "Initiates an asynchronous request to the AI agent team…"; role-based access ("The access is based on the role assigned to you", OAuth 2.0 bearer). |
| SAP AI Agents product page | https://www.sap.com/products/artificial-intelligence/ai-agents.html | [V] (partial) | "Joule Agents are AI agents with business process expertise that automate workflows at scale"; grounded by SAP Knowledge Graph + Business Data Cloud. |
| SAP Connect announcement | https://news.sap.com/2025/10/sap-connect-business-ai-new-joule-agents-embedded-intelligence/ | [V] (partial) | 14 new Joule Agents (finance/HR/procurement/supply chain); some agents GA-planned Q1 2026; Joule Studio GA from Dec 2025; deep research beta. |
| SAP availability caveat | https://learning.sap.com | [V] (partial) | "Availability spans general release, early access, and planned releases across 2026" — productivity figures (e.g., 20–30% close improvement) are targets/marketing, not measured. |
| SAP Joule product page | https://www.sap.com/products/ai-machine-learning/joule.html | [A] | Companion page from prior research. |

## 9. Primary Documents (Local)

| Source | Path | Label | Notes |
|---|---|---|---|
| Agent-Native ERP PRD | `01-spec/agent_native_erp_prd.md` (+ root copy) | [V] | 4,709 lines, 185 sections. Read in full this session. Not modified. Root copy is named `agent_native_erp_prd.md`, not `MASTER_PRD.md` as referenced in the task brief. |
| First-pass research package | `00-research/*.md` (prior versions) | [V] | Read in full this session; corrected and rewritten in this pass. |

---

## Uncertain Items Requiring Follow-Up (updated)

1. **Odoo webhook/event subscription for JSON-2-era writes** — not documented on the API page read; verify against Odoo 19 release notes before designing event-based verification.
2. **Odoo API-key `scope` granularity** — documented scopes are coarse (e.g., `rpc`); whether finer scoping exists is unresolved.
3. **Pre-retry reconciliation feasibility** — whether a platform-supplied reference/dedup field can be mapped onto `sale.order` (custom field or `client_order_ref`) for ambiguous-outcome recovery; test in sandbox (PRD experiment E3).
4. **OpenAI's official tool-description-quality guidance wording** — page confirmed to exist; exact wording not captured this session.
5. **ERPNext native idempotency** — whether ERPNext REST supports idempotency keys is unconfirmed.
6. **Arabic benchmark adoption trajectory** — arXiv 2601.05101 is the first dedicated benchmark but is workshop-grade; watch for an adopted industry leaderboard.
7. **τ-bench successor (τ²/τ³-bench)** — official README declares original tasks outdated; successor adds banking/voice; re-verify latest numbers before citing.
8. **MCP 2025-11-25 vs 2026-07-28 diff** — current version is 2026-07-28; a section-level diff has not been performed.
9. **ERPNext multi-company within a site** — supported per product docs, but evidence this session is search-summary level.
