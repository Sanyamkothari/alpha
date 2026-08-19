# Alpha Research Engine — WorldQuant BRAIN

A local, single-researcher system for **generating alphas that clear the BRAIN submission bar, repeatably.**

The loop it exists to run:

```
pick an under-mined dataset
  → read its fields, triage economic mechanisms
    → constructor expands mechanisms across the structure × settings grid
      → batch-simulate on your own BRAIN account (polite runner, 3 concurrent)
        → honest multi-tier filter (plateau ridge + DSR + subperiod + correlation)
          → YOU review, correlation-check, and submit manually
```

**Simulation is automated. Submission is not, and there is no submission code path in this repository.** See [docs/DECISIONS.md](docs/DECISIONS.md) for why that line sits where it does.

Read **[STRATEGY.md](STRATEGY.md)** first — it contains the diagnosis of why naive trial-and-error alphas fail and the foundational rules that govern this tool.

---

## Core Invariants

1. **Simulation is automated; submission is strictly manual.**  
   `POST /simulations` runs backtests on the user's account with polite rate-limiting, exponential backoff, and concurrency caps. No code path can ever submit an alpha to the platform.
2. **The LLM never writes expression syntax.**  
   LLMs propose economic hypotheses and fill slot choices; deterministic AST constructors and validator compilers emit the code. Syntax and type correctness are guaranteed by construction.
3. **Plateau, not peak.**  
   Isolated spikes are overfitted flukes. Candidates are judged by their neighbourhood median Sharpe across complete `(lookback_window × decay)` surfaces.
4. **Honest multiple-testing haircuts & DSR.**  
   Mass search produces false discoveries by chance. Trials are discounted via Bailey & Lopez de Prado's Deflated Sharpe Ratio (DSR), with an extreme-value haircut priced on the *effective* number of trials, and an empirical correlation gate against the submitted portfolio.
5. **Correlation is judged on magnitude.**
   The gate compares `|r|`, not `r`. An alpha anti-correlated with something you already submitted is that alpha inverted — one idea twice, not a hedge. The reported figure keeps its sign so you can tell the two apart.

---

## Architecture & Pipeline

```
  ┌────────────────────────┐       ┌────────────────────────┐
  │  BRAIN Data Catalog    │  ───► │  MAB Dataset Allocator │
  │  4,367+ fields / 14 ds │       │  (Diversity-capped UCB)│
  └────────────────────────┘       └───────────┬────────────┘
                                               │
                                               ▼
  ┌────────────────────────┐       ┌────────────────────────┐
  │   LLM Field Triage     │  ───► │ Deterministic Family & │
  │ (Sign, Frequency, Why) │       │ Composite Constructors │
  └────────────────────────┘       └───────────┬────────────┘
                                               │
                                               ▼
  ┌────────────────────────┐       ┌────────────────────────┐
  │ Polite Batch Simulator │  ◄─── │  AST Compiler / Gate   │
  │ (3 concurrent, backoff)│       │  (Lexer, Parser, KB)   │
  └───────────┬────────────┘       └────────────────────────┘
              │
              ▼
  ┌─────────────────────────────────────────────────────────┐
  │         Honest Multi-Tier Statistical Robustness        │
  │  1. 2D Surface Plateau Ridge (single-linkage cluster)   │
  │  2. Pre-declared BRAIN Metric Checks (Sharpe, Fitness)  │
  │  3. Deflated Sharpe (DSR) & EVT Asymptotic Hurdle (EVT) │
  │  4. Sub-Period Stability & Lo (2002) SE Z-Tests         │
  │  5. Empirical Daily PnL Correlation Gate (|r| < 0.55)   │
  │  6. CSCV (PBO), Perturbation Stability & Feedback Loop  │
  └───────────────────────────┬─────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────┐
  │      Web Console / Desktop App Morning Review Queue     │
  │  • Keyboard workflow: [c] copy strip → [s] submitted    │
  │  • Interactive 2D Heatmaps with missing cell fill       │
  │  • Searchable library, portfolio ledger & lineage trees │
  └─────────────────────────────────────────────────────────┘
```

---

## Project Layout

```
backend/
├── app/
│   ├── validator/        AST compiler: lexer → parser → AST → KB validation → features
│   ├── services/         Generation core, plateau filter, simulator, DSR, allocator
│   │   ├── brain/        Polite BRAIN API HTTP client (GET catalog, POST simulations)
│   │   ├── constructor.py           Deterministic family grid expansion & stratified sampling
│   │   ├── composite_constructor.py Cross-field blends, spreads, residuals & triggers
│   │   ├── evolution.py             Bloat-controlled genetic search & genealogy trees
│   │   ├── clustering.py            Intra-family single-linkage clustering at rho >= 0.90
│   │   ├── plateau.py               2D surface plateau filter, ridge scores & EVT hurdles
│   │   ├── subperiod.py             DSR, split-half stability & Lo (2002) SE Z-tests
│   │   ├── cscv.py                  Combinatorially Symmetric Cross-Validation (CSCV & PBO)
│   │   ├── perturbation.py          Parameter & noise perturbation stability testing
│   │   ├── novelty.py               Structural AST & semantic novelty scoring
│   │   ├── orthogonalization.py     Greedy batch Gram-Schmidt residualization
│   │   ├── feedback_loop.py         Closed-loop dynamic campaign adaptation
│   │   ├── filter_backtest.py       Monte Carlo statistical filter classification suite
│   │   ├── filter_config.py         Centralized filter config with SHA-256 fingerprinting
│   │   ├── correlation.py           Empirical PnL correlation (gates on |r|) & proxy fallback
│   │   ├── allocator.py             Multi-armed bandit (Discounted Thompson / UCB) with 20% cap
│   │   ├── campaign_runner.py       Resumable DB-checkpointed overnight campaign executor
│   │   ├── field_triage.py          LLM semantic dataset triage & slot filling
│   │   └── simulation_runner.py     Async batch runner with concurrency caps
│   ├── models/           SQLAlchemy ORM (21 tables: alphas, metrics, fields, pnl, logs)
│   ├── routers/          FastAPI endpoints (UI summary, surfaces, library, telemetry)
│   ├── static/           Zero-dependency single-page HTML console (dark/light themes)
│   ├── desktop.py        Desktop launcher with auto port selection & browser launch
│   └── seeds/            Operator knowledge base seeds (105 operators + signatures)
├── scripts/              CLI workflows (run_family, report, fetch_catalog, build_desktop)
├── migrations/           Alembic database migrations
└── tests/                270 unit and integration tests (isolated SQLite, no network)
fields/                   Field catalog samples and fixtures
operators/                Operator definitions, signatures, and type constraints
docs/                     Architecture, study validation, operating guides, decision records
    ├── strategy/         Validation protocol, product strategy, roadmap, business model
    ├── briefs/           Phase briefs (inventory, phase 0, phase 1)
    ├── DECISIONS.md      Architectural decision records (D1–D10)
    ├── IMPLEMENTATION_RECORD.md Part A & Part B architecture, scaling & quant remediations
    ├── OPEN_DECISIONS.md Architectural trade-offs & resolutions (B1–B4)
    ├── PHASE1_OPERATING_GUIDE.md Step-by-step operating runbook
    ├── GOLD_LEVEL_GUIDE.md WorldQuant BRAIN Gold Level roadmap & expansion
    └── BRAIN_API.md      Empirical API reference and verified behaviors
```

---

## Status

| Stage | Milestone | Capability | Status |
|---|---|---|---|
| **0** | Operator KB, AST compiler & validator | Syntax validation, infix precedence climbing, operator typing | **Done** |
| **1** | Real BRAIN field catalog | 6,583 fields across 33 datasets with user counts & coverage | **Done** |
| **2** | Batch simulation runner | Polite async runner (3 concurrent cap, backoff, retry-after) | **Done** |
| **3** | Family & composite constructors | Grid sweeps, cross-field composites, bloat-controlled genetic search | **Done** |
| **4** | Honest statistical filters | 2D plateau ridge, Deflated Sharpe Ratio (DSR), subperiod stability | **Done** |
| **5** | Correlation & portfolio gates | Empirical daily PnL correlation (\|r\| < 0.55) against the submitted portfolio, structural proxy only where PnL is missing | **Done** |
| **6** | Diversity-capped allocator | Multi-armed bandit (Thompson/UCB) with 20% dataset crowding cap | **Done** |
| **7** | Web console & desktop app | Interactive heatmaps, keyboard review, PyInstaller standalone binary | **Done** |

---

## Setup

### 1. Environment & Dependencies

Requirements: Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
cd backend
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 2. Configuration

Copy `.env.example` to `.env` and fill in your BRAIN credentials and optional LLM API keys:

```bash
cp ../.env.example ../.env
```

Key environment variables:
- `BRAIN_EMAIL` / `BRAIN_PASSWORD`: Your WorldQuant BRAIN credentials.
- `LLM_PROVIDER`: `openrouter`, `anthropic`, or `fake` (for local offline testing).
- `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY`: Required if running LLM field triage.

### 3. Database Initialization

Run database migrations and load the offline seed data (operators, lookups, and sample 122-field catalog):

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.seeds.seed_all          # offline: operators, lookups, sample catalog

# Optional — replaces the sample catalog with your account's live one:
.venv/bin/python -m scripts.fetch_brain_catalog
```

> Everything except remote simulation runs fully offline on the seeded sample catalog, allowing full evaluation without credentials.

---

## Running It

### Web Console (The Morning Pass)

Start the local server and open [http://127.0.0.1:8000](http://127.0.0.1:8000):

```bash
python -m uvicorn app.main:app
```

The single-page console is zero-dependency, works fully offline, and is optimized for the morning review routine:
- `?` : Toggle keyboard shortcuts overlay.
- `j` / `k` : Navigate up and down through the promoted shortlist.
- `c` : Activate the **Armed Copy Strip** (copies expression, settings, universe, decay to clipboard for fast manual entry into BRAIN).
- `s` : Mark candidate as **Submitted** (moves it to the portfolio ledger and activates correlation tracking).
- `x` : Discard / Archive candidate.
- `u` : Undo the last status action.
- `n` : Launch next recommended family from the allocator.

---

## Command-Line Workflows

#### 1. Expand and Simulate an Alpha Family
Expands a `(field, denominator)` mechanism across the complete window × decay grid, stores candidates, and batch-simulates a capped subset on BRAIN:

```bash
# Expand grid and simulate 48 candidates
python -m scripts.run_family --field liabilities --denominator cap --simulate 48

# Expand without simulating (dry run)
python -m scripts.run_family --field news_open_vol --denominator cap --simulate 0
```

#### 2. Generate Daily Research Report
Applies the 2D plateau filter and prints the ranked shortlist, surface matrices, dataset hit rates, and next family recommendations:

```bash
python -m scripts.report
```

#### 3. LLM-Assisted Field Triage
Performs semantic analysis over newly discovered dataset fields to infer frequency, expected return sign, and economic mechanism:

```bash
python -m scripts.triage_fields --dataset fundamental2 --limit 50
```

#### 4. Import Historical Platform Alphas & Backfill PnL
Imports existing simulated alphas from your BRAIN account and downloads daily PnL return vectors for empirical correlation tracking:

```bash
python -m scripts.import_brain_alphas
python -m scripts.backfill_pnl
```

Daily PnL is what turns the filter on: sub-period stability, the Deflated Sharpe
Ratio and the empirical correlation gate all require it, and a candidate without a
stored series is held back rather than guessed at. Series live under
`database/pnl/` for the default database; any other `DATABASE_URL` gets its own
`database/pnl-<digest>/` directory, because the files are keyed by `alpha_id` and
that id only means something inside one database.

#### 5. Calibrate the Filter

Two complementary questions — does the filter reject noise, and does it accept what
already worked?

```bash
# Synthetic ground truth: false-discovery rate on pure noise vs survival of a true signal
python -m scripts.calibrate_filter --replications 50

# Real ground truth: how many of your own submitted alphas would this filter promote?
python -m scripts.calibrate_against_portfolio
```

The second is the one that catches over-tightening. Every alpha in your submitted
portfolio is a known positive — BRAIN accepted it and you chose to submit it — so a
filter that rejects them is mis-tuned no matter how clean the synthetic scorecard
looks. It needs backfilled PnL to score sub-period stability.

#### 6. Reproduce the Gating Baseline

```bash
python -m scripts.repro_review_findings
```

Builds a fixed 49-point family end to end and asserts the funnel against a recorded
baseline, then checks that the promoted alpha actually reaches the report and the
surface API. Seeds are deterministic, so any movement in the printed counts is a
real change in the filter rather than run-to-run noise.

---

## Standalone Desktop Distribution

Alpha Research can be packaged into a single, standalone executable (`.exe` on Windows, binary on macOS/Linux) that requires no local Python installation.

To build the desktop executable:

```bash
cd backend
python -m scripts.build_desktop
```

The compiled binary will be placed in `backend/dist/`:
- **macOS / Linux**: `backend/dist/alpha-research-desktop`
- **Windows**: `backend/dist/alpha-research-desktop.exe`

When launched, it automatically selects an open port, initializes local user data at `~/.alpha_research/`, runs migrations, and opens your default browser. See [docs/PACKAGING.md](docs/PACKAGING.md) for full details.

---

## Testing & Quality

The test suite runs with zero network/API dependencies, executing against an isolated in-memory/temporary SQLite database:

```bash
cd backend
.venv/bin/python -m pytest
```

Coverage includes:
- Operator signature validation & AST parsing (`test_validator.py`, `test_phase1_api.py`).
- Precedence-climbing infix arithmetic desugaring.
- Grid constructor completeness & cross-field composites (`test_constructor.py`, `test_composite_constructor.py`).
- Plateau filter, Deflated Sharpe Ratio (DSR), and multiple testing haircuts (`test_plateau.py`).
- Subperiod stability & split-half decay validation (`test_subperiod.py`).
- Empirical PnL correlation & proxy calibration (`test_correlation.py`, `test_proxy_calibration.py`).
- Review-finding regressions, including the correlation gate's magnitude rule and
  per-database PnL isolation (`test_review_findings.py`).
- Filter calibration on synthetic ground truth (`test_filter_backtest.py`).
- Bloat-controlled genetic mutations & lineage CTE trees (`test_evolution.py`, `test_genealogy.py`).
- Multi-armed bandit allocation & diversity caps (`test_allocator.py`, `test_allocator_bandit.py`).
- Strict HTTP safety assertions (`test_brain_no_post.py` — enforces zero automated submission code paths).
- Web console API endpoints & UI workflows (`test_ui.py`, `test_app.py`).
