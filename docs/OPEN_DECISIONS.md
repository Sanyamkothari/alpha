# Open Architectural Decisions & Design Rationale

**Document Context:** Integration Review Follow-Up & Loop Restoration  
**Date:** 16 August 2026  
**Status:** Approved & Implemented in Codebase  

---

## 1. Correlation Architecture & Scaling (Section B1)

### 1.1 Scope Reduction: Submitted Portfolio vs Entire Alpha Library
In earlier designs, candidate alphas were empirically cross-correlated against every alpha that had ever passed a backtest. As simulation runs accumulated, this resulted in an unbounded $O(N^2)$ growth in pairwise correlation checks, degrading evaluation performance.

Under the corrected architecture (Section A3/A4):
- **Portfolio Scoping:** Empirical correlation is computed **exclusively** against confirmed submissions (`SubmissionAttempt.result == 'submitted'` or `Alpha.status == 'submitted'`) via `submitted_portfolio(db, exclude_alpha_id)`.
- **Intra-Family Redundancy:** Sibling alphas generated on the same parameter surface are not treated as portfolio collisions; instead, they are clustered by surface structure and marked with `redundant_with = <representative_id>`.

### 1.2 Post-A3 Performance Benchmark
With the portfolio strictly scoped to submitted alphas, benchmark measurements on 1,300-day daily PnL vectors demonstrate sub-linear to low linear scaling:

| Submitted Portfolio Size ($N$) | Mean Correlation Check Time | Throughput (Checks / sec) |
| :--- | :--- | :--- |
| **10 alphas** | **2.76 ms** | ~362 / sec |
| **50 alphas** | **10.25 ms** | ~98 / sec |
| **100 alphas** | **20.21 ms** | ~49 / sec |
| **200 alphas** | **38.73 ms** | ~26 / sec |
| **500 alphas** | **95.92 ms** | ~10 / sec |
| **1,000 alphas** | **190.28 ms** | ~5.2 / sec |

### 1.3 Architectural Implications & Future Scaling
Because an active quantitative researcher typically maintains a portfolio of 10–100 submitted alphas, correlation checks during nightly evaluation complete in **< 25 ms per candidate**.

For institutional scale ($N > 5,000$ submitted alphas):
- Pre-computing a daily normalized return matrix $\mathbf{Z} \in \mathbb{R}^{T \times N}$ allows candidate correlation $\mathbf{r} = \frac{1}{T-1} \mathbf{z}_{\text{cand}}^T \mathbf{Z}$ to execute as a single vectorized matrix-vector multiplication in under 5 ms via BLAS/LAPACK.
- PnL storage uses compressed per-alpha binary arrays on disk (`PnLStore`), avoiding database bloat and eliminating redundant network fetches during evaluation.

---

## 2. Correlation Gate Calibration & Strategy Selection (Section B2)

### 2.1 The Plateau-Ridge Paradox & Resolution
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

## 3. Execution Models, Concurrency & Budget Control (Section B3)

### 3.1 Account-Wide Concurrency Lock (`MAX_CONCURRENT_SIMULATIONS = 3`)
WorldQuant BRAIN enforces strict per-account concurrency limits. Attempting more than 3 simultaneous simulations across multiple processes or background workers triggers HTTP 429 / authentication lockouts.

- **Module-Level Semaphore:** Implemented via `_ACCOUNT_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_SIMULATIONS)` in `app/services/brain/client.py`.
- **Comprehensive Protection:** Both `simulate()` and `config_available()` acquire `_ACCOUNT_SLOTS` before sending `POST /simulations` requests.
- **Worker Queuing:** In `app/services/jobs.py`, simulation-bearing jobs (`run_family`, `fill`) are routed through a dedicated single-worker queue `_sim_queue` while compute-only jobs (evaluation, stats) run concurrently in a thread pool.

### 3.2 Exact Arithmetic Budget Closure & Resume Accounting
- **Whole-Surface Planning:** Campaigns allocate budget in whole-surface units (multiples of 49) across the three arms: `exploit` (50%), `random_stratified` (30%), and `plateau_fill` (20%).
- **Minimum Budget Guard:** Budgets below 30 simulations are rejected (`MIN_VIABLE_TERRITORY_SIMS = 30`), ensuring partial or single-cell fragments are never emitted.
- **Resumption Accounting:** `Campaign.budget_completed` strictly records the SQL aggregate `SUM(alphas_simulated)` from child tasks. If a task fails or is interrupted midway, resuming the campaign preserves completed simulations and prevents budget overshoots.
- **Zero-Work Task Classification:**
  - Expected completion (surface already fully simulated): Task marked `status = "skipped"`, error `"surface already complete"`.
  - Unexpected generator failure: Task marked `status = "failed"`, error `"expansion produced no candidates"`.

---

## 4. Representative Selection & Invariant 8 (Section B4)

### 4.1 Invariant 8 — Plateau Neighbourhood Over Peak
When choosing which alpha to promote from a robust surface, raw Sharpe ratio is easily polluted by sample noise or overfitted parameter spikes. Invariant 8 mandates that representative selection is governed by **neighbourhood strength**:

$$\text{Score} = \text{median}\left( \{\text{Sharpe}(p') : p' \in \text{Neighbours}(p)\} \right)$$

### 4.2 Deterministic Ranking Hierarchy
When multiple candidate points on a surface pass all statistical and hurdle gates, the representative is selected using the following strict tiebreaking hierarchy:

1. **`neighbour_median_sharpe` (Highest):** Prioritizes broad ridges surrounded by consistently profitable configurations.
2. **`plateau_ratio` (Highest):** Ratio of neighbour median Sharpe to self Sharpe (must satisfy $\ge 0.80$).
3. **`decay` (Lowest):** Lower decay parameter ensures faster reaction time and reduced execution turnover.
4. **`sharpe` (Highest):** Raw point performance used only as a final tiebreaker among structurally equivalent neighbours.

---

## 5. Evolution Engine & Production Integration (Section B5)

### 5.1 Architecture of `evolution.py`
The evolution engine implements genetic recombination of high-performing alpha expressions:
- **Parent Selection:** Sampled from top-performing alphas across distinct feature families.
- **AST Crossover & Mutation:** Safe AST manipulation substituting operators, windows, and cross-sectional normalizers while respecting grammatical constraints.
- **Diversity Gating:** Enforces non-monolithic seed pools (`check_seed_diversity`), ensuring expressions represent distinct semantic mechanisms.

### 5.2 Production Deployment Plan
1. **Scheduled Evolutionary Arm:** Add an optional 4th allocation arm (`arm="evolution"`, 10–20% budget share) in `allocator.py` that ingests top-quartile alphas from the preceding 7 days of campaign runs.
2. **Surface Grid Generation:** Every child expression generated by evolution forms the base template for a 7x7 parameter exploration grid, preserving the **Complete Surface Invariant** (Invariant 2).
3. **Automated Submission Pipeline:** Evolved alphas follow the standard evaluation pipeline (`plateau.evaluate` $\rightarrow$ `correlation.check_portfolio_empirical_correlation` $\rightarrow$ promotion), ensuring that genetic exploration never bypasses production risk controls.
