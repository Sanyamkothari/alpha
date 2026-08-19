# User review — Alpha Research Engine

> **Status: resolved.** All ten findings below were fixed on
> `claude/project-feature-review-qxc27g` and hardened further after the follow-up
> review in [CODE_REVIEW.md](CODE_REVIEW.md). This document is kept as the original
> point-in-time record — the numbers in it describe the code as it was, not as it is.
> Current state: 270 tests passing, the console promotes alphas, and the heatmap
> renders every emitted cell. See
> [docs/IMPLEMENTATION_RECORD.md](docs/IMPLEMENTATION_RECORD.md) §6 for what changed.


A hands-on review done the way a new user would: clone, follow `README.md` literally,
run the CLI workflows, drive the web console in a browser, and check whether the
product's promise — *a morning shortlist of submittable alphas* — actually arrives.

Environment: Linux, Python 3.11.15, `uv`, no WorldQuant BRAIN account.

---

## Verdict

The engineering underneath is strong: the install is clean, all 194 tests pass with no
network, and the project's central safety claim — no automated submission — is genuinely
enforced, not just documented. But the pipeline's **last gate rejects everything**. In a
full end-to-end run with a healthy 49-point family, honest metrics and 1300 days of
deliberately uncorrelated PnL per alpha, the shortlist came back empty, and it does so
structurally rather than by bad luck. On top of that, the plateau heatmap — the visual
centrepiece — silently hides 84% of the results it is drawing.

Neither of these shows up in the test suite, because the tests pass explicit portfolios
and explicit grids where the product uses defaults.

---

## Blocking findings

### 1. The orthogonality gate rejects every candidate, always

`check_portfolio_empirical_correlation()` is documented as "empirical with structural
fallback", but the structural proxy at `backend/app/services/correlation.py:115` runs
whenever the empirical check found no collision — including when full PnL exists and the
measured correlation is ~0.00. The proxy therefore overrides real evidence instead of
standing in for missing evidence.

That interacts badly with the portfolio definition. `plateau.py:259` and
`plateau.py:110` both admit `PASSED` alphas, not just `SUBMITTED`, into the portfolio.
Every candidate that clears BRAIN's checks becomes `passed` on import, so a family's own
grid siblings become each other's portfolio. And because the structural skeleton buckets
lookback windows (`<WIN:fast>` / `<WIN:slow>`), the 49 distinct grid points collapse into
just **4 structural hashes** — groups of 14, 14, 14 and 7.

Measured on a real run: **49 of 49 candidates blocked by structural collision.** Every
member of every group is blocked by another member of the same group. Nothing can escape.

The funnel from `scripts/report`, with PnL present and metrics that should clear:

| Family | Simulated | 1. Checks | 2. Plateau | 3. Sub-Period | 4. DSR | 5. Orthogonal | Promoted |
|---|---|---|---|---|---|---|---|
| `liabilities/cap@USA/TOP3000/d1` | 49 | 49 | 42 | 29 | 4 | **0** | **0** |

One candidate (Sharpe 1.90) cleared plateau, sub-period and DSR and was held back by
*nothing but* the collision.

Two independent fixes are needed:
- Run the structural proxy only when empirical data is genuinely unavailable (no candidate
  PnL, or no portfolio alpha met `MIN_COMMON_TRADING_DAYS`).
- Exclude same-family siblings from the portfolio during a family evaluation, or restrict
  the structural rule to `SUBMITTED` alphas the way the same-family rule at
  `plateau.py:139` already does.

### 2. The collision message names an alpha that was never submitted

`plateau.py:135` hardcodes the word "submitted":

> `structural correlation collision with submitted alpha #22`

Alpha #22 has status `passed`. It was never submitted, and the console showed
`0 submitted` at the same moment. This is the one place the tool asks the user to trust
its judgement over a good-looking Sharpe, and it misstates its own evidence.

### 3. The plateau heatmap hides 41 of 49 results

Two grid definitions disagree:

| | windows | decays | cells |
|---|---|---|---|
| Constructor default (`constructor.py:56`) | 5, 10, 20, 40, 60, 120, 250 | 0, 1, 2, 4, 6, 8, 16 | 49 |
| Display ladders (`plateau.py:49`) | 5, 10, **22, 63, 126, 252** | 0, 4, 8, 16 | 24 |

`/api/ui/surfaces` returns the *display* axes alongside cells keyed by the *constructor's*
coordinates, and both the console (`index.html:754`) and the ASCII surface in
`scripts/report` iterate those axes. Verified against a real family:

```
total result cells: 49
cells that land on the rendered axes: 8
INVISIBLE in heatmap and report: 41   (84%)
```

Worse, 16 of the 24 rendered cells (windows 22/63/126/252) can never be filled — the
constructor never emits them, so the "fill missing cell" recovery path has no `alpha_id`
to offer. The user sees a mostly-empty surface for a fully-simulated family and is invited
to spend simulation budget closing holes that are not real.

The plateau *filter* is fine — `_neighbours()` resolves ladders dynamically from the
observed points (`plateau.py:209`). Only the display is wrong. Deriving the axes the same
way would fix both call sites.

---

## Correctness and polish

### 4. "N simulated" counts imports, not alphas

`ui.py:102` uses `count(AlphaMetric.id)`. Re-importing a corrected result for the same
alpha double-counts, and the header reports an impossible figure:

> **49 alphas · 98 simulated · 49 pass checks**

It also inflates the "BRAIN today" budget meter (`98 / 2,880 sims`). Should be
`count(distinct alpha_id)`.

### 5. "0 sat on a plateau" is a hardcoded zero

`index.html:629` prints the literal `0` in the zero-survivor summary line. In the run
above, 42 candidates sat on a plateau and `is_plateau: true` is right there in the payload
the same view already fetched. The headline number contradicts the report and the API.

---

## Onboarding

### 6. Following the README leaves the database unusable without BRAIN credentials

Setup step 3 runs migrations, `app.seeds.load_operators`, then
`scripts.fetch_brain_catalog` — which needs credentials. Without an account the user has
operators but **no fields and no lookups**, and every validation fails:

```
POST /api/validate  {"expression": "rank(ts_delta(close, 5))"}
→ "field 'close' is not in the catalog for USA/TOP3000/delay1"
```

The fix already exists and is not mentioned: `python -m app.seeds.seed_all` loads lookups,
operators and the 122-field sample catalog. After running it the same expression validates
and `run_family --simulate 0` expands 49 candidates offline. Recommend making `seed_all`
the documented step and `fetch_brain_catalog` the optional upgrade.

More broadly: there is no demo or fixture path, so the tool cannot be evaluated at all
before committing real credentials to it.

### 7. Missing credentials produce a stack trace

`scripts.fetch_brain_catalog` raises `BrainAuthError` uncaught. The message itself is
good — "set BRAIN_EMAIL and BRAIN_PASSWORD in the repo-root .env" — but it arrives under
14 lines of traceback, so the expected first-run outcome reads as a crash.

### 8. Three README links point at the author's laptop

`file:///Users/sanya/Projects/alpha/...` for `DECISIONS.md`, `STRATEGY.md` and
`PACKAGING.md` (README lines 16, 18, 234). Dead for every other reader and on GitHub.
They should be relative paths.

### 9. Empty database reports "Everything has been tried"

`scripts.report` on a fresh install ends "What to try next: Everything has been tried. Add
a dataset or widen the search grid." — the opposite of the true state.

### 10. Minor

- `/favicon.ico` 404s on every page load (the console's only network error).
- "Unresolved" sits in the tab bar but opens a modal; while it is open the other tabs
  look clickable and silently do nothing. `Esc` and the two buttons do dismiss it.
- Doc drift: README says "16 tables" (22 exist) and "120+ tests" (194 collected).

---

## What works well

- **Install is genuinely 5 seconds.** `uv venv` + `uv pip install -e ".[dev]"`, then six
  Alembic migrations apply cleanly on SQLite. Nothing needed a compiler.
- **194 tests pass, zero network, no flakes.**
- **The no-submission invariant is real.** `ALLOWED_POST_PATHS` is a frozenset of
  `/authentication` and `/simulations`, every POST in the package targets it, and
  `test_brain_no_post.py` enforces it. The strongest claim in the README is the one best
  backed by code.
- **Validator error quality.** Structured codes with character spans, and multiple
  independent errors reported per expression rather than failing at the first.
- **Near-miss reasons are specific and honest** — "recent 252d Sharpe (0.55) decayed below
  50% of full (1.50); DSR 0.414 below 0.95 threshold" tells the user exactly which gate
  fired and by how much. This is the best part of the product.
- **Empty states have real copy**, not spinners: "49 candidates are built and waiting.
  Pick a field from Next up on the right, or press `n`."
- **The console is fast and dependency-free**, and reads well in a 1440px viewport.

---

## Suggested order of work

1. Stop the structural proxy from overriding measured PnL correlation, and keep a family's
   own siblings out of its portfolio (finding 1). Nothing else matters until a shortlist
   can populate.
2. Derive the surface axes from the observed points instead of the static ladders
   (finding 3).
3. Correct "submitted" in the collision message, the `simulated` count, and the hardcoded
   plateau zero (findings 2, 4, 5).
4. Document `seed_all` and fix the three local file:// links (findings 6, 8).

Findings 1 and 3 both slip past the suite because the tests supply explicit portfolios and
explicit ladders. Two regression tests — promote from a single family with default
arguments, and assert the surface axes cover every emitted cell — would have caught both.
