# Phase 1: High-Diversity Generation & Evidence Production

## 1. Executive Summary

Phase 0 established instrumentation, append-only field tracking, and ground-truth submission attempt tracking. Phase 1 shifts the system from passive measurement to **active evidence production**, targeted at reaching **40 submission attempts with recorded outcomes within 4 months** to estimate true platform pass rates to within $\pm 15\%$.

The primary bottleneck identified in Phase 0 was a single-template monoculture ($94.9\%$ of generated alphas were from the identical `rank(ts_zscore(...))` template across 12 crowded fields). Phase 1 resolves this by reshaping search grids, introducing a 3-arm budget allocator, establishing automated pre-submission self-correlation checks, and deploying database-checkpointed overnight campaigns.

---

## 2. Grid Geometry & Search Reshaping (Task 1)

### Standard 7×7 Grid (49 Alphas / Territory)
- **Lookback Windows**: $(5, 10, 20, 40, 60, 120, 250)$ — 7 points spanning daily to annual horizons.
- **Decays**: $(0, 1, 2, 4, 6, 8, 16)$ — 7 points.
- **Unit Size**: $7 \times 7 = 49$ alphas per territory (dense enough for plateau filter and neighbourhood Sharpe estimation).
- **Territory Capacity**: A simulation budget of 200 sims/day covers **~122 territories/month** (490 territories across the 4-month target), compared to only ~15.6 territories/month under the legacy 384-candidate wide grid.

### Grid Capacity Projections
| Daily Budget | Standard Grid (7×7=49) | Wide Grid (384) | 4-Month Territories (Standard) |
|---|---|---|---|
| 50 sims/day | 1.0 terr/day (31/mo) | 3.9 terr/mo | 122 territories |
| 100 sims/day | 2.0 terr/day (61/mo) | 7.8 terr/mo | 245 territories |
| 200 sims/day | 4.1 terr/day (122/mo) | 15.6 terr/mo | **490 territories** |
| 500 sims/day | 10.2 terr/day (306/mo) | 39.1 terr/mo | 1,224 territories |

---

## 3. Template & Operator Diversity (Tasks 2a – 2d)

### Expanded Time-Series Operators
- `ts_zscore`: Z-score normalization over rolling window.
- `ts_rank`: Rolling time-series percentile rank.
- `ts_delta`: Absolute momentum / rate of change over window.
- `ts_mean`: Rolling moving average (smoothed signal).
- `ts_decay_linear`: Linearly weighted moving average.
- `ts_std_dev`: Rolling historical volatility.
- `ts_quantile`: Robust rank bucket / quantile transformation.

### Wrapper & Normalization Shapes
- **Cross-Sectional**: `rank`, `zscore`, `normalize`, `None` (raw signal).
- **Group-Relative**: `group_rank`, `group_zscore`, `group_neutralize` across `sector`, `industry`, `subindustry`.
- **Denominators**: `cap` (market cap), `volume` (dollar volume), `assets` (total balance-sheet assets), `close` (unadjusted price), `None`.

### Composite & Evolutionary Constructors
- `scripts/run_composite.py`: Multi-factor interactions supporting `blend`, `spread`, `orthogonal`, and `conditional` AST topologies.
- `scripts/run_evolution.py`: Genetic mutation, parameter tuning, and AST crossover.
  - **Diversity Pre-flight Gate**: Strictly requires $\ge 3$ distinct time-series operator families in the seed pool to prevent self-correlated inbreeding.

---

## 4. Pre-Submission Self-Correlation & Ground Truth (Task 2e)

- **Single Source of Truth**: Evaluates correlation against confirmed submissions in `submission_attempts` (`result = 'submitted'`). Never relies on stale `alphas.status`.
- **Auto-Backfill & Proxy Fallback**: Automatically backfills PnL vectors from BRAIN for shortlist candidates. When historical PnL is unavailable or overlaps are $<500$ trading days, computes structural proxy correlation and flags as `(proxy)`.
- **Visual Alerting**:
  - Green ($<0.55$): Low correlation, clean candidate.
  - Orange ($0.55 - 0.70$): Moderate correlation, proceed with care.
  - Red ($\ge 0.70$): **BRAIN self-correlation collision hazard** (submission will likely fail).

---

## 5. Three-Arm Budget Allocation (Task 3)

Allocations partition daily simulation capacity across three strategic arms:

1. **Exploit Arm (50%)**: Allocator selects uncrowded, high-coverage fields in neglected datasets (e.g. fundamental ratios, analysts, options flow).
2. **Random Stratified Arm (30%) — Calibration**:
   - Uniform random sampling across catalog crowding quartiles ($Q1, Q2, Q3, Q4$).
   - Explicitly includes crowded fields (e.g. `close`, `volume`, $48,000+$ users) as clean negative controls.
   - Tagged in console/UI as: `🔬 Calibration (expected to fail — required for validation study)`.
3. **Plateau Fill Arm (20%)**: Completes incomplete 7×7 surfaces for families showing promising ridge behavior.

---

## 6. Resumable Campaign Runner (Task 4)

- **Database-Checkpointed**: Campaigns and tasks are persisted in SQLite (`campaigns`, `campaign_tasks`), ensuring that interruptions or process restarts resume exactly where stopped.
- **Politeness & Rate Limits**: 3 concurrent simulations max, with exponential backoff on HTTP 429 and automatic `Retry-After` adherence.
- **CLI Commands**:
  - `python -m scripts.run_campaign --nightly`
  - `python -m scripts.run_campaign --resume <id>`
  - `python -m scripts.run_campaign --list`
  - Auto-resumes on web server startup via FastAPI lifespan hook.

---

## 7. BRAIN Platform Operating Limits & Submission Rules (Task 6)

| Parameter | Platform Limit | System Implementation | Status |
|---|---|---|---|
| **Simulations Concurrent** | 3 concurrent jobs | Semaphore pool capped at 3 | Verified |
| **Simulation Timeout** | ~180 seconds | Polling timeout at 240s with backoff | Verified |
| **Self-Correlation Gate** | 0.70 correlation vs user alphas | Pre-submission self-correlation badge | Verified |
| **Prod-Correlation Gate** | 0.70 correlation vs platform alphas | Evaluated at submission gate | Verified |
| **Simulation Daily Quota** | ~500 sims / day (consultant tier) | 200 sims standard nightly budget | Verified |
| **Submission Quota** | Weekly / Daily submission cap | `NEEDS HUMAN` (see notes below) | `NEEDS HUMAN` |
| **Quota Reset Schedule** | Midnight UTC vs Rolling window | `NEEDS HUMAN` (see notes below) | `NEEDS HUMAN` |

### `NEEDS HUMAN` Items for Human Verification:
1. **Exact Submission Quota by Consultant Tier**: Check BRAIN user settings/dashboard to confirm your account's exact daily or weekly submission cap (e.g. Bronze = 3/week, Silver = 5/week, Gold/Platinum = 10+/week).
2. **Weekly Quota Reset Time**: Confirm whether weekly submission limits reset on Monday 00:00 UTC or on a rolling 7-day window.
3. **Turnover & Subuniverse Thresholds**: If BRAIN rejects a submission with `SUBUNIVERSE` or `TURNOVER`, copy the exact prompt failure detail into the Unresolved / Rejection Modal in the console.
