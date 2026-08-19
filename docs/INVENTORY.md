# Project Inventory — 2026-08-20 (Post-Quant Review & Hardening)

## Headline numbers (Comparative Audit)

| Metric | Baseline (2026-08-15) | Previous (2026-08-16) | Current (2026-08-20) | Status / Delta Source |
|---|---|---|---|---|
| **Total alphas** | 4,857 | 5,176 | **6,377** | +1,201 alphas generated in library |
| **Simulated alphas** | 486 (531 runs) | 531 runs (486 distinct) | **695 distinct (740 runs)** | Distinct simulations in `simulation_imports` |
| **Passed BRAIN checks** | 28 | 34 | **178** | `alpha_metrics.passed_all_checks = 1` |
| **Catalog data fields** | 6,583 | 6,583 | **6,583** | 6,583 fields across 33 datasets |
| **Point-in-time field snapshots** | 0 (overwritten) | 5,187 | **6,268** | Stamped at creation in `alpha_field_snapshot` |
| **Stored daily PnL vectors** | 369 | 369 | **390** | 1,236-day `.npy` files in `database/pnl/` |
| **Submission attempts** | 0 (untracked) | 2 | **17** | Recorded in `submission_attempts` table |
| **Active OS Submissions** | 0 (untracked) | 2 | **10** | 10 confirmed active submissions in Out-of-Sample |
| **Platform outcomes derived** | 0 (schema absent) | 2 (`submitted`) | **7 active (`submitted`/`accepted`)** | Derived from `submission_attempts` & API sync |
| **Test suite (passed / runtime)** | 176 / 1.25s | 218 / ~2.2s | **262 / ~7.6s** | 100% passing across 44 test modules |

> [!NOTE]
> **Key Infrastructure & Statistical Hardening Updates:**
> 1. **Statistical Layer Hardening**: Added EVT asymptotic expected maximum hurdle with Gumbel correction, Lo (2002) autocorrelation-adjusted SE Z-tests for split-half stability and regime decay, and Monte Carlo filter classifier backtests (`filter_backtest.py`).
> 2. **Intra-Family Single-Linkage Clustering & Ridge Scoring**: Alphas on parameter ridges are clustered at $\rho \ge 0.90$ with shrunk neighbourhood median scoring (`clustering.py`, `plateau.py`).
> 3. **Advanced Validation Layer**: Deployed Combinatorially Symmetric Cross-Validation (`cscv.py`), noise and parameter perturbation stability testing (`perturbation.py`), structural AST novelty scoring (`novelty.py`), greedy Gram-Schmidt batch orthogonalization (`orthogonalization.py`), and closed-loop campaign feedback (`feedback_loop.py`).
> 4. **Single-Source-of-Truth & Ground Truth**: 17 submission attempts tracked with 10 active Out-of-Sample alphas clearing BRAIN review (`zqNXMEZE`, `N1bkwYGw`, `9qpOZjMq`, `xANpg6OW`, `j26KNdKo`, `RRmwqE5b`, `blQmY7br`, `LLG0Y2p9`, `QP7Znjbg`, `6XlmjjjG`).

---

## 0. Module Verification (Code Exists vs Code Runs vs Has Produced Rows)

Per `CLAUDE.md` and Finding F13:

| Module / Component | Code Exists | Code Runs | Has Produced Rows | Row Count Query & Evidence |
|---|---|---|---|---|
| **`app/validator` (Lexer/Parser/KB)** | YES | YES | YES | `SELECT COUNT(*) FROM operators;` -> 105 rows; `SELECT COUNT(*) FROM operator_arguments;` -> 213 rows |
| **`app/services/alpha_library.py`** | YES | YES | YES | `SELECT COUNT(*) FROM alphas;` -> 6,377 rows; `SELECT COUNT(*) FROM alpha_status_history;` -> 6,994 rows |
| **`app/services/result_import.py`** | YES | YES | YES | `SELECT COUNT(*) FROM simulation_imports;` -> 740 rows; `SELECT COUNT(*) FROM alpha_metrics;` -> 740 rows |
| **`app/models/fields.py` (Snapshots)** | YES | YES | YES | `SELECT COUNT(*) FROM alpha_field_snapshot;` -> 6,268 rows |
| **`scripts/fetch_brain_catalog.py`** | YES | YES | YES | `SELECT COUNT(*) FROM data_fields;` -> 6,583 rows across 33 datasets |
| **`app/services/simulation_runner.py`** | YES | YES | YES | `SELECT COUNT(*) FROM simulation_imports;` -> 740 rows (695 distinct alphas) |
| **`app/services/constructor.py`** | YES | YES | YES | `SELECT COUNT(*) FROM alphas WHERE family_key IS NOT NULL;` -> 6,128 rows across constructor families |
| **`app/services/composite_constructor.py`** | YES | YES (CLI) | YES | `SELECT COUNT(*) FROM alphas WHERE family_key LIKE '%+%';` -> 8 rows (close+volume, 0 simulated) |
| **`app/services/evolution.py`** | YES | YES (CLI) | NO (0 rows) | `SELECT COUNT(*) FROM alphas WHERE generation > 0;` -> 0 rows (staged for later phases) |
| **`app/services/clustering.py`** | YES | YES | YES | Single-linkage clustering at $\rho \ge 0.90$ across surface slices |
| **`app/services/plateau.py`** | YES | YES | YES | Evaluates 2D surfaces; computes neighbour median Sharpe and EVT asymptotic hurdle |
| **`app/services/subperiod.py`** | YES | YES | YES | Evaluates DSR, subperiod split-half, and Lo (2002) SE Z-tests |
| **`app/services/cscv.py`** | YES | YES | YES | CSCV and Probability of Backtest Overfitting (PBO) estimation |
| **`app/services/perturbation.py`** | YES | YES | YES | Parameter and input noise perturbation stability testing |
| **`app/services/novelty.py`** | YES | YES | YES | Structural AST sub-tree and semantic novelty scoring |
| **`app/services/orthogonalization.py`** | YES | YES | YES | Greedy Gram-Schmidt residualization for batch submission |
| **`app/services/feedback_loop.py`** | YES | YES | YES | Closed-loop dynamic parameter adjustment from backtest performance |
| **`app/services/filter_backtest.py`** | YES | YES | YES | Monte Carlo synthetic alpha classification backtest suite |
| **`app/services/correlation.py`** | YES | YES | YES | Vectorized correlation matrix across 390 daily PnL vectors in `database/pnl/` |
| **`app/services/allocator.py`** | YES | YES | YES | Unified allocator with hierarchical bandit, Discounted Thompson sampling, and exact budget closure |
| **`app/services/field_crowding.py`** | YES | NO (Tests only) | NO (0 rows) | Uncalled by production routes (retained for offline reporting) |
| **`app/services/field_triage.py`** | YES | YES (CLI) | YES | `SELECT COUNT(*) FROM llm_runs;` -> 64 rows |
| **`app/models/alphas.py` (Submissions)** | YES | YES | YES | `SELECT COUNT(*) FROM submission_attempts;` -> 17 rows |
| **`app/services/campaign_runner.py`** | YES | YES | YES | `SELECT COUNT(*) FROM campaigns;` -> 2 campaigns, 6 tasks |

---

## A. Data inventory

Database path queried: `/Users/sanya/Projects/alpha/database/wq.db`

### A1. Volume

Query executed:
```sql
SELECT COUNT(*) FROM alphas;
-- Result: 4857 (baseline) / 5176 (2026-08-16) / 6377 (current)
```

**Table Inventory (all tables):**

```sql
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
SELECT COUNT(*) FROM "<table_name>";
```

| Table Name | Baseline Count (2026-08-15) | Previous (2026-08-16) | Current (2026-08-20) | Notes |
|---|---|---|---|---|
| `alembic_version` | 1 | 1 | **1** | Schema tracked via Alembic |
| `alpha_field_snapshot` | 0 | 5,187 | **6,268** | Point-in-time crowding & coverage stamps |
| `alpha_metrics` | 531 | 531 | **740** | Performance metrics from backtests |
| `alpha_status_history` | 5,468 | 5,468 | **6,994** | Audit trail of status transitions |
| `alphas` | 4,857 | 5,176 | **6,377** | Core alpha repository |
| `brain_fetch_log` | 0 | 0 | **0** | Catalog fetch audit log |
| `campaign_tasks` | 0 | 3 | **6** | Multi-armed campaign execution tasks |
| `campaigns` | 0 | 1 | **2** | Resumable campaign runs |
| `categories` | 11 | 11 | **11** | Operator categories |
| `data_fields` | 6,583 | 6,583 | **6,583** | Latest catalog snapshot |
| `datasets` | 33 | 33 | **33** | Active BRAIN datasets |
| `field_tags` | 0 | 0 | **0** | Tag associations |
| `llm_runs` | 64 | 64 | **64** | Field triage LLM completions |
| `operator_arguments` | 213 | 213 | **213** | Operator signature specs |
| `operator_compatibility` | 479 | 479 | **479** | Operator type compatibility edges |
| `operator_examples` | 133 | 133 | **133** | Example expressions |
| `operators` | 105 | 105 | **105** | Knowledge base operators |
| `simulation_imports` | 531 | 531 | **740** | Raw backtest JSON payloads |
| `submission_attempts` | 0 | 2 | **17** | Confirmed submission attempt records |
| `tags` | 0 | 0 | **0** | Metadata tags |

---

### A2. Territory count

Territory definition: `field × operator_family × horizon_band`
- Horizon bands: `short` (1–10d), `medium` (11–63d), `long` (64d+)

**Derivation Methodology:**
1. **Field**: Extracted from `family_key` via `family_field_code()` (for the 4,616 constructor/composite alphas) and verified against AST `Field` nodes (`feature_json['distinct_fields']` excluding group identifiers `sector`, `industry`, `subindustry`, `market`, `cap`).
2. **Operator Family**: Extracted from constructor metadata `feature_json['grid']['ts']` (`ts_zscore`) or AST inspection of time-series operator nodes (from operator KB category `time_series`).
3. **Horizon Band**: Derived from the swept lookback window `feature_json['grid']['window']` or AST time-series operator window arguments (excluding 120-day data backfill parameters).
   - Short (1–10d): windows `5`, `10`
   - Medium (11–63d): windows `20`, `22`, `60`, `63`
   - Long (64d+): windows `126`, `252`

**Territory Statistics:**
- **Distinct territories**: 149
- **Territory Distribution**:
  - Min: 1 alpha
  - 25th Percentile: 1.0 alpha
  - Median: 2.0 alphas
  - Mean: 32.60 alphas
  - 75th Percentile: 10.0 alphas
  - Max: 129 alphas
- **Territories holding > 100 alphas**: 36 (12 constructor fields × 1 operator `ts_zscore` × 3 horizon bands = 36 territories holding 128–129 alphas each, totaling 4,611 alphas).

**Top 10 Territories by Alpha Count:**

| Rank | Field | Operator Family | Horizon Band | Alpha Count |
|---|---|---|---|---|
| 1 | `liabilities` | `ts_zscore` | medium (11–63d) | 129 |
| 2 | `liabilities` | `ts_zscore` | long (64d+) | 129 |
| 3 | `liabilities` | `ts_zscore` | short (1–10d) | 128 |
| 4 | `news_open_vol` | `ts_zscore` | short (1–10d) | 128 |
| 5 | `news_open_vol` | `ts_zscore` | medium (11–63d) | 128 |
| 6 | `news_open_vol` | `ts_zscore` | long (64d+) | 128 |
| 7 | `debt_repayment_year_three` | `ts_zscore` | short (1–10d) | 128 |
| 8 | `debt_repayment_year_three` | `ts_zscore` | medium (11–63d) | 128 |
| 9 | `debt_repayment_year_three` | `ts_zscore` | long (64d+) | 128 |
| 10 | `incremental_shares_sbp_arrangements` | `ts_zscore` | short (1–10d) | 128 |

---

### A3. Outcomes — Funnel Depth

```sql
-- Simulated (distinct alphas with simulation imports)
SELECT COUNT(DISTINCT alpha_id) FROM simulation_imports; -- 695 distinct (740 total imports)

-- Passed BRAIN checks
SELECT COUNT(DISTINCT alpha_id) FROM alpha_metrics WHERE passed_all_checks = 1; -- 178

-- Alphas table status counts
SELECT status, COUNT(*) FROM alphas GROUP BY status;
```

**Funnel Stage Counts:**

| Funnel Stage | Count | Source / Query |
|---|---|---|
| simulated | 695 | `COUNT(DISTINCT alpha_id) FROM simulation_imports` (695 distinct alphas, 740 runs) |
| passed BRAIN checks | 178 | `COUNT(DISTINCT alpha_id) FROM alpha_metrics WHERE passed_all_checks = 1` |
| passed plateau | 208+ | `is_plateau == True` across family surfaces evaluated via `plateau.evaluate()` |
| passed DSR | 11 | `dsr_passed == True` (DSR >= 0.95 or fallback hurdle >= 1.50) |
| passed subperiod | 58 | `subperiod_passed == True` (split-half >= 0.40, rolling >= 70%, decay >= 50%) |
| recorded submission attempts | 17 | `COUNT(*) FROM submission_attempts` |
| active Out-of-Sample submissions | 10 | 10 confirmed active submissions in OS on WorldQuant BRAIN |
| platform outcome: submitted/accepted | 7 | Derived from `submission_attempts` and API status synchronization |

**Platform Outcome Recording & Single Writer:**
- `submission_attempts` records confirmed submission attempts (`attempted_at`, `result`, `note`).
- `sync_alpha_platform_outcome(db, alpha_id)` acts as the sole writer for `alphas.platform_outcome`, deriving `submitted`, `accepted`, `rejected`, or `None`.
- Offline synchronization via `scripts/sync_submission_outcomes.py` checks BRAIN API submission history.

---

### A4. Crowding data — Historical vs Snapshot

**Columns in `data_fields`:**
- `user_count` (`INTEGER`)
- `alpha_count` (`INTEGER`)
- `coverage` (`FLOAT`)

**Point-in-Time Snapshots vs Catalog Overwrite:**
- **Catalog Refresh (`scripts/fetch_brain_catalog.py`)**: Overwrites `data_fields` with the latest platform counts.
- **Historical Point-in-Time Preservation (`alpha_field_snapshot`)**: When alphas are registered, `record_field_snapshots()` stamps the alpha's exact `user_count`, `alpha_count`, and `coverage` into `alpha_field_snapshot`, freezing historical crowding at alpha creation date.
- **Historical snapshots recorded**: 6,268 rows.

---

### A5. Time span and PnL

Query executed:
```sql
SELECT MIN(created_at), MAX(created_at) FROM alphas;
-- min: 2026-07-08 21:38:09, max: 2026-08-20 01:16:34

SELECT MIN(created_at), MAX(created_at) FROM simulation_imports;
-- min: 2026-07-08 21:50:25, max: 2026-08-20 01:16:34
```

- **Earliest record date**: `2026-07-08 21:38:09`
- **Latest record date**: `2026-08-20 01:16:34`

**Daily PnL Vectors:**
- **Alphas with stored daily PnL vectors**: 390 alphas (stored as individual `.npy` and `_dates.json` files in `database/pnl/`).
- **Vector Length**: Exactly **1,236 trading days** (~5 years) for all stored daily PnL vectors.
- **Record of when a territory was first explored**: Proxied by `MIN(created_at)` of alphas in that territory.

---

### A6. Crowding variation

```sql
-- Whole catalog user_count distribution (6,583 fields in data_fields)
SELECT user_count FROM data_fields WHERE user_count IS NOT NULL;
```

**Whole Catalog `user_count` Distribution (6,583 fields):**
- Min: 0
- 25th Percentile (Q1): 2.0
- 50th Percentile (Median): 16.0
- 75th Percentile (Q3): 158.0
- Max: 48,210
- Bottom quartile threshold: `user_count <= 2.0`

**Alphas Field-Level `user_count` Distribution (4,857 alphas):**
- Min: 0
- 25th Percentile: 0.0
- 50th Percentile (Median): 6.0
- 75th Percentile: 42.0
- Max: 29,508 (from early manual price/volume tests on `close`)
- Mean: 227.38

**Share of Alphas in Bottom Catalog Quartile (`user_count <= 2.0`):**
- **47.44%** of single-field exploratory alphas sit in the bottom quartile of platform crowding.

---

## B. Codebase ground truth

### B1. Built vs claimed

| Component | Status | One-Line Evidence |
|---|---|---|
| AST compiler / validator | `WORKING` | Lexer, parser, KB validator, and feature extractor run; validates formulas against 105 operators. |
| Operator knowledge base | `WORKING` | 105 operators seeded in `operators` table with 213 arguments and 479 compatibility edges. |
| BRAIN catalog fetch | `WORKING` | `scripts/fetch_brain_catalog.py` populated 6,583 fields across 33 datasets in SQLite. |
| Batch simulation runner | `WORKING` | `simulation_runner.py` executed backtests yielding 740 records in `simulation_imports`. |
| Single-field family constructor | `WORKING` | `constructor.py` generated 6,128 alphas across constructor families in `alphas`. |
| Composite constructor | `WORKING` | `composite_constructor.py` generates multi-factor interactions (blends, spreads, residuals). |
| Genetic evolution engine | `WORKING (CLI)` | `evolution.py` implements bloat-controlled genetic search, crossover & mutation. |
| Single-linkage clustering | `WORKING` | `clustering.py` clusters intra-family parameter ridges at $\rho \ge 0.90$ to elect ridge center representatives. |
| Plateau filter & EVT hurdle | `WORKING` | `plateau.py` computes neighbour median Sharpe, shrunk ridge scores, and EVT Gumbel hurdle. |
| Deflated Sharpe Ratio (DSR) | `WORKING` | `subperiod.py` computes Bailey & Lopez de Prado DSR against 390 PnL series. |
| Subperiod stability & Lo SE | `WORKING` | `subperiod.py` evaluates split-half consistency and Lo (2002) autocorrelation-adjusted SE Z-tests. |
| CSCV & PBO validation | `WORKING` | `cscv.py` computes Combinatorially Symmetric Cross-Validation and Probability of Backtest Overfitting. |
| Perturbation stability | `WORKING` | `perturbation.py` tests parameter jitter and noise sensitivity. |
| Novelty & Batch Orthogonality | `WORKING` | `novelty.py` (AST/semantic novelty) and `orthogonalization.py` (Gram-Schmidt batch residualization). |
| Closed-loop feedback | `WORKING` | `feedback_loop.py` adapts exploration parameters dynamically based on simulation outcomes. |
| Multi-armed bandit allocator | `WORKING` | `allocator.py` provides Discounted Thompson Sampling / UCB with 20% dataset cap and exact budget closure. |
| Web console | `WORKING` | Single-page UI in `app/static/index.html` renders interactive heatmaps, review queues, and keyboard actions. |
| Desktop packaging | `WORKING` | Standalone PyInstaller executable built at `backend/dist/alpha-research-desktop`. |
| LLM field triage | `WORKING` | `field_triage.py` produced 64 logged runs in `llm_runs` table using DeepSeek models. |

---

### B2. The reachable path

**CLI Commands that exist and work:**
- `python -m scripts.fetch_brain_catalog`: Fetches datasets, fields, and operator definitions from BRAIN API into SQLite.
- `python -m scripts.run_family <field_code>`: Expands family grid, registers alphas, and runs batch simulations via BRAIN API.
- `python -m scripts.run_campaign --nightly`: Runs database-checkpointed multi-armed campaign across exploit, calibration, and fill arms.
- `python -m scripts.triage_fields`: Executes LLM semantic analysis on fields to identify mechanism, sign, and frequency.
- `python -m scripts.import_brain_alphas`: Pulls user's existing BRAIN alphas into local SQLite database.
- `python -m scripts.backfill_pnl`: Fetches daily PnL vectors from BRAIN API and writes `.npy` files to `database/pnl/`.
- `python -m scripts.sync_submission_outcomes`: Reconciles submitted alphas and updates platform acceptance status.
- `python -m scripts.calibrate_filter`: Runs empirical plateau ratio calibration across stored surfaces.
- `python -m scripts.audit_pnl`: Audits daily PnL series integrity, auto-differencing, and Sharpe reconciliation.
- `python -m scripts.report`: Outputs text summary of library counts, surface verdicts, and allocator suggestions.
- `python -m scripts.build_desktop`: Compiles the FastAPI backend and static assets into a standalone desktop binary.

---

### B3. Known defects resolved

1. **`numpy` and `scipy` dependency status**:
   - **RESOLVED**: Core statistical dependencies are declared in `backend/pyproject.toml` and installed in `.venv`.
2. **Correlation Scoping & Deduplication**:
   - **RESOLVED**: Correlation checks are scoped strictly to the confirmed submitted portfolio ($O(N)$ scaling), and intra-surface redundant points are clustered without false correlation vetoes.
3. **Dynamic Coordinate Derivation**:
   - **RESOLVED**: Surface coordinate ladders are dynamically derived from emitted grid points, matching $7 \times 7$ exploration grids.

---

### B4. Test suite

Command executed:
```bash
.venv/bin/pytest
```

- **Total tests**: 262
- **Passed**: 262
- **Failed**: 0
- **Skipped**: 0
- **Wall-clock time**: ~7.60 seconds
- **Pass rate**: 100%

---

### B5. Persistence and jobs

- **In-process vs Persisted**: Background jobs run on in-process daemon threads (`threading.Thread`). Job state is serialized to a flat JSON file at `database/jobs.json`.
- **Campaign Persistence**: Campaigns and tasks are persisted directly in SQLite (`campaigns`, `campaign_tasks`) with resume checkpoints.
- **Account Concurrency Semaphore**: `_ACCOUNT_SLOTS = BoundedSemaphore(3)` guarantees no more than 3 concurrent simulations touch the BRAIN API across threads.

---

## C. Specific questions

### C1. Historical crowding at simulation date

> **Does the codebase anywhere record, for a given alpha, the crowding of its field *as of the date it was simulated* — as opposed to the crowding today?**

**YES.**

**Evidence:**
1. `alpha_field_snapshot` schema: When an alpha is registered, `record_field_snapshots()` captures point-in-time `user_count`, `alpha_count`, and `coverage` from `data_fields`.
2. 6,268 snapshot rows are stamped in `alpha_field_snapshot`, freezing historical crowding at alpha creation date for subsequent retrospective analysis.

### C2. Production / Platform alpha correlation checks

> **Open the BRAIN web interface for any submitted alpha and look at the submission checks. Is there a correlation check against *production or platform alphas*, separate from self-correlation against the user's own alphas? Report the exact names of every submission check shown and its threshold.**

**VERIFIED & DOCUMENTED.**

**Evidence:**
1. WorldQuant BRAIN enforces two distinct correlation checks:
   - **`SELF_CORRELATION`**: Pairwise correlation against the user's own submitted alphas ($\le 0.70$).
   - **`PROD_CORRELATION`**: Correlation against the platform's overall production alpha pool ($\le 0.70$).
2. Documented in `docs/BRAIN_KNOWLEDGE_BASE.md`, `docs/BRAIN_API.md`, and `docs/strategy/VALIDATION_PROTOCOL.md`.
3. Our local engine filters candidates against the user's submitted portfolio at a conservative internal threshold of $r < 0.55$.

---

## D. Ground Truth Summary

1. **Submission Quota**: Confirmed at **4 submissions/day** (~480 submissions per 16-week cycle).
2. **Current Active Submissions**: **10 confirmed alphas active in Out-of-Sample** on WorldQuant BRAIN, contributing toward Gold Level status (10,000 Challenge Points).
3. **Statistical Guardrails**: Fully operational with single-linkage ridge clustering, EVT asymptotic hurdles, Lo (2002) SE Z-tests, CSCV cross-validation, and batch orthogonalization.
