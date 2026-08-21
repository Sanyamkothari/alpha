# Quant engineering review — 2026-08-21

Scope: `backend/app/services/{plateau,subperiod,correlation,pnl_storage,allocator,spend,constructor,evolution}.py`,
`backend/app/services/brain/client.py`, `backend/scripts/{backfill_pnl,verify_pnl_reconciliation}.py`.

Method: code read. **The test suite was NOT run** — `pytest` is not installed in this
environment (`python -m pytest` → `No module named pytest`). No claim below rests on a
test result, and no DB query was run (no `wq.db` present). Findings are static reads with
file:line references; the ones marked *unverified against live data* need a query or a
BRAIN call to close.

---

## Severity 1 — results are wrong, or silently may be

### 1.1 The daily-PnL series is never verified to be non-cumulative, and every statistic assumes it is

`scripts/backfill_pnl.py:71-74` and `app/services/correlation.py:164-169` store the raw
`/alphas/{id}/recordsets/daily-pnl` payload verbatim:

```python
pnl = np.array([float(r[1]) for r in records], dtype=float)
store.save_pnl(local_alpha.id, dates, pnl)
```

No differencing, no check. Everything downstream treats that array as a series of
**daily increments**: `compute_dsr` takes `mean/std` of it (`subperiod.py:83-88`),
`evaluate_subperiod_stability` computes split-half and rolling Sharpes from it
(`subperiod.py:126-175`), and `compute_pairwise_correlation` runs `np.corrcoef` on it
(`correlation.py:40-45`).

`scripts/verify_pnl_reconciliation.py:3-5` states the question is still open — its own
docstring says it exists to "empirically test **whether** … daily-pnl returns discrete
daily dollar PnL series (non-cumulative) vs cumulative PnL curves". So the codebase
admits it does not know, and ships the statistical gate anyway.

If the endpoint returns a cumulative curve, then:

- Every Sharpe recomputed locally is meaningless (mean/std of a trending level series).
- The correlation gate is the worst affected: Pearson correlation between two cumulative
  PnL curves is a spurious regression. Two unrelated alphas both drifting upward correlate
  near 1.0. A 0.55 threshold on cumulative curves rejects almost everything, or — if signs
  differ — passes things it should not. This is the classic non-stationary-series
  correlation error.
- Split-half and rolling-window "stability" measure trend, not consistency.

`verify_pnl_reconciliation` (`subperiod.py:203-243`) is the right check and exists, but is
only wired into `backfill_pnl.py:78-80` where it increments a counter and is never gated
on. `plateau.evaluate` never calls it. **Recommendation:** make reconciliation a hard
precondition — refuse to compute DSR/correlation for an alpha whose recomputed Sharpe does
not match the BRAIN-reported Sharpe within tolerance. That single check also answers the
cumulative-vs-daily question empirically, for free.

*Unverified against live data — needs one BRAIN call to close, and it is the highest-value
call available.*

### 1.2 A probability threshold is used as a Sharpe bar (`plateau.py:354`)

```python
dsr_passed = bool(
    point.sharpe is not None
    and point.sharpe >= (DSR_PROMOTION_THRESHOLD if use_dsr else COLD_START_SHARPE_BAR)
    ...
```

`DSR_PROMOTION_THRESHOLD = 0.95` is a **probability** (the DSR hurdle). `COLD_START_SHARPE_BAR
= 1.50` is an **annualized Sharpe**. They are being selected between and compared against
`point.sharpe`. Two consequences on the `require_pnl=False` path:

1. The bar becomes "Sharpe ≥ 0.95" — well below the `BASE_SHARPE_BAR` of 1.25 that the same
   function applies twelve lines earlier.
2. The direction is inverted. `use_dsr` is true when the family is *large*
   (`max_slice_trials >= 30`), i.e. when multiple-testing risk is *highest* — and that is
   exactly when this line makes the bar *easier* (0.95 instead of 1.50).

### 1.3 The multiple-testing correction only counts trials inside one family

`plateau.py:326` passes `family_sharpes` — the Sharpes of one `family_key` — as the trial
population for DSR, and `subperiod.compute_dsr:90-92` uses `len(sharpes_clean)` as the trial
count. Per CLAUDE.md the DB holds 4,857 alphas across 486 simulations; the deflation sees
only the current family.

Worse, the trials it does count are the *most correlated ones available* — neighbouring
`(window, decay)` points on one surface. `sigma_sr` (`subperiod.py:93`) is their
cross-sectional std, which for a smooth surface is small, so `sr_star` is small, so DSR is
inflated. The module has the fix — `compute_effective_trials` (`subperiod.py:41-64`)
implements the eigenvalue participation ratio — but **nothing ever calls it**. `n_eff` is
`None` at every production call site.

The correct shape: build the family PnL matrix via `PnLStore.get_aligned_matrix`, get
`N_eff` from its correlation matrix, and use the *global* trial count as the ceiling. Right
now the system deflates against the friendliest possible trial set.

### 1.4 A statistical threshold is keyed off free-text prose (`plateau.py:329`)

```python
is_re_promoting = bool(alpha_obj and "watchlist" in (alpha_obj.comments or "").lower())
target_dsr_hurdle = DSR_RE_PROMOTION_THRESHOLD if is_re_promoting else DSR_PROMOTION_THRESHOLD
```

Which of two DSR hurdles (0.97 vs 0.95) an alpha faces depends on whether the substring
`watchlist` appears in an operator's free-text comment. Anyone typing "removed from
watchlist" gets the *stricter* bar. This is unauditable, silently mutable, and is the same
class of defect as the drift incident CLAUDE.md documents: control state living somewhere
it cannot be verified. It needs a column and a migration.

### 1.5 Missing data reads as "uncorrelated" in the gate

`correlation.py:40-45`, `compute_pairwise_correlation` returns `0.0` when the arrays are
short or misaligned. `check_portfolio_empirical_correlation:97-99` silently `continue`s past
any portfolio alpha with fewer than `MIN_COMMON_TRADING_DAYS = 500` overlapping days. If no
pair clears the overlap bar, the function returns `max_corr = 0.0` — indistinguishable from
"measured, and genuinely uncorrelated".

The codebase already knows this is wrong: `compute_max_self_correlation_with_submitted`
(same file, 175-238) was written specifically to return `None` for unmeasured, with a
docstring promising it "never fabricates". But the function that actually gates promotion is
the other one. Two functions compute the same quantity with opposite missing-data semantics,
and the gate got the unsafe one.

For a correlation constraint, unknown must fail closed, not open.

---

## Severity 2 — the Phase 1 experimental design is not what the code runs

### 2.1 The random-stratified arm does not get 30%, and can get 0%

`allocator.py:877-879` reports `exploit_simulations=declared_exploit` etc., computed at
667-669 from the declared shares. The tasks actually built use a different quantity —
`exploit_budget`/`random_budget` (683-685), further quantized by
`budget // sims_per_territory` (694, 762). With the defaults (`total=200`,
`sims_per_territory=49`):

- exploit: `max(1, 100//49)` = 2 territories × 49 = 98
- random_stratified: `max(1, 60//49)` = **1** territory × 49 = 49
- plateau_fill: the loop at 815-830 ends in an unconditional `break`, so **at most one**
  task ever = 49

Total 196; the closure step at 861-864 distributes the remaining 4 round-robin across arms.
Actual split ≈ **50 / 25 / 25**, reported as 50 / 30 / 20. The calibration arm is
systematically underfunded by integer division, and `BudgetPlan` reports the intent rather
than the outcome, so nothing downstream can detect it.

Worse, the whole random-stratified block is guarded by
`if all_fields and total_simulations >= (2 * sims_per_territory)` (`allocator.py:737`). Any
campaign under 98 simulations produces **zero** calibration tasks, silently. CLAUDE.md says
this arm must not be disabled or weakened; a small campaign disables it.

**Recommendation:** allocate territories by largest-remainder apportionment against the
declared shares, assert `sum(task.target) == total` *per arm*, and populate `BudgetPlan`
from the realized tasks, not from `declared_*`.

### 2.2 Quartile stratification silently collapses (`allocator.py:767`)

```python
pool = q_fields[quartile_idx] or all_fields
```

`user_count` on data fields is heavily zero-inflated. If the 25th and 50th percentiles are
both 0 (`allocator.py:743-747`), then the `uc <= q_bounds[0]` / `elif uc <= q_bounds[1]`
chain at 753-761 puts every zero-user field in Q1 and leaves Q2 (and possibly Q3) empty. The
`or all_fields` fallback then draws from the **whole population** while still labelling the
task `quartile=2`.

The result is a dataset that claims stratified crowding coverage and does not have it —
mislabelled, with no warning logged. Since `docs/strategy/VALIDATION_PROTOCOL.md` is
pre-registered on this stratification, this quietly invalidates the Phase 2 study that the
arm exists to make possible. It should raise or log loudly, not fall back.

Related: the fallback boundaries `q_bounds = [10.0, 100.0, 1000.0]` (749) are magic constants
presented as percentiles.

### 2.3 Dataset hit-rate counts alphas, not territories

`allocator.py:322-338` increments `tried[ds]` and `passed[ds]` once per `(Alpha, AlphaMetric)`
row. CLAUDE.md is explicit: "Territory = field × operator_family × horizon_band. This is the
unit of analysis for everything statistical. 384 near-duplicate alphas in one territory count
as ~1 observation."

`_dataset_priority` (`allocator.py:345-350`) then ranks the exploit arm on that number. Given
4,608 of 5,177 alphas share one template, whichever dataset that template ran on dominates
the ranking. The metric measures how much you sampled, not how well it worked. It should
aggregate to distinct territory keys first.

Compounding it: `min(1.0, hit * 10)` saturates at a 10% hit rate, and there is no minimum
sample size or shrinkage — one simulation that passes gives `hit_rate = 1.0` and a maximal
score. A Beta prior is the obvious fix and the file already imports the machinery for it (see 3.1).

### 2.4 `plateau_fill` rebuilds a different territory than the one it is completing

`allocator.py:815-830` selects `fkey` from `incomplete_families`, extracts only the field code
via `family_field_code`, then hardcodes:

```python
operator_family="ts_zscore",
wrapper_shape="rank",
horizon_band="medium",
```

The operator family and horizon of the family it is supposedly completing are parsed away and
discarded. So "complete the surface for `assets:ts_rank:long@…`" emits work for
`assets:ts_zscore:medium@…` — a different territory, in the single template Phase 1 exists to
break. `parse_territory_signature` already returns exactly the fields needed here.

### 2.5 The surface can never reach the size the allocator waits for

`constructor.py:57` sets `STANDARD_WINDOWS = (5,10,20,40,60,120,250)`; `expand` filters it by
horizon band at 470. Against `derive_horizon_band` (126-146) that yields **short: 2 windows,
medium: 3, long: 2**. With 7 decays, one structural slice holds 14–21 points.

`allocator.py:58-61` sets `DEFAULT_SIMS_PER_TERRITORY = SURFACE_SIZE = 49` (the 7×7 grid, from
before horizon banding), and `incomplete_families` is `0 < count < sims_per_territory` (663).
A horizon-banded family therefore **stays "incomplete" permanently** and keeps drawing
plateau_fill budget it can never satisfy.

The counts are also not comparable: `sim_counts` (`allocator.py:637-651`) counts alphas per
`family_key` across all structural slices, while `surface_size` is one slice.

### 2.6 Structural axes are truncated in product order, not sampled

`constructor.py:519-522`:

```python
for ts_op, cs_op, group, neutralization, truncation, universe in configs:
    ...
    if len(out) + surface_size > max_candidates:
        break
```

`break`, not `continue`, over an `itertools.product`. With `max_candidates=400` and a
21-point surface, ~19 of 700 configs are emitted — and because `product` varies the last axis
fastest, they are all a **prefix**: `SUBINDUSTRY` neutralization only. `MARKET` and `NONE`
never appear.

This directly defeats the module's own stated design (`constructor.py:16-19`): "Settings are
part of the family, not a wrapper around it. Neutralization, decay and truncation move Sharpe
by 0.3–0.6 … which is the single biggest reason the first 51 alphas failed: each idea was
sampled at exactly one settings point." The code samples at exactly one neutralization point.
The docstring at 12-14 says structural axes "are sampled"; they are truncated.

### 2.7 `suggest()` advertises seeding it does not do (`allocator.py:360-369`)

```python
_rng = rng or (random.Random(seed) if seed is not None else random.Random())
```

`_rng` is never read again anywhere in the function. Every choice inside is deterministic
index rotation (`len(out) % len(...)`, 545/560). The docstring's "Unseeded calls use
random.Random() for diverse interactive UI recommendations; reproducible campaigns pass an
explicit seed" is false in both halves. `plan_budget_allocation` passes `rng=rng` at 699,
which does nothing — the campaign `seed` column has no effect on the exploit arm.

Either use the RNG or delete the parameter; a dead reproducibility knob is worse than none,
because the campaign records a seed that implies replayability.

### 2.8 Evolution's window jitter is dead for 5 of 7 production windows

`evolution.py:55-62` keys `_WINDOW_JITTER` on `(5, 10, 22, 63, 126, 252)` — the **WIDE**
ladder. Production default is `STANDARD_WINDOWS = (5,10,20,40,60,120,250)`. The lookup at
108 is `if val in _WINDOW_JITTER`, so windows 20, 40, 60, 120 and 250 never jitter. Only 5
and 10 mutate. The same stale ladder appears as the fallback in `plateau.py:44-45`.

---

## Severity 3 — correctness and robustness bugs

### 3.1 `backfill_pnl.py` can attach one alpha's PnL to a different alpha

`scripts/backfill_pnl.py:34-37`:

```python
expr_to_alpha[(a.expression.strip(), a.neutralization, a.decay)] = a
expr_to_alpha[a.expression.strip()] = a
```

Both keys go in the same dict, and the expression-only key is overwritten by whichever alpha
happens to come last across 4,857 rows. The lookup at 48 falls back to it:

```python
local_alpha = expr_to_alpha.get((code, neutr, decay)) or expr_to_alpha.get(code)
```

So a remote alpha whose settings do not match any local row is matched on expression alone
and its PnL is saved (74) under an arbitrary local alpha id with **different neutralization
and decay** — a different backtest. That PnL then feeds DSR, subperiod and correlation for
the wrong alpha. This is the drift incident's failure mode (local state written without
platform verification) in a different column. The fallback should be removed, or at minimum
refuse to match when it is ambiguous.

Also at 77: `float((ra.get("is") or {}).get("sharpe", 0.0))` raises `TypeError` if the key is
present with a `None` value — `.get` returns `None`, not the default.

Also: `db_alphas` is read inside `session_scope` (32) and the ORM instances are used after the
block exits (48-74). It happens to work only because `expire_on_commit=False`
(`db/session.py:66`) — an implicit dependency on a session flag, not an intended contract.

### 3.2 `verify_pnl_reconciliation.py` crashes when nothing reconciles

Line 64: `valid_count/len(reported)*100` divides by zero whenever no stored `.npy` file has a
matching `AlphaMetric` row. The guard at 36 only covers "no `.npy` files at all". Line 60,
`stats.linregress` on empty arrays, fails first. The one script whose job is to tell you the
PnL data is untrustworthy is the one that cannot run when it is most untrustworthy.

### 3.3 `Any` is used but never imported (`subperiod.py:206`)

`from typing import Sequence` at 16 is the only typing import; `pnl_store: Any` at 206 is
undefined. It does not raise today only because `from __future__ import annotations` (line 12)
defers evaluation — but `typing.get_type_hints`, Pydantic, or any runtime introspection over
this signature raises `NameError`. Same latent pattern in `plateau.py:263`, where the
`PnLStore` annotation resolves to a name imported only inside the function body (line 268).

### 3.4 The plateau test passes valleys as well as ridges (`plateau.py:296-303`)

```python
ratio = neigh_median / point.sharpe
...
is_plateau = bool(judgeable and positive and ratio is not None and ratio >= PLATEAU_RATIO)
```

The ratio is unbounded above. A point at Sharpe 0.3 surrounded by neighbours at 2.0 scores
`ratio = 6.7` and is classified a plateau. The test as documented ("a lone spike surrounded by
dead neighbours is a coincidence; a broad ridge is a mechanism") wants a two-sided band —
something like `PLATEAU_RATIO <= ratio <= 1/PLATEAU_RATIO`. It matters because representative
selection (`plateau.py:392-401`) sorts by `neighbour_median_sharpe` **first**, which actively
prefers the point with the strongest neighbours — i.e. the valley — over the ridge top.

### 3.5 Margin unit is inferred from magnitude (`result_import.py:89-104`)

```python
_MARGIN_FRACTION_CEILING = 0.01
def _margin_to_bps(value: float) -> float:
    if value and abs(value) < _MARGIN_FRACTION_CEILING:
        return value * 10_000.0
    return value
```

The comment asserts "The two scales cannot overlap in practice". They do: a fraction margin
of exactly 0.01 is 100 bps — an entirely ordinary low-turnover alpha — and passes through
unconverted, stored as `0.01` in a column named `margin_bps`. Guessing units from magnitude is
the wrong shape of fix; normalize once at each ingest boundary and record which scale arrived.

Related doc drift: `brain/client.py:18-20` still claims `normalize_is_block` does this
conversion. It does not — `normalize_is_block` (`client.py:104-106`) is `return dict(is_block)`.

### 3.6 Preflight silently spends a simulation (`brain/client.py:335-360`)

`config_available` POSTs a real `/simulations` for `close` and returns as soon as the POST is
accepted, never polling or cancelling. Given that BRAIN throughput is described throughout as
*the* binding resource (`spend.py:9-13`) and measured throughput is ~13 sims/day, a preflight
that consumes a slot and abandons a running job should at least be logged and counted. The
429 branch returning `(True, "assuming available")` also converts "I don't know" into "yes".

### 3.7 Capacity reporting is a theoretical maximum presented as a measurement

`spend.py:35`: `DAILY_SIM_CAPACITY = int(86_400 / 90 * 3)` = 2,880/day, and
`simulation_spend` (159-171) reports `capacity_used_pct` against it. CLAUDE.md records actual
throughput as **~13 sims/day**. The dashboard therefore reports ~0.5% utilization for a
pipeline running at its practical limit.

`wall_clock_hours = total * per_sim / MAX_CONCURRENT / 3600` (163) assumes perfect 3-wide
packing with zero gaps, understating elapsed time by more than two orders of magnitude
against the measured rate. Either report the realized rate from `AlphaMetric.created_at`
timestamps, or label these fields `theoretical_*`.

---

## Severity 4 — single-source-of-truth violations

CLAUDE.md invariant 4 and the drift incident. Submission truth is defined as derived from
`submission_attempts`. Four modules disagree:

| Location | Reads submission from |
|---|---|
| `correlation.py:28-32` | `SubmissionAttempt.result == "submitted"` ✅ |
| `spend.py:182` | `Alpha.status == AlphaStatus.SUBMITTED.value` ❌ |
| `constructor.py:503-506` | `filter_by(status=AlphaStatus.SUBMITTED.value)` ❌ |
| `plateau.py:130-131` | `port_alpha.status == AlphaStatus.SUBMITTED.value` ❌ |
| `allocator.py:404` + `418` | **both**, appended to the same list ❌ |

`allocator.suggest` reads the status column at 404 *and* `submission_attempts` at 418, pushing
both into `submitted_sigs` — so genuinely-submitted alphas are double-counted and
status-only rows (the drift failure mode) are trusted. `plateau.py:130` is the sharpest case:
the portfolio it is filtering was *already* built from `submission_attempts`, and it then
re-filters on the status column, so a row where the two disagree is dropped from the
correlation gate.

Also `allocator.py:404` compares against the string literal `"submitted"` rather than
`AlphaStatus.SUBMITTED.value`.

**Doc-level instance:** `docs/PHASE1_OPERATING_GUIDE.md:11` states "Submission quota is 4/day
(confirmed)" and that it is not the binding constraint. `CLAUDE.md:84` still lists "BRAIN
submission quota per week" as an open question needing a human. One of the two is stale.

---

## Severity 5 — practices worth changing

- **`plateau.evaluate` is O(N²) in disk reads.** `db.get(Alpha, …)` at 328 runs per point
  (N+1), and `check_portfolio_empirical_correlation` at 361 re-loads the candidate *and every
  portfolio alpha's* PnL from the store on each iteration. `PnLStore.get_aligned_matrix`
  already exists to do this once per family; it is unused here.
- **Dead code presented as API.** `DiscountedThompsonSampler` and
  `SimulationBudgetOrchestrator` (`allocator.py:213-259`) are labelled "Backward
  Compatibility" and referenced nowhere in the allocation path. `SimulationBudgetOrchestrator`
  takes `daily_budget` and `explore_ratio` in `__init__` and then ignores both — `get_allocation`
  is a `@classmethod` returning hardcoded slot counts, and `allocate_slots` duplicates it with
  a different signature. Either delete, or wire the Beta prior into 2.3 where it is needed.
- **36 bare `except Exception`** across `app/` and `scripts/`, most logging a warning and
  returning a neutral value. In `PnLStore.save_pnl` (`pnl_storage.py:40-43`) a failed write
  logs and returns, leaving the in-memory cache populated — so within one process the PnL
  looks saved and after restart it is gone.
- **`PnLStore._cache` is unbounded** and never invalidated. `verify_pnl_reconciliation.py:29`
  reaches into `store._dir` (a private attribute) from a script.
- **Docstring/constant mismatches.** `subperiod.py:8` promises rolling positivity ">= 75%";
  the parameter default at 115 is `0.70`. `plateau.py:41-42` documents fallback ladders that
  no longer match `constructor.STANDARD_WINDOWS`.
- **`alphahandoff.zip` (48 KB binary) is tracked in git**, and `backend/repro.py` — a scratch
  reproduction harness that imports `tests.conftest` — is tracked at the backend root, outside
  `tests/`.
- **Test suite could not be run here** (no `pytest`). CLAUDE.md targets 194 tests under ~5s;
  that was not verified.

---

## Suggested order of work

1. **Resolve the PnL semantics** (1.1). One BRAIN call. Everything statistical is conditional
   on it, and if the series is cumulative most of §1 collapses into a single root cause.
2. **Fix 1.2** (one-line unit confusion) and **1.5** (fail closed on unmeasured correlation).
3. **Fix the arm arithmetic and quartile collapse** (2.1, 2.2) before more Phase 1 budget is
   spent — every campaign run under the current code produces data the validation protocol
   cannot use.
4. **Reconcile submission truth to one source** (§4), and add a test that fails if
   `Alpha.status` is read as submission evidence outside the derivation path.
5. Then 2.4–2.8, which are diversity defects — they bound how much the phase can learn, but
   they do not corrupt what it has already recorded.

Items 3 and 4 touch the frozen filters' *inputs*, not the filters themselves; the
Phase 1 freeze on plateau/DSR/subperiod/correlation thresholds is not affected by any
recommendation above. 1.2 and 1.5 are bug fixes to gates that do not currently implement
their documented thresholds, not re-tunings of them.
