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

**v0.1 in progress — `lab.md` v0.1 and Director system prompt complete.**

Remaining v0.1 scope per SPEC.md §15: `coverage.md` v1, EODHD MCP wrap, peer_regression skill, US analyst prompt, single-stock memo end-to-end via Slack bridge.

## Next Highest-Leverage Artifacts

From SPEC.md §17 (updated):

1. ~~**`lab.md` v0.1**~~ — **done** (v0.1.0, 2026-05-27).
2. ~~**Director system prompt**~~ — **done** (v0.1.0, 2026-05-27).
3. **EODHD wrapped as the first MCP** — fastest way to validate the capability pattern end-to-end on infrastructure already owned.

Also required for v0.1: **`coverage.md` v1** (per SPEC.md §15, not listed in §17 but blocks end-to-end runs).

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

## Changelog

| Date | Agent / note | Change |
|------|----------------|--------|
| 2026-05-27 | Bootstrap agent | Initial scaffold: stubs, AGENTS.md, README.md, pyproject.toml, .gitignore. |
| 2026-05-27 | Mandate agent | Authored lab.md v0.1.0 — full research mandate replacing stub. |
| 2026-05-27 | Director prompt agent | Authored prompts/director.md v0.1.0 — full Director system prompt replacing stub. |
