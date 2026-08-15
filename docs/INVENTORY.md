# Project Inventory — 2026-08-15

## Headline numbers

| Metric | Ground Truth Value |
|---|---|
| Total alphas | 4,857 |
| Distinct territories | 149 (36 dense constructor territories, 113 sparse exploratory) |
| Median alphas per territory | 2.0 (mean: 32.60, max: 129) |
| Submitted | 3 (manually marked by human in UI) |
| Acceptance outcomes recorded | 0 (NOT PRESENT in database schema) |
| Historical crowding available | **NO** (overwritten current snapshots only) |
| Date range of data | 2026-07-08 to 2026-08-14 |

> [!WARNING]
> **Critical Study Feasibility Constraints:**
> 1. **Historical Crowding is UNAVAILABLE (NO)**: `data_fields` only stores current snapshot values (`user_count`, `alpha_count`). Re-fetching deletes and replaces existing records. There is no historical revision table, snapshot log, or `as_of_date` column. Point-in-time crowding when an alpha was simulated cannot be recovered.
> 2. **Territory Breadth is NARROW (36 dense territories across 12 fields)**: While 4,857 alphas exist, 94.9% (4,608 alphas) are concentrated in exactly 12 single-field constructor families (384 alphas each, varying windows, decays, neutralizations, and group normalizations over a single operator `ts_zscore`). Only 29 total fields have ever been touched out of 6,583 fields in the catalog.
> 3. **Funnel Stops at UI Submission Marker**: No mechanism (API, script, or DB field) tracks whether WorldQuant BRAIN accepted or rejected submitted alphas. Data collection terminates at the local user pressing `s` in the web console.

---

## A. Data inventory

Database path queried: `/Users/sanya/Projects/alpha/database/wq.db`

### A1. Volume

Query executed:
```sql
SELECT COUNT(*) FROM alphas;
-- Result: 4857
```

**Table Inventory (all 16 tables):**

```sql
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
SELECT COUNT(*) FROM "<table_name>";
```

| Table Name | Row Count |
|---|---|
| `alembic_version` | 1 |
| `alpha_metrics` | 531 |
| `alpha_status_history` | 5,468 |
| `alphas` | 4,857 |
| `brain_fetch_log` | 0 |
| `categories` | 11 |
| `data_fields` | 6,583 |
| `datasets` | 33 |
| `field_tags` | 0 |
| `llm_runs` | 64 |
| `operator_arguments` | 213 |
| `operator_compatibility` | 479 |
| `operator_examples` | 133 |
| `operators` | 105 |
| `simulation_imports` | 531 |
| `tags` | 0 |

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

*(Note: If operator family is defined broadly by KB category `time_series`, distinct territories = 131, median = 2.0, with the same 36 top territories).*

---

### A3. Outcomes — Funnel Depth

```sql
-- Simulated (distinct alphas with simulation imports)
SELECT COUNT(DISTINCT alpha_id) FROM simulation_imports; -- 486 (531 total imports)

-- Passed BRAIN checks
SELECT COUNT(DISTINCT alpha_id) FROM alpha_metrics WHERE passed_all_checks = 1; -- 28

-- Alphas table status counts
SELECT status, COUNT(*) FROM alphas GROUP BY status;
```

**Funnel Stage Counts:**

| Funnel Stage | Count | Source / Query |
|---|---|---|
| simulated | 486 | `COUNT(DISTINCT alpha_id) FROM simulation_imports` (486 distinct alphas, 531 runs) |
| passed BRAIN checks | 28 | `COUNT(DISTINCT alpha_id) FROM alpha_metrics WHERE passed_all_checks = 1` |
| passed plateau | 208 | `is_plateau == True` across 13 family surfaces evaluated via `plateau.evaluate()` |
| passed DSR | 11 | `dsr_passed == True` (DSR >= 0.95 or fallback hurdle >= 1.50) |
| passed subperiod | 58 | `subperiod_passed == True` (split-half >= 0.40, rolling >= 70%, decay >= 50%) |
| promoted / shortlisted | 16 | `COUNT(*) FROM alphas WHERE status = 'passed'` |
| marked submitted | 3 | `COUNT(*) FROM alphas WHERE status = 'submitted'` (Alphas #243, #267, #2558) |
| ACCEPTED by BRAIN | **NOT PRESENT** (0) | No column, status, or log exists for post-submission platform acceptance |
| REJECTED by BRAIN | **NOT PRESENT** (0) | No column, status, or log exists for post-submission platform rejection |

**Precise Answers on Acceptance:**
- **Is acceptance recorded?**: NO.
- **Does data stop at user pressing `s`?**: YES. Pressing `s` calls `POST /api/ui/alphas/{id}/mark` with `action="submitted"`, writing `status="submitted"` to `alphas` and logging note `ui:submitted` in `alpha_status_history`.
- **How would acceptance enter?**: NOT PRESENT. There is no API fetch, import script, or manual entry field for BRAIN platform review outcomes.
- **Rows with non-null acceptance value**: 0.

---

### A4. Crowding data — Historical vs Snapshot

**Columns in `data_fields`:**
- `user_count` (`INTEGER`)
- `alpha_count` (`INTEGER`)
- `coverage` (`FLOAT`)

**Overwrite vs History:**
- **When catalog is re-fetched, previous values are OVERWRITTEN.**
- In `backend/scripts/fetch_brain_catalog.py` (lines 109–115):
  ```python
  db.execute(
      delete(DataField).where(
          DataField.region == region,
          DataField.delay == delay,
          DataField.universe == universe,
      )
  )
  ```
- **Revision / snapshot / history table**: NOT PRESENT.
- **`as_of_date` column**: NOT PRESENT.
- **Distinct fetch dates in data**: 2 (`2026-08-03` with 6,488 fields, `2026-08-14` with 95 fields).
- **Retrieve user_count from six months ago**: **NO. We only have current values.**

---

### A5. Time span and PnL

Query executed:
```sql
SELECT MIN(created_at), MAX(created_at) FROM alphas;
-- min: 2026-07-08 21:38:09, max: 2026-08-14 20:13:10

SELECT MIN(created_at), MAX(created_at) FROM simulation_imports;
-- min: 2026-07-08 21:50:25, max: 2026-08-14 20:28:24
```

- **Earliest record date**: `2026-07-08 21:38:09`
- **Latest record date**: `2026-08-14 20:28:24`

**Alphas created per month:**
- `2026-07`: 51 alphas (57 simulation imports)
- `2026-08`: 4,806 alphas (474 simulation imports)

**Daily PnL Vectors:**
- **Alphas with stored daily PnL vectors**: 369 alphas (stored as individual `.npy` and `_dates.json` files in `database/pnl/`).
- **Vector Length**: Exactly **1,236 trading days** (~5 years) for all 369 alphas (min: 1236, median: 1236, max: 1236).
- **Record of when a territory was first explored**: NOT STORED (no territory entity exists; can only be proxied by `MIN(created_at)` of alphas in that territory).

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
- **47.44%** (2,304 of 4,857 alphas).
- *Evidence*: 6 of the 12 single-field families mined have `user_count = 0` (`anl4_fs_detail_estimate_1qf_v4_nd_totgw_median`, `anl4_fs_detail_estimates_basic_qf_delay1_v4_nd_cfps_high`, `debt_repayment_year_three`, `incremental_shares_sbp_arrangements`, `max_reported_pretax_profit_quarterly_estimate`, `pretax_income_reported_min`).

---

## B. Codebase ground truth

### B1. Built vs claimed

| Component | Status | One-Line Evidence |
|---|---|---|
| AST compiler / validator | `WORKING` | Lexer, parser, KB validator, and feature extractor run; normalized 4,854 formulas in DB. |
| Operator knowledge base | `WORKING` | 105 operators seeded in `operators` table with 213 arguments and 479 compatibility edges. |
| BRAIN catalog fetch | `WORKING` | `scripts/fetch_brain_catalog.py` populated 6,583 fields across 33 datasets in SQLite. |
| Batch simulation runner | `WORKING` | `simulation_runner.py` executed backtests yielding 531 records in `simulation_imports`. |
| Single-field family constructor | `WORKING` | `constructor.py` generated 4,608 alphas across 12 families (384 alphas each) in `alphas`. |
| Composite constructor | `PARTIAL` | `composite_constructor.py` generated 8 alphas for 1 family (`close+volume`), but 0 simulated. |
| Genetic evolution engine | `CODE ONLY` | `evolution.py` and unit tests exist, but 0 evolved alphas exist in DB (`generation=0` for all 4,857 rows). |
| Plateau filter | `WORKING` | `plateau.py` computes 2D neighbour median Sharpe ratios across surfaces; 208 candidates pass in DB. |
| Deflated Sharpe Ratio (DSR) | `WORKING` | `subperiod.py` computes Bailey & Lopez de Prado DSR against 369 PnL series; 11 candidates pass. |
| Subperiod stability | `WORKING` | `subperiod.py` evaluates split-half consistency and rolling 126d positivity; 58 candidates pass. |
| PnL correlation gate | `WORKING` | `correlation.py` computes Pearson correlation across 369 PnL arrays in `database/pnl/`. |
| Multi-armed bandit allocator | `PARTIAL` | Basic allocator heuristic in `allocator.py` works in UI; Thompson sampling in `allocator_bandit.py` is uncalled library code. |
| Web console | `WORKING` | Single-page UI in `app/static/index.html` renders interactive heatmaps, review queues, and keyboard actions. |
| Desktop packaging | `WORKING` | Standalone PyInstaller executable built at `backend/dist/alpha-research-desktop` (27.8 MB). |
| LLM field triage | `WORKING` | `field_triage.py` produced 64 logged runs in `llm_runs` table using DeepSeek models. |

---

### B2. The reachable path

**CLI Commands that exist and work:**
- `python -m scripts.fetch_brain_catalog`: Fetches datasets, fields, and operator definitions from BRAIN API into SQLite.
- `python -m scripts.run_family <field_code>`: Expands family grid, registers alphas, and runs batch simulations via BRAIN API.
- `python -m scripts.triage_fields`: Executes LLM semantic analysis on fields to identify mechanism, sign, and frequency.
- `python -m scripts.import_brain_alphas`: Pulls user's existing BRAIN alphas into local SQLite database.
- `python -m scripts.backfill_pnl`: Fetches daily PnL vectors from BRAIN API and writes `.npy` files to `database/pnl/`.
- `python -m scripts.report`: Outputs text summary of library counts, surface verdicts, and allocator suggestions.
- `python -m scripts.build_desktop`: Compiles the FastAPI backend and static assets into a standalone desktop binary.

**Web UI Actions that exist and work:**
- Launch via `alpha-desktop` or `uvicorn app.main:app`.
- **Review Queue**: `c` (copy submission strip), `s` (mark submitted), `x` (archive/discard), `z` (undo), `y` (copy TSV audit row).
- **2D Surface Heatmaps**: Interactive lookback window × decay grid with neighbour median Sharpe inspection.
- **Grid Fill**: One-click fill of unsimulated surface holes (`POST /api/ui/families/fill`).
- **Allocator Launch**: Launch family simulation directly from allocator suggestions (`POST /api/ui/families/run`).
- **Library & Search**: Filter and search by expression, field code, or status (`GET /api/ui/library`).
- **Correlation Matrix**: View interactive Pearson correlation matrix across promoted/submitted alphas (`GET /api/ui/correlation-matrix`).

**Reachable only via direct Python library calls:**
- Composite multi-factor constructor (`app/services/composite_constructor.py`).
- Genetic evolution / mutation engine (`app/services/evolution.py`).
- Discounted Thompson Sampling allocator (`app/services/allocator_bandit.py`).

---

### B3. Known defects

1. **`numpy` and `scipy` dependency status**:
   - **CONFIRMED**: In git commit `2be5cbf`, `numpy` and `scipy` were absent from `backend/pyproject.toml` despite being imported by core services (`subperiod.py`, `correlation.py`, `pnl_storage.py`). They were added in the working tree but remain uncommitted. A clean clone from git fails without manual package installation.

2. **`/api/system/modules` status**:
   - **CONFIRMED**: Endpoint returns hardcoded stale metadata from Stage 1. It reports implemented modules (field-catalog, sim-runner, constructor, filter, allocator, report) as `implemented: false`.
   - **Actual Output**:
     ```json
     {
       "modules": [
         {"id": "operator-kb", "name": "Operator Knowledge Base", "stage": 0, "implemented": true},
         {"id": "validator", "name": "Expression Validator (compiler)", "stage": 0, "implemented": true},
         {"id": "alpha-library", "name": "Alpha Library", "stage": 0, "implemented": true},
         {"id": "result-import", "name": "Simulation Result Importer", "stage": 0, "implemented": true},
         {"id": "field-catalog", "name": "Real BRAIN Field Catalog", "stage": 1, "implemented": false},
         {"id": "sim-runner", "name": "Simulation Runner (batch)", "stage": 2, "implemented": false},
         {"id": "constructor", "name": "Family Constructor (grid)", "stage": 3, "implemented": false},
         {"id": "filter", "name": "Plateau + Correlation Filter", "stage": 4, "implemented": false},
         {"id": "allocator", "name": "Allocator (bandit + forced exploration)", "stage": 5, "implemented": false},
         {"id": "report", "name": "Daily Shortlist Report", "stage": 6, "implemented": false}
       ],
       "current_stage": 1
     }
     ```

3. **Other defects breaking clean installation/execution:**
   - **17 untracked files in working directory**: Critical services (`subperiod.py`, `correlation.py`, `pnl_storage.py`, `allocator_bandit.py`, `composite_constructor.py`, `evolution.py`) and 7 test files are not committed to git.
   - **Default database path divergence**: `app/config.py` defaults database location to `~/.alpha-research/database/wq.db`, whereas project database is checked into `/Users/sanya/Projects/alpha/database/wq.db`. Requires `ALPHA_DATA_DIR` env variable.
   - **Starlette deprecation warning**: Test suite raises deprecation warning regarding `httpx` with `starlette.testclient`.

---

### B4. Test suite

Command executed:
```bash
python -m pytest
```

- **Total tests**: 176
- **Passed**: 176
- **Failed**: 0
- **Skipped**: 0
- **Wall-clock time**: 1.25 seconds
- **Failures**: None.

---

### B5. Persistence and jobs

- **In-process vs Persisted**: Background jobs run on in-process daemon threads (`threading.Thread`). Job state is serialized to a flat JSON file at `database/jobs.json`.
- **Durable queue**: **NOT PRESENT** (no Redis, Celery, RabbitMQ, or arq).
- **Behavior on process termination**: If the backend process dies while a batch simulation is running:
  1. The execution thread terminates immediately.
  2. On server restart, `JobRegistry._load()` reads `database/jobs.json` and updates any `"queued"` or `"running"` job to status `"interrupted"` with error `"interrupted on server restart"`.
  3. Simulations already submitted to BRAIN continue running on the platform, but local polling stops.
  4. Completed simulations already saved in SQLite remain intact; remaining unsimulated alphas in the family remain in `untested` status.

---

## C. Specific questions

### C1. Historical crowding at simulation date

> **Does the codebase anywhere record, for a given alpha, the crowding of its field *as of the date it was simulated* — as opposed to the crowding today?**

**NO.**

**Evidence:**
1. `alphas` schema: Columns are static expression definitions and structural AST metrics (`feature_json`). No snapshot of field `user_count` or `alpha_count` is stored on creation.
2. `simulation_imports` and `alpha_metrics` schemas: Store backtest performance metrics (`sharpe`, `fitness`, `turnover`, `returns`, `margin_bps`, `drawdown`), but do not capture dataset or field crowding metrics at execution time.
3. `data_fields` schema: Stores only the latest fetch values (`user_count`, `alpha_count`, `coverage`). When `scripts/fetch_brain_catalog.py` runs, existing records for that region/universe/delay are deleted and overwritten.

---

### C2. Production / Platform alpha correlation checks

> **Open the BRAIN web interface for any submitted alpha and look at the submission checks. Is there a correlation check against *production or platform alphas*, separate from self-correlation against the user's own alphas? Report the exact names of every submission check shown and its threshold.**

**NEEDS HUMAN**

*(Reason: As an automated local coding agent, I do not have access to an authenticated web browser session on the proprietary WorldQuant BRAIN platform portal).*

---

## D. Things I could not determine

1. **BRAIN platform-level submission checks and correlation thresholds against platform alphas (Question C2)**:
   - *What would be needed*: A human researcher logging into `platform.worldquantbrain.com`, navigating to a submitted alpha, and recording the exact submission check names, descriptions, and threshold values displayed in the submission review modal.
2. **True platform acceptance rate of submitted alphas**:
   - *What would be needed*: Access to the user's WorldQuant BRAIN consultant account submissions tab to compare the 3 locally marked submitted alphas (#243, #267, #2558) against their platform acceptance/rejection status.
