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

**v0.1.2** — `lab.py` boot, coverage refactor, peer_regression skill, OpenRouter model routing. Next: first end-to-end smoke test, then Slack bridge (SPEC.md §15).

## Repository layout

```
research_lab/
├── lab.md, coverage.md       # two-file control plane
├── prompts/                  # role system prompts
├── mcp_servers/              # MCP integrations (one folder per server)
├── skills/                   # in-process Python utilities
├── coverage_state/           # per-ticker persistent memory
├── transcripts/              # per-run audit logs
└── lab.py                    # boot script (Director entrypoint)
```

## Getting started

### Requirements

- Python **3.11+**
- API keys in `.env`: `OPENROUTER_API_KEY`, `EODHD_API_KEY`, and `LAB_MODEL_*` vars (see `.env.example`)

### Install (recommended: editable)

From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

Optional dev tools (pytest, ruff):

```powershell
pip install -e ".[dev]"
```

This installs the `research-lab` CLI and the `skills` package. **Use editable install** for development — `lab.py` reads `prompts/`, `lab.md`, and `coverage.md` from the source tree.

### Configure

```powershell
copy .env.example .env
# Edit .env — set OPENROUTER_API_KEY, EODHD_API_KEY, LAB_MODEL_DIRECTOR, etc.
```

### Verify boot

```powershell
python -c "from lab import load_config; print(load_config().resolved_models)"
```

### Run

```powershell
research-lab "Initiate coverage on 9988 HK"
# or
python lab.py "Initiate coverage on 9988 HK"
```

Output is JSON to stdout. Slack mode (`python lab.py --slack`) is a stub in v0.1.

For agents: read **SPEC.md**, then **AGENTS.md**, then identify the next task for the current build phase.
