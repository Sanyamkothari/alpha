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

Config fingerprint `9298528f5622`, 500 null families and 200 signal families,
`programme_trials = 400`, five-year backtest (1 236 days):

| Measure | Result | 95% interval | Criterion |
|---|---|---|---|
| False-discovery rate (null families promoting) | **2.40%** (12/500) | 1.38% – 4.15% | A4: < 5% ✓ |
| Power at true Sharpe 1.50 | **88.5%** (177/200) | 83.3% – 92.2% | A3: ≥ 85% ✓ |

Where candidates died:

| Outcome | Null families | Signal families |
|---|---|---|
| promoted | 12 | **177** |
| died at haircut bar | 276 | 22 |
| died at plateau test | 212 | — |
| died at sub-period | — | 1 |

The two gates are doing distinguishable work: the plateau test and the bar each
kill roughly half the nulls, while the same stack passes seven of every eight
real alphas.

---

## Why 500 replications, and not 20

The shipped test asserted A4 from **20** null families and reported "0.0%".
Zero out of twenty bounds the true rate at 13.9% with 95% confidence — it cannot
distinguish 0% from 13%.

The measured rate is **2.40%**. A 60-replication run of the same configuration
returned 0/60. Both are consistent with the truth; neither is a measurement of
it. Anything asserting "< 5%" needs at least 60 replications to be expressible
and ~500 to be estimated.

---

## The finding that mattered: a perfect false-discovery rate meant nothing

The first calibration run of the corrected bar produced this:

| `bar_form` | programme trials | FDR | power @ SR 1.5 |
|---|---|---|---|
| `debias` (shipped) | 400 | 0.0% | **0.0%** |
| `debias` | 1 000 | 0.0% | **0.0%** |
| `debias` | 4 000 | 0.0% | **0.0%** |
| `noise_floor` | 400 | 0.0% | 93.3% |
| `noise_floor` | 1 000 | 0.0% | 85.0% |
| `noise_floor` | 4 000 | 0.0% | 58.3% |

*(60 replications each — the exploratory sweep, superseded by the 500-run above.)*

A gate that promotes **nothing** scores a flawless false-discovery rate. The
shipped operating point was a degenerate classifier, and the A4 test it passed
was structurally incapable of noticing, because it only ever asked whether false
things got through.

**A4 is a constraint. A3 is the objective.** They are only meaningful together,
which is why `test_filter_is_not_a_degenerate_classifier` now runs in the fast
lane.

### The two readings of a multiple-testing bar

```
noise_floor:  bar = max(target, se * E[max N])     <- shipped default
debias:       bar = target + se * E[max N]
```

- **noise_floor** asks two questions and takes whichever binds: *is this
  distinguishable from the best of N noise draws*, and *is it economically worth
  submitting*.
- **debias** asks whether the alpha's **true** Sharpe clears the target after
  subtracting the selection inflation a winner carries — where that inflation is
  estimated under the null.

`debias` is the stricter and, in isolation, the more principled statement. It is
also unusable: it charges the full null-case inflation against an alpha that has
real skill, and on a five-year backtest nothing survives it. The project shipped
`debias`; the measurement is why the default is now `noise_floor`.

Note where the correction starts to bite. Below roughly **200 effective trials**
the economic target binds and the bar does not move at all — searching harder is
free. Past that, every additional order of magnitude of search costs real power.

---

## Searching more costs real alphas

From the same sweep, holding true Sharpe at 1.50:

| Effective trials | Bar | Power |
|---|---|---|
| 400 | 1.28 | 93.3% |
| 1 000 | 1.41 | 85.0% |
| 4 000 | 1.58 | 58.3% |

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
