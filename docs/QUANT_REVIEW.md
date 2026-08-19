# Quant Research Review — Alpha Research Engine

Reviewed at `983c134`. All 194 tests pass. Findings below were verified by running
code, not by reading it; reproductions are included.

---

## Verdict

The engineering is well above the median for a research tool of this size: the
deterministic compiler is the right asset to have built, the "simulation
automated / submission manual" line is enforced by a test, the threading model
is honest, and the comments explain *why* rather than *what*. The strategy
documents diagnose the original failure correctly.

The problem is that **the statistical layer — the part STRATEGY.md calls "the
product" — is the least-validated part of the system.** Every component exists
and is unit-tested in isolation; almost none of them are calibrated, and three of
them interact in a way that makes the pipeline's terminal output structurally
zero. The system currently cannot promote an alpha from a *good* family, and the
search it does run covers about 1/28th of the space it was designed to cover.

Ranked by expected impact on the only metric that matters — accepted
uncorrelated alphas per week.

---

## F1 — The correlation gate deadlocks against itself. Yield is structurally zero.

**Severity: critical. This alone explains a zero hit-rate.**

`AlphaStatus.PASSED` means "cleared BRAIN's own `checks[]`"
(`result_import.py:228`, `simulation_runner.py:149`). The correlation gate's
default portfolio is `SUBMITTED ∪ PASSED` (`plateau.py:263`,
`correlation.py:64`). So a candidate is correlation-checked against **every alpha
that ever cleared BRAIN's checks, including its own siblings on the same plateau
ridge** — which by construction are ~0.95 correlated with it.

The consequence is a symmetric deadlock. Reproduction:

```python
# 6x4 grid, one genuinely stable Sharpe-1.9 mechanism, two adjacent ridge points
# clear BRAIN checks, sibling PnL ~0.94 correlated (which is what adjacent grid
# points really look like).
vs = evaluate(db, "probe/self", pnl_store=store)
```
```
CHECK-PASSING IDS: [10, 14]
  id=10 promoted=False reasons=['empirical correlation 0.94 with portfolio alpha #14 exceeds threshold 0.55']
  id=14 promoted=False reasons=['empirical correlation 0.94 with portfolio alpha #10 exceeds threshold 0.55']
PROMOTED COUNT: 0
```

Each blocks the other. There is no ordering, no tie-break, no "best of the
cluster wins". **Any family that produces two or more check-passing points
promotes nothing**, and the better the family, the more certain this is. A
family with exactly one check-passing point — i.e. a spike, the thing the
plateau filter exists to reject — is the only shape that can survive.

The fix is two changes:

1. **The portfolio is what you submitted, not what BRAIN scored.** Gate against
   alphas with a `SubmissionAttempt(result='submitted')` — the definition
   `compute_max_self_correlation_with_submitted()` already uses correctly.
   `AlphaStatus.PASSED` should never appear in a correlation portfolio.
2. **Cluster before gating.** Sibling correlation is a *within-family* fact, not
   a portfolio collision. Group the family's surviving points by PnL correlation
   (single-linkage at ~0.9), elect one representative per cluster — the ridge
   centre, not the peak, see F6 — and only then run the portfolio gate on the
   representatives.

While you are there: `abs(rho)` (`correlation.py:129`) rejects strongly
*negatively* correlated alphas, which are the most valuable diversifiers you can
own and which BRAIN's own self-correlation check passes. Gate on signed rho.
And `if len(common_dates) < min_overlap: continue` fails **open** — an
unmeasurable pair is silently scored as uncorrelated. For a safety gate, an
unmeasurable comparison must block, not pass.

---

## F2 — The constructor explores one operator. 96% of the designed grid is unreachable.

**Severity: critical. This is the direct cause of low structural diversity.**

STRATEGY.md Rule 2 promises "200–800 candidates per family, **sampled** rather
than full cross-product". The implementation is not a sample — it is a
`itertools.product(...)` prefix with a `break` when the budget fills
(`constructor.py:356-386`). Since the leftmost axis varies slowest, the budget is
consumed entirely by the first ts-transform.

Measured, default axes, `max_candidates=400`:

```
EMITTED: 392
TS OPS:  Counter({'ts_zscore': 392})
DEPTH:   Counter({1: 392})
CS:      Counter({'rank': 392})
N_SLICES: 8   MAX_SLICE: 49
```

Every emitted candidate is `ts_zscore` × `rank`. Zero `ts_rank`, `ts_delta`,
`ts_mean`, `ts_decay_linear`, `ts_std_dev`, `ts_quantile`. Zero depth-2
templates. Zero `ts_corr`. The entire second and third layers of `expand()` are
dead code at the default budget — the `break` fires before they are reached.

So the axes actually swept per family are `{group, neutralization, window,
decay}`: settings, not structure. The project's own diagnosis was that the
original 51 alphas failed because they were five fields' worth of one idea; the
constructor reproduces the same monoculture one level up.

Fix: sample the config space instead of enumerating it. Shuffle the product with
a seeded RNG, or better, stratify — guarantee each ts-transform and each depth
gets a floor share of the budget before any axis gets a second surface. Assert it
in a test: `len({c.grid['ts'] for c in expand(...)}) >= 5`.

Related monoculture, same theme: `DEFAULT_UNIVERSES = ("TOP3000",)`,
`AlphaSettings.delay = 1`, `DEFAULT_TRUNCATIONS = (0.08,)`. STRATEGY.md §1 names
"USA/TOP3000/delay-1" as structural cause #1 of the original failure, and it is
still the only cell the constructor emits. Delay-0 and the smaller universes are
materially less mined; they are one tuple away.

---

## F3 — The stability gates reject 37–57% of genuinely good alphas.

**Severity: high.**

`evaluate_subperiod_stability()` thresholds *point estimates* without accounting
for their sampling error. A 252-day Sharpe has a standard error near 1.0; a
half-sample Sharpe over 2.5 years has SE ≈ 0.63. The gates ask those noisy
estimates to hit fixed ratios.

Type-II error, by Monte Carlo, for an alpha whose **true** Sharpe is constant
across the whole window (i.e. no decay at all, the ideal case):

| True Sharpe | P(fail recent-252d ≥ 50% of full) | P(fail split-half ratio ≥ 0.40) | P(fail either) |
|---|---|---|---|
| 1.0 | 30.9% | 37.4% | **56.7%** |
| 1.5 | 22.7% | 18.6% | **37.0%** |
| 2.0 | 15.9% |  7.8% | **22.5%** |

This showed up unprompted in the F1 reproduction: alpha #10, built from i.i.d.
normal draws with a constant positive drift, failed with
`split-half ratio 0.35 below floor 0.40`. There was nothing to detect — the
generating process was stationary by construction.

Three further problems in the same function:
- The docstring says the rolling-positive floor is 75%; the default is `0.70`.
- The rolling windows are 126 days stepped by 21, so consecutive windows share
  83% of their data. The "positive ratio" is not a count of independent
  successes and cannot be thresholded as if it were.
- The recent-252d window is a *subset* of the full window it is compared against,
  so the two statistics are mechanically correlated and the test is weaker than
  it looks.

Fix: make these significance tests, not ratio thresholds. Reject when the
half-to-half difference is *statistically* significant against SE, not when a
ratio falls below an arbitrary line — e.g. reject if
`(SR₁ − SR₂) / SE_diff < −2`. Same for decay. That preserves the intent (catch
alphas that genuinely died) while stopping the gate from taxing good alphas at
40%.

---

## F4 — DSR is deflating for the wrong multiple-testing problem.

**Severity: high.**

The implementation of Bailey & López de Prado in `subperiod.py:compute_dsr` is
correct — the test against the closed form is real and passes. The problem is
the inputs.

`plateau.py:325` passes `family_daily_sharpes` = the Sharpes of **one family**:
one field, one operator, one wrapper, 49 window/decay points that are 0.8–0.95
correlated with each other. Two errors follow:

**The trial universe is too small.** The program runs thousands of trials across
many mechanisms and datasets; DSR sees a few hundred trials of one mechanism.
The selection burden that actually produced the winner — "best of everything I
tried this month" — is never deflated for.

**The gate is driven by the family's dispersion, not the alpha's quality.**
Because `SR* = σ_SR · E[max]`, a tightly-clustered family hands its members a
near-free pass. Measured, T=1260, N=400:

| True Sharpe | σ_SR = 0.20 | σ_SR = 0.35 | σ_SR = 0.60 |
|---|---|---|---|
| 1.0 | 0.81 | 0.47 | 0.04 |
| 1.3 | **0.93** | 0.74 | 0.18 |
| 1.6 | **0.99** | 0.88 | 0.38 |

The same Sharpe-1.3 alpha scores 0.93 or 0.18 depending entirely on how similar
its siblings happened to be. In the F1 reproduction, where all family Sharpes
were identical, every point scored DSR ≈ 0.9997 — the 0.95 hurdle was free.

`compute_effective_trials()` — the eigenvalue N_eff estimator — is written,
tested, and **never called from the pipeline**. Note that wiring it in as-is
makes the gate *weaker*, not stronger (equicorrelated at ρ=0.5, it collapses 400
trials to N_eff = 4.0). N_eff is the right correction for the σ_SR side, not for
the trial-count side.

**The linear haircut has the wrong functional form.** `haircut_bar()` adds
`0.10·log₁₀(N)` to a 1.25 floor. The selection bias it is meant to offset grows
like `SE · √(2 ln N)` where `SE ≈ √(252/T)` ≈ 0.45 for a 5-year backtest:

| N trials | E[max Sharpe \| zero skill] | Code's bar | Bar it needs |
|---|---|---|---|
| 20 | 0.76 | 1.38 | ~2.01 |
| 100 | 1.06 | 1.45 | ~2.31 |
| 400 | 1.27 | 1.51 | ~2.52 |
| 5000 | 1.59 | 1.62 | ~2.84 |

At N=400 — one family — pure noise produces a best-of Sharpe of 1.27 while the
gate asks for 1.51. The haircut is roughly 5× too flat, and its shape is wrong:
it grows by 0.1 per decade where the bias grows by ~0.2.

Fix: maintain a **program-wide trial ledger** (every simulated alpha, ever) and
deflate against it. Recommended replacement:

```
bar(N, T) = target_SR + sqrt(252/T) * E_max(N_eff_program)
E_max(N) ≈ sqrt(2·ln N) − (ln ln N + ln 4π) / (2·sqrt(2·ln N))
```

with `N_eff_program` estimated from the PnL correlation matrix across families,
and DSR's `σ_SR` taken from the cross-family Sharpe distribution.

---

## F5 — Nothing enforces that stored PnL is what the statistics assume it is.

**Severity: high, and cheap to close.**

Every statistical gate consumes `PnLStore.load_pnl()` and assumes it holds
**daily** PnL increments. `backfill_pnl.py:73` stores BRAIN's
`/recordsets/daily-pnl` payload verbatim, with no differencing and no unit check.
If that recordset is a cumulative curve — which is how BRAIN renders it, and
which community clients `.diff()` before using — then:

- `Sharpe = mean/std` of a trending series is large and positive for everything;
- every pairwise correlation is a correlation of two integrated series, i.e.
  spuriously near 1;
- `evaluate_subperiod_stability` passes trivially (both halves rise, all rolling
  windows positive);
- DSR ≈ 1.0 for every candidate.

That is: a cumulative series **passes every gate**. The filter would not fail
loudly; it would go quietly inert, which is the precise failure mode STRATEGY.md
Rule 5 was written to prevent.

This project has already been bitten by exactly this class of bug once —
`result_import.py:89` documents margin differing by 10,000× between two import
paths, "silently, in a column whose name asserts the unit."

`verify_pnl_reconciliation()` already detects it. It is called once, in
`backfill_pnl.py:78`, purely to increment a counter that nothing reads. Make it a
hard precondition: an alpha whose recomputed Sharpe does not match BRAIN's
reported Sharpe is not eligible for promotion, and the backfill refuses to store
the vector. Diagnostic to run against real data before anything else on this
list:

```python
dates, arr = store.load_pnl(<any_id>)
print(np.mean(arr)/np.std(arr, ddof=1)*np.sqrt(252))   # should ≈ BRAIN's reported Sharpe
print(arr[:5], (arr > 0).mean())                        # ~0.5 for daily; ~1.0 for cumulative
```

(Note also: `docs/BRAIN_API.md:245` says BRAIN annualizes with **√250**; the code
uses √252 throughout. Immaterial for gating, material for a 0.05 reconciliation
tolerance.)

---

## F6 — The shortlist is ranked by the most selection-biased statistic available.

**Severity: medium-high, trivial to fix.**

`report.py:125` sorts the promoted list by raw in-sample Sharpe; `plateau.py`
sorts verdicts the same way. The plateau test's entire purpose is to establish
that a point's own Sharpe is an unreliable estimate — and then the ranking hands
the operator's attention to whichever point has the largest positive error.

Worse, the plateau ratio `neighbour_median / own_sharpe ≥ 0.6` is asymmetric: a
point is never penalised for having *better* neighbours. Combined with the
Sharpe ranking, the system systematically surfaces the **peak** of each ridge —
the single most upward-biased point on the surface — when the ridge *centre* is
the better out-of-sample bet.

Fix: rank by the neighbourhood median (or a James–Stein style shrinkage of own
Sharpe toward it), and promote the ridge centre rather than the peak. This is a
two-line change and probably the highest ratio of expected-OOS-Sharpe gained per
line of code in this review.

---

## F7 — A single morning's shortlist can be ten copies of one idea.

**Severity: medium.**

`evaluate()` never writes status, so within one report run the correlation gate
sees a portfolio that is frozen as of the start. Ten mutually-correlated
candidates from different families can all be promoted together — the
constraint STRATEGY.md §2 identifies as *the* hard one is enforced only against
history, never within the batch.

Fix: after promotion, greedily select a maximally-orthogonal subset of the
shortlist (accept highest-scoring, drop everything above threshold against
accepted, repeat). This is the same routine as F1's clustering step.

---

## F8 — The bandit forgets everything older than ~20 simulations, and does not optimise diversity.

**Severity: medium.**

`DiscountedThompsonSampler.update()` applies `γ = 0.95` **per trial**. At
200–500 simulations/day, the posterior's effective memory is ~20 simulations —
minutes of throughput. The arm is not learning a dataset's hit-rate; it is
tracking the last handful of results, which at a realistic hit-rate is almost
entirely noise. Discount per *batch* or per *day*, not per trial, and pick γ from
a target half-life in days.

Second: STRATEGY.md §6 specifies scoring on `expected_pass × novelty_vs_portfolio`.
The bandit scores `expected_pass` only; novelty appears nowhere in the objective.
The only diversity mechanism is a 20% share cap — which has an escape hatch that
returns the top-ranked dataset anyway when every arm exceeds it
(`allocator_bandit.py:104`), and which is measured against cumulative lifetime
usage, so a dataset can be hammered for a long while before the cap bites.

Third: `random.betavariate` draws from the global RNG. Research results are not
reproducible run-to-run. Take an injected `random.Random(seed)`.

Fourth: `SimulationBudgetOrchestrator` documents 80/20 and 40/40/20 splits and
returns 2/1/0 and 1/1/1 (67/33 and 33/33/33), ignoring its own `max_concurrent`
argument.

Note also that two allocators exist — `allocator.py` (crowding-weighted
heuristic) and `allocator_bandit.py` (Thompson) — with no stated precedence.
Pick one as authoritative.

---

## F9 — BRAIN will tell you the answer the gate is approximating.

**Severity: medium, high leverage.**

`docs/BRAIN_API.md:164` records `GET /alphas/{id}/correlations/self` — the
platform's own self-correlation number, computed over its own ~2-year window,
and the number that actually decides whether a submission is accepted. The
project reimplements it locally from PnL and, when PnL is missing, falls back to
`compute_max_self_correlation_with_submitted()` returning a **hardcoded 0.85 or
0.20** (`correlation.py:238-241`) — magic constants presented to the UI as
correlations.

Call the endpoint for shortlist candidates and gate on the authoritative value.
Keep the local Pearson as a cheap pre-screen. Same for production correlation.
Never display a fabricated number in the same field as a measured one — if it
cannot be measured, show "unmeasured" and block.

---

## F10 — Smaller items

- **Stale ladders.** `plateau.WINDOW_LADDER/DECAY_LADDER` are the *wide* grid
  `(5,10,22,63,126,252)/(0,4,8,16)`; the constructor's default is the *standard*
  grid `(5,10,20,40,60,120,250)/(0,1,2,4,6,8,16)`. Neighbour resolution is
  derived dynamically so nothing breaks, but the constants now lie and the tests
  pin the wrong ones.
- **The decay ladder dilutes the plateau test.** Moving from `(0,4,8,16)` to
  `(0,1,2,4,6,8,16)` makes decay-neighbours near-duplicates. The plateau test's
  power comes from neighbours being economically *distinct*; adjacent decay 1 vs
  2 is the same alpha, so the ratio test passes trivially. Keep the fine grid for
  settings search, but compute the plateau over the coarse ladder.
- **Evolution's window jitter is keyed to the wrong grid.** `_WINDOW_JITTER`
  keys are `(5,10,22,63,126,252)`; the standard grid emits `20,40,60,120,250`, so
  parameter mutation fires on 2 of 7 windows. And jittered windows (3, 7, 12, 15)
  land off-grid, where a candidate has no complete surface and therefore can
  never pass the plateau test — the evolution arm's output may be structurally
  unpromotable. Worth an end-to-end assertion.
- **Dead turnover pre-filter.** `constructor.py:249` guards `window < 5` when the
  minimum window on every ladder is 5. The comment says `w <= 5`. Note that if it
  ever did fire it would silently void the entire surface, since `_emit_surface`
  discards incomplete surfaces.
- **Undefined type annotations.** `plateau.evaluate(pnl_store: PnLStore)` and the
  local `list[PlateauPoint]`; `subperiod.verify_pnl_reconciliation(pnl_store: Any)`.
  Harmless at runtime under `from __future__ import annotations`, but they mean
  no type checker has ever run over these modules.
- **`is_re_promoting`** keys the DSR hurdle off the substring `"watchlist"` in a
  free-text comments field (`plateau.py:335`). A statistical threshold should not
  depend on prose.
- **No locked holdout.** Every promotion statistic is computed on the full
  backtest window, including the segment used to notice the alpha. BRAIN's
  simulation window is fixed, but the PnL vector is yours: reserve the last
  ~18 months, exclude it from plateau/DSR/ranking entirely, and spend it once per
  candidate as confirmation. Selection and validation currently share data.

---

## What is genuinely right

Worth stating, because most of the above is fixable precisely *because* the
structure underneath it is sound:

- The AST compiler / operator KB is the correct thing to have built, and "the LLM
  never writes syntax" is the right invariant. It is what makes volume safe.
- `test_brain_no_post.py` enforcing the no-submission invariant is exactly how a
  safety property should be held.
- Surface-completeness as a precondition for plateau analysis
  (`_emit_surface` returning `[]` for partial surfaces) is a subtle and correct
  call.
- The DSR implementation matches the paper and is tested against the closed form.
  The inputs are wrong; the mathematics is not.
- Frequency-driven backfill (a quarterly fundamental never gets a 5-day window)
  is the kind of domain detail that most implementations miss.
- The tri-state `passed_all_checks` — refusing to collapse "unscorable" into
  "rejected" — is the right instinct about missing data.

---

## Suggested order of work

**Before anything else** — run the F5 diagnostic. If the stored PnL is
cumulative, every number this system has ever produced is void, and nothing else
on this list matters until it is fixed.

1. **F1** — portfolio = submitted only; cluster siblings before gating. Without
   this the pipeline's output is zero regardless of what else is fixed.
2. **F5** — make PnL reconciliation a hard precondition for promotion.
3. **F2** — stratified sampling of the config space. This is where structural
   diversity, and therefore uncorrelated alphas, actually comes from.
4. **F6** — rank by shrunk score, promote the ridge centre. Two lines.
5. **F3** — replace ratio thresholds with significance tests.
6. **F4** — program-wide trial ledger; replace the log₁₀ haircut with the
   √(2 ln N) form.
7. **F7 / F8 / F9** — batch orthogonality, per-day bandit discount, authoritative
   BRAIN correlation.

A useful discipline for all of it: **the filter needs its own backtest.** Feed
the pipeline synthetic families with known ground truth — some pure noise, some
with a real embedded signal — and measure false-discovery and false-negative
rates end to end. Every threshold in this codebase (0.6, 0.55, 0.40, 0.70, 1.25,
0.95) is currently a number someone chose, not a number someone measured. A
harness that reports "at these settings, 40% of promotions are noise and 37% of
real alphas are rejected" turns all seven findings above into one dashboard, and
turns the next threshold argument into an experiment.
