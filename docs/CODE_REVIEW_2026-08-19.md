# Code review — search-gap implementation (Phases 0–6)

**Branch reviewed:** `claude/project-feature-review-qxc27g` @ `9958c5e`
**Base:** `origin/main` @ `683fe54` · **Diff:** 81 files, +6167 / −1162
**Reviewer method:** read every new module and the changed regions of
`constructor.py` / `plateau.py` / `pnl_storage.py`; installed the environment and
ran the suite; reproduced two findings with executable checks rather than by
inspection.

---

## Verdict

**Phase 0 (A1/A2/A3) and the constructor work (B1/B2/B3/D1/D2) are genuinely
integrated and largely correct.** The reconciliation gate fails closed, `N_eff`
is in the production call path, and `_structure_of` no longer collides on
universe or hump. That is the hard, load-bearing half and it landed.

**Phases 3, 5 and 6 are not integrated.** `novelty.py`, `orthogonalization.py`,
`perturbation.py`, `cscv.py` and `feedback_loop.py` have **zero importers
anywhere in `app/` or `scripts/`** — each is reachable only from its own phase
test. The tests pass because they call the modules directly. The acceptance
criteria for those items were integration-level by design ("`scripts/report.py`
prints PBO per family alongside DSR"), so they are not met.

**The suite does not reproduce the claimed result.** `262 passed` is reported;
this environment gives **261 passed, 1 failed**.

Two findings were reproduced with code rather than argued from reading:
the selector's coverage collapse (F2) and CSCV's missing degradation guard (F5).

---

## What was done well

Worth stating plainly, because the review below is long:

- **A1 fails closed.** `PnLStore.save_pnl` now *rejects the write* when the
  recomputed Sharpe misses the reported one, and `plateau.evaluate()` refuses to
  promote an alpha whose PnL will not reconcile. That is the correct shape — a
  bad series produces a missing promotion, not a confident wrong one.
- **A2 is real and orientation-consistent.** `build_family_matrix` →
  `compute_correlation_matrix` → `compute_effective_trials` → `compute_dsr(n_eff=…)`
  is wired end to end, and `(N, T)` is used consistently through all three, which
  is the easiest thing in this chain to get silently backwards.
- **A3 is complete.** `_structure_of` gained `universe` and `hump`, and all three
  constructor layers (depth-1, depth-2, `ts_corr`) now carry `universe` in
  `grid_extra` — the inconsistency that would have made the fix partial.
- **The CSCV kernel is correct and fast.** Block-aggregate precomputation makes any
  subset's Sharpe O(1), exactly the right design. Verified numerically: a matrix
  with one genuinely superior column returns **PBO = 0.000**; pure noise returns
  **0.492**. The statistic behaves.
- **`hump` is placed correctly** — outside the cross-sectional wrap, with distinct
  structural hashes per level — and `test_phase2_turnover.py` checks the wrapping,
  KB validation, and hash distinctness. A good test.
- **The stratified selector is a better idea than the structure-major loop the plan
  specified.** Round-robin over strata is the right primitive. It needs one fix
  (F2), not a redesign.

---

## Findings

Severity: **S1** blocks the phase from being considered done · **S2** correctness
or design defect · **S3** hygiene.

### F1 · S1 — Five of seven new modules are dead code in production

```
module              prod_importers   test_importers
novelty                     0              1
cscv                        0              1
perturbation                0              1
orthogonalization           0              1
feedback_loop               0              1
```

Verified by exhaustive grep across `app/` and `scripts/` for both
`services.<mod>` and `from … import <mod>` forms. The only surviving "novelty"
reference in production is `allocator_bandit.py`'s pre-existing dataset-level
weight, which is unrelated to C1.

Consequences, item by item:

| Item | Claimed | Actual |
|---|---|---|
| C1 novelty prior | "candidate re-ranking prior to simulation" | `rank_candidates_by_novelty` is never called; `expand()` returns unsorted |
| C2 orthogonalisation | "generating residual candidates" | never invoked; no run produces a residual |
| E1 PBO | "sub-second engine" | never reaches `report.py` or the UI |
| E2 perturbation | "rejects isolated spikes" | never consulted by `evaluate()`; promotion logic unchanged |
| F1 feedback loop | "measuring realized degradation" | no CLI, no caller; `sync_submission_outcomes.py` gained 5 lines and does not call it |

This is the "tests green, feature absent" failure mode. The modules themselves are
mostly good — the work remaining is wiring, not rewriting.

### F2 · S1 — The selector reintroduces the coverage collapse on structure axes

`select_surface_configs` strata on `(layer, ts_sig)`, and `ts_sig` embeds the hump
level and vector reducer. `strata_keys = sorted(by_stratum.keys())` then
round-robins in **alphabetical** order — and `hump(...)` sorts before `ts_*`.

Reproduced:

```python
# 7 ts transforms x humps (None, 0.01, 0.05) = 21 strata, budget 8
sel = select_surface_configs(cfgs, 8)
```
```
strata available: 21
selected: hump(ts_decay_linear,0.01), hump(ts_decay_linear,0.05),
          hump(ts_delta,0.01),        hump(ts_delta,0.05),
          hump(ts_mean,0.01),         hump(ts_mean,0.05),
          hump(ts_quantile,0.01),     hump(ts_quantile,0.05)

distinct ts transforms:  4   (down from 7)
un-humped selected:      0
```

So the moment B3's axis is actually used at a realistic budget, structural coverage
drops and **the un-humped control arm disappears entirely** — you can no longer
measure what hump bought, which is the entire point of the axis. This is the same
class of defect as §0.2 of the plan, relocated from the settings product to the
stratum key.

`test_phase2_turnover.py` cannot catch it: it uses a 2×2 grid with
`max_candidates=100`, giving a 25-surface budget against 2 configs — no selection
pressure at all.

**Fix:** stratify on the *base* structure — `(layer, base_ts_sig)` where
`base_ts_sig` excludes hump and reducer — and treat hump as a within-stratum
dimension, with `hump=None` pinned first in each stratum's queue so the control arm
is always drawn before any variant.

### F3 · S1 — The reference truncation is unreachable by default

`DEFAULT_TRUNCATIONS = (0.01, 0.08)` with `0.01` first, and
`settings_per_structure=1` selects `settings_combinations[0]`:

```
settings_per_structure=1 selects: ('SUBINDUSTRY', 0.01, 'TOP3000')
```

Every default run now emits **truncation 0.01 only**. `0.08` — the setting of the
single alpha that has ever cleared every BRAIN check — is no longer produced by any
default invocation, and new results are not comparable to the 295 historical
simulations.

B1's own contract says the first settings entry is the reference configuration so a
default run reproduces the baseline. That ordering was inverted.

The walkthrough also states three levels `(0.01, 0.05, 0.08)`; the code has two.

**Fix:** `DEFAULT_TRUNCATIONS = (0.08, 0.01)` (reference first), and add a
characterization test asserting that a default `expand()` emits the reference
settings tuple.

### F4 · S1 — The reconciliation guard is bypassed on the on-demand path

`save_pnl` enforces reconciliation only when `reported_sharpe` is passed. Three
production call sites omit it:

- `correlation.py:196` — inside `ensure_alpha_pnl`, the **live on-demand fetch**
- `filter_backtest.py:200` and `:249`

So A1 is armed on the batch path (`backfill_pnl.py:84`) and disarmed on the path
that runs during evaluation. Worse, `PnLSaveResult.reconciled` is set to `True` in
that case — reporting a verification that never happened. Any consumer trusting
that field is misled.

**Fix:** make `reported_sharpe` required (or make `reconciled` tri-state
`True/False/None` and treat `None` as unverified everywhere); look up the reported
Sharpe from `AlphaMetric` inside `ensure_alpha_pnl` before saving.

### F5 · S1 — CSCV reports confident nonsense on degenerate input

The guard is `n_strategies < 2 or t_days < n_subperiods`, which permits
`block_len = t_days // n_subperiods == 1`. Reproduced with `N=2, T=20, S=16`:

```
CSCVResult(pbo=0.551, pbo_loss=0.291, n_splits=12870,
           n_strategies=2, n_subperiods=16,
           median_oos_sharpe=3.573, degradation_pct=0.618)
```

A median OOS Sharpe of 3.57 computed from eight 1-day blocks, presented with no
qualification. The degenerate branch also returns `pbo=0.0` — which reads as *the
best possible score*, i.e. "no overfitting" — for a family too small to evaluate.

The plan specified a `degraded: bool` field and that the report render "insufficient"
rather than a number. Neither exists.

**Fix:** add `degraded: bool`; require `t_days >= 2 * n_subperiods * MIN_BLOCK_LEN`
(suggest `MIN_BLOCK_LEN = 20`, i.e. ~640 days at S=16) and `n_strategies >= 4`; on
degradation return `pbo=float("nan")` with `degraded=True`, never `0.0`.

### F6 · S2 — E2 is not the specified check, and duplicates the plateau test

`check_perturbation_robustness` filters `matching_points = [p for p in
surface_points if p.structure == peak.structure]` and then walks 4-connected
`(window, decay)` neighbours. Since `structure` includes `neutralization`,
neutralization can never vary — so this is `plateau._neighbours` with a different
name and a rescaled ratio.

E2's purpose was a **cross-surface** check at matched coordinates along the
neutralization ladder `NONE → MARKET → SECTOR → INDUSTRY → SUBINDUSTRY`, testing a
perturbation the existing surface test structurally cannot reach.

**Fix:** either implement the cross-surface version, or delete the module and say
plainly that E2 was descoped. Keeping a redundant second implementation of the
plateau test invites the two to drift apart.

### F7 · S2 — `BudgetPolicy.max_surfaces` is dead

`policy` is constructed or accepted at `constructor.py:511`, but
`select_surface_configs` is called with `budget_surfaces` (derived from
`max_candidates // surface_size`). `policy.max_surfaces` is never read. A caller
passing `BudgetPolicy(max_surfaces=3)` silently gets the budget-derived value.

### F8 · S2 — The novelty corpus is every alpha, not the ones that worked

`NoveltyScorer.from_session` does `select(Alpha.feature_json)` over **all** rows.
The plan specified `statuses=(PASSED, SUBMITTED)`. With 625 alphas dominated by
generated-and-never-simulated grid members, the IDF measures *what our own
constructor emits*, not *what has ever worked* — and it conflates "already tried
and failed" with "already succeeded", which point in opposite directions.

### F9 · S2 — The `n_eff` fallback contradicts itself

```python
# plateau.py:365
n_trials = fam_n_eff if fam_n_eff is not None else (trial_ledger.n_eff if ledger is not None else max(1, simulated_count))
# plateau.py:445
n_eff=fam_n_eff if fam_n_eff is not None else trial_ledger.n_eff
```

`trial_ledger` is always built. Line 365 uses it only when the caller supplied
`ledger` explicitly; line 445 uses it unconditionally. So on the normal path the
haircut bar and the DSR are deflated by **different trial counts**.

### F10 · S2 — Tier-1 size proxy regresses on raw `cap`

`STANDARD_RISK_PROXIES["size"] = "cap"`. Market cap is extremely right-skewed; a
cross-sectional regression on raw cap is driven by a handful of mega-caps, so the
residual is not size-neutral in any useful sense. The standard proxy is
`log(cap)`, and the operator KB's own example for `regression_neut` is
`regression_neut(signal, log(cap))`.

Also `max_complexity=15.0` for Tier 2 is an invented constant; the plan called for
calibrating it against the first length-related rejection.

### F11 · S2 — A submitted slice can zero out an entire family

```python
for settings_idx in range(num_settings):
    neutralization, truncation, universe = settings_combinations[settings_idx]
    if (family_key, neutralization, truncation) in submitted_slices:
        continue
```

With `num_settings == 1`, if that single combination is already submitted the loop
body never runs and `expand()` returns **zero candidates** for the whole family.
Previously the skip was per-config, so other structures still emitted. There is a
`family_expanded_zero_candidates` warning, which is good, but the behaviour is a
silent dead end for any family whose reference settings were already used.

### F12 · S3 — The suite does not reproduce; the failure is a wall-clock assert

```
261 passed, 1 failed
FAILED tests/test_phase5_statistics.py::test_e1_cscv_pbo_performance_and_accuracy
E   AssertionError: CSCV took 1.1735s >= 0.500s
```

Wall-clock assertions in unit tests are environment-dependent by construction and
will fail on any slower or contended runner. The walkthrough's "< 200 ms" is a
property of one machine, not of the code.

**Fix:** drop the timing assertion from the correctness test; if the performance
budget matters, put it in a separate benchmark that is not part of the gate.

### F13 · S3 — E1's discrimination claim is untested

`test_e1` builds the "signal" matrix as `rng.normal(0.20, 1.0, size=(49, 1250))` —
**all 49 strategies share the same true mean**. There is no genuinely better
strategy to find, so the IS winner is still selected by noise and PBO ≈ 0.5 is the
*correct* answer. Measured: signal 0.555 vs noise 0.577 — statistically
indistinguishable.

The assertions that do pass (`pbo_loss <= 0.05`, `median_oos_sharpe > 1.5`) follow
trivially from every column having positive drift, and would pass for a PBO
implementation that returned a constant.

The kernel is fine — a heterogeneous matrix (one column at +0.25, rest noise)
returns **PBO = 0.000** against **0.492** for pure noise. The test just needs to be
that matrix. `assert res_noise.pbo >= 0.20` should also pin ≈ 0.5, not a floor that
a broken implementation would clear.

### F14 · S3 — Smaller items

- `cscv.py` docstring step 5 says "PBO = fraction of splits where Sharpe_OOS ≤ 0",
  which describes `pbo_loss`, not the rank-based `pbo` the field actually holds.
- `cscv.py`: `Sequence` imported and unused.
- `pnl_storage.py`: `diff_dates = dates[1:]` computed and never used in the
  cumulative branch.
- `family_matrix.py` docstring promises "reconcilable series"; it filters on length
  and overlap only.
- `family_matrix.py` imports `_structure_of` (private) inside a per-row loop.
- `is_cumulative_series` requires `frac_pos > 0.95 or frac_pos < 0.05`. A real
  cumulative curve that dips below zero early — common in year one — has
  `frac_pos ≈ 0.7` and will not be detected. The reconciliation guard catches the
  consequence, so this fails safe, but the detection heuristic is not doing the job
  its name claims. A one-off documented convention beats per-series inference.

---

## Next plan

Ordered by what unblocks the most. N1–N3 are the difference between "phases
implemented" and "phases delivered".

| # | Item | Fixes | Effort | Done when |
|---|---|---|---|---|
| **N1** | Wire the five orphaned modules into the pipeline | F1 | ~4 d | Each has ≥1 production importer, and its plan-level acceptance criterion passes end to end |
| **N2** | Re-key the selector; pin the control arm | F2 | ~half d | Widening humps to 3 levels keeps 7 distinct ts transforms and ≥1 un-humped surface, asserted at a realistic budget |
| **N3** | Reference-first settings order | F3 | ~1 h | Default `expand()` emits truncation 0.08; characterization test pins it |
| **N4** | Close the reconciliation bypass | F4 | ~half d | No production `save_pnl` call omits `reported_sharpe`; `reconciled` is tri-state |
| **N5** | CSCV degraded guard | F5 | ~half d | `N=2, T=20` returns `degraded=True` and no numeric PBO; report renders "insufficient" |
| **N6** | Resolve E2 — implement cross-surface or delete | F6 | ~1 d | Either a neutralization-ladder check exists, or the module is gone and descoping is recorded |
| **N7** | Test hardening | F12, F13, F2, F3 | ~1 d | Heterogeneous PBO test; timing assertion moved to a benchmark; budget-pressure test for the selector |
| **N8** | Correctness cleanups | F7–F11, F14 | ~1 d | Each fixed with a regression test |

### N1 in detail — the wiring, per module

**C1 novelty.** Call `rank_candidates_by_novelty` at the end of `expand()` (or in
`run_family` before the simulate cap), scope the corpus to `PASSED`/`SUBMITTED`
(F8), and surface `novelty_score` in the review console beside `complexity_score`.
Keep it a ranking key, never a filter. Add `scripts/backfill_subtree_hashes.py` so
existing rows join the corpus.

**C2 orthogonalisation.** Trigger from the correlation gate: when
`check_portfolio_empirical_correlation` names a collision, emit Tier-1 residuals for
that alpha as child candidates with `parent_id` set, so `test_genealogy` lineage
still resolves. Switch the size proxy to `log(cap)` (F10).

**E1 PBO.** `plateau.evaluate()` already builds the family matrix for `N_eff` —
feed the same matrix to `compute_pbo_cscv` and carry the result on the family
header. Add a PBO column to `scripts/report.py` and the UI family view.
**Report only** until ten families have a value; choose the threshold from the
observed distribution and record the reasoning.

**E2.** See N6 — do not wire the current implementation; it would add a second,
differently-scaled copy of a test `evaluate()` already runs.

**F1 feedback loop.** Add `scripts/report_decay.py` (or a `--decay` section on
`report.py`) and call it from `sync_submission_outcomes.py`. Critically, join the
**promotion-time statistics** — DSR, plateau ratio, PBO, `N_eff` — onto each row.
The current module compares IS Sharpe to production Sharpe and stops there, which
measures decay but cannot answer the question the item exists for: *does our filter
predict decay?* Without that join, F1 produces a number rather than a verdict.

### What not to do next

- **Do not add axes.** Every axis added now inherits F2 and will consume budget
  while quietly dropping structural coverage. N2 first.
- **Do not gate promotion on PBO or perturbation yet.** Both need their
  distributions observed on real families before a threshold means anything, and
  F5 means one of them can currently return a maximally-good score on garbage.
- **Do not tune the hard-coded constants** (`max_fragility=0.35`,
  `min_median_sharpe=1.25`, `max_decay_pct=0.50`, `max_complexity=15.0`). They are
  placeholders standing in for measurements that have not been taken. Measure, then
  set.
