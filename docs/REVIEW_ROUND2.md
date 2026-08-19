# Code Review — Quant Remediation (F1–F10), Round 2

Reviewed: `01f08bc` on `claude/project-feature-review-qxc27g`, diffed against its base `683fe54`.
Every finding below was reproduced by running the committed code.

---

## Headline

**The committed code does not run.** `plateau.evaluate()` raises `TypeError` on every
invocation, so the promotion path — the thing all ten findings are about — is dead.

```
backend/app/services/plateau.py:266   def _select_representatives(db, verdicts, surface) -> None:
backend/app/services/plateau.py:502   _select_representatives(db, verdicts, surface, store, cfg=cfg)

E  TypeError: _select_representatives() got an unexpected keyword argument 'cfg'
```

Actual suite result on `01f08bc`:

```
18 failed, 220 passed, 1 warning in 16.63s
```

not `238 passed`. The walkthrough's numbers came from a working tree that differs
from what was committed. Among the failures are the tests backing four of the six
acceptance criteria:

| Claimed | Actual on `01f08bc` |
|---|---|
| A1 `test_sibling_ridge_promotes_one` — PASSED | **FAILED** (TypeError) |
| A2 `test_constructor_diversity` — PASSED | **FAILED** — `expected >= 1 depth-2 candidates, found 0` |
| A4 `test_synthetic_null_false_discovery_a4` — PASSED (0.0%) | **FAILED** (TypeError) |
| A5 `test_reconciliation_enforced` — PASSED | **FAILED** (TypeError) |

Also failing: all five `test_plateau.py` tests, three `test_ui.py`, three
`test_loop_integration.py`, `test_e2e_pipeline.py`.

**First action: run the suite on a clean checkout of the pushed SHA before
reporting a result.** A green run in a dirty working tree is not evidence about
the branch. This is worth a pre-push hook.

---

## What is genuinely well done

Stated first, because the substance below is mostly about the remaining 30%.

### Phase 0 is exemplary — and it closed the biggest risk in the whole review

`docs/BRAIN_API.md §7` and `scripts/verify_pnl_reconciliation.py` settle the
cumulative-PnL question empirically rather than defensively:

- The series **is** discrete daily PnL, not a cumulative curve. Sample values from
  alpha #257 shown, signs alternating.
- **355/355** stored vectors reconcile with BRAIN's reported Sharpe within ±0.05.
- The residual slope is *explained*, not waved at: regression gives
  `recomputed = 1.003473 × reported − 0.000582` (R² = 0.9999), and
  `√(252/250) = 1.003992` accounts for essentially all of it.

That is how an invariant should be established. The single largest risk flagged in
QUANT_REVIEW §F5 — that every historical number might be void — is now closed with
evidence. Nothing else in this review outranks it.

### Other work that landed correctly

- **F3 stability z-tests** — `sharpe_standard_error()` implements Lo (2002)
  properly, and the decay test compares the recent 252d against a **non-overlapping
  prior window** rather than against the full period that contains it. That was the
  subtle half of the finding and it was handled.
- **F1 portfolio redefinition** — `submitted_portfolio()` is correct, including
  `is_recalled`. This is what actually breaks the deadlock, and it works.
- **Signed correlation** — the portfolio gate now uses signed rho, so negatively
  correlated diversifiers survive. `abs()` correctly retained for clustering.
- **Structural proxy scoping** — restricting the skeleton heuristic to portfolio
  members that could not be measured empirically is *better* than what the plan
  specified. "The proxy is a stand-in for missing evidence, not a veto over
  evidence we have" is the right principle, well expressed.
- **F2 constructor** — measured on a live expansion: **5 distinct ts-transforms**
  where the old code produced exactly 1. Real, verifiable improvement.
- **`clustering.py`** — DSU single-linkage, `|rho|`, ridge-score election. Well built.
- **F7** — `select_orthogonal_batch` is wired into `report.py:148` with an explicit
  deferred list.
- **`filter_backtest.py`** — models sibling correlation through a common-factor
  blend (`w_common = √rho`), which is the correct construction.

---

## C1 — The EVT haircut is *weaker* than the log₁₀ bar it replaced

**Severity: critical. F4 regressed rather than landing.**

`plateau.py:361`:

```python
n_trials = fam_n_eff if fam_n_eff is not None else (trial_ledger.n_eff if ledger is not None else max(1, simulated_count))
bar = haircut_bar(n_trials, cfg=cfg)
```

`fam_n_eff` is the effective trial count **within one family** — 49 points that are
0.85–0.97 correlated by construction. The eigenvalue estimator collapses that to
approximately 1. Measured:

| Family sibling ρ | family `N_eff` | resulting bar |
|---|---|---|
| 0.85 | 1.37 | **1.43** |
| 0.92 | 1.18 | **1.38** |
| 0.97 | 1.06 | **1.33** |

Against the bar it replaced (`1.25 + 0.10·log₁₀ N`): **1.42** at N=49, **1.51** at
N=400. And against the programme ledger the plan specified:

| programme `N_eff` | bar |
|---|---|
| 400 | 2.53 |
| 2 000 | 2.75 |
| 4 800 | 2.85 |

So the new machinery computes the right number — `build_ledger()` runs on *every*
`evaluate()` call, at real cost — and then discards it. `trial_ledger.n_eff` is
consulted only when `fam_n_eff` is `None` **and** a ledger was passed explicitly;
with ≥2 stored PnL vectors in the family, `fam_n_eff` always wins.

This is the original F4 defect — deflating against one family's correlated trials
instead of the programme's trial universe — preserved intact, now with an EVT
formula on top and a lower number coming out.

**Fix.** The bar takes the programme ledger, full stop:

```python
bar = haircut_bar(trial_ledger, cfg=cfg)   # TrialLedger overload already exists
```

`fam_n_eff` has one legitimate use — as the `n_eff` argument to `compute_dsr`,
where it corrects the *within-family* σ_SR. Keep it there; keep it out of the bar.

---

## C2 — `cluster_family` is imported and never called

**Severity: critical for F1/F6.**

`plateau.py:331` imports `cluster_family`. Nothing calls it — the only other
reference in the codebase is the test file. The dedup that actually runs is
`_select_representatives()`, which differs from the plan in two consequential ways:

1. **It groups by `structural_hash`, not by measured PnL correlation.** The
   skeleton buckets windows coarsely (`<WIN:short>` / `<WIN:long>`), so it
   over-groups genuinely different mechanisms that share a skeleton and
   under-groups one ridge that straddles a bucket boundary. Two adjacent
   ridge points at window 63 and 126 land in the same bucket; window 5 and 63 do
   not, and both promote — two near-identical submissions.

2. **It elects the peak, not the ridge centre** (`plateau.py:295`):

   ```python
   members.sort(key=lambda v: (-(v.sharpe or 0.0), -(v.plateau_ratio or 0.0), v.alpha_id))
   ```

   `ridge_score` is computed, stored on the `Verdict`, and used for the final sort
   order — but the *representative election* sorts by raw Sharpe. F6's whole point
   was that the peak carries the largest positive error, and the election is
   precisely where that matters: it decides which single alpha reaches the operator.

**Fix.** Call `cluster_family(...)` and elect by `ridge_score`; delete
`_select_representatives` or reduce it to the fallback for candidates with no
stored PnL. `clustering.py` already implements the right thing.

---

## C3 — A4 is not established by its own test

**Severity: critical to the claim, not to the code.**

`test_synthetic_null_false_discovery_a4` runs `n_null_replications=20`. Zero
promotions out of 20 gives a 95% upper confidence bound on the false-discovery
rate of:

| observed | 95% upper bound | establishes "< 5%"? |
|---|---|---|
| 0/20 | **13.9%** | **no** |
| 0/60 | 4.9% | yes, marginally |
| 0/100 | 3.0% | yes |
| 0/500 | 0.6% | yes, with room |

The reported "0.0%" is a point estimate from a sample too small to distinguish 0%
from 13%. The plan specified 500 replications for exactly this reason. The
companion `test_synthetic_signal_promotion_scorecard` runs 5 nulls and 10 signals —
smaller still.

`test_synthetic_signal_stability_survival_a3` (100 replications, 100% survival) is
sound arithmetic but tests **only** `evaluate_subperiod_stability` in isolation. A3
as written is about the stability gates, so that is defensible — but it is not
evidence about the full stack, and the walkthrough presents it as if it were.

**Fix.** Raise the null count to 500, mark it `-m slow`, and run it in CI nightly
rather than on every `pytest`. Report the interval, not the point estimate.

---

## H1 — The fail-closed overlap rule is documented but not implemented

`correlation.py:71` docstring: *"Insufficient trading day overlap fails closed
(returns unmeasured/blocking)."*

`correlation.py:118`:

```python
if len(common_dates) < overlap:
    continue
```

Still fail-open. The partial mitigation — unmeasured portfolio members now fall
through to the structural proxy — only catches candidates that share a skeleton
*and* a base field. A candidate with a different skeleton and insufficient overlap
passes the gate with no measurement of any kind.

A docstring that asserts a safety property the code does not implement is worse
than the missing property alone: it stops the next reader from checking.

---

## H2 — The evaluation cache is global and never cleared

`plateau.py:527` is documented as "request-scoped" but is a module-level dict keyed
on `(family_key, config_fingerprint)`, with no TTL and no invalidation.
`clear_eval_cache()` has **zero callers** outside its own definition.

Under `uvicorn`, the first render of a family populates the cache for the process
lifetime. New simulation results, newly backfilled PnL, and newly recorded
submissions will not change the verdicts the morning report shows. The operator
reviews a stale shortlist and cannot tell.

**Fix.** Either scope it to a request (FastAPI dependency, or an explicit context
manager around `report.build()`), or key it on a cheap freshness token —
`max(AlphaMetric.id)` for the family plus the portfolio size — so it self-invalidates.

---

## H3 — A third of the expansion budget evaporates silently

Measured on a live expansion at `max_candidates=400`: **245 emitted, 147 rejected**,
where the old code emitted 392.

```
incomplete_surface_discarded  emitted=0 expected=49 grid={'ts':'ts_decay_linear','cs':'normalize','group':'sector',...} rejected=49
incomplete_surface_discarded  emitted=0 expected=49 grid={'ts':'ts_std_dev', ...} rejected=49
incomplete_surface_discarded  emitted=0 expected=49 grid={'ts':'ts_delta(ts_rank)','depth':2, ...} rejected=49
```

`select_surface_configs` allocates 8 surface slots; 3 of them select strata whose
every point fails validation, and the slot is consumed rather than refilled. That
is also **why A2 finds zero depth-2 candidates** — the sampler did select a depth-2
stratum, and the surface was discarded whole.

**Fix.** Validate one representative point per stratum before committing a slot,
and refill from the remaining strata when a surface is discarded. Log
`strata_starved` so the loss is visible instead of inferred from a candidate count.

---

## H4 — `ui.py` still calls bare `evaluate()`, now much more expensively

Four sites (`ui.py:145`, `:188`, `:225`, `:730`) call `evaluate(db, family)`
directly. Each such call now runs `build_ledger()` — a full simulated-alpha count,
a group-by across every family, and a 100-alpha aligned PnL matrix with an
eigendecomposition. `ui.py:730` does this in a loop over every family.

D3 was applied to `report.py` and not to the router that renders the console the
operator actually uses. The pre-existing N+1 is now an N+1 over a much heavier
operation.

---

## H5 — The ledger's σ_SR and N_eff are each estimated from the wrong quantity

`trials.build_ledger()`:

- **σ_SR comes from per-family *mean* Sharpes** (`func.avg(AlphaMetric.sharpe)`
  grouped by `family_key`). Selection does not operate on family means — it
  operates on the best point in each family. Averaging within family shrinks the
  dispersion by roughly `1/√n`, which *understates* σ_SR, which lowers `SR*`, which
  makes **DSR more lenient**. Use the dispersion of trial Sharpes, or of family
  maxima, since maxima are what you select on.
- **N_eff is extrapolated by ratio** from ≤100 family representatives to
  `total_simulated`. But the population being extrapolated to is dominated by
  *within-family* siblings at ρ ≈ 0.95, whose marginal contribution to N_eff is
  near zero. With 4 800 alphas in 100 families the true programme N_eff is closer
  to the number of families than to `total × ratio`. This *overstates* N_eff.

The two errors push in opposite directions and land in different gates, so the net
effect on promotions is not predictable from either alone. Estimate both from the
same object: the cross-family correlation matrix of family-maximum alphas.

Minor, same function: `select(func.max(Alpha.id)).group_by(...).limit(100)` has no
`ORDER BY`, so the sample — and therefore the bar — is not reproducible across
query plans.

---

## Medium

- **M1 — the monoculture moved one axis over.** The stratum key is
  `(layer, ts_sig)`, so cross-section, group, and neutralization are not stratified.
  Measured: all 245 emitted candidates have `cs='zscore'`. Structural diversity in
  the ts axis went 1 → 5, which is the win; the cross-sectional axis went from
  uniformly `rank` to uniformly `zscore`. Add `cs` to the stratum key.
- **M2 — the plateau is still computed on the fine decay ladder.** `_neighbours`
  derives its ladder from the surface, which is now `(0,1,2,4,6,8,16)`. `decay=1`
  and `decay=2` are near-identical alphas, so the ratio test passes trivially
  between them. Plan §2.3 (snap to a coarse `PLATEAU_DECAY_LADDER`) was not
  implemented, and the plateau test remains diluted.
- **M3 — `expected_max_normal` small-n branch is ad hoc.** `0.5·√(2 ln n)` for
  n < 5 returns 0.83 at n=4 where the true value is 1.03. Harmless once the bar
  uses the programme ledger (C1); load-bearing while it uses family N_eff ≈ 1.2.
- **M4 — `haircut_bar` omits the `(1 + SR²/2)` term** from its SE. Understates the
  SE at high Sharpe, so the bar is slightly too low exactly where candidates cluster.

---

## Next plan

Ordered by what unblocks what. Steps 1–3 are one afternoon and restore the branch
to the state the walkthrough describes; 4–6 are what make the remediation real.

### 1. Make it run, and make the claim checkable *(blocking)*
- Fix the `_select_representatives` signature or the call site.
- Re-run the full suite **on a clean checkout of the pushed SHA**; publish the
  actual counts.
- Add a pre-push hook or CI job that runs `pytest` on the committed tree. The
  discrepancy between "238 passed" and `18 failed, 220 passed` is the finding
  that matters most here — everything else in this review is downstream of not
  having had that signal.

### 2. Point the bar at the programme ledger *(C1)*
- `bar = haircut_bar(trial_ledger, cfg=cfg)`.
- Keep `fam_n_eff` for `compute_dsr`'s `n_eff` only.
- Expect promotions to become rare. That is the correction working. Establish
  *how* rare with step 4 before adjusting `target_sharpe` in response.

### 3. Wire the clustering that already exists *(C2)*
- Call `cluster_family()`; elect by `ridge_score`, not `sharpe`.
- Retire `_select_representatives`, or demote it to the no-PnL fallback.
- Extend `test_sibling_ridge_promotes_one` to assert *which* point is elected —
  a ridge centre at Sharpe 1.6 with neighbours at 1.5 should beat a peak at 2.1
  with neighbours at 0.4. Today's test only counts promotions.

### 4. Give the harness enough replications to support its claims *(C3)*
- 500 nulls, `-m slow`, nightly CI.
- Report the Wilson interval alongside the point estimate.
- Add the power curve the plan asked for — SR ∈ {0.0, 0.5, 1.0, 1.5, 2.0} — and
  `stage_attrition`. With the bar at 2.5+, knowing *which* gate is binding stops
  being a nicety and becomes the only way to tune anything.
- Record a **baseline scorecard on the pre-remediation thresholds** so the
  improvement is measured rather than asserted.

### 5. Close the safety gaps *(H1, H2)*
- Make the overlap rule actually fail closed, or correct the docstring. Do not
  leave those two disagreeing.
- Give the eval cache an invalidation token, or scope it to a request.

### 6. Finish the search work *(H3, H4, M1, M2)*
- Refill starved strata instead of consuming the slot; log `strata_starved`.
- Convert `ui.py`'s four `evaluate()` calls to `evaluate_families`.
- Add `cs` to the stratum key.
- Snap the plateau neighbourhood to a coarse decay ladder.

### 7. Then re-estimate the ledger properly *(H5)*
Do this last: it changes the operating point, and it is only interpretable once
step 4 can measure the change. Estimate σ_SR and N_eff from one object — the
cross-family correlation matrix over family-maximum alphas — rather than from two
differently-biased proxies.

---

## Assessment

The parts of this remediation that were *empirical* are the parts that came out
best: Phase 0 is genuinely excellent work, and F3's z-tests handle the subtle case
correctly. The parts that came out worst are the parts where a correct
implementation was written and then not connected — `cluster_family` imported and
unused, `build_ledger` computed and discarded, a fail-closed rule documented and
not written. Those are integration failures, not comprehension failures, and the
uncaught `TypeError` is the same class of problem: the code was reasoned about more
carefully than it was run.

The fastest durable fix is not in any single module. It is making "did it run on
what I pushed?" impossible to skip.
