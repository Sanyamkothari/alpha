# Open Architectural Decisions: B2, B3, and B4

**Document Context:** Phase 1 Design Decisions & Architecture Tradeoffs  
**Date:** August 2026  
**Status:** Open for Alignment / Recommendations Specified (No Code Changes in this Document)  
**Historical Implementation Record:** See [docs/IMPLEMENTATION_RECORD.md](file:///Users/sanya/Projects/alpha/docs/IMPLEMENTATION_RECORD.md) for completed Part A work (B1 scaling, intra-surface deduplication, concurrency locking, Invariant 8 implementation).

---

## 1. Decision B2: Neutralization Sweep & Settings Axis Expansion

### 1.1 Context & Problem Statement
Currently, campaign territories are generated with a single fixed structural configuration:
- `neutralization = "SUBINDUSTRY"`
- `group = None` (ungrouped)
- Single-field depth-1 time-series transformations

However, `STRATEGY.md §10` documents that the settings axis (specifically neutralization: `NONE`, `SECTOR`, `INDUSTRY`, `SUBINDUSTRY`) separates platform pass from fail, representing one of the project's primary empirical insights. Certain economic signals (e.g. fundamental ratios vs price momentum) behave dramatically differently under broad sector vs granular subindustry neutralization.

### 1.2 The Core Conflict: Territory Definition vs Budget Mathematics
If neutralization is swept across all 4 levels during generation:
- A $7 \times 7$ grid ($49$ alphas) becomes $7 \times 7 \times 4 = 196$ alphas per territory.
- At a 200 sims/day budget, monthly coverage drops from **~122 territories/month down to ~30 territories/month** (only 122 territories over the 4-month Phase 1 timeline vs the 490-territory target in `PHASE1.md §2`).
- Sweeping neutralization inside the exploration grid quadruples the simulation cost spent on unpromising fields.

### 1.3 Options Evaluated

| Option | Description | Budget Impact | Strategic Coverage |
| :--- | :--- | :--- | :--- |
| **Option A: Fixed Baseline (`SUBINDUSTRY`)** | Maintain territory as 49 alphas with fixed `SUBINDUSTRY` neutralization. | Preserves 490 territories / 4-month target (100% budget efficiency). | Fails to discover alphas that only clear hurdles under `SECTOR` or `INDUSTRY`. |
| **Option B: Full 3D Grid Sweep ($7 \times 7 \times 4 = 196$)** | Sweep lookback $\times$ decay $\times$ neutralization in every campaign task. | $4\times$ simulation cost; cuts territory throughput to 122 territories / 4 months. | Complete settings coverage, but spends $75\%$ of budget varying settings on non-viable signals. |
| **Option C: Two-Tier Discovery + 4-Point Confirmation Probe (Recommended)** | Keep primary exploration at 49 alphas (`SUBINDUSTRY`). For passing plateau representatives, run an automated 4-point neutralization probe (`NONE`, `SECTOR`, `INDUSTRY`, `SUBINDUSTRY`) during confirmation. | Negligible overhead: 4 extra simulations per passing representative (<0.5% of daily budget). | Captures the settings-axis insight on viable signals without diluting primary exploratory search. |

### 1.4 Recommendation: Option C (Two-Tier Discovery + Confirmation Probe)
1. Maintain the canonical 49-alpha territory definition (`field_code`, `operator_family`, `horizon_band`) at `SUBINDUSTRY` neutralization for all primary campaign arms (`exploit`, `random_stratified`, `plateau_fill`).
2. Add a lightweight post-pass confirmation step: when an alpha satisfies Invariant 8 and clears the DSR/subperiod hurdles, simulate the 3 remaining neutralization variants (`NONE`, `SECTOR`, `INDUSTRY`).
3. If an alternate neutralization variant achieves superior Sharpe and lower turnover, update the promoted representative before submission.

---

## 2. Decision B3: Plateau Ratio & Ladder Geometry Calibration

### 2.1 Context & Problem Statement
In `app/services/plateau.py:50-51`, the fallback coordinate ladders (used when a surface is empty, while `_neighbours` dynamically derives ladders from populated surface points) and plateau threshold are defined as:
```python
WINDOW_LADDER: tuple[int, ...] = (5, 10, 22, 63, 126, 252)
DECAY_LADDER: tuple[int, ...] = (0, 4, 8, 16)
PLATEAU_RATIO = 0.6
```

Under Phase 1's standard $7 \times 7$ grid, the populated surface parameter points are:
- Windows: $(5, 10, 20, 40, 60, 120, 250)$
- Decays: $(0, 1, 2, 4, 6, 8, 16)$

### 2.2 The Geometric Artifact
When `_neighbours` dynamically derives coordinates from a populated $7 \times 7$ surface:
1. A 1-step decay neighbour on the surface differs by only 1 (e.g. decay 1 vs decay 2).
2. Backtests with decay 1 and decay 2 share over $98\%$ PnL correlation; their Sharpe ratios almost always agree.
3. Consequently, `PLATEAU_RATIO = 0.60` is substantially easier to pass on the $7 \times 7$ grid than when the threshold was originally calibrated on the wide $(0, 4, 8, 16)$ grid.
4. Because **Invariant 8** selects representatives by `neighbour_median_sharpe`, this geometric artifact inflates neighbourhood scores and distorts representative selection.

### 2.3 Options Evaluated

| Option | Description | Tradeoffs |
| :--- | :--- | :--- |
| **Option A: Heuristic Threshold Adjustment** | Guess new constants (e.g. raise `PLATEAU_RATIO` to 0.80) without empirical measurement. | Fast, but risks either choking off viable discoveries or preserving subtle distortion. |
| **Option B: Empirical Plateau Calibration & Fallback Ladder Alignment (Recommended)** | 1. Primary: Run empirical calibration of `PLATEAU_RATIO` against real surfaces in `database/wq.db` to measure true neighbour decay distributions.<br>2. Secondary: Synchronize fallback `WINDOW_LADDER` and `DECAY_LADDER` constants to match active $7 \times 7$ coordinates for consistency. | Grounded in empirical data; ensures Invariant 8 representative rankings reflect genuine economic stability. |

### 2.4 Recommendation: Option B (Empirical Calibration Protocol)
1. **Primary Calibration Target (`PLATEAU_RATIO`):** Execute a calibration script across the 36 dense territories (4,608 alphas in `database/wq.db`) and recent campaign results to compute the distribution of $\frac{\text{neighbour\_median\_sharpe}}{\text{self\_sharpe}}$. Set `PLATEAU_RATIO` at the 75th percentile of the empirical noise distribution to separate broad ridges from noisy points without guessing constants.
2. **Secondary Fallback Consistency:** Align fallback `WINDOW_LADDER` and `DECAY_LADDER` constants in `plateau.py` to $(5, 10, 20, 40, 60, 120, 250)$ and $(0, 1, 2, 4, 6, 8, 16)$ so fallback behavior matches dynamically derived surfaces.

---

## 3. Decision B4: Architecture for the 5 Uncalled Modules & Allocation Roles

### 3.1 Explanation of `evolution_slots` in `allocator.py`
In `backend/app/services/allocator.py`, the dataclass `BudgetAllocation` contains an `evolution_slots` field, and `SimulationBudgetOrchestrator` contains logic allocating `evolution_slots = 1` in `"mature"` mode.

**What it does:**
- `SimulationBudgetOrchestrator` is a legacy 3-slot allocator (originally in `allocator_bandit.py`) preserved under the backward-compatibility section of `allocator.py`.
- It allocates daily simulation capacity in a 3-slot model: in bootstrap mode (`passed_alphas < 5`), it assigns `explore_slots=2, confirm_slots=1, evolution_slots=0`; in mature mode, it assigns `explore_slots=1, confirm_slots=1, evolution_slots=1`.
- **Current Operational Status:** It is **not** called by the Phase 1 overnight campaign runner (`plan_budget_allocation`), which operates strictly on the 3-arm split (`exploit` 50%, `random_stratified` 30%, `plateau_fill` 20%).

### 3.2 Evaluation and Recommendation for the 5 Uncalled Modules

```
┌────────────────────────────────────────────────────────────────────────┐
│                      UNRESOLVED MODULE DISPOSITION                     │
├──────────────────────────────┬───────────────────┬─────────────────────┤
│ Module                       │ Current State     │ Recommendation      │
├──────────────────────────────┼───────────────────┼─────────────────────┤
│ 1. field_crowding.py         │ Uncalled          │ Retain for Reports  │
│ 2. compute_effective_trials  │ Never passed to   │ Wire into DSR when  │
│    (subperiod.py)            │ compute_dsr       │ PnL is available    │
│ 3. constructor.py (Layer 3)  │ Unreachable       │ Deprecate Layer 3   │
│ 4. evolution.py              │ Uncalled in prod  │ Keep Offline Tool   │
│ 5. composite_constructor.py  │ Uncalled in camp. │ Keep Offline CLI    │
└──────────────────────────────┴───────────────────┴─────────────────────┘
```

#### 1. `field_crowding.py` (Historical Crowding Velocity)
- **Current State:** Phase 0 built and backfilled crowding history in SQLite (`field_snapshots`). The allocator currently scores crowding based on the latest snapshot (`DataField.user_count`).
- **Tradeoff:** Computing velocity across time-series snapshots adds database join overhead without changing field ranking materially over daily intervals.
- **Recommendation:** Retain allocator snapshot scoring (`CROWDED_USER_COUNT = 2000`) for the real-time allocation loop. Use `field_crowding.py` as an offline batch reporting script for monthly catalog refresh audits. Do not wire into nightly campaign allocation.

#### 2. `compute_effective_trials` (`subperiod.py` / `plateau.py`)
- **Current State:** `compute_effective_trials` implements the Bailey & Lopez de Prado eigenvalue-based $N_{\text{eff}}$ calculation from a correlation matrix. However, `plateau.py:326` calls `compute_dsr(sharpe, n_trials=...)` without passing `n_eff`, defaulting to nominal trial count.
- **Tradeoff:** Passing nominal $N$ over-penalizes correlated grid cells ($N=49$ instead of $N_{\text{eff}} \approx 5\text{--}10$), whereas computing $N_{\text{eff}}$ requires PnL vectors for all surface points.
- **Recommendation:** Wire `n_eff` into `plateau.py::evaluate()` when the empirical PnL correlation matrix of the territory is available on disk; when PnL data is absent, fall back gracefully to nominal trial count $N$.

#### 3. `constructor.py` Layer 3 (`ts_corr` / `secondary_field`)
- **Current State:** Layer 3 in `constructor.py` generates expressions for two-field operators like `ts_corr(f1, f2, window)`. However, `FamilySpec` has no `secondary_field` attribute, and campaign tasks only specify a single `field_code`. Multi-field interaction logic is implemented separately in `composite_constructor.py`.
- **Tradeoff:** Keeping unreachable Layer 3 branches in `constructor.py` creates confusion about which module owns multi-field generation.
- **Recommendation:** Deprecate Layer 3 in `constructor.py` and designate `composite_constructor.py` as the single source of truth for cross-field interactions.

#### 4. `evolution.py` (Genetic Search Engine)
- **Current State:** Implements AST crossover, point mutation, and seed diversity gating (`check_seed_diversity`). Unit tests pass, but 0 evolved alphas exist in production DB.
- **Tradeoff:** Wiring evolution into automated nightly campaigns introduces stochastic, un-gridded expressions that violate the complete surface invariant (Invariant 2) before the baseline 3-arm campaign has validated standard pass rates.
- **Recommendation:** Keep `evolution.py` as an offline research CLI (`scripts/run_evolution.py`). Do **not** wire `evolution.py` into the production campaign allocator until Phase 1 reaches its 40-submission milestone and measures baseline empirical pass rates.

#### 5. `composite_constructor.py` (Multi-Factor Combinations)
- **Current State:** Generates multi-factor blends, spreads, residuals, and conditional triggers. Tested in unit tests, but not scheduled by the campaign runner.
- **Tradeoff:** Multi-factor grids have exponential parameter spaces ($49 \times 49 = 2,401$ combinations), which would exhaust nightly simulation budgets on a single interaction.
- **Recommendation:** Retain `composite_constructor.py` as a targeted CLI tool (`scripts/run_composite.py`) for hypothesis-driven research. Keep automated nightly campaigns strictly focused on single-field 7×7 grids for statistical rigor.
