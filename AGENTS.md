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

**Pre-v0.1 — scaffolding.**

Repository skeleton per SPEC.md §13 is in place. No runtime, MCP servers, skills, or boot logic yet. Next work begins v0.1 per SPEC.md §15.

## Next Highest-Leverage Artifacts

From SPEC.md §17:

1. **`lab.md` v0.1** — the mandate. Most of the lab's "intelligence" lives here, not in code.
2. **Director system prompt** — the second-highest-leverage artifact. Interprets feedback, routes work, runs dependency-aware partial re-runs, surfaces pattern-based mandate edits.
3. **EODHD wrapped as the first MCP** — fastest way to validate the capability pattern end-to-end on infrastructure already owned.

Everything else is downstream of these three.

## Decisions Log

Architectural decisions made beyond what SPEC.md specifies, with rationale.

| Version | Decision | Rationale |
|---------|----------|-----------|
| v0.0.1 | Repository scaffolded per SPEC.md §13. | Phase 1 onboarding: directory skeleton and stub files only; no implementation. |

## Changelog

| Date | Agent / note | Change |
|------|----------------|--------|
| 2026-05-27 | Bootstrap agent | Initial scaffold: stubs, AGENTS.md, README.md, pyproject.toml, .gitignore. |
