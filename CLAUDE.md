# CLAUDE.md — Alpha Research Engine

Context for any agent working in this repository. Read this before acting.

---

## What this project is

A local research system that generates candidate trading alphas for the WorldQuant BRAIN platform, filters them statistically, and presents a shortlist for a human to submit manually.

**It is currently a personal research tool, not a product.** There is a long-term plan to turn it into one (`docs/strategy/BUSINESS_MODEL.md`), but that plan is explicitly gated behind evidence that does not yet exist. Do not build product features.

## Hard invariants — never violate

1. **No code path may submit an alpha to BRAIN.** Simulation is automated; submission is manual. `tests/test_brain_no_post.py` enforces this. Reading submission status via GET is fine.
2. **The LLM never writes expression syntax.** LLMs propose economic mechanisms; deterministic AST constructors emit code.
3. **The statistical filters are frozen during Phase 1** (plateau, DSR, subperiod, correlation). They are themselves unvalidated and the current phase exists partly to test them. Do not tune, improve, or "fix" them.
4. **One source of truth per fact.** See the drift incident below.

---

## Actual state — verified 20 Aug 2026

The README describes intended capability. These are measured numbers. Trust these.

| | |
|---|---|
| Project age | ~6 weeks (first alpha 2026-07-08) |
| Alphas in DB | 6,377 |
| Actually simulated | 695 distinct (740 backtest imports) |
| Passed BRAIN checks | 178 |
| Stored daily PnL vectors | 390 (in `database/pnl/`) |
| Recorded submission attempts | 17 |
| **Submitted to BRAIN** | **10** (`zqNXMEZE`, `N1bkwYGw`, `9qpOZjMq`, `xANpg6OW`, `j26KNdKo`, `RRmwqE5b`, `blQmY7br`, `LLG0Y2p9`, `QP7Znjbg`, `6XlmjjjG`) |
| **Accepted / active OS** | **10 active in Out-of-Sample** (Score: 7,913.0 pts, 10 submissions in OS, pending UTC leaderboard reset to Gold) |
| Fields touched | 32 of 6,583 (0.49%) |
| Operator families used | `ts_zscore`, `ts_rank`, `ts_delta`, `ts_mean`, `ts_decay_linear`, `ts_std_dev`, `ts_quantile` |
| Test suite | 262 tests passing in ~7.6s |

**The premise "this system produces good alphas" is unproven.** Ten submissions cleared BRAIN's gate into Out-of-Sample, which proves the loop closes. It does not establish a long-term pass rate.

### The single-template problem

Early iterations concentrated alphas in one shape: `rank(ts_zscore(divide(ts_backfill(FIELD,120), cap), W))`. Because BRAIN rejects submissions correlating >0.70 with the user's own alphas, the practical yield is roughly **one submittable alpha per field**, not per alpha generated. Phase 1 exists largely to break this monoculture.

### The drift incident — why single-source-of-truth matters here

For two weeks the local DB marked three alphas as submitted whose BRAIN IDs did not correspond to anything actually submitted, while the two genuinely submitted alphas were unrecorded. Cause: the `s` keystroke wrote local state with no platform verification.

Consequences now baked into the design, do not undo them:

- `platform_outcome` is **derived** from `submission_attempts`, never set directly
- `submission_attempts` records **attempts**, including failures, not just successes
- `data_field_snapshots` and `alpha_production_snapshots` are **append-only time series**, never single-value columns
- `sync_submission_outcomes.py` treats the **platform** as authoritative

If you find yourself adding a field that duplicates a fact stored elsewhere, stop.

---

## Current phase — Phase 1: produce evidence

**Goal: 40 submission attempts with recorded outcomes, within ~4 months.** That estimates the true pass rate to about ±15%, which decides whether any of the rest is worth doing.

Build work is complete (see [brief-phase1.md](file:///Users/sanya/Projects/alpha/docs/briefs/brief-phase1.md)). The phase is now **operational**, not engineering — see [PHASE1_OPERATING_GUIDE.md](file:///Users/sanya/Projects/alpha/docs/PHASE1_OPERATING_GUIDE.md).

### Rules for this phase

- **The metric is submission attempts, not simulations.** Simulations are cheap and create an illusion of progress.
- **Do not disable or "improve" the random stratified arm** (30% of budget). It deliberately samples crowded, unpromising territory. Its scientific value comes precisely from being unbiased — it is what makes the Phase 2 validation study possible. An earlier study was impossible because all data sat in one narrow band of crowding.
- **Territory** = `field × operator_family × horizon_band` (short 1–10d, medium 11–63d, long 64d+). This is the unit of analysis for everything statistical. 384 near-duplicate alphas in one territory count as ~1 observation.
- Record the budget `arm` on the alpha, not only on the campaign task.

### Out of scope until Phase 2 passes

No billing, accounts, multi-user features, crowding map, network layer, fertility model, landing page, or outreach. No changes to statistical filters.

---

## Open questions & Status

| Question | Status / Finding | Impact |
|---|---|---|
| **BRAIN submission quota per day/week** | Confirmed: **4 submissions/day** (documented in `PHASE1_OPERATING_GUIDE.md`) | ~480 possible attempts over 16 weeks vs 40 target; quota is not the binding constraint |
| **Past submission attempts and which check failed** | 17 attempts recorded in `submission_attempts` | 10 confirmed active submissions in OS, 0 platform rejections recorded |
| **Platform correlation checks (`PROD_CORRELATION` vs `SELF_CORRELATION`)** | Confirmed: Platform evaluates both `SELF_CORRELATION` ($\le 0.70$) and `PROD_CORRELATION` ($\le 0.70$) | Validates that research space is shared across users and multi-testing haircuts are necessary |

---

## Where things are

```
docs/
├── BRAIN_CONSULTANT_ELIGIBILITY_FAQ.md consultant eligibility, onboarding steps & legal checklist
├── BRAIN_KNOWLEDGE_BASE.md      8 platform submission gates, failure fixes & operator math
├── FINANCE_BASICS_GUIDE.md      section 5.5 finance basics, long/short equity & CAPM (alpha vs beta)
├── QUANTITATIVE_ANALYSIS_GUIDE.md section 4.5 statistical concepts, pillars & overfitting prevention
├── HOW_TO_USE_THE_BRAIN_PLATFORM.md section 3.5 fast expressions, settings & operators
├── METHODS_OF_ANALYZING_THE_STOCK_MARKET.md section 2.5 fundamental, technical & quant analysis
├── RESEARCH_CONSULTANT_GUIDE.md section 1.5 Research Consultant onboarding & compensation
├── WHAT_IS_WORLDQUANT_CHALLENGE_AND_HOW_IT_IS_SCORED.md support article FAQ & scoring breakdown
├── WORLDQUANT_CHALLENGE_GUIDELINES.md official competition rules & compliance mapping
├── GOLD_LEVEL_GUIDE.md          10K Gold level targets, daily points & expansion rules
├── PHASE1_OPERATING_GUIDE.md    what the human does weekly, and stop conditions
├── INVENTORY.md                 ground-truth survey of code and data
├── IMPLEMENTATION_RECORD.md     architecture records, benchmarks & review remediations
├── OPEN_DECISIONS.md            decisions record & architectural trade-offs
├── DECISIONS.md                 historical immutable decision log (D1-D10)
├── TELEMETRY_AUDIT.md           performance audit & evaluation latency benchmarks
├── PHASE0.md                    instrumentation work
├── PHASE1.md                    diversity + campaign work
├── strategy/
│   ├── BUSINESS_MODEL.md        the eventual product, gated on evidence
│   ├── PRODUCT_STRATEGY.md      earlier positioning work
│   ├── ROADMAP.md               phase sequencing and why it was reordered
│   └── VALIDATION_PROTOCOL.md   pre-registered study — READ BEFORE ANY ANALYSIS
└── briefs/                      the task briefs each phase was built from
```

**If asked to analyse whether crowding predicts alpha success:** read [VALIDATION_PROTOCOL.md](file:///Users/sanya/Projects/alpha/docs/strategy/VALIDATION_PROTOCOL.md) first. It is pre-registered. Running variants until one is significant is precisely the error this project exists to prevent, and doing it on the project's own business case would be self-defeating.

---

## Working style expected here

- **Run queries; do not infer from code.** If you report a number, show the query.
- **Report absences as absences.** `NOT PRESENT` and `CANNOT DETERMINE` are acceptable answers. Inventing a plausible number is not.
- Distinguish *code exists* from *code runs* from *code has been used*.
- **For any new constraint, exclusion, or scoring path: prove it fires on data written by the production writer, not by the test fixture.** Where a test constructs an identifier, it must construct it using the same function production uses.
- Migrations via Alembic only. Test both upgrade and downgrade.
- Keep the test suite green and under ~8 seconds (262 tests passing).
- If a task is larger than described, or the design looks wrong, **stop and report** rather than improvising.
