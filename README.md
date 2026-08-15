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

**Simulation is automated. Submission is not, and there is no submission code path in this repository.** See [docs/DECISIONS.md](file:///Users/sanya/Projects/alpha/docs/DECISIONS.md) for why that line sits where it does.

Read **[STRATEGY.md](file:///Users/sanya/Projects/alpha/STRATEGY.md)** first — it contains the diagnosis of why naive trial-and-error alphas fail and the foundational rules that govern this tool.

---

## Core Invariants

1. **Simulation is automated; submission is strictly manual.**  
   `POST /simulations` runs backtests on the user's account with polite rate-limiting, exponential backoff, and concurrency caps. No code path can ever submit an alpha to the platform.
2. **The LLM never writes expression syntax.**  
   LLMs propose economic hypotheses and fill slot choices; deterministic AST constructors and validator compilers emit the code. Syntax and type correctness are guaranteed by construction.
3. **Plateau, not peak.**  
   Isolated spikes are overfitted flukes. Candidates are judged by their neighbourhood median Sharpe across complete `(lookback_window × decay)` surfaces.
4. **Honest multiple-testing haircuts & DSR.**  
   Mass search produces false discoveries by chance. Trials are discounted via Bailey & Lopez de Prado's Deflated Sharpe Ratio (DSR) and empirical correlation gates against existing portfolio alphas.

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
  │               Honest Multi-Tier Filter                  │
  │  1. 2D Surface Plateau Ridge (neighbour median)         │
  │  2. Pre-declared BRAIN Metric Checks                    │
  │  3. Deflated Sharpe Ratio (DSR) Multiple-Testing Bar    │
  │  4. Sub-Period Stability & Split-Half Consistency       │
  │  5. Empirical Daily PnL Correlation Gate (< 0.55)       │
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
│   │   ├── constructor.py           Deterministic family grid expansion
│   │   ├── composite_constructor.py Cross-field blends, spreads, residuals & triggers
│   │   ├── evolution.py             Bloat-controlled genetic search & genealogy trees
│   │   ├── plateau.py               2D surface plateau filter & DSR multiple testing
│   │   ├── subperiod.py             In-sample / out-of-sample stability validation
│   │   ├── correlation.py           Empirical PnL Pearson correlation & proxy fallback
│   │   ├── allocator_bandit.py      Multi-armed bandit dataset allocation (20% cap)
│   │   ├── field_triage.py          LLM semantic dataset triage & slot filling
│   │   └── simulation_runner.py     Async batch runner with concurrency caps
│   ├── models/           SQLAlchemy ORM (16 tables: alphas, metrics, fields, pnl, logs)
│   ├── routers/          FastAPI endpoints (UI summary, surfaces, library, telemetry)
│   ├── static/           Zero-dependency single-page HTML console (dark/light themes)
│   ├── desktop.py        Desktop launcher with auto port selection & browser launch
│   └── seeds/            Operator knowledge base seeds (102 operators + signatures)
├── scripts/              CLI workflows (run_family, report, fetch_catalog, build_desktop)
├── migrations/           Alembic database migrations
└── tests/                120+ unit and integration tests (fast, isolated SQLite)
fields/                   Field catalog samples and fixtures
operators/                Operator definitions, signatures, and type constraints
docs/                     BRAIN API reference, architectural decision records, packaging
```

---

## Status

| Stage | Milestone | Capability | Status |
|---|---|---|---|
| **0** | Operator KB, AST compiler & validator | Syntax validation, infix precedence climbing, operator typing | **Done** |
| **1** | Real BRAIN field catalog | 4,367+ fields across 14+ datasets with user counts & coverage | **Done** |
| **2** | Batch simulation runner | Polite async runner (3 concurrent cap, backoff, retry-after) | **Done** |
| **3** | Family & composite constructors | Grid sweeps, cross-field composites, bloat-controlled genetic search | **Done** |
| **4** | Honest statistical filters | 2D plateau ridge, Deflated Sharpe Ratio (DSR), subperiod stability | **Done** |
| **5** | Correlation & portfolio gates | Empirical daily PnL correlation (< 0.55) & structural proxy check | **Done** |
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

Run database migrations, seed operator signatures, and fetch the real BRAIN catalog:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.seeds.load_operators
.venv/bin/python -m scripts.fetch_brain_catalog
```

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

### Command-Line Workflows

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

When launched, it automatically selects an open port, initializes local user data at `~/.alpha_research/`, runs migrations, and opens your default browser. See [docs/PACKAGING.md](file:///Users/sanya/Projects/alpha/docs/PACKAGING.md) for full details.

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
- Bloat-controlled genetic mutations & lineage CTE trees (`test_evolution.py`, `test_genealogy.py`).
- Multi-armed bandit allocation & diversity caps (`test_allocator.py`, `test_allocator_bandit.py`).
- Strict HTTP safety assertions (`test_brain_no_post.py` — enforces zero automated submission code paths).
- Web console API endpoints & UI workflows (`test_ui.py`, `test_app.py`).
