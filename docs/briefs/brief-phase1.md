# Brief for the coding agent — Phase 1

**Paste everything below the line into your agent running in `/Users/sanya/Projects/alpha`.**

---

## Context

Phase 0 instrumented the system. Phase 1 makes it produce evidence.

Current state: 4,857 alphas, but **4,608 of them are the same structural template** — `rank(ts_zscore(divide(ts_backfill(FIELD,120), cap), W))` — across only 12 fields, with one operator family (`ts_zscore`). 486 alphas simulated, 2 submitted, both from that template, both cleared BRAIN's submission gate.

**The goal of Phase 1 is 40 submission attempts with recorded outcomes within four months.** That number estimates the true pass rate to about ±15%, which is what decides whether this system works.

### The binding constraint is diversity, not throughput

BRAIN rejects submissions correlating above ~0.70 with the user's own alphas. Alphas from the same template on the same field, differing only in window or decay, are typically far above that.

So the existing library yields roughly **one submittable alpha per field**, not per alpha generated. Twelve fields, two used, ~10 left. It cannot reach 40 no matter how many simulations run.

Expect submissions 3–6 to start failing `SELF_CORRELATION`. That is imminent.

**Therefore: widen structure and fields first, raise throughput second.**

### A second constraint that shapes the design

The Phase 2 validation study needs many *territories*, not many alphas. 384 near-duplicate alphas in one territory count as roughly one observation. The current grid is statistically wasteful.

## Rules

1. Do not change the statistical filters (plateau, DSR, subperiod, correlation). They are unvalidated and must stay fixed so Phase 1 can test them.
2. Every change must preserve the existing invariant: **no code path submits an alpha.**
3. Keep the test suite green and under ~5 seconds.
4. If a task is larger than described, stop and report rather than improvising.

---

## Task 1 — Reshape the grid: many territories, fewer alphas each

Currently `run_family` emits ~384 alphas per family. Change the default to a **7×7 window × decay grid = 49 alphas per territory**.

Same simulation budget then covers **~490 territories instead of ~62**, which is the sample size the validation study needs.

- Make grid dimensions configurable (`--windows`, `--decays`), defaulting to 7×7
- Keep the grid dense enough for the plateau filter to find neighbours — 7×7 is the floor; do not go below 5×5
- Do not delete the wide-grid capability; make it opt-in via `--grid wide`
- Report the projected territory count for a given simulation budget in `scripts/report.py`

---

## Task 2 — Break the single-template monoculture

Every alpha in the database uses `ts_zscore` inside one fixed shape. This is the root cause of the self-correlation ceiling.

**2a. Sweep operator families.** The constructor currently hardcodes `ts_zscore`. Extend it to sweep across time-series operator families from the KB — `ts_rank`, `ts_delta`, `ts_mean`, `ts_decay_linear`, `ts_std`, `ts_corr` and others the compatibility table allows.

Territory becomes `field × operator_family × horizon_band`, matching the definition Phase 0 instrumented. Add `--operators` to select families; default to sweeping a diverse set rather than one.

**2b. Vary the wrapper, not just the inner operator.** All 4,608 use `rank(... divide by cap ...)`. Add alternatives: `zscore`, `normalize`, different denominators, and group-relative forms. These change the correlation profile more than window changes do.

**2c. Wire the composite constructor into the CLI.** `composite_constructor.py` is implemented and tested but reachable only from library code, so in practice it does not exist. Add `scripts/run_composite.py` with the same campaign semantics as `run_family`.

**2d. Wire the evolution engine into the CLI.** Same situation — `evolution.py` works but nothing calls it. Add `scripts/run_evolution.py` seeded from alphas that passed the filters. Keep the existing bloat controls.

**2e. Add a pre-submission self-correlation check.** Before an alpha reaches the review queue, compute its correlation against the user's already-submitted alphas and show it in the console. Do not filter on it — **display it**, so the user stops copying alphas that will bounce. This is the highest-value small feature in Phase 1.

---

## Task 3 — Split the simulation budget deliberately

If the allocator sends every simulation toward promising uncrowded ground, Phase 1 recreates the range-restriction problem that made the first study impossible: no variation in crowding means nothing to correlate against.

Implement a three-way budget split, configurable, defaulting to:

| Arm | Share | Purpose |
|---|---|---|
| **Exploit** | 50% | Allocator's choice. The actual research. |
| **Random stratified** | 30% | **Randomly chosen territory, stratified across all four crowding quartiles.** Exists purely so Phase 2 is possible. |
| **Plateau fill** | 20% | Complete surfaces for promising families. |

The random arm is not optional and must not be "improved" by making it smarter. Its scientific value comes precisely from being unbiased. Log every allocation with its arm so the analysis can separate them later.

Stratify using the crowding quartiles from `data_field_snapshots`, computed across the whole catalog rather than across mined fields.

---

## Task 4 — Make overnight campaigns survivable

At 13 simulations/day, in-process threads are fine. At 200/day they are not — a restart currently marks jobs `interrupted` and abandons the remaining alphas in the family.

Build a **resumable campaign runner**:

- A campaign is a persisted unit of work: territory list, grid, budget arm, target count
- Progress checkpoints to the database, not to `jobs.json`
- On restart, campaigns resume from where they stopped
- `scripts/run_campaign.py --resume <id>`, and auto-resume on server start
- Respect the existing politeness limits: 3 concurrent, backoff, `Retry-After`
- A nightly campaign that runs the budget split unattended

Do **not** introduce Redis, Celery or a broker. Database-backed checkpointing is sufficient at this scale and adds no operational burden.

---

## Task 5 — Make throughput visible

The tool is designed for 200–500 simulations/day and is running at 13. Add to the console:

- Simulations today, this week, and a 30-day trend
- Territories touched, cumulative and new this week
- Distinct operator families and wrapper shapes used
- Submission attempts: total, passed, failed, and **failure counts by check** — this is the direct read on which local filters are blind
- Progress toward 40 attempts
- Alphas per territory (should fall toward ~49 as Task 1 takes effect)

The failure-by-check breakdown is the most important panel. If `SELF_CORRELATION` dominates, diversity is the problem. If `PROD_CORRELATION` dominates, that is evidence the crowding hypothesis is real and the map has value. If `LOW_SHARPE` dominates, the filters are too permissive.

---

## Task 6 — Verify BRAIN's operating limits before scaling

Before running anything at volume, determine and document:

- Simulation rate limits, concurrency caps, daily quotas
- **Submission quota** — how many alphas can be submitted per day or week. If it is one per week, the 40-attempt target needs a longer timeline and everything downstream shifts
- Whether limits differ by consultant tier
- What happens on quota exhaustion — error, throttle, or silent drop

If it cannot be determined from the API or docs, mark `NEEDS HUMAN` with exactly what to look for. **Do not scale throughput until this is answered** — hitting an undocumented limit at 200/day risks the account, which is the one irreplaceable asset here.

---

## Out of scope

No product features, billing, accounts, multi-user work, crowding map, network layer, or fertility model. No changes to the statistical filters. No new filtering techniques.

If you finish early, add tests.

---

## Definition of done

- [ ] Default grid 7×7; wide grid opt-in; territory projection in the report
- [ ] Constructor sweeps operator families and wrapper shapes
- [ ] Composite and evolution engines reachable from the CLI
- [ ] Pre-submission self-correlation shown in the review queue
- [ ] Three-arm budget split implemented, with the random arm stratified and every allocation logged by arm
- [ ] Campaigns resume after restart; nightly unattended run works
- [ ] Throughput dashboard live, including failure-by-check
- [ ] BRAIN rate and **submission** limits documented, or `NEEDS HUMAN`
- [ ] Tests green, suite under ~5s

## Report

Write to `docs/PHASE1.md`, leading with:

1. Projected territories per month at 200 simulations/day under the new grid
2. Count of distinct operator families and wrapper shapes now reachable
3. BRAIN's submission quota — **the number that determines whether 40 attempts in four months is feasible**
4. Anything needing the user
