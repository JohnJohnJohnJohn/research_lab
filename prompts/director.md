# Research Director — System Prompt

**Status:** Stub — not yet populated.

**Purpose:** System prompt for the Research Director: orchestrator, mandate enforcer, and Slack bridge. The Director is the only Slack-visible agent. It loads `lab.md` as doctrine and `coverage.md` as active context, dispatches work to Coverage Agents, Regional Analysts, and Specialist Sub-Agents, and enforces the mandate.

**Role (from SPEC.md §3):**
- Top of the hierarchical multi-agent team
- Dispatch order (covered name): Director → Coverage Agent → Regional Analyst → Specialists
- Dispatch order (uncovered name): Director → Regional Analyst → Specialists
- Stateless executor of the mandate within a run

**Feedback responsibilities (from SPEC.md §9):**
- Classify feedback as factual / methodological / scope
- Run dependency-aware partial re-runs (not full pipeline); preserve locked sections
- Track recurring feedback patterns and surface `lab.md` edit suggestions
- Confirm before re-running on judgment-call feedback; auto-correct factual errors
- Route threaded replies, reactions, and slash commands to the owning agent

**Governed by:** SPEC.md §3 (Agent Architecture), §7 (Slack Bridge), §9 (Feedback Model), §14 (Locked Architectural Principles).

**Content TBD:** Full Director system prompt for v0.1 — see SPEC.md §15 and §17.
