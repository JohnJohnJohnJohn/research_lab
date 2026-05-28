# Agent Onboarding — Research Lab

This document orients every agent that works on this repository. Read it after `SPEC.md` and before making changes.

## Source of truth

- **`SPEC.md`** — authoritative specification for architecture, scope, build phases, and locked principles. If a decision is not in SPEC.md, do not silently invent it; log it in the Decisions Log below or propose a SPEC edit for human confirmation.
- **`.cursor/rules/karpathy-guidelines.mdc`** — behavioral doctrine: surface assumptions, simplicity first, surgical changes, verifiable success criteria.

## Agent workflow

1. Read **`SPEC.md`** in full (or the sections relevant to your task).
2. Check **Current Build Phase** (below) — do not skip ahead unless SPEC.md or a human directs it.
3. Identify the **smallest valuable next task** aligned with the current phase.
4. **Propose a plan** with verifiable success criteria before implementing.
5. **Implement surgically** — touch only what the task requires.
6. **Update this file** when you make a non-trivial decision (Decisions Log) or when the build phase advances (Current Build Phase).

## Current Build Phase

**peer_regression skill complete — first end-to-end smoke test is next.**

Remaining v0.1 scope per SPEC.md §15: Slack bridge completion.

## Next Highest-Leverage Artifacts

1. ~~All agent prompts (Director, regional, specialists, Coverage Agent)~~ — **done** (v0.1.0).
2. ~~**`lab.py` v0.1 boot**~~ — **done** (v0.1.0): env, prompts, agents, EODHD MCP, local CLI.
3. ~~**`coverage.md` v1**~~ — **done** (v0.1.1): thin dispatch index + seeded `coverage_state/` for 9988.HK, 700.HK, CRM.US; selective Director injection in `lab.py`.
4. ~~**`peer_regression` skill**~~ — **done** (v0.1.2): OLS cross-sectional regression, fetcher injection, EODHD fallback.
5. **Slack bridge** — complete `run_slack_mode()` / `handle_slack_message()` seam.

## Open Items

Questions and implementation gaps surfaced during build; resolve before or during the task that depends on them.

| ID | Item | Status | Notes |
|----|------|--------|-------|
| OI-1 | Pattern-tracking persistence for Director feedback themes | **Open** | Director prompt §6 defines behavior; cross-session storage not implemented. Candidate: append-only log under `coverage_state/` or dedicated `patterns/` file — TBD at boot-script / v0.1 wiring. |
| OI-2 | Director direct MCP access vs. route-only | **Resolved** | Director routes research tool calls to sub-agents; no direct MCP/skill calls for data. See Decisions Log v0.1.0 Director. |

## Decisions Log

Architectural decisions made beyond what SPEC.md specifies, with rationale.

| Version | Decision | Rationale |
|---------|----------|-----------|
| v0.0.1 | Repository scaffolded per SPEC.md §13. | Phase 1 onboarding: directory skeleton and stub files only; no implementation. |
| v0.1.0 | Capability tiers expressed as domain classes (premium institutional → standard market data → filing repositories → news), not vendor names. | Task constraint: no vendor names in lab.md; aligns with SPEC §5.4 (degradation by mandate). |
| v0.1.0 | FactorRegime reuse: task-scoped; reference prior regimes only for drift detection; re-run Phase 1 when drift suspected or full depth required. | SPEC §16-B open; task instruction specified reuse policy; encoded pending human review. |
| v0.1.0 | Rigor levels (initiation / refresh / event-triggered) codified in Research Standards. | SPEC §12 describes trigger modes at lab level but not per-task depth; needed for consistent agent behavior. |
| v0.1.0 Director | Director does not make direct MCP/skill tool calls for research data; routes all data work to sub-agents. | Task §1 specifies no direct tool calls except routing; keeps Director as orchestrator/synthesizer per SPEC §3, §7. |
| v0.1.0 Director | Quality-gate retry limit: 3 attempts before escalate. | SPEC/lab.md silent on retry count; concrete threshold needed for agent behavior. |
| v0.1.0 Director | Tool failure retry: 2 retries then escalate. | Prevents silent infinite retry; aligns with lab.md honesty requirements. |
| v0.1.0 Director | Dual-listed names: both regional analysts in parallel; Director synthesizes. | Task §3b; SPEC §3 implies regional layer but dual-list handling unspecified. |
| v0.1.0 Director | Latent coverage re-touch: notify user when `coverage_state/` exists but name absent from `coverage.md`. | Task §3a; makes latent state visible per SPEC §3 latent Coverage Agent model. |
| v0.1.0 EODHD MCP | Async + httpx throughout client and server. | MCP Python SDK (FastMCP) is async-native; avoids mixed sync/async call chains. |
| v0.1.0 EODHD MCP | FastMCP stdio transport; server metadata name `eodhd`, version `0.1.0` via `mcp._mcp_server.version`. | Official MCP SDK stdio pattern; FastMCP lacks public version param on constructor. |
| v0.1.0 EODHD MCP | `get_earnings_history` sourced from fundamentals API `Earnings` block, not `/calendar/earnings`. | Single API call for v0.1; calendar endpoint adds scope without v0.1 requirement. |
| v0.1.0 EODHD MCP | EODHD search returns 403 on demo key; mapped to structured `not_found` error. | EODHD documents search as unavailable on demo; agents get explicit fallback guidance. |
| v0.1.0 EODHD MCP | Template pattern: `client.py` (HTTP + typed exceptions) + `server.py` (tools + shaping + error dicts) + README + `.env.example`, stdio only. | First MCP server; subsequent servers (Bloomberg, EDGAR, etc.) should replicate unless logged deviation. |
| v0.1.0 EODHD MCP | No existing EODHD fetcher in repo; implemented direct REST wrapper. | SPEC §5.5 references external fetcher but none present in repo at build time. |
| v0.1.0 Regional | Three regional prompts share identical 7-section structure; region-specific content only in §3a, §4b, §7. | Task requirement; enables consistent Director handoff and future maintenance. |
| v0.1.0 Regional | MCP tool names (`get_price_history`, etc.) referenced as standard market data capability tools, not vendor names. | Aligns with lab.md tier language and task constraint. |
| v0.1.0 Regional | Exchange-specific guidance in §7 (Stock Connect eligibility, CSRC/controlling shareholder, policy bodies) is operational checklist detail, not new lab.md doctrine. | Task-authorized §7 additions; proposed lab.md §3 patch below if human wants doctrine-level encoding. |
| v0.1.0 Specialists | Four specialist prompts share 5-section structure (Role, Input, Core Task, Output, Quality). | Consistent with regional prompt pattern; narrower scope per Director §3c. |
| v0.1.0 Specialists | Valuation methodologies limited to DCF (`dcf_engine` skill per SPEC §5.5) + trading multiples aligned to FactorRegime. | SPEC does not define broader methodology set; no invented models. |
| v0.1.0 Specialists | Macro output uses structured five-dimension tag object; aggregate confidence = lowest dimension confidence. | Task specification (truncated in onboarding); aligns with lab.md §4 macro signal input. |
| v0.1.0 Specialists | Task onboarding message truncated at macro §4 output contract; completed from Director §3c + SPEC §8. | Logged for audit; output schemas inferred from handoff contracts. |
| v0.1.1 Specialists | Revised to full task spec: MacroRegimeTag, ValuationOutput, ScenarioOutput + RiskRegister verbatim formats. | User supplied complete onboarding; prior v0.1.0 used inferred JSON schemas. |
| v0.1.1 Specialists | Valuation Engine emits conviction (BUY/HOLD/SELL) with default mapping; extends Director §3c handoff. | Full task assigns conviction to ValuationOutput; Director §3c lists price target only — Director synthesizes memo header. |
| v0.1.1 Specialists | Valuation methodology selection (DCF/P-E, P/B/NAV, DDM/yield) driven by FactorRegime factors — proposed lab.md doctrine. | SPEC §5.5 names dcf_engine skill only; factor-driven method table from task, not yet in lab.md. |
| v0.1.1 Specialists | Sector Expert uses fundamentals only; no price history per full task §3b. | Aligns with narrow specialist scope; peer regression remains Regional Analyst + Phase 1. |
| v0.1.0 Coverage | Prompt at `prompts/coverage/agent.md` (not `template.md`). | Task specifies agent.md; template.md remains scaffold stub until removed or redirected in lab.py. |
| v0.1.0 Coverage | v0.1 operational state files: `standing_thesis.md`, `kpis.md`, `regime_history.md`, `memo_history/`, `locked_sections.md`. | SPEC §13 lists `{thesis.md, kpis.json, regimes/, memos/}` — same purposes, different names; lab.py should map or SPEC should be updated. |
| v0.1.0 Coverage | Drift detection runs on `post_phase1` invocation (after FactorRegime logged), not at pre-dispatch. | Director dispatch is Coverage Agent → Regional Phase 1; current FactorRegime unavailable at first injection. |
| v0.1.0 Coverage | Coverage Agent creates `coverage_state/[TICKER]/` on first touch after memo publish. | SPEC §11 silent on who creates directory; Coverage Agent is owner per §11. |
| v0.1.0 Coverage | `locked_sections.md` not in SPEC §13 — proposed addition for 📌 persistence across runs. | Director prompt handles locks within run; cross-run lock persistence needs storage. |
| v0.1.0 lab.py | OpenAI Agents SDK + `LitellmProvider` via `RunConfig` for all roles. | SPEC §6 names LiteLLM adapter; `.env.example` uses `deepseek/`, `dashscope/`, `claude-*` model strings. |
| v0.1.0 lab.py | Prompts loaded from disk at startup into in-memory dict; no templating engine. | Task constraint; Director gets `lab.md` + `coverage.md` appended to instructions. |
| v0.1.0 lab.py | Slack bridge is stub only (`run_slack_mode`, `handle_slack_message`); local CLI is v0.1 entrypoint. | SPEC §7 full Socket Mode deferred; missing Slack vars must not block CLI. |
| v0.1.0 lab.py | EODHD MCP via `MCPServerStdio` subprocess; attached to regional analysts + valuation + sector. | SPEC §5.1 self-describing MCP; boot wiring dict `CAPABILITIES` only. |
| v0.1.0 lab.py | Director handoffs to all eight sub-agents; no multi-phase choreography in code. | Orchestration stays in Director prompt; boot only instantiates agents. |
| v0.1.0 lab.py | `pyproject.toml` uses `openai-agents[litellm]` for multi-provider models. | Required for `.env.example` provider prefixes without scattering provider logic. |
| v0.1.1 coverage | `coverage.md` is a thin dispatch index only (<40 lines); per-name depth lives in `coverage_state/[TICKER]/`. | Injected wholesale into Director context every run; flat per-name detail would swamp context window at scale. |
| v0.1.1 lab.py | `detect_ticker()` + `load_coverage_state()` + `build_coverage_context()` inject task-scoped state before `Runner.run()`. | Director gets thin `coverage.md` always; relevant `coverage_state/` files only when ticker detected in task. `memo_history/` excluded — Coverage Agent handles memo injection. |
| v0.1.2 peer_regression | Skill accepts injected `fetch_prices` / `fetch_fundamentals` callables; sync httpx EODHD REST fallback when omitted. | Skill does not manage MCP subprocess; keeps unit-testable and matches SPEC §5.2 in-process skill model. |
| v0.1.2 peer_regression | `PeerRegressionResult` uses `{factor, direction, t_stat, note}`; analyst maps to FactorRegime `{factor, weight, rationale}`. | Task-spec output differs from lab.md §4 weight/rationale fields; mapping left to Regional Analyst synthesis layer. |
| v0.1.2 lab.py | `SKILLS` dict alongside `CAPABILITIES`; `peer_regression` assigned to regional analysts. | Minimal boot wiring per SPEC §5.3; no custom registry beyond import confirmation. |

### Proposed SPEC.md edits (applied 2026-05-28)

| Item | Proposed change | Status |
|------|-----------------|--------|
| §2 coverage.md description | Thin dispatch index; per-name depth in `coverage_state/[TICKER]/` | Applied |
| §13 coverage_state layout | `standing_thesis.md`, `kpis.md`, `regime_history.md`, `locked_sections.md`, `memo_history/` | Applied |
| §13 prompts/coverage path | `agent.md` (replaces `template.md`) | Applied |

### Proposed lab.md edits (not applied — human confirmation required)

| Region | Proposed addition | Rationale |
|--------|-------------------|-----------|
| Hong Kong | Add explicit Stock Connect **eligibility** check alongside southbound flow | Prompt §7 operational detail; lab.md §3 mentions flow but not eligibility |
| China A/H | Add explicit controlling-shareholder lookup via regulatory filings | Prompt §7 detail; lab.md §3 covers state ownership generically |
| Valuation (§2 or new) | Factor-driven methodology selection: earnings quality/growth → DCF or forward P/E; asset backing → P/B or NAV; yield → DDM or yield-spread | SPEC §5.5 names `dcf_engine` only; full specialist task defines selection table |

## Changelog

| Date | Agent / note | Change |
|------|----------------|--------|
| 2026-05-27 | Bootstrap agent | Initial scaffold: stubs, AGENTS.md, README.md, pyproject.toml, .gitignore. |
| 2026-05-27 | Mandate agent | Authored lab.md v0.1.0 — full research mandate replacing stub. |
| 2026-05-27 | Director prompt agent | Authored prompts/director.md v0.1.0 — full Director system prompt replacing stub. |
| 2026-05-27 | EODHD MCP agent | Implemented mcp_servers/eodhd/ — first MCP server (stdio, four tools). |
| 2026-05-27 | Regional prompts agent | Authored US/HK/China A/H regional analyst prompts v0.1.0; root .env.example. |
| 2026-05-27 | Specialist prompts agent | Authored macro, sector, valuation, risk specialist prompts v0.1.0. |
| 2026-05-27 | Specialist prompts agent | Revised specialist prompts to v0.1.1 per full task spec (verbatim output contracts). |
| 2026-05-27 | Coverage Agent agent | Authored prompts/coverage/agent.md v0.1.0 — stateful name-scoped coverage memory. |
| 2026-05-27 | lab.py boot agent | Implemented lab.py v0.1 — config, prompts, nine agents, EODHD MCP, local CLI. |
| 2026-05-28 | coverage refactor agent | coverage.md v1 thin index; seeded coverage_state/ for 9988.HK, 700.HK, CRM.US; lab.py selective state injection. |
| 2026-05-28 | peer_regression agent | Implemented skills/peer_regression.py; SKILLS wiring in lab.py. |
