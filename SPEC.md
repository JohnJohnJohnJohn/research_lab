# AI Research Lab — SPEC v0.2

A region-aware, fundamental-driven AI research organization for US, Hong Kong, and Chinese markets. Built around two human-edited markdown files, a Slack-fronted Director agent, a programmatic multi-agent backend, and an MCP-based capability layer.

***

## 1. Mission

Augment institutional research process with an AI team operating at machine scale but with institutional discipline. Fundamental research only — no technical analysis, no momentum, no auto-execution, no backtest-based evaluation.

**Explicit non-goals:**
- No technical analysis, price-action models, or momentum signals
- No high-frequency trading or auto-execution
- No backtest-based evaluation (training data contamination invalidates it)
- No invented configuration formats or central registries where existing standards suffice

***

## 2. Two-File Control Plane

The entire human-editable surface is two markdown files. Everything else is agent-owned or self-describing capability code.

- **`lab.md`** — research mandate. Stable doctrine: scope, source hierarchy, regional awareness questions (not answers), factor discovery protocol, quality gates. Edited rarely. Version-controlled. Evolves with market cycles.
- **`coverage.md`** — active context. Thin dispatch index: one row per covered name (ticker, sector, priority, conviction), one-liner macro regime tags, and a priority queue. Edited weekly or on portfolio change. Stays small permanently — per-name depth (KPIs, thesis, regime history, locked sections) lives in `coverage_state/[TICKER]/` and is loaded on demand by the Coverage Agent.

Every research output is stamped with the `lab.md` version + `coverage.md` hash that produced it.

**Bootstrap sequence:** load `lab.md` → Director system prompt; load `coverage.md` → active context; dispatch tasks; stamp every output.

***

## 3. Agent Architecture

Hierarchical multi-agent team. All agents are stateless executors of the mandate; Coverage Agents are stateless within a run but inject persistent context drawn from `coverage_state/` and prior memos.

```
Research Director (orchestrator, mandate enforcer, Slack bridge)
│
├── Coverage Agents (longitudinal ownership; auto-spawned per name)
│   - Active: declared in coverage.md; scheduled checks, event triggers
│   - Latent: auto-spawned but not in coverage.md; state preserved,
│            dormant until re-invoked
│
├── Regional Analysts (methodology layer)
│   ├── US Analyst
│   ├── HK Analyst
│   └── China A/H Analyst
│
└── Specialist Sub-Agents (shared pool)
    ├── Sector Expert
    ├── Macro Analyst
    ├── Valuation Engine
    └── Risk / Scenario Agent
```

**Dispatch order (covered name):** Director → Coverage Agent → Regional Analyst → Specialists.
**Dispatch order (uncovered name):** Director → Regional Analyst → Specialists.

**Key principle:** regional analysts carry a *region-aware discovery prompt*, not a hardcoded factor table. Region-awareness = knowing what *questions* to ask, not what *answers* to expect.

***

## 4. Factor Discovery Protocol

Region-awareness is contextual, not prescriptive. Agents discover what factors drive each market at the moment of analysis rather than applying hardcoded weights.

### Phase 1 — Regime Discovery
Before analyzing a stock, the regional analyst derives the current factor regime via three inputs:
1. **Peer regression** — correlate trailing 12M returns of 20-30 comps against candidate fundamentals; surface top explanatory factors
2. **Broker consensus mining** — extract dominant factors cited in recent sell-side research for the sector/region
3. **Macro signal** — current regime tag (risk-off, policy easing, earnings revision cycle) from the Macro Analyst

Output is a `FactorRegime` object:

```
{
  region, sector, as_of_date,
  primary_factors: [{factor, weight, rationale}],
  regime_source: [...],
  confidence: low/medium/high,
  regime_note: free-text deviation flags
}
```

Structured, logged, auditable, challengeable by the Director.

### Phase 2 — Stock Analysis Under Discovered Framework
Stock is analyzed using the framework Phase 1 just built — never reused blindly across tasks.

***

## 5. Capability Layer — Zero Invented Convention

The lab adopts existing standards rather than inventing its own.

### 5.1 MCP Servers
Each external integration is an MCP server. Self-describing via the protocol's native discovery: server `name`, `version`, `instructions` field, `tools/list`, `resources/list`, `prompts/list`. The lab adds no metadata layer on top.

Lab-specific routing context (domain, coverage, quality tier) is encoded as natural language in the MCP `instructions` field.

### 5.2 Skills
In-process Python modules under `skills/`. Self-describing via module docstrings and type signatures. The agent reads docstrings as tool descriptions, same way it reads MCP tool descriptions.

### 5.3 Discovery & Routing
At boot, the lab connects to configured MCP servers and imports the skills package. The resulting tool catalog is handed to the LLM agents as their toolset. No custom registry, manifest format, or metadata schema. **The LLM is the router** — it reads natural-language descriptions and picks what fits.

### 5.4 Graceful Degradation
Capability fallback (e.g., Bloomberg unavailable → use EODHD) is expressed in `lab.md` as a behavioral instruction, not as routing code. Outputs stamp which tier was used.

### 5.5 Capability Build Catalog

| Capability | Type | Build / Buy / Wrap | Effort | Notes |
|---|---|---|---|---|
| EODHD | MCP | Wrap existing fetcher | Low (1-2 days) | Existing fetcher  |
| Bloomberg | MCP | Build custom | High (1-2 wks) | blpapi wrapper; no reliable off-the-shelf alternative |
| EDGAR | MCP | Build or community | Low-Med | Community MCP servers exist; evaluate first |
| HKEX | MCP | Build custom | Medium | HTML scraping; no clean API |
| CNINFO | MCP | Build custom | Medium-High | Chinese-language parsing |
| Broker research | MCP (RAG) | Build custom | High | Email ingest + portal scrape + vector DB + retrieval |
| News | MCP | Wrap existing OpenClaw | Low | Already exists  |
| Peer regression | Skill | Build | Low | ~100 lines, pandas + statsmodels |
| DCF engine | Skill | Build | Medium | Likely have prior code  |

***

## 6. Runtime

**OpenAI Agents SDK** with **Claude as primary model** via OpenAI-compatible client or LiteLLM adapter. The SDK is multi-provider despite the name.

Why this choice:
- **MCP-native** — first-class support, not bolted on
- **Handoff-based orchestration** — maps cleanly to Director-led delegation
- **Lightweight** — boot script under ~200 lines; prompts do the heavy lifting
- **Provider-agnostic** — Claude as default; per-agent model selection possible (Director on Opus, specialists on Sonnet, mechanical tasks on cheaper models)
- **Replaceable** — prompts, MCP servers, skills, and the two-file control plane are runtime-independent; migration cost bounded to the boot script

**Rejected alternatives:** AutoGen (outdated, group-chat pattern fallen out of favor), LangGraph (heavier than needed for v0.1; revisit at v1.0 if compliance demands strict state machines), CrewAI (sequential rather than handoff-based), agent-swarm/desplega-ai (built for AI coding agents — domain-mismatched for research outputs).

***

## 7. Slack Bridge — Single Front Door

The user interacts with **only one bot in Slack**: the Director. Everything else runs programmatically behind it.

```
Slack #research-lab
  └── @director-bot          ← only Slack-visible agent
         │
         ▼
OpenAI Agents SDK runtime (Claude via LiteLLM)
  ├── Regional Analysts (US / HK / China A/H)
  ├── Specialists (Sector / Macro / Valuation / Risk)
  └── Coverage Agents (auto-spawned per name)
         │
         ▼
MCP capability layer
```

This preserves Slack's UX wins (chat, mobile, audit trail, easy interjection) without paying the cost of in-channel orchestration (rate limits, latency, tool-call clutter, sprawl).

***

## 8. Output Format

Capped at ~4 pages. Posted to Slack as **structured threaded messages** — one parent post per major section — to enable section-level feedback.

```
INVESTMENT MEMO — [TICKER] [REGION]
Stamped: lab.md vX.Y | coverage.md hash | date | data tier used
─────────────────────────────────
Conviction: BUY/HOLD/SELL | PT: $X | Upside: X% | Divergence vs. consensus: high/med/low
─────────────────────────────────
1. Investment Thesis (3-5 bullets)
2. Factor Regime (discovered for this task)
3. Fundamental Snapshot
4. Regional Context
5. Sell-Side Consensus (with disagreement flags)
6. Scenario Analysis (Bear / Base / Bull)
7. Catalysts & Timeline
8. Risks
```

No chain-of-thought visible; verdict + rationale + citations only.

***

## 9. Feedback Model

### Mechanics in Slack
- **Threaded reply** under a section = feedback for that section's owning agent (Director routes automatically)
- **Reactions** for lightweight signals:
  - 👍 / 👎 = approve / re-run with feedback
  - 🔁 = re-run section from scratch
  - 📌 = lock section verbatim; preserve on re-run
  - ⚠️ = compliance flag / hold
- **Slash commands** for explicit control: `/macro [feedback]`, `/rerun macro`, `/rerun all`, `/lock thesis`, `/escalate`

### Director Responsibilities
- **Classify feedback** as factual / methodological / scope
- **Dependency-aware partial re-runs** (not full pipeline): macro change → regime → analyst → valuation → memo; locked sections preserved
- **Pattern tracking**: surface `lab.md` edit suggestions when recurring feedback themes emerge across runs
- **Confirmation default**: ask before re-running on judgment-call feedback; auto-correct on factual errors

The act of giving feedback is the same act that improves the lab over time. No separate "lessons learned" process.

***

## 10. Evaluation — Forward-Only Qualitative

No backtests (training data contamination invalidates them). No automated scoring.

### Per-Output Review Card
Filled in by the user at read-time:
- Thesis clarity, factor regime logic, region awareness, source discipline, surprise value, action taken, free notes

### 90-Day Forward Tracking
Per output:
- Thesis still intact? Did identified factors actually drive the stock? What did the memo miss?

Accumulated review cards → patterns → `lab.md` edits. This is the lab's only learning loop.

***

## 11. State Architecture

| Path | Owner | Purpose |
|---|---|---|
| `lab.md` | Human | Doctrine |
| `coverage.md` | Human | Active context, watchlist |
| `coverage_state/[TICKER]/` | Coverage Agents | Standing thesis, prior memos, tracked KPIs, historical regimes |
| `transcripts/[run_id]/` | Runtime | Per-run agent interaction log (audit trail) |
| `prompts/` | Human (rarely) | Role system prompts |
| `mcp_servers/`, `skills/` | Developers | Capability implementations |

**Stateless agents within a run; persistent coverage memory across runs.** Every memo reproducible from its inputs + stamped doctrine version.

***

## 12. Operational Behavior

### Trigger Modes
- **Ad-hoc** — user requests research in Slack (v0.1)
- **Event-triggered** — earnings, filings, news flagged by OpenClaw  (v0.4+)
- **Scheduled** — nightly watchlist sweep, weekly thesis health check (v1.0)

### Failure Modes (expressed in `lab.md`)
- Premium MCP unavailable → use next-best capability, stamp tier in output
- Broker research empty for a stock → state explicitly, don't fabricate consensus
- Conflicting filing data across sources → halt, escalate to human
- Factor regime discovery low-confidence → output "regime unclear" rather than false precision

### Memory & Bias Avoidance
- Stateless re-analysis by default — bias avoidance over recall convenience
- Position context injected via `coverage.md`, never inferred
- Prior memos accessible only when the Coverage Agent explicitly pulls them forward

***

## 13. Project Structure

```
research_lab/
├── lab.md                      # research mandate (human-edited)
├── coverage.md                 # active context (human-edited)
│
├── coverage_state/             # machine-owned per-ticker memory
│   └── [TICKER]/{standing_thesis.md, kpis.md, regime_history.md,
               locked_sections.md, memo_history/}
│
├── prompts/                    # role-based system prompts
│   ├── director.md
│   ├── regional/{us, hk, china_ah}.md
│   ├── specialists/{sector, macro, valuation, risk}.md
│   └── coverage/agent.md
│
├── mcp_servers/                # one folder per MCP
│   └── {bloomberg, eodhd, edgar, hkex, cninfo, broker_research, news}/
│
├── skills/                     # in-process Python utilities
│   └── {peer_regression, dcf_engine}.py
│
├── transcripts/                # per-run logs (audit trail)
│
└── lab.py                      # boot: load files, connect MCPs, register
                                # skills, instantiate agents, run Slack bridge
```

Two markdown files for control. A prompts directory. MCP servers for data. Skills for in-process logic. A boot script. That's the entire lab.

***

## 14. Locked Architectural Principles

1. **Two-file control plane** — `lab.md` + `coverage.md` are the only human input
2. **Region-awareness is contextual, not prescriptive** — agents discover regimes; mandate provides questions, not answers
3. **Zero invented convention** — MCP and Python self-description natively; LLM is the router
4. **Slack as the only human interface** — single Director bot; structured threaded memos; feedback via threading + reactions + slash commands
5. **Forward-only qualitative evaluation** — no backtests; review cards and 90-day tracking are the only learning signal
6. **Stateless agents within a run; persistent coverage memory across runs**
7. **Graceful degradation by mandate, not by code** — capability fallback expressed in `lab.md`
8. **Feedback compounds into doctrine** — Director tracks patterns, surfaces `lab.md` edit suggestions; using the lab is how it learns

***

## 15. Build Phases

| Phase | Scope |
|---|---|
| **v0.1** | `lab.md` + `coverage.md` v1; EODHD wrapped as MCP; one Skill (peer_regression); Director + US analyst; single-stock memo end-to-end via Slack bridge |
| **v0.2** | Bloomberg MCP custom build; HK + China analysts; FactorRegime + discovery protocol live |
| **v0.3** | Filings MCPs (EDGAR / HKEX / CNINFO); broker_research RAG MCP; consensus mining feeds regime discovery |
| **v0.4** | Cross-regional synthesis for dual-listed names; event-triggered runs via OpenClaw integration  |
| **v1.0** | Coverage Agent auto-spawn live; nightly autonomous coverage sweep; review card workflow; 90-day tracking dashboard |

***

## 16. Open Refinement Items

- **A.** Director conviction calibration (BUY/HOLD/SELL vs. numeric score)
- **B.** FactorRegime TTL and cross-task reuse rules
- **C.** Coverage roster lifecycle on position exit (archive vs. retain in latent state)
- **D.** Coverage universe scope cap (~30 names initial; scale post-v1.0)
- **E.** Compliance retention policy for transcripts and memos
- **F.** GitHub-as-memo-store as additional artifact channel (versioning, PR-based review) — under evaluation

***

## 17. Highest-Leverage Next Artifacts

1. **`lab.md` v0.1** — the mandate. Most of the lab's "intelligence" lives here, not in code.
2. **Director system prompt** — the second-highest-leverage artifact. Interprets feedback, routes work, runs dependency-aware partial re-runs, surfaces pattern-based mandate edits.
3. **EODHD wrapped as the first MCP** — fastest way to validate the capability pattern end-to-end on infrastructure already owned.

Everything else is downstream of these three.
