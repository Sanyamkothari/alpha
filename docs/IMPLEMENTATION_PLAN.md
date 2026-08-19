# Implementation Plan — Quant Review Remediation (F1–F10)

Companion to [QUANT_REVIEW.md](./QUANT_REVIEW.md). That document establishes *what*
is wrong and proves it. This document specifies *how* it gets fixed, in what
order, and what evidence closes each item.

Baseline: `983c134`, 194 tests passing, `backend/.venv` provisioned.

---

## 1. Objective and success criteria

The programme metric is unchanged from STRATEGY.md §9: **accepted, uncorrelated
alphas per week**. Every change below is justified by its effect on that number,
not by internal tidiness.

The work is complete when all six of the following hold:

| # | Acceptance criterion | Measured by |
|---|---|---|
| A1 | A family containing a genuine multi-point plateau promotes **exactly one** representative, not zero | `test_quant_review_regressions.py::test_sibling_ridge_promotes_one` |
| A2 | Default-budget expansion emits **≥ 5 distinct ts-transforms** and **≥ 1 depth-2 template** | `test_constructor_diversity` |
| A3 | A stationary synthetic alpha with true SR 1.5 survives the stability gates **≥ 85%** of the time | filter backtest harness, 2 000 replications |
| A4 | A pure-noise family of 400 trials promotes an alpha **< 5%** of the time | filter backtest harness, 500 replications |
| A5 | No promotion can occur from a PnL vector that fails Sharpe reconciliation | `test_unreconciled_pnl_blocks_promotion` |
| A6 | Every threshold in the gate stack is recorded, versioned, and reproduced in the report header | `FilterConfig.fingerprint()` asserted in report snapshot test |

A3 and A4 are the pair that matters. Today the system fails both simultaneously —
it rejects real alphas at 37% while its multiple-testing bar sits below the noise
floor. Any change that improves one at the other's expense has not helped.

---

## 2. Sequencing and dependency graph

The findings are not independent. Fixing them in the wrong order produces
misleading evidence — for example, re-tuning the stability thresholds (F3) before
the PnL units are verified (F5) would calibrate against a possibly-corrupt input.

```
  Phase 0  ── F5a  PnL ground truth ──────────────► BLOCKS EVERYTHING
                     │
                     ▼
  Phase 1  ── F1  portfolio definition + ridge clustering
              F5b hard reconciliation precondition
              F6  ridge-centre election & shrunk ranking
                     │              (yield goes 0 → non-zero here)
                     ▼
  Phase 2  ── F2  stratified constructor sampling
              F10a ladder synchronisation
                     │              (search breadth restored)
                     ▼
  Phase 3  ── H   filter backtest harness  ◄── prerequisite for F3/F4
              F3  significance-based stability gates
              F4  programme-wide trial ledger + EVT haircut
                     │              (thresholds become measured, not chosen)
                     ▼
  Phase 4  ── F7  intra-batch orthogonal selection
              F8  bandit discounting, novelty term, seeded RNG
              F9  authoritative BRAIN correlation endpoints
                     │
                     ▼
  Phase 5  ── F10b hygiene: dead code, annotations, evolution jitter, N+1 calls
```

Phases 1 and 2 are independently shippable and each is worth shipping alone.
Phase 3 must land as a unit — the harness and the two recalibrations are one
change, because the harness is the only evidence that the new thresholds are
better than the old ones.

---

## 3. Cross-cutting decisions

These apply to every phase and should be settled before code is written.

### D1 — Thresholds move into one declared, versioned object

Today the numbers `0.6`, `0.55`, `1.25`, `0.10`, `1.50`, `0.95`, `0.97`, `0.40`,
`0.70`, `0.50`, `500`, `30` are literals scattered across four modules. Two
problems follow: nobody can see the operating point, and nothing prevents a
threshold from being nudged after seeing a result one likes — which is p-hacking
with extra steps.

**[NEW] `backend/app/services/filter_config.py`**

```python
@dataclass(frozen=True)
class FilterConfig:
    # plateau
    plateau_ratio: float = 0.60
    min_neighbours_to_judge: int = 2
    # promotion bar
    target_sharpe: float = 1.25
    backtest_days: int = 1236
    # stability (Phase 3 replaces the ratio floors with z-thresholds)
    split_half_z_floor: float = -2.0
    recent_decay_z_floor: float = -2.0
    rolling_pos_floor: float = 0.70
    # multiple testing
    dsr_threshold: float = 0.95
    # correlation
    portfolio_corr_threshold: float = 0.55
    sibling_cluster_threshold: float = 0.90
    min_common_days: int = 500
    # reconciliation
    sharpe_reconciliation_tolerance: float = 0.05

    def fingerprint(self) -> str:
        """Stable short hash of the operating point, stamped onto every Verdict
        and printed in the report header."""
```

Every gate function takes `cfg: FilterConfig` as a keyword argument with a module
default. `Verdict` gains `config_fingerprint: str`. The daily report prints it.
This makes "the bar was not re-tuned to fit a result we like" — already claimed in
`plateau.py`'s docstring — a checkable property rather than an assertion.

### D2 — No silent fallbacks in a safety gate

Three places currently fail open or fabricate: insufficient date overlap
(`correlation.py:129`), missing PnL under `require_pnl=False` (`plateau.py:353`),
and the hardcoded 0.85/0.20 proxy (`correlation.py:238`). The rule going forward:

> A gate that cannot be evaluated returns `UNMEASURED`, and `UNMEASURED` blocks
> promotion. It never resolves to "pass", and it never resolves to an invented
> number.

Introduce a tri-state where needed rather than overloading `float`/`bool` — the
codebase already does this well for `passed_all_checks`, so follow that precedent.

### D3 — One evaluation pass per family per request

`report.build()` calls `evaluate(db, family)` **three times per family**
(`report.py:123`, `:137`, `:189`), and `ui.py` calls it four more times across its
endpoints. Each call recomputes DSR, sub-period statistics, and the full pairwise
correlation scan over every stored PnL vector. Beyond the cost, three independent
evaluations of the same family can disagree if anything mutates in between.

Add `evaluate_families(db, families, *, cfg) -> dict[str, list[Verdict]]` and a
request-scoped cache; convert all seven call sites. This is a prerequisite for
Phase 4's batch orthogonality, which needs the whole promoted set in one place
anyway.

### D4 — Determinism

Any module that samples takes an injected `random.Random` / `np.random.Generator`
seeded from config, never the global RNG. Applies to `allocator_bandit.py`,
`evolution.py`, the new constructor sampler, and the filter backtest harness. A
research result that cannot be reproduced run-to-run cannot be trusted or
bisected.

---

## Phase 0 — PnL ground truth (F5a) · BLOCKING

**Nothing else in this plan may be merged before this closes.** Every statistical
gate consumes `PnLStore.load_pnl()` and assumes daily increments. If BRAIN's
`/recordsets/daily-pnl` returns a cumulative curve — which is how the platform
renders it, and which community clients difference before use — then a cumulative
series passes every gate silently (see QUANT_REVIEW §F5), and any recalibration
done in Phase 3 would be fitted to a corrupt input.

### 0.1 Audit the existing store

**[NEW] `backend/scripts/audit_pnl.py`**

For all 369 stored vectors, report:

| Column | Meaning | Expected if daily | Expected if cumulative |
|---|---|---|---|
| `recomputed_sharpe` | `mean/std·√252` | ≈ BRAIN's reported Sharpe | 10–40 |
| `frac_positive` | `(arr > 0).mean()` | ≈ 0.5 | ≈ 1.0 (or ≈ 0.0) |
| `lag1_autocorr` | `corr(arr[1:], arr[:-1])` | ≈ 0 | ≈ 1 |
| `reconciles` | `verify_pnl_reconciliation` verdict | `True` | `False` |

Exit non-zero if fewer than 95% of vectors reconcile. Print the distribution, not
just a pass/fail — a mixed population (some daily, some cumulative) is the worst
case and must be visible.

### 0.2 Normalise at the boundary

**[MODIFY] `backend/app/services/pnl_storage.py`**

`save_pnl()` gains detection and normalisation, so the invariant is enforced at
the single point of entry rather than assumed by nine downstream consumers:

```python
def save_pnl(self, alpha_id, dates, pnl_values, *, reported_sharpe: float | None = None,
             series_kind: Literal["daily", "cumulative", "auto"] = "auto") -> PnLSaveResult
```

- `auto` detects cumulative form (lag-1 autocorrelation > 0.99 **and**
  `frac_positive > 0.95`) and differences it, recording `series_kind` in the
  sidecar JSON alongside `dates`.
- When `reported_sharpe` is supplied, reconcile after normalisation and **refuse
  to store** a vector that does not reconcile within `cfg.sharpe_reconciliation_tolerance`.
  A rejected vector is logged with both Sharpes; it is not silently dropped.
- The sidecar gains `{"series_kind": ..., "source": ..., "reconciled": bool}`.
  Existing sidecars without the key are treated as `"unknown"` and are ineligible
  for promotion until re-audited.

**[MODIFY] `backend/scripts/backfill_pnl.py`** — pass `reported_sharpe=rep_sr`
into `save_pnl`, and promote the reconciliation result from a stats counter
(`backfill_pnl.py:78`) to a hard skip with a logged reason.

**Note on annualisation.** `docs/BRAIN_API.md:245` documents BRAIN annualising
with **√250**; the code uses **√252** throughout. That is a 0.4% discrepancy — 
immaterial for gating, material for a 5% reconciliation tolerance. Introduce
`TRADING_DAYS_PER_YEAR = 252` in `filter_config.py`, use it everywhere, and widen
the reconciliation tolerance to accommodate the convention gap rather than
chasing it.

### 0.3 Definition of done

- `scripts/audit_pnl.py` exits zero on the real store.
- `test_pnl_store_rejects_cumulative_series` — a synthetic cumulative vector is
  either differenced (auto) or refused (strict), never stored as-is.
- `test_pnl_store_rejects_unreconciled` — a vector whose Sharpe disagrees with the
  reported figure is refused.

---

## Phase 1 — Unblock yield (F1, F5b, F6)

This is the phase that moves promotions from zero. The three findings are
addressed together because they all live in the same promotion path and share a
test fixture.

### 1.1 The portfolio is what you submitted (F1, part 1)

**Root cause.** `AlphaStatus.PASSED` means "cleared BRAIN's `checks[]`"
(`result_import.py:228`, `simulation_runner.py:149`), yet it is treated as
portfolio membership in `plateau.py:114`, `plateau.py:263`, `correlation.py:64`,
and `ui.py:629`. A candidate is therefore correlation-checked against its own
grid siblings, which are ~0.95 correlated by construction.

**[NEW] in `backend/app/services/correlation.py`**

```python
def submitted_portfolio(db: Session) -> list[Alpha]:
    """The only definition of 'portfolio' the correlation gate may use.

    An alpha is in the portfolio iff it has a SubmissionAttempt with
    result == 'submitted' and is_recalled is false. AlphaStatus.PASSED means
    'BRAIN scored it', which is a property of a simulation, not of a portfolio.
    """
```

Replace all four `SUBMITTED ∪ PASSED` queries with this function. `is_recalled`
(`models/alphas.py:190`) must exclude an alpha — a recalled submission no longer
occupies portfolio space.

**Migration note.** If the operator's real database has submitted alphas recorded
only via `AlphaStatus.SUBMITTED` and not via `SubmissionAttempt` rows, this change
would silently empty the portfolio and disable the gate. `scripts/record_past_attempts.py`
already exists for this purpose: Phase 1 must include a one-shot reconciliation
that asserts `count(status == SUBMITTED) == count(SubmissionAttempt(result='submitted'))`
and refuses to proceed on a mismatch. **A gate that silently becomes a no-op is
worse than the deadlock it replaces.**

### 1.2 Cluster the family before gating it (F1, part 2)

Sibling correlation is a within-family fact and must be resolved before the
portfolio gate ever runs.

**[NEW] `backend/app/services/clustering.py`**

```python
@dataclass
class RidgeCluster:
    representative_id: int
    member_ids: list[int]
    intra_max_corr: float
    election_reason: str

def cluster_family(verdicts, pnl_store, *, cfg) -> list[RidgeCluster]:
    """Single-linkage clustering of a family's surviving points by |PnL correlation|
    at cfg.sibling_cluster_threshold (0.90), electing one representative per cluster."""
```

- Points with no measurable overlap form singleton clusters (D2: unmeasured never
  merges silently).
- Election criterion, in order: highest `ridge_score` (§1.4) → higher
  `neighbours_simulated` → lower `decay` → lower `turnover` → lowest `alpha_id`.
  The last is a determinism tie-break, not a preference.
- `election_reason` is a human-readable string surfaced in the report, so the
  operator can see *why* this point and not its neighbour.

**[MODIFY] `plateau.evaluate()`** — the promotion pipeline becomes explicitly
staged, and each stage's outcome is recorded on the `Verdict`:

```
BRAIN checks → plateau shape → reconciliation → stability → multiple-testing bar
      → intra-family clustering (elect representative)
      → portfolio gate vs submitted-only
```

Non-representatives receive `promoted=False` with reason
`"clustered into representative #N (rho=0.94)"` — which is materially different
information from today's `"empirical correlation 0.94 ... exceeds threshold"`, and
correctly reads as *not a rejection*.

### 1.3 Signed correlation, fail-closed overlap (F1, part 3)

**[MODIFY] `correlation.py`**

- `compute_pairwise_correlation` keeps returning signed rho. The gate compares
  `rho >= threshold`, not `abs(rho) >= threshold`. A strongly negatively
  correlated alpha is the most valuable thing the portfolio can acquire, and
  BRAIN's own self-correlation check passes it.
  *Clustering (§1.2) continues to use `abs(rho)`* — a sign-flipped duplicate is
  still a duplicate. The two uses are genuinely different and the code should say so.
- Insufficient overlap returns `CorrelationResult(status=UNMEASURED)` and blocks,
  replacing the bare `continue` at `correlation.py:129`.
- Delete the 0.85 / 0.20 constants (`correlation.py:238-241`). Return
  `(None, target_id, "unmeasured")`.

**[MODIFY] `app/routers/ui.py:77-79` and `app/static/index.html:697-703`** — render
`null` self-correlation as an explicit grey `UNMEASURED` badge, never as a number.
The current `(proxy)` suffix on a fabricated 0.20 is the most dangerous display in
the UI: it looks measured.

### 1.4 Rank the ridge centre, not the peak (F6)

**[MODIFY] `plateau.py`** — add to `Verdict`:

```python
ridge_score: float | None   # median of [own_sharpe, *neighbour_sharpes]
```

`ridge_score` becomes the ranking key everywhere the shortlist is ordered
(`plateau.py:390`, `report.py:125`, `report.py:191`) and the primary election
criterion in §1.2. `sharpe` remains displayed — the operator should see both, and
seeing a peak of 2.1 with a ridge score of 1.4 is exactly the diagnostic the
plateau test exists to produce.

Rationale: the plateau test already establishes that a point's own Sharpe is an
unreliable estimate. Ranking by that same statistic hands the operator's attention
to whichever point carries the largest positive error. The median over the local
neighbourhood is a crude but unbiased-in-the-right-direction shrinkage, it needs no
new parameters, and it costs two lines.

### 1.5 Reconciliation as a hard precondition (F5b)

**[MODIFY] `plateau.evaluate()`** — before any statistic is computed from a PnL
vector, require `verify_pnl_reconciliation(...).is_valid`. Failure yields
`promoted=False` with reason `"PnL failed Sharpe reconciliation (recomputed 8.42 vs reported 1.91)"`.

**Remove the `require_pnl: bool = True` escape hatch.** The `False` branch
(`plateau.py:353-360`) promotes on Sharpe and fitness alone with
`subperiod_passed=True` asserted without evidence. There is no legitimate caller
for "promote without the statistics"; tests that need it should build fixture PnL,
which the new harness makes trivial.

### 1.6 Definition of done

New tests in `tests/test_quant_review_regressions.py`:

| Test | Pins |
|---|---|
| `test_sibling_ridge_promotes_one` | The F1 reproduction — 6×4 grid, two adjacent check-passing points, ρ≈0.94 → **exactly 1** promotion (today: 0) |
| `test_portfolio_excludes_brain_passed` | An alpha with `status=PASSED` and no submission attempt does not gate anything |
| `test_recalled_submission_leaves_portfolio` | `is_recalled=True` frees the portfolio slot |
| `test_negative_correlation_passes_gate` | ρ = −0.8 vs portfolio → promoted |
| `test_insufficient_overlap_blocks` | 200 common days with `min_common_days=500` → blocked, reason mentions unmeasured |
| `test_ridge_centre_outranks_peak` | Peak (2.1, neighbours 0.4) ranks below centre (1.6, neighbours 1.5) |
| `test_unreconciled_pnl_blocks_promotion` | Reconciliation failure blocks regardless of every other gate passing |

---

## Phase 2 — Restore search breadth (F2, F10a)

### 2.1 Stratified sampling of the configuration space (F2)

**Root cause.** `expand()` consumes its budget as an `itertools.product` prefix
with a `break` (`constructor.py:356`, `:389`, `:434`). Because the leftmost axis
varies slowest, the first ts-transform absorbs the entire budget. Measured at
defaults: **392/392 candidates were `ts_zscore` × `rank`**; layers 2 and 3 never
execute.

**[MODIFY] `backend/app/services/constructor.py`**

Replace the three sequential `for ... : break` loops with an explicit two-step
design:

1. **Enumerate configurations, do not expand them.** Build the full list of
   `SurfaceConfig` descriptors across all three layers (depth-1, depth-2,
   multi-field) without emitting a single candidate. A `SurfaceConfig` is cheap —
   it is the tuple that identifies a surface, not the 49 alphas on it.

2. **Allocate the budget across strata, then expand the winners.**

```python
def select_surface_configs(configs, budget_surfaces, *, rng) -> list[SurfaceConfig]:
    """Round-robin across strata, shuffling within each stratum.

    Stratum key: (layer, ts_signature). Guarantees every ts-transform and every
    depth layer receives a surface before any stratum receives a second one.
    """
```

With the default budget of 400 and a 49-point standard surface, 8 surfaces are
affordable. Round-robin over 7 depth-1 transforms + 4 depth-2 pairs + optional
`ts_corr` yields ≥ 8 distinct operator signatures — the exact inversion of
today's behaviour.

**Budget accounting.** The current guard `if len(out) + surface_size > max_candidates: break`
under-fills by up to one surface and — more seriously — silently changes meaning
across layers. Compute `budget_surfaces = max_candidates // surface_size` once and
allocate against it explicitly, logging `emitted`, `surfaces`, `strata_covered`,
and `strata_starved`.

**Reachability regression.** The default budget must reach layers 2 and 3. If
after stratification 400 candidates still cannot cover the strata meaningfully,
that is a signal to *raise the default budget* to `n_strata × surface_size`, not
to accept starvation. State the chosen default and its arithmetic in the module
docstring.

### 2.2 Settings monoculture

STRATEGY.md §1 identifies "USA / TOP3000 / delay-1 price-volume" as structural
cause #1 of the original 0/51. The constructor still emits only that cell:
`DEFAULT_UNIVERSES = ("TOP3000",)`, `DEFAULT_TRUNCATIONS = (0.08,)`,
`AlphaSettings.delay = 1`.

Widen to `universes = ("TOP3000", "TOP1000")` and `truncations = (0.01, 0.08)`,
and make `delay` a swept axis with `0` included. Two caveats to handle explicitly:

- Delay-0 and each universe are **separate BRAIN configurations**; `BrainClient.config_available()`
  (`client.py:335`) already exists to check entitlement. Query it once per
  region/universe/delay triple at expansion time and skip unavailable cells with a
  logged reason rather than burning simulation slots on guaranteed failures.
- Widening axes multiplies surfaces. Do this **after** §2.1, so the added breadth
  is distributed by the stratified sampler instead of being swallowed by the prefix.

### 2.3 Ladder synchronisation (F10a)

- `plateau.WINDOW_LADDER` / `DECAY_LADDER` are the *wide* grid; the constructor's
  default is the *standard* grid. Nothing breaks — `_neighbours` derives ladders
  dynamically — but the constants now lie, and `tests/test_plateau.py` pins the
  wrong ones. Import both ladders from `constructor.py` and have `plateau.py`
  reference them by name.
- **Compute the plateau over the coarse ladder.** Moving decay from `(0,4,8,16)`
  to `(0,1,2,4,6,8,16)` makes decay-neighbours near-duplicates, and the plateau
  test's power comes from neighbours being economically *distinct*: `decay=1` vs
  `decay=2` is the same alpha, so the ratio test passes trivially. Keep the fine
  grid for settings search, but have `_neighbours` step over a declared
  `PLATEAU_DECAY_LADDER = (0, 4, 8, 16)`, snapping intermediate points to the
  nearest coarse rung. Same treatment for windows.
- Fix the dead turnover pre-filter (`constructor.py:249`): the guard tests
  `window < 5` where the minimum window on every ladder is 5, and the comment says
  `w <= 5`. Decide which is meant. Note that if it ever fires it voids the whole
  surface, since `_emit_surface` discards incomplete surfaces — so it must
  additionally shrink the expected `surface_size`, or the filter must be applied
  before the completeness check rather than inside it.

### 2.4 Definition of done

| Test | Pins |
|---|---|
| `test_constructor_diversity` | Default axes, `max_candidates=400` → ≥ 5 distinct `grid["ts"]`, ≥ 1 with `grid["depth"] == 2` |
| `test_constructor_stratification_is_seeded` | Same seed → identical config list; different seed → different, both covering all strata |
| `test_every_surface_is_complete` | Existing invariant preserved under the new sampler |
| `test_plateau_ladders_match_constructor` | The constants cannot drift apart again |
| `test_plateau_neighbours_use_coarse_decay` | `decay=1` and `decay=2` are not treated as independent neighbours |

---

## Phase 3 — Calibrate the gates (H, F3, F4)

Phases 1 and 2 are corrections with obviously-right answers. Phase 3 is a
*calibration*, and calibration without measurement is how the current thresholds
were arrived at. The harness therefore lands first and is the deliverable that
justifies the rest.

### 3.1 The filter backtest harness (H) — the centrepiece

**[NEW] `backend/app/services/filter_backtest.py`**
**[NEW] `backend/scripts/calibrate_filter.py`**

The filter is a classifier. It has never been scored as one.

```python
@dataclass
class SyntheticFamily:
    true_sharpe: float          # 0.0 for a null family
    n_points: int               # surface size
    intra_corr: float           # sibling correlation, default 0.92
    backtest_days: int
    regime_break_at: float | None   # fraction through the window where the signal dies

def generate_family(spec, *, rng) -> tuple[list[Alpha], PnLStore]:
    """Emit a family whose ground truth is known: correlated daily PnL vectors with
    a specified true Sharpe, realistic sibling correlation, and optional decay."""

@dataclass
class FilterScorecard:
    promotions_per_1000_null_trials: float   # false discovery
    power_at_sharpe: dict[float, float]      # P(promote | true SR)
    stage_attrition: dict[str, int]          # where real alphas die
    config_fingerprint: str
```

`scripts/calibrate_filter.py` runs the full gate stack — plateau, reconciliation,
stability, DSR, haircut, clustering, portfolio gate — over a population of
synthetic families and prints the scorecard. It answers, for the first time:

- Of promoted alphas, what fraction came from null families? *(A4: target < 5%)*
- Of true-SR-1.5 families, what fraction promote at least one point? *(A3: target ≥ 85%)*
- **Which stage kills real alphas?** `stage_attrition` is the single most useful
  output — it converts every future threshold argument into an experiment.

The harness runs against the same `FilterConfig` the production path uses, so a
scorecard is always attributable to a fingerprint. Sweeping a threshold means
re-running with a different config, not editing a literal.

Cost control: 500 replications × 24-point families × 1 236 days is ~15 M floats —
seconds, not minutes. Gate it behind `-m slow` so `pytest` stays fast, and run it
in CI nightly.

### 3.2 Significance-based stability gates (F3)

**Root cause.** `evaluate_subperiod_stability()` thresholds point estimates
without reference to their standard error. A 252-day Sharpe has SE ≈ 1.0; a
2.5-year half-sample Sharpe has SE ≈ 0.63. Measured type-II error against a
*stationary* generating process: **37% at true SR 1.5, 57% at true SR 1.0**.

**[MODIFY] `backend/app/services/subperiod.py`**

Add the Lo (2002) standard error and convert every ratio test into a z-test:

```python
def sharpe_standard_error(sr_daily: float, n_obs: int) -> float:
    """SE of the *annualised* Sharpe. Var(SR_hat) ≈ (1 + SR_d²/2) / n."""
    return math.sqrt((1.0 + 0.5 * sr_daily**2) / n_obs) * math.sqrt(TRADING_DAYS_PER_YEAR)
```

| Gate | Today | Replacement |
|---|---|---|
| Split-half | `min/max < 0.40` → reject | `z = (SR₁ − SR₂)/√(SE₁² + SE₂²)`; reject if `z < −2.0` |
| Sign | either half `<= 0` → reject | reject only if a half is **significantly** negative (`SR/SE < −1.0`); a noisy near-zero half is not evidence of failure |
| Recent decay | `recent < 0.50 × full` → reject | compare recent 252d against the **prior, non-overlapping** window, `z < −2.0`. The current comparison against the *full* window includes the recent period on both sides, which is mechanically correlated and weaker than it appears |
| Rolling positivity | 70% of 126d windows stepped by 21 | Windows overlap by 83%, so the count is not a count of independent successes. Either step by 126 (non-overlapping) or scale the floor by `n_eff = T/126`. Also reconcile the docstring, which says 75% while the default is 0.70 |

`SubPeriodVerdict` gains `split_half_z`, `recent_decay_z`, and per-gate SEs so
the report can show *how far* from the line a candidate sat.

**These thresholds are provisional until §3.1 scores them.** `−2.0` is the
starting point, not the answer; the harness sweeps it and the chosen value is
recorded with its measured power.

### 3.3 Programme-wide trial ledger and EVT haircut (F4)

**Root cause, restated.** `plateau.py:325` passes one family's Sharpes — one
field, one operator, 49 window/decay points at ρ ≈ 0.8–0.95 — as the trial
universe. Two consequences: the real selection burden ("best of everything tried
this month") is never deflated for, and because `SR* = σ_SR · E[max]`, the gate
tracks the *family's dispersion* more than the alpha's quality. A Sharpe-1.3
alpha scores DSR 0.93 or 0.18 depending only on how similar its siblings were.

**[NEW] `backend/app/services/trials.py`**

```python
@dataclass
class TrialLedger:
    n_trials: int                  # every simulated alpha, programme-lifetime
    n_eff: float                   # eigenvalue-based, over the cross-family PnL matrix
    sigma_sr_daily: float          # dispersion across FAMILIES, not within one
    window_days: int

def build_ledger(db, pnl_store, *, cfg, lookback_days: int = 365) -> TrialLedger
def expected_max_normal(n: float) -> float:
    """E[max of n standard normals], Gumbel approximation:
       √(2 ln n) − (ln ln n + ln 4π) / (2√(2 ln n))"""
```

`n_eff` reuses `compute_effective_trials()` — written, tested, and currently
**never called from the pipeline**. Important subtlety to encode in the docstring:
N_eff corrects the **σ_SR** side of the calculation, not the trial-count side.
Feeding N_eff in as `n_trials` makes the gate *weaker* (400 equicorrelated trials
at ρ=0.5 collapse to N_eff = 4.0). The ledger therefore carries both numbers and
uses each where it belongs.

**[MODIFY] `plateau.haircut_bar()`**

```python
def haircut_bar(ledger: TrialLedger, *, cfg: FilterConfig) -> float:
    se = math.sqrt(TRADING_DAYS_PER_YEAR / cfg.backtest_days)
    return cfg.target_sharpe + se * expected_max_normal(ledger.n_eff)
```

Replaces `1.25 + 0.10·log₁₀(N)`, which is ~5× too flat and grows by 0.1 per decade
where the bias grows by ~0.2:

| N | E[max \| zero skill] | Current bar | New bar |
|---|---|---|---|
| 20 | 0.76 | 1.38 | ~2.01 |
| 100 | 1.06 | 1.45 | ~2.31 |
| 400 | 1.27 | 1.51 | ~2.52 |
| 5 000 | 1.59 | 1.62 | ~2.84 |

**[MODIFY] `plateau.evaluate()`** — pass `ledger.sigma_sr_daily` and
`ledger.n_eff` into `compute_dsr` instead of the family's own Sharpe list. The
`compute_dsr` implementation itself is correct and tested against the closed form;
only its inputs change.

**Expected consequence, stated plainly:** promotions will become rarer. A bar
near 2.5 on a 5-year backtest is demanding, and it should be — that is what "best
of 400 trials" costs. The §3.1 scorecard is what distinguishes *appropriately*
rare from *uselessly* rare, and the `stage_attrition` output is what tells you
whether to spend the next increment of budget on a higher bar or a longer backtest.

Retire `MIN_TRIALS_FOR_DSR` / `COLD_START_SHARPE_BAR` / the `gate_mode` split: with
a programme-wide ledger there is no cold start after the first few hundred
simulations, and a single gate is far easier to reason about than two that
alternate. Replace `DSR_RE_PROMOTION_THRESHOLD` keyed off the substring
`"watchlist"` in a free-text comments field (`plateau.py:335`) with an explicit
`Alpha.is_rewatched` boolean column — a statistical threshold must not depend on prose.

### 3.4 Definition of done

- `scripts/calibrate_filter.py` produces a scorecard meeting **A3** and **A4**.
- The chosen thresholds are recorded in `FilterConfig` with a comment citing the
  scorecard run that justified them.
- `test_stationary_alpha_survives_stability` — true SR 1.5, no decay, ≥ 85% survival
  over 200 replications.
- `test_haircut_matches_evt_growth` — `haircut_bar` tracks `√(2 ln N)` within
  tolerance across N ∈ {20, 100, 400, 5 000}.
- `test_dsr_uses_programme_ledger` — identical alpha in a tight family and a
  dispersed family receives the same DSR.

---

## Phase 4 — Portfolio construction and allocation (F7, F8, F9)

### 4.1 Intra-batch orthogonality (F7)

`evaluate()` never writes status, so a single report run can promote ten
mutually-correlated candidates from ten different families. The constraint
STRATEGY.md §2 names as *the* hard one is enforced only against history, never
within the batch.

**[NEW] in `backend/app/services/clustering.py`**

```python
def select_orthogonal_batch(verdicts, pnl_store, *, cfg) -> list[Verdict]:
    """Greedy maximally-orthogonal subset: accept in descending ridge_score,
    reject any candidate correlating above cfg.portfolio_corr_threshold with an
    already-accepted member. Deferred candidates are returned flagged, not dropped —
    they may be the best available tomorrow."""
```

Applied in `report.build()` and the UI shortlist endpoint after promotion, before
display. Deferred candidates appear in a distinct "held back — correlates with
today's #2" section, because that is a different fact from "rejected by the filter"
and the operator should not confuse the two.

### 4.2 Bandit corrections (F8)

**The user-facing draft of this plan placed `DiscountedThompsonSampler` in
`allocator.py`. It lives in `allocator_bandit.py:37`.** `allocator.py` is a
separate crowding-weighted heuristic (`_dataset_priority`, `suggest`,
`plan_budget_allocation`). Both exist with no stated precedence — that ambiguity
is itself a finding.

**[MODIFY] `backend/app/services/allocator_bandit.py`**

1. **Discount per batch, not per trial.** `update()` applies γ = 0.95 on every
   individual reward; at 200–500 simulations/day the posterior's effective memory
   is ~20 simulations — minutes of throughput. The arm is not learning a hit-rate,
   it is tracking noise. Add `update_batch(dataset_code, rewards: Sequence[float])`
   that discounts **once** then accumulates the batch, and derive γ from a declared
   half-life in days: `gamma = 0.5 ** (1 / half_life_days)`.
2. **Injected RNG.** `random.betavariate` draws from the global RNG
   (`allocator_bandit.py:73`). Take `rng: random.Random` in `__init__`.
3. **Novelty in the objective.** STRATEGY.md §6 specifies
   `expected_pass × novelty_vs_portfolio`; the implementation scores
   `expected_pass` alone. Multiply the posterior draw by a novelty factor derived
   from the submitted portfolio's dataset composition and the field-crowding score
   `allocator.py:75` already computes.
4. **Close the cap's escape hatch.** `select_best_dataset` returns the top-ranked
   dataset anyway when every arm exceeds the 20% share cap
   (`allocator_bandit.py:104`), which defeats the cap precisely when it binds.
   Fall back to the **least-used eligible** dataset instead. Also measure share
   over a trailing window rather than cumulative lifetime usage, so the cap
   responds within a campaign instead of after one.
5. **Reconcile the documented split.** `SimulationBudgetOrchestrator` documents
   80/20 and 40/40/20 and returns 2/1/0 and 1/1/1 (67/33 and 33/33/33), ignoring
   its own `max_concurrent` argument. Compute slots from the declared fractions
   and the argument, with `largest_remainder` rounding.
6. **Declare precedence** between the two allocators in a module docstring — one
   picks the dataset, the other picks the field/territory within it — or delete
   the loser.

### 4.3 Authoritative BRAIN correlation (F9)

`docs/BRAIN_API.md:164` records `GET /alphas/{id}/correlations/self` — the
platform's own number, over its own ~2-year window, and the one that actually
decides acceptance. The project reimplements it locally and, when PnL is missing,
fabricates it.

**[MODIFY] `backend/app/services/brain/client.py`**

```python
def self_correlation(self, alpha_id: str) -> dict[str, Any]:      # /alphas/{id}/correlations/self
def prod_correlation(self, alpha_id: str) -> dict[str, Any]:      # /alphas/{id}/correlations/prod
```

Both are GETs; the `test_brain_no_post.py` invariant is unaffected and must be
re-asserted. Call them **only for shortlist candidates** — a handful per day, not
per simulated alpha. Local Pearson remains the cheap pre-screen; the platform
number is the gate of record and is displayed with its source.

**Also fix the N+1 network call.** `ui.py:50` calls
`compute_max_self_correlation_with_submitted` per verdict row, which calls
`ensure_alpha_pnl` per portfolio member, which may hit the network — inside a
synchronous UI render path. Hoist to one batched pass per request, reusing D3's
evaluation cache.

### 4.4 Definition of done

| Test | Pins |
|---|---|
| `test_batch_orthogonality` | Ten mutually-correlated promotions → one accepted, nine flagged deferred |
| `test_bandit_is_deterministic_under_seed` | Same seed → identical dataset sequence |
| `test_bandit_batch_discount_half_life` | Evidence from N days ago carries the declared weight |
| `test_diversity_cap_has_no_escape_hatch` | All arms over cap → least-used eligible arm, not the top-ranked |
| `test_budget_slots_sum_to_max_concurrent` | For `max_concurrent` ∈ {3, 6, 10} in both modes |
| `test_brain_client_has_no_post_paths` | Existing invariant, re-asserted after the new GETs |

---

## Phase 5 — Hygiene (F10b)

Small, independent, no ordering constraints.

- **Evolution window jitter (`evolution.py:56`).** `_WINDOW_JITTER` keys are the
  wide ladder; the standard grid emits `20, 40, 60, 120, 250`, so parameter
  mutation fires on 2 of 7 windows. Worse, jittered values (3, 7, 12, 15) land
  **off-grid**, where a candidate has no complete surface and therefore can never
  pass the plateau test — the evolution arm's output may be structurally
  unpromotable. Re-key to the standard ladder, restrict jitter to on-ladder rungs,
  and add `test_evolved_candidate_can_be_promoted` as an end-to-end assertion.
- **Type annotations.** `plateau.evaluate(pnl_store: PnLStore)` and the local
  `list[PlateauPoint]` reference undefined names; `subperiod.verify_pnl_reconciliation(pnl_store: Any)`
  uses an unimported `Any`. Harmless under `from __future__ import annotations`,
  but they prove no type checker has run over these modules. Add `mypy` (or
  `pyright`) over `app/services/` to the dev extra and to CI.
- **`load_surface` latest-metric selection (`plateau.py:170`).** The
  `latest[alpha.id] = ...` overwrite relies on `ORDER BY AlphaMetric.id` to leave
  the newest metric last. That is correct today and silently wrong the moment an
  ordering changes. Make it an explicit `max(by id)` per alpha.
- **Report/README drift.** README claims "120+ tests" (there are 194) and
  documents `scripts.triage_fields`, which does not exist in `backend/scripts/`.
  README and STRATEGY.md both contain absolute `file:///Users/sanya/...` links
  that break for any other reader; make them repo-relative.

---

## 6. Test strategy

**[NEW] `backend/tests/test_quant_review_regressions.py`** — one file, organised
by finding, each test named for the behaviour it pins rather than the function it
calls. Every test in it must **fail on `983c134`** and pass after its phase. A
regression test that passes before the fix is pinning nothing; assert this by
running the new file against a stashed checkout during review.

**[NEW] `backend/tests/test_filter_backtest.py`** — harness self-tests: a null
generator really has zero edge; a specified-SR generator really produces that
Sharpe; `intra_corr` really materialises in the correlation matrix. The harness is
now load-bearing for threshold decisions, so it needs its own correctness proof.

Existing suites keep their role. `test_plateau.py` and `test_subperiod.py` will
need updates where they pin superseded behaviour — each such change must be
called out in review with a one-line justification, because "the test was updated
to match the new code" is exactly how a real regression gets normalised.

**Convention.** Keep the codebase's existing docstring style: state the *stake*,
not the mechanics. `test_plateau.py`'s "The spike case is the one that matters — it
is the failure mode that would put overfit noise in front of the operator" is the
model to follow.

---

## 7. Verification

```bash
cd backend

# full suite — must stay green at every phase boundary
.venv/bin/python -m pytest -q

# the regression file, verbose
.venv/bin/python -m pytest tests/test_quant_review_regressions.py -v

# Phase 0 gate — run against the REAL store before anything else merges
.venv/bin/python -m scripts.audit_pnl

# Phase 3 calibration — the evidence behind every threshold
.venv/bin/python -m scripts.calibrate_filter --replications 500 --out docs/calibration/

# Phase 2 manual check — diversity of a real expansion
.venv/bin/python -m scripts.run_family --field liabilities --denominator cap --simulate 0

# end-to-end on a seeded synthetic database
.venv/bin/python -m scripts.report
```

**Manual review checklist per phase**

1. Does the daily report's funnel telemetry still add up, stage by stage?
2. Does `Per-Family Sequential Gating Breakdown` now show attrition at the stage
   the change was meant to move — and *only* there?
3. Does the report header carry a `FilterConfig` fingerprint matching the
   committed config?
4. Spot-check one promoted alpha end to end: expression → surface → PnL vector →
   reconciliation → each gate's statistic → cluster election → portfolio gate.
   Every number on the report line should be re-derivable by hand.

---

## 8. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Stored PnL is cumulative → every historical result void | Medium | Phase 0 blocks all other work; `audit_pnl.py` reports the distribution before anything is rewritten; original `.npy` files are backed up (precedent: `docs/PHASE0.md`) before any normalisation |
| Portfolio redefinition (§1.1) silently empties the gate | Medium | Explicit assertion that `SubmissionAttempt` rows reconcile with `AlphaStatus.SUBMITTED`; refuse to run on mismatch |
| EVT haircut (§3.3) is so strict nothing promotes | High | This is a *calibration*, not a correctness fix. The §3.1 scorecard sets it, and `stage_attrition` shows whether the bar or an upstream gate is binding. Ship the harness first |
| Widened settings axes (§2.2) burn simulation budget on unavailable configs | Medium | `config_available()` pre-check; log and skip rather than simulate |
| Test churn normalises a real regression | Medium | Every modified existing test needs a written justification in review; new regression tests must be shown failing on `983c134` |
| Phase 3 changes two things at once (stability + haircut) | Medium | Land the harness alone first and record a **baseline scorecard on current thresholds**. Without that baseline there is nothing to compare against |

---

## 9. Deliberately out of scope

- **Automated submission.** Untouched. `test_brain_no_post.py` must keep passing
  at every commit; the new BRAIN calls in §4.3 are GETs.
- **Multi-user / multi-tenant correlation.** STRATEGY.md §8 keeps the portfolio as
  an argument rather than a global, which this plan preserves. No further work now.
- **Replacing the AST compiler or operator KB.** It is the asset the project was
  right to keep. Nothing here touches `app/validator/`.
- **New economic mechanisms or datasets.** This plan restores the machine's
  ability to search and judge. What to search is the next question, and a better
  one to ask once the scorecard exists.

---

## 10. Estimated shape of the work

| Phase | Modules touched | New modules | New tests | Risk |
|---|---|---|---|---|
| 0 | `pnl_storage`, `backfill_pnl` | `scripts/audit_pnl` | 3 | Low, blocking |
| 1 | `plateau`, `correlation`, `report`, `ui`, `index.html` | `clustering`, `filter_config` | 7 | Medium |
| 2 | `constructor`, `plateau` | — | 5 | Low |
| 3 | `subperiod`, `plateau` | `trials`, `filter_backtest`, `scripts/calibrate_filter` | 8 | High |
| 4 | `allocator_bandit`, `report`, `ui`, `brain/client` | — | 6 | Medium |
| 5 | `evolution`, `plateau`, docs | — | 2 | Low |

Phases 1 and 2 are the ones that change the output of the machine tomorrow.
Phase 3 is the one that makes the output trustworthy. Neither substitutes for
the other.
