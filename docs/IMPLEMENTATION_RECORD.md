# Implementation Record: Part A Architecture & Verification

**Document Context:** Part A Loop Restoration, Concurrency Hardening & Scaling Implementation  
**Date:** August 2026  
**Status:** Implemented, Tested & Independently Verified in Codebase  

---

## 1. Correlation Architecture & Scaling (B1)

### 1.1 Scope Reduction: Submitted Portfolio vs Entire Alpha Library
In earlier designs, candidate alphas were empirically cross-correlated against every alpha in the database that had ever passed a backtest. As simulation runs accumulated, this resulted in an unbounded $O(N^2)$ growth in pairwise correlation checks, degrading summary evaluation performance to unmanageable levels.

Under the implemented architecture:
- **Portfolio Scoping:** Empirical correlation is computed **exclusively** against confirmed submissions (`SubmissionAttempt.result == 'submitted'`) via `submitted_portfolio(db, exclude_alpha_id)`. `Alpha.status` is never queried as a secondary source of truth.
- **Intra-Family Redundancy:** Sibling alphas generated on the same parameter surface are not treated as portfolio collisions; instead, they are clustered by surface structure and marked with `redundant_with = <representative_id>`.

### 1.2 Side-by-Side Benchmark Curves: Full Family Evaluation (`GET /api/ui/summary`)
The benchmark measures full `evaluate()` sweeps across $K$ families of 49 alphas (with 1,300-day daily PnL vectors on disk) against a confirmed submitted portfolio ($N_{\text{submitted}} = 10$).

| $K$ Families | Total Alphas Evaluated | Pre-Fix Time ($O(N^2)$ Library Scope) | Post-Fix Time (Scoped to Submissions) | Speedup Factor |
| :--- | :--- | :--- | :--- | :--- |
| **$K = 2$** | **98 alphas** | 5.5 s | **0.250 s** | **22× faster** |
| **$K = 5$** | **245 alphas** | 30.9 s | **0.622 s** | **50× faster** |
| **$K = 10$** | **490 alphas** | 121.7 s | **1.255 s** | **97× faster** |
| **$K = 20$** | **980 alphas** | 489.4 s | **2.536 s** (independent repro: **2.64 s**) | **193× faster** |

### 1.3 Extrapolation to Production Target (490 Territories / 24,010 Points)
The target universe in `docs/PHASE1.md §2` specifies 490 candidate territories $\times$ 49 grid cells = 24,010 surface points.

- **Pre-Fix Extrapolation:** At $O(N^2)$ scaling over all passing alphas, evaluating 24,010 points required approximately **29,200 seconds (~8.1 hours)** per summary sweep.
- **Post-Fix Extrapolation:** With correlation checks scoped to confirmed submissions, evaluation scales **strictly linearly** in the number of evaluated territories:
  $$\text{Time} = 490 \times \approx 0.125\text{ s/territory} \approx \mathbf{61.3\text{ seconds}}$$
  A full-universe scan of all 490 territories completes in approximately **1 minute** for a 10-alpha submitted portfolio (or ~3.5 minutes for a 50-alpha portfolio).

### 1.4 Supporting Benchmark: Per-Candidate Correlation Overhead vs Portfolio Size
For a single candidate alpha evaluated against varying sizes of the confirmed submitted portfolio:

| Confirmed Submissions ($N_{\text{sub}}$) | Mean Check Time per Candidate | Throughput |
| :--- | :--- | :--- |
| **10 alphas** | **2.76 ms** | ~362 checks/sec |
| **50 alphas** | **10.25 ms** | ~98 checks/sec |
| **100 alphas** | **20.21 ms** | ~49 checks/sec |
| **200 alphas** | **38.73 ms** | ~26 checks/sec |
| **500 alphas** | **95.92 ms** | ~10 checks/sec |
| **1,000 alphas** | **190.28 ms** | ~5.2 checks/sec |

---

## 2. Intra-Surface Deduplication & Structural Slices

### 2.1 The Plateau-Ridge Deduplication Resolution
The automated discovery pipeline relies on parameter plateaus: an alpha is robust if slight parameter perturbations (e.g., window 20 vs 22, decay 4 vs 5) produce similarly strong returns. 

When correlation checks were performed unconditionally across all passing alphas, every point on a viable ridge was flagged as correlated with its adjacent neighbours ($r > 0.85$), causing the correlation gate to veto the exact robust plateau it discovered.

### 2.2 Dual-Layer Deduplication Strategy
1. **Intra-Surface Deduplication (Structure Slices):**
   - Candidates sharing the same structural tuple `(ts, cs, group, neutralization, truncation)` are grouped together.
   - Exactly one representative is selected per surface slice according to **Invariant 8**.
   - Non-representative points are marked `promoted = False` and assigned `redundant_with = <rep_id>`. They are **not** marked `is_correlated = True` or failed.
2. **Inter-Family Portfolio Gate:**
   - Candidate representatives are evaluated against `submitted_portfolio(db)`.
   - `INTERNAL_CORRELATION_THRESHOLD` remains **`0.55`** (`correlation.py:23`).
   - If empirical PnL is unavailable, a deterministic structural proxy check inspects field and operator collisions as a fallback.

---

## 3. Execution Models, Concurrency & Campaign Accounting

### 3.1 Account-Wide Concurrency Lock (`MAX_CONCURRENT_SIMULATIONS = 3`)
WorldQuant BRAIN enforces strict per-account concurrency limits. Attempting more than 3 simultaneous simulations across multiple processes or background workers triggers HTTP 429 / authentication lockouts.

- **Module-Level Semaphore:** Implemented via `_ACCOUNT_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_SIMULATIONS)` in `app/services/brain/client.py`.
- **Comprehensive Protection:** Both `simulate()` and `config_available()` acquire `_ACCOUNT_SLOTS` before sending `POST /simulations` requests.
- **Worker Queuing:** In `app/services/jobs.py`, simulation-bearing jobs (`run_family`, `fill`) are routed through a dedicated single-worker queue `_sim_queue` while compute-only jobs (evaluation, stats) run concurrently in a thread pool.

### 3.2 Exact Arithmetic Budget Closure & Resume Accounting
- **Whole-Surface Planning:** Campaigns allocate budget in whole-surface units (multiples of 49) across the three arms: `exploit` (50%), `random_stratified` (30%), and `plateau_fill` (20%).
- **Minimum Budget Guard:** Budgets below 49 simulations are rejected (`MIN_VIABLE_TERRITORY_SIMS = 49`), ensuring partial or single-cell fragments are never emitted.
- **Resumption Accounting:** `Campaign.budget_completed` strictly records the SQL aggregate `SUM(alphas_simulated)` from child tasks. If a task fails or is interrupted midway, resuming the campaign preserves completed simulations and prevents budget overshoots.
- **Zero-Work Task Classification:**
  - Expected completion (surface already fully simulated): Task marked `status = "skipped"`, error `"surface already complete"`.
  - Unexpected generator failure: Task marked `status = "failed"`, error `"expansion produced no candidates"`.

---

## 4. Representative Selection & Invariant 8

### 4.1 Invariant 8 — Plateau Neighbourhood Over Peak
When choosing which alpha to promote from a robust surface, raw Sharpe ratio is easily polluted by sample noise or overfitted parameter spikes. Invariant 8 mandates that representative selection is governed by **neighbourhood strength**:

$$\text{Score} = \text{median}\left( \{\text{Sharpe}(p') : p' \in \text{Neighbours}(p)\} \right)$$

### 4.2 Deterministic Ranking Hierarchy
When multiple candidate points on a surface pass all statistical and hurdle gates, the representative is selected using the following strict tiebreaking hierarchy:

1. **`neighbour_median_sharpe` (Highest):** Prioritizes broad ridges surrounded by consistently profitable configurations.
2. **`plateau_ratio` (Highest):** Ratio of neighbour median Sharpe to self Sharpe (must satisfy $\ge 0.60$).
3. **`decay` (Lowest):** Lower decay parameter ensures faster reaction time and reduced execution turnover.
4. **`sharpe` (Highest):** Raw point performance used only as a final tiebreaker among structurally equivalent neighbours.

---

## 5. Genetic Evolution Engine (`evolution.py`)

### 5.1 Architecture of `evolution.py`
The evolution engine implements genetic recombination of high-performing alpha expressions:
- **Parent Selection:** Sampled from top-performing alphas across distinct feature families.
- **AST Crossover & Mutation:** Safe AST manipulation substituting operators, windows, and cross-sectional normalizers while respecting grammatical constraints.
- **Diversity Gating:** Enforces non-monolithic seed pools (`check_seed_diversity`), requiring $\ge 3$ distinct time-series operator families in the seed pool to prevent self-correlated inbreeding.
- **Genealogy Tracking:** Lineage recorded in `alphas.parent_id`, `alphas.generation`, and `alphas.mutation_type`.

---

## 6. Quant Research Review Remediation & Statistical Layer Hardening (Part B)

### 6.1 Intra-Family Single-Linkage Clustering (F1/A1)
- **Problem:** When multiple candidate points sit on a continuous profitable plateau, adjacent cells exhibit high mutual correlation ($\rho > 0.85$). Naive correlation filtering either vetoed the whole ridge or picked arbitrary boundary points.
- **Resolution (`clustering.py`):** Implemented single-linkage agglomerative clustering at threshold $\rho \ge 0.90$. All points within the connected cluster are grouped into a single component, and exactly one **ridge center representative** is elected based on shrunk neighbourhood median Sharpe (`ridge_score`).
- **Portfolio Gating:** Inter-family portfolio correlation is evaluated against confirmed
  submissions (`SubmissionAttempt.result == 'submitted'`) on the **magnitude**
  $|\rho| \ge 0.55$. Gating on signed $\rho$ was a defect — it cleared an exact
  negation of a submitted alpha and reported it as zero-correlated; see §8.1. The
  reported figure keeps its sign.

### 6.2 Stratified Round-Robin Constructor Sampling (F2/A2)
- **Problem:** Constructor sweeps previously concentrated on a single operator family (`ts_zscore`), creating operator monoculture.
- **Resolution (`constructor.py`):** Added stratified round-robin sampling across `(layer, ts_sig)` strata during candidate generation, guaranteeing that every expanded family emits $\ge 5$ distinct time-series transforms (`ts_zscore`, `ts_rank`, `ts_delta`, `ts_mean`, `ts_decay_linear`, `ts_std_dev`, `ts_quantile`) and $\ge 1$ depth-2 composite candidate.

### 6.3 Extreme Value Theory (EVT) Hurdle & Lo (2002) SE Z-Tests (F3/F4/A3/A4)
- **Problem:** Simple Sharpe hurdles ($SR > 1.25$) fail to account for the number of trials searched ($N$), leading to false discoveries from data mining. Furthermore, subperiod Sharpe tests ignored return autocorrelation.
- **Resolution (`plateau.py`, `subperiod.py`):**
  1. **EVT Asymptotic Expected Maximum Hurdle (Gumbel correction):**
     $$E[\max_{i=1..N} SR_i] \approx \sqrt{2 \ln N} + \frac{\gamma}{\sqrt{2 \ln N}}$$
     Alphas must clear this multiple-testing expected maximum hurdle based on effective independent trials.
  2. **Lo (2002) Autocorrelation-Adjusted SE Z-Tests:** Computes spectral density / Newey-West adjusted standard errors for split-half and regime stability tests, rejecting decay if $Z$-score indicates statistically significant performance deterioration.

### 6.4 PnL Auto-Differencing, Sidecars & Strict Sharpe Reconciliation (F5b/A5)
- **Problem:** Cumulative PnL vectors from certain simulation sources could be mistaken for daily incremental returns, distorting variance and Sharpe calculations.
- **Resolution (`pnl_storage.py`):**
  - Daily PnL arrays on disk are validated with auto-differencing heuristics (checking monotonicity and bounds).
  - Sidecar metadata (`.meta.json`) tracks vector provenance, dates, and reported backtest metrics.
  - **Hard Precondition:** Enforces strict Sharpe reconciliation:
    $$|\text{sample\_sr} - \text{reported\_sr}| \le 0.10$$
    If sample Sharpe diverges beyond 0.10 from reported backtest Sharpe, the series is flagged and re-differenced.

### 6.5 Shrunk Ridge Scores & Discounted Thompson Sampling (F6–F10/A6)
- **Ridge Scoring:** Candidate ranking uses James-Stein style shrunk neighbourhood median Sharpe:
  $$\text{ridge\_score} = \alpha \cdot \text{median}(\text{Neighbours}) + (1-\alpha) \cdot \text{self\_sharpe}$$
- **Discounted Thompson Sampling (`allocator.py`):** Multi-armed bandit maintains exponential decay on historical rewards to adapt to shifting dataset fertility, with a strict **20% maximum dataset allocation cap** preventing over-concentration in any single dataset.
- **Filter Config Centralization (`filter_config.py`):** All filter thresholds and parameters are centralized in a frozen dataclass with runtime SHA-256 fingerprinting to guarantee configuration immutability during campaigns.

---

## 7. Advanced Validation Layer (CSCV, Perturbation, Novelty & Feedback)

### 7.1 Combinatorially Symmetric Cross-Validation (`cscv.py`)
- Implements Bailey et al. (2016) CSCV to compute the **Probability of Backtest Overfitting (PBO)**.
- Partitions the $T$-day return series into $S$ slices (e.g. $S=16$) and evaluates performance across all $\binom{S}{S/2}$ training/testing combinations. Alphas with $PBO > 0.50$ are rejected as overfitted.

### 7.2 Parameter & Noise Perturbation Analysis (`perturbation.py`)
- Evaluates alpha robustness under $\pm 10\%$ lookback window jitter and additive Gaussian noise.
- Measures Sharpe degradation and weight rank stability; signals that collapse under slight perturbation are flagged as unstable parameter spikes.

### 7.3 Structural & Semantic Novelty (`novelty.py`)
- Computes AST subtree isomorphism distance and token Jaccard distance against the existing submitted portfolio.
- Prioritizes structurally distinct mechanisms to maximize portfolio diversification and prevent `PROD_CORRELATION` collisions on WorldQuant BRAIN.

### 7.4 Batch Orthogonalization (`orthogonalization.py`)
- Applies greedy Gram-Schmidt residualization across shortlisted candidates before batch submission.
- Ensures that every alpha put forward adds incremental, orthogonal information to the existing portfolio.

### 7.5 Closed-Loop Feedback (`feedback_loop.py`)
- Continuously ingests simulation outcomes, pass rates, and submission results.
- Dynamically adjusts constructor search bounds, operator weights, and dataset exploration priorities in real time based on empirical platform feedback.

## 8. Post-Review Hardening (Part C)

Changes made after the independent code review in `CODE_REVIEW.md`. Each was verified
by a regression test that fails against the previous code, not by inspection.

### 8.1 The Correlation Gate Judges Magnitude, Not Sign

**Defect.** The gate compared signed $\rho$ against the threshold. An alpha whose daily
PnL is the exact negation of a submitted alpha ($\rho = -1.00$) therefore cleared it,
and — because `max_corr` was initialised at $0.0$ and only raised on $\rho >
\text{max}$ — was reported to the operator as **self-correlation 0.00**, the strongest
possible evidence of diversification. The console badge coloured on the same raw value,
so a perfect mirror rendered green with no warning.

**Reasoning.** The gate exists as a conservative proxy for BRAIN's own self-correlation
limit. BRAIN measures *duplication*, not portfolio variance: $X$ and $-X$ is one idea
submitted twice, however well the pair diversifies in theory.

**Resolution.** `check_portfolio_empirical_correlation` and
`compute_max_self_correlation_with_submitted` rank candidates on $|\rho|$ and report the
signed value, so the number displayed agrees with the number acted on. The badge colours
and warns on magnitude. Other consumers were audited and left alone: $N_{\text{eff}}$ is
eigenvalue-based and already sign-agnostic, and `/api/ui/correlation-matrix` returns raw
signed values for display.

### 8.2 PnL Storage Is Bound to Its Database

**Defect.** `PnLStore` wrote `<alpha_id>.npy` into a single directory derived from
`USER_DATA_DIR` alone, independent of `DATABASE_URL`. A rebuilt or second database
reusing ids $1..N$ silently inherited the first one's PnL and fed it to the
reconciliation, sub-period, DSR and correlation gates. Nothing errors — every file loads
successfully — so the failure surfaces only as confidently wrong verdicts.

**Resolution.** `default_pnl_dir()` binds the directory to the configured database. The
default database keeps the historic `database/pnl` path, so an existing single-database
install migrates nothing; every other URL gets `database/pnl-<digest>`.

### 8.3 One Source of Truth for "Submitted"

`submitted_portfolio()` defines membership from `SubmissionAttempt`, deliberately not
from `Alpha.status` (§1.1). `check_portfolio_correlation` nevertheless branched on
`Alpha.status` for both the sibling-skip and the collision label. The two agree today
only because `sync_alpha_platform_outcome` writes the status whenever an attempt
resolves. Both now read the attempt, so a genuinely submitted sibling cannot stop
blocking, and a recalled submission stops blocking immediately.

### 8.4 Verification That Can Fail

Three defects in the verification itself, none of which changed product behaviour but
all of which would have let a regression through:

- **`repro_review_findings.py` was not reproducible.** It seeded NumPy from `hash()` of a
  tuple containing a `str`, which `PYTHONHASHSEED` salts per process; the funnel differed
  every run (sub-period measured 33–40 across six runs). Seeds now derive from `crc32`,
  and the `>= 9` floors are replaced by an exact recorded baseline that names any drift.
- **It verified the funnel through a store the product never uses.** `evaluate()` received
  an explicit temporary `pnl_store`, while the report and surfaces checks that followed
  resolved the default store, found no PnL, and promoted nothing — so the script printed
  success over an empty shortlist. It now redirects the process-wide default store and
  asserts the promoted alpha reaches both the report and the surface payload.
- **A wall-clock assertion.** `test_e1_cscv_pbo_performance_and_accuracy` asserted
  `elapsed < 0.500`, which passed in isolation and failed under full-suite load on the
  same commit. Replaced by a best-of-3 scaling assertion that still catches a fall back to
  a Python-level loop over the 12,870 splits.

### 8.5 Recall Against the Live Portfolio

`scripts/calibrate_filter.py` scores the stack on synthetic families whose ground truth it
generated. `scripts/calibrate_against_portfolio.py` asks the complementary question: how
many alphas that BRAIN *already accepted* would this filter promote? Every submitted alpha
is a known positive, so the promotion rate over that set is a lower bound on real recall.

Against the ten live alphas in `GOLD_LEVEL_GUIDE.md` §3 (Sharpe 1.30–1.91, median 1.56):

| $N_{\text{eff}}$ | Haircut bar | Accepted alphas clearing it |
| :--- | :--- | :--- |
| 1 | 1.25 | 10 / 10 |
| 3 | 1.58 | 5 / 10 |
| 5 | 1.68 | 3 / 10 |
| 8 | 1.81 | 1 / 10 |
| 20 | 2.02 | 0 / 10 |

The bar is priced on *effective* trials, so the operative question is what
$N_{\text{eff}}$ real families produce once their PnL is backfilled — the synthetic
backtest runs at intra-family correlation 0.90, which prices the bar near 1.38. Anything
that pushes $N_{\text{eff}}$ above ~3 starts rejecting alphas this account has already
had accepted, and that is the signal to re-calibrate rather than to trust the synthetic
scorecard.
