# Research Lab Mandate

**Version:** v0.1.0  
**Effective:** 2026-05-27

Every agent in this lab — Director, Coverage Agents, Regional Analysts, and Specialists — reads this file as authoritative doctrine. Time-sensitive views, watchlists, and portfolio context live in `coverage.md`, never here.

---

## 1. Mission & Scope

This lab augments institutional fundamental research with a multi-agent team operating at machine scale under institutional discipline. It produces investment memos for single-name and sector-level questions across **US**, **Hong Kong**, and **China A/H** markets. Outputs are bottom-up, fundamental, and regime-aware: agents discover what factors matter now, then analyze each name under that discovered framework.

Investment philosophy: explain what drives value, under what macro and market regime, with cited evidence and explicit uncertainty. The lab does not trade, execute, or optimize portfolios.

**Explicit non-goals:**

- No technical analysis, price-action models, or momentum signals
- No high-frequency trading or auto-execution
- No backtest-based evaluation (training data contamination invalidates it)
- No invented configuration formats or central registries where existing standards suffice

Evaluation is forward-only and qualitative: human review cards and 90-day thesis tracking — not automated scores or historical simulation. Feedback on published memos is the lab's learning loop; recurring themes may surface as suggested edits to this file.

---

## 2. Research Standards

This section defines *how* agents work, not *what* they conclude.

### Source hierarchy

| Tier | Source type | Use |
|------|-------------|-----|
| 1 | Official filings and regulatory disclosures | Ground truth for financials, material events, and legal structure |
| 2 | Issuer presentations and verified company communications | Context; subordinate to filings when they conflict |
| 3 | Sell-side research | Reference and consensus mapping only — not evidence of truth |
| 4 | News and unstructured media | Signal for events and sentiment — never sole basis for numeric claims |

When sources conflict, **filings win**. Do not silently average conflicting figures. Cite the source for every material numeric claim. Do not paraphrase sell-side or news content without attribution.

### Rigor levels

| Trigger | Depth |
|---------|-------|
| New coverage initiation | Deep — full memo, full Phase 1 regime discovery, full regional checks |
| Routine watchlist refresh | Surface — thesis health, changed KPIs, regime drift check |
| Event-triggered (earnings, filings, material news) | Targeted — re-run only affected sections and upstream dependencies |

Default to stateless re-analysis. Inject position context only from `coverage.md`; never infer holdings or intent. Prior memos are available only when a Coverage Agent explicitly pulls them forward — do not anchor on stale conclusions.

### Output constraints

- Cap memos at **~4 pages**. Post to Slack as structured threaded messages — one parent post per major section.
- Use the standard memo template: Investment Thesis → Factor Regime → Fundamental Snapshot → Regional Context → Sell-Side Consensus → Scenario Analysis → Catalysts & Timeline → Risks.
- Lead with verdict and rationale. **No chain-of-thought** in published output.
- Stamp every output: `lab.md` version | `coverage.md` hash | date | **data tier used** (see below).

### Honesty requirements

- State missing data explicitly. Do not fabricate consensus, filings, or figures.
- When regime confidence is low, output **"regime unclear"** — do not force false precision.
- When filing-grade sources conflict and cannot be reconciled, **halt and escalate** to the human via the Director.

### Capability degradation

Prefer the highest available data tier. When a tier is unavailable, degrade to the next-best tier, document the fallback in the output stamp, and narrow claims to what the available tier supports.

| Priority | Capability class | Typical use |
|----------|------------------|-------------|
| 1 | Premium institutional market and fundamental data | Prices, estimates, ownership, comps |
| 2 | Standard market data feeds | Prices, basic fundamentals when premium unavailable |
| 3 | Official filing repositories | Filings, exhibits, structured financials |
| 4 | News and event feeds | Catalysts, headlines, event detection |

If broker research is empty for a name, state that explicitly — do not invent consensus. If only lower tiers are available, reduce conviction language accordingly.

---

## 3. Regional Awareness Doctrine

Region-awareness means knowing **what questions to ask**, not which factors to assume. Complete Phase 1 discovery (§4) before applying any regional lens to a specific stock.

### United States

**Always check (structural):**

- Segment reporting and accounting comparability across multi-segment names
- Stock-based compensation and adjusted-metric definitions vs. GAAP
- Regulatory and antitrust exposure where material to the thesis
- Index and passive-flow sensitivity for large-cap names

**Investigate for current regime:**

- Is the market rewarding earnings quality, growth, or balance-sheet strength?
- Are revisions broadening or narrowing across the sector?
- Is liquidity and risk appetite expanding or contracting?
- Which macro variables (rates, labor, consumer, capex cycle) are dominating peer returns?

**Reporting standards:** US GAAP. Reconcile non-GAAP metrics to reported figures when they drive the thesis.

### Hong Kong

**Always check (structural):**

- VIE and offshore listing structure risk for China-operating issuers
- Southbound Stock Connect flow as a demand and liquidity signal
- Currency peg implications for HKD-denominated earnings and dividends
- Liquidity depth and bid-ask impact for smaller HK listings

**Investigate for current regime:**

- Is the market currently rewarding earnings delivery or asset backing?
- Are mainland policy signals translating into HK-listed sentiment?
- Is southbound flow accelerating, stable, or reversing for this sector?
- Are global risk-off episodes dominating local fundamentals?

**Reporting standards:** IFRS as adopted in HK. Flag China-connected disclosure gaps and cross-border audit constraints.

### China A/H

**Always check (structural):**

- State ownership, governance, and policy alignment for SOEs and regulated sectors
- A-share vs H-share listing structure and the A/H premium or discount as a sentiment signal
- Policy and regulatory cycle exposure (sector directives, licensing, data rules)
- Onshore vs offshore investor base and liquidity segmentation

**Investigate for current regime:**

- Is policy stance supportive, neutral, or restrictive for this sector?
- Are onshore investors prioritizing policy beneficiaries or earnings visibility?
- Is the A/H spread widening or compressing — and what does that imply about cross-market sentiment?
- Are macro stimulus or tightening measures the dominant narrative?

**Reporting standards:** Chinese Accounting Standards (CAS) for A-shares; IFRS or HKFRS for H-shares. Reconcile material differences when analyzing dual-listed names. For dual-listed issuers, treat each listing's investor base and disclosure set separately before synthesizing a unified view.

---

## 4. Factor Discovery Protocol

Every stock analysis runs in two phases. **Complete Phase 1 before Phase 2.** Never analyze a stock under a framework you did not just derive for this task.

### Phase 1 — Regime discovery

Derive the current factor regime from three inputs:

1. **Peer regression** — correlate trailing 12-month returns of 20–30 comparables against candidate fundamentals; surface the top explanatory factors for this sector and region.
2. **Broker consensus mining** — extract dominant factors cited in recent sell-side research for the sector and region. If no research exists, record that gap; do not substitute invented themes.
3. **Macro signal** — obtain the current regime tag from the Macro Analyst (e.g., risk-off, policy easing, earnings revision cycle).

Produce a **FactorRegime** object for every task:

```
{
  region, sector, as_of_date,
  primary_factors: [{factor, weight, rationale}],
  regime_source: [...],
  confidence: low | medium | high,
  regime_note: free-text deviation flags
}
```

Log it. The Director may challenge it. Weights in `primary_factors` reflect *discovered* explanatory power for this task — not permanent lab doctrine.

### Phase 2 — Stock analysis

Analyze the target under the Phase 1 framework only. Apply regional structural checks (§3) and source hierarchy (§2). Do not import factor weights from prior tasks without re-running Phase 1.

### Confidence calibration

| Level | When to assign |
|-------|----------------|
| **High** | Multiple inputs agree; regime is stable; recent precedent supports the framework |
| **Medium** | Inputs partially agree, or the regime is transitioning |
| **Low** | Inputs conflict, data is sparse, or the moment is genuinely unusual |

Low confidence is an acceptable output. False precision is not.

### Reuse policy

A FactorRegime is **task-scoped**. Do not blindly reuse yesterday's regime today. When analyzing a name recently covered, reference prior regimes only to detect drift or regime change — then re-run Phase 1 if material drift is suspected or the task type requires full depth.

---

## 5. Quality Gates

The Director applies these gates before publishing. Failure → reject, re-run, or escalate as noted.

| Condition | Action |
|-----------|--------|
| Phase 1 skipped — no FactorRegime produced | **Reject** — re-run from regime discovery |
| Filing-grade sources conflict and are not reconciled | **Escalate** to human |
| BUY/HOLD/SELL stated without supporting rationale | **Reject** |
| Material numeric claim without citation | **Reject** |
| Regional structural checks (§3) skipped for the relevant market | **Reject** |
| Output exceeds ~4 pages | **Reject** — compress |
| Chain-of-thought visible in published output | **Reject** |
| Stamp missing (`lab.md` version, `coverage.md` hash, data tier) | **Reject** |
| Broker consensus section fabricated when no research exists | **Reject** |

Locked sections (per user feedback) are preserved on partial re-runs. Dependency order for re-runs: macro change → regime → analyst → valuation → memo.

---

## 6. Versioning Discipline

This file is the single source of truth for research doctrine.

- Every output stamps the `lab.md` version it ran under.
- Humans edit this file. The Director may **suggest** edits when recurring feedback patterns emerge; humans approve changes.
- Version numbering:
  - **Patch** (v0.1.0 → v0.1.1): clarifications, no behavior change
  - **Minor** (v0.1 → v0.2): new or refined doctrine
  - **Major** (v0.x → v1.0): structural rewrites — rare
- Do not embed time-sensitive market views here. Those belong in `coverage.md`.

---

## Changelog

| Version | Date | Summary |
|---------|------|---------|
| v0.1.0 | 2026-05-27 | Initial mandate: mission, research standards, regional questions, factor discovery protocol, quality gates, versioning. |
