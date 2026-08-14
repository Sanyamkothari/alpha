# Alpha research tool — WorldQuant BRAIN

A local, single-researcher tool for **generating alphas that clear the BRAIN
submission bar, repeatably.**

The loop it exists to run:

```
pick an under-mined dataset
  → read its fields, pick economic mechanisms
    → constructor expands each mechanism across the structure × settings grid
      → batch-simulate on your own BRAIN account
        → plateau filter surfaces a shortlist
          → YOU review, correlation-check, and submit manually
```

**Simulation is automated. Submission is not, and there is no submission code
path in this repository.** See [docs/DECISIONS.md](./docs/DECISIONS.md) for why
that line sits where it does.

Read **[STRATEGY.md](./STRATEGY.md)** first — it contains the diagnosis of why
the previous 51 alphas all failed and the five rules that follow from it.

## Layout

```
backend/app/validator/   the compiler: lexer → parser → AST → KB validation → features
backend/app/services/    alpha library, result import, validation, LLM gateway
backend/app/models/      ORM (16 tables — the alpha-generation core)
backend/migrations/      Alembic
operators/               operator knowledge base (102 operators + args + compatibility)
fields/                  field catalog sample — currently a MOCK, see below
database/                local SQLite
docs/                    BRAIN API reference + decision records
```

## Status

| Stage | What | Makes it | State |
|---|---|---|---|
| 0 | Operator KB, validator, alpha library, result importer | — | **done** |
| 1 | Real BRAIN field catalog — 4,367 fields / 14 datasets | possible | **done** |
| 2 | Batch simulation runner (3 concurrent, the platform cap) | fast | **done** |
| 3 | Family constructor — grid expansion, valid by construction | productive | **done** |
| 4 | Plateau filter + multiple-testing haircut | **trustworthy** | **done** |
| 5 | Allocator — diversity-capped, refuses to over-exploit | **self-sustaining** | **done** |
| 6 | Daily report — ranked shortlist, one approval pass | low-expertise | **done** |

Infix arithmetic (`vwap/close`, `a+b`, `(high-low)/(volume*close)`) is supported
by the parser via precedence climbing, desugaring to canonical KB operators.

The correlation gate in stage 4 performs a **local structural proxy and portfolio collision check**
against all accepted/submitted alphas, preventing self-correlated duplicates
before manual submission.

**The mock field catalog is the top blocker.** `fields/usa_top3000_delay1_sample.json`
holds 122 invented fields. It is currently kept only because
`tests/test_validator.py` builds its knowledge base from it — it is a test
fixture, not research data. Stage 1 replaces `data_fields` with the real
~4,000-field catalog; until then, any alpha built on a non-price-volume field
will not simulate on BRAIN.

## Setup

```bash
cd backend
uv venv --python 3.11 .venv
VIRTUAL_ENV=.venv uv pip install -e ".[dev]"

cp ../.env.example ../.env          # fill BRAIN_EMAIL / BRAIN_PASSWORD
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.seeds.load_operators
.venv/bin/python -m scripts.fetch_brain_catalog     # the real ~4,367-field catalog
```

## Running it

```bash
# the console — the morning pass lives here
python -m uvicorn app.main:app          # then open http://127.0.0.1:8000
```

One self-contained HTML file (`app/static/index.html`), no build step and no
`node_modules`. Press `?` for the keymap. The morning is `c` (copy) → paste into
BRAIN → `s` (record that you submitted) → `j` (next) → `n` (launch the next
family).

The heatmap is the point: a smooth ridge is a mechanism, an isolated bright cell
is a fluke. The **outlined region** is everything clearing the multiple-testing
bar — a connected continent answers "is it a ridge?" and "does it clear?" at
once. Unsimulated cells are dashed holes, never dark values, and a near-miss
whose neighbours were never run offers to simulate exactly those.

```bash
# same information as a markdown file, for a terminal-only day
python -m scripts.report

# expand one mechanism across the grid and simulate part of it
python -m scripts.run_family --field liabilities --denominator cap --simulate 48

# pull results for alphas already simulated on the platform
python -m scripts.import_brain_alphas
```

`run_family` expands a `(field, denominator)` mechanism into complete
window × decay surfaces — every expression valid by construction, no LLM in the
loop — saves them, and simulates a capped subset. `report` then applies the
plateau filter and prints a ranked shortlist plus the surface grids that justify
it. **You review and submit; nothing is ever submitted by this code.**

Tests: `cd backend && .venv/bin/python -m pytest` (80 tests, no DB/network/API key
required — `conftest.py` builds a throwaway SQLite database per session).

## The one invariant

**The LLM never writes expression syntax.** It proposes economic mechanisms and
fills slot choices; deterministic code emits the expression and the validator
gates it. This is what makes generation safe to run at volume — see STRATEGY.md
Rule 3.
