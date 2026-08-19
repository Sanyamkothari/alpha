# Filter Calibration

Every threshold in the gate stack used to be a number someone chose. This is the
record of measuring them. Reproduce with:

```bash
cd backend && .venv/bin/python -m pytest -m slow          # the acceptance runs
cd backend && .venv/bin/python -m scripts.calibrate_filter # the full scorecard
```

The harness (`app/services/filter_backtest.py`) generates families with **known
ground truth** — pure-noise families and families carrying a specified true
Sharpe, with sibling correlation of 0.92 built through a common-factor blend
because adjacent grid points really are the same alpha nudged — and runs the
complete gate stack over them. It scores the filter as what it is: a classifier.

---

## The operating point

Config fingerprint `7d5bc0c4b50f` — `bar_form="noise_floor"`, `dsr_threshold=0.70`.
500 null families and 200 signal families, `programme_trials = 400`, five-year
backtest (1 236 days):

| Measure | Result | 95% interval | Criterion |
|---|---|---|---|
| False-discovery rate (null families promoting) | **2.40%** (12/500) | 1.38% – 4.15% | A4: < 5% ✓ |
| Power at true Sharpe 1.50 | **88.5%** (177/200) | 83.3% – 92.2% | A3: ≥ 85% ✓ |

Where families died:

| Outcome | Null | Signal |
|---|---|---|
| promoted | 12 | **177** |
| died at haircut bar | 280 | 22 |
| died at plateau test | 208 | — |
| died at sub-period | — | 1 |

The gates do distinguishable work: the plateau test and the bar each kill roughly
half the nulls, while the same stack passes seven of every eight real alphas.

---

## Why 500 replications, and not 20

The shipped test asserted A4 from **20** null families and reported "0.0%".
Zero out of twenty bounds the true rate at 13.9% with 95% confidence — it cannot
distinguish 0% from 13%.

The measured rate is **2.40%**. A 60-replication run of the same configuration
returned 0/60. Both are consistent with the truth; neither measures it. Anything
asserting "< 5%" needs at least 60 replications to be expressible and ~500 to be
estimated.

---

## Finding 1: a perfect false-discovery rate meant nothing

The first calibration run of the corrected bar produced this:

| `bar_form` | programme trials | FDR | power @ SR 1.5 |
|---|---|---|---|
| `debias` (shipped) | 400 / 1 000 / 4 000 | 0.0% | **0.0% / 0.0% / 0.0%** |
| `noise_floor` | 400 / 1 000 / 4 000 | 0.0% | 93.3% / 85.0% / 58.3% |

*(60 replications each — the exploratory sweep.)*

A gate that promotes **nothing** scores a flawless false-discovery rate. The
shipped operating point was a degenerate classifier, and the A4 test it passed
was structurally incapable of noticing, because it only ever asked whether false
things got through.

**A4 is a constraint. A3 is the objective.** They are only meaningful together,
which is why `test_filter_is_not_a_degenerate_classifier` runs in the fast lane.

### The two readings of a multiple-testing bar

```
noise_floor:  bar = max(target, se * E[max N])     <- default
debias:       bar = target + se * E[max N]
economic:     bar = target                          <- no correction in the bar
```

`debias` charges the full null-case selection inflation against an alpha that
has real skill; on a five-year backtest nothing survives it. Below roughly
**200 effective trials** the economic target binds under `noise_floor` and the
bar does not move at all — searching harder is free until then.

---

## Finding 2: the DSR and the bar were correcting for the same thing twice

Once the ledger estimated `sigma_SR` and `N_eff` from one population (below), the
DSR began deflating for the same programme-wide selection the bar already
deflates for. Applying both as AND gates charges the multiple-testing correction
twice, and the DSR became the binding gate:

| `bar_form` | FDR | power @ SR 1.5 |
|---|---|---|
| `economic` (no correction in the bar) | 0.0% | 36.0% |
| `noise_floor` | 0.0% | 36.0% |

*(100 replications, `programme_trials=400`, `dsr_threshold=0.95`.)*

Identical — because the DSR, not the bar, was doing the rejecting.

### Choosing the DSR threshold on evidence

150 null and 150 signal families, `programme_trials=400`:

| `dsr_threshold` | FDR | 95% upper | power @ SR 1.5 |
|---|---|---|---|
| **0.70** | **0.67%** | 3.7% | **85.3%** |
| 0.80 | 0.00% | 2.5% | 73.3% |
| 0.90 | 0.00% | 2.5% | 63.3% |
| 0.95 | 0.00% | 2.5% | 46.0% |

The false-discovery rate is indistinguishable from zero across the whole range
while power falls by 39 points. 0.95 is the right convention when DSR is the
*only* multiple-testing control; here it is the last of five gates, and what
matters is the stack's joint false-discovery rate, not this gate's confidence
level in isolation.

**On the discipline:** A3 (≥ 85% power) and A4 (< 5% FDR) were written into
`docs/IMPLEMENTATION_PLAN.md` before any of this was measured. Selecting an
operating point against pre-declared criteria is calibration. Running variants
until one looks good and then declaring the criterion afterwards is the error
`docs/strategy/VALIDATION_PROTOCOL.md` exists to prevent, and it is not what
happened here — the full curve is above, including the points that fail.

---

## Finding 3: the ledger's two statistics described different populations

`sigma_SR` was estimated from per-family **mean** Sharpes, and `N_eff` by
extrapolating a ratio from a sample of families out to every simulated alpha.

- Averaging within a family shrinks dispersion by roughly `1/√n`. That
  understates `sigma_SR`, lowers `SR*`, and makes the DSR lenient.
- The extrapolation counted thousands of `rho ≈ 0.95` siblings as independent
  chances to be fooled. That overstates `N_eff`, and the bar with it.

Two biases, opposite directions, landing in different gates — so their net effect
on promotions was not predictable from either one.

Both now come from a single population: the **family-maximum** alphas, one per
family, which is what selection actually operates on. Nobody submits a family's
average. `N_eff` is measured directly from their correlation matrix rather than
scaled, and unmeasurable families each count as one trial. Measured on a
synthetic 48-alpha, 4-family database with siblings at `rho = 0.95`: `N_eff` =
**3.99**, against the 48 the old estimator would have implied.

---

## Searching more costs real alphas

From the same sweep, holding true Sharpe at 1.50:

| Effective trials | Bar | Power |
|---|---|---|
| 400 | 1.28 | 93.3% |
| 1 000 | 1.41 | 85.0% |
| 4 000 | 1.58 | 58.3% |

*(60 replications each, at `dsr_threshold=0.95`; the shape is what matters here,
not the levels, which the threshold change above shifts upward.)*

This is the quantitative form of STRATEGY.md's argument for diversity. The bar
deflates for *effective* trials, so 4 000 near-duplicate variants of one
mechanism cost the same power as 4 000 genuinely independent ideas while
producing far fewer chances to find something. **Correlated volume is paid for
twice**: once in the bar it raises, once in the alphas it does not contain.

The lever is `N_eff`, not `N`. Fewer mechanisms explored more diversely beats
more variants of the same one — and that is now a measured statement rather than
a stylistic preference.

---

## Caveats

- Synthetic PnL is i.i.d. normal around a target Sharpe. Real alpha returns are
  fat-tailed and autocorrelated, so the sub-period gates will be somewhat
  harsher in production than they measure here. The DSR's skew/kurtosis terms
  handle the alpha under test but the harness does not exercise them hard.
- Sibling correlation is fixed at 0.92. Real families vary, and the dedup step's
  behaviour depends on it.
- `programme_trials` is supplied to the harness rather than measured from a real
  ledger. Once `build_ledger` runs against the production database, re-run this
  with the observed `N_eff` — which is the number that decides the real bar.
- All families are single-structure with a complete 49-point surface. Partial
  surfaces are common in production and are judged by the same gates.
