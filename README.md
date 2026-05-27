# AI Research Lab

A region-aware, fundamental-driven AI research organization for US, Hong Kong, and Chinese markets. The lab augments institutional research with a multi-agent team operating at machine scale under institutional discipline — fundamental research only, no technical analysis, no auto-execution, no backtest-based evaluation.

Human control lives in two markdown files (`lab.md` + `coverage.md`). A Slack-fronted Research Director orchestrates Coverage Agents, Regional Analysts, and Specialists behind a single bot. External data and tools are exposed via MCP servers; in-process logic lives in Python skills. See **SPEC.md** for the full specification.

## Architecture

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

(Source: SPEC.md §7)

## Documentation

| Document | Purpose |
|----------|---------|
| [SPEC.md](./SPEC.md) | Authoritative specification — read first |
| [AGENTS.md](./AGENTS.md) | Onboarding and workflow for agent contributors |
| [lab.md](./lab.md) | Research mandate (human-edited doctrine) |
| [coverage.md](./coverage.md) | Active context and watchlist (human-edited) |

## Current build phase

**Pre-v0.1 — scaffolding.** Repository structure and stubs are in place. v0.1 scope: `lab.md` + `coverage.md` v1, EODHD MCP, peer_regression skill, Director + US analyst, single-stock memo end-to-end via Slack bridge (SPEC.md §15).

## Repository layout

```
research_lab/
├── lab.md, coverage.md       # two-file control plane
├── prompts/                  # role system prompts
├── mcp_servers/              # MCP integrations (one folder per server)
├── skills/                   # in-process Python utilities
├── coverage_state/           # per-ticker persistent memory
├── transcripts/              # per-run audit logs
└── lab.py                    # boot script (stub)
```

## Getting started (future)

Implementation begins in v0.1. Dependencies are declared in `pyproject.toml` but not yet installed or wired. Do not run `lab.py` — it is a stub.

For agents: read **SPEC.md**, then **AGENTS.md**, then identify the next task for the current build phase.
