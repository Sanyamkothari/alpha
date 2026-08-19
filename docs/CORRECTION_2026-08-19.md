# Correction — the family recommendation was wrong

An independent query-backed audit refuted the central recommendation of
`docs/RESEARCH_2026-08.md` and the analysis that followed it. This records what was
wrong, why, and what replaces it. The audit's verdicts are the authority here; the
earlier documents are superseded on every point below.

## The error

I recommended spending ~33 simulations completing
`single_sector_pureplay_company_count/cap`, on the grounds that it cleared the bars 44%
of the time — the best rate in the database.

**It clears 0 of 16.** It is the *worst* family, not the best. Every one of its 16
simulations fails `LOW_SUB_UNIVERSE_SHARPE` (value 0.76 against a limit reported as
0.80). Had that recommendation been followed, 33 simulations would have gone into a
family that cannot pass.

## Why it happened

`scripts/diagnose_settings.py` reconstructed "passing" from stored scalars — Sharpe,
fitness, turnover. BRAIN applies **eight** checks. Four of them
(`LOW_SUB_UNIVERSE_SHARPE`, `CONCENTRATED_WEIGHT`, `MATCHES_COMPETITION`,
`SELF_CORRELATION`) leave no reconstructable trace, so a reconstruction can only ever
overstate. `alpha_metrics.passed_all_checks` — BRAIN's own verdict across all eight —
was sitting in the same table, unused.

The failure mode is specific and worth naming: **a partial reconstruction of a gate is
not a conservative approximation of it, it is a systematically optimistic one.** Every
check you cannot see is a check you implicitly mark as passed.

This is a second instance of a pattern already in `CLAUDE.md`: the drift incident
happened because local state was trusted over the platform's. Here a local
reconstruction was trusted over the platform's stored verdict.

## Verdicts

| Claim | Verdict |
|---|---|
| C1 — truncation cannot bind on any real family | **CONFIRMED** — smallest family holds 255 positions, max weight 0.78% < 1% |
| C2 — fitness formula is exact | **CONFIRMED** — max error 0.005, mean 0.003 over 288 rows |
| C3 — passing alphas cluster at low turnover | **REFUTED as stated** — pass median 0.139 holds, but failing median is 0.20, not 0.32, and 43% sit at ≤0.125, not "roughly half" of all rows (8.7% once synthetic rows are included) |
| C4 — family hit rates | **REFUTED** — ordering inverted; see below |
| C5 — 147 synthetic rows | **CONFIRMED** on counts and formula mismatch; metrics are *predominantly*, not strictly, identical (5 distinct tuples in one family), and two of the three carry `source='constructor'`, not a test marker |
| C6 — 44 simulations produced no portfolio | **CONFIRMED**, and the cause identified: both fields exist with 0.5 coverage and the simulator ran 85–112s — the expression genuinely evaluates to nothing in this universe |
| C7 — best family two-thirds unexplored | **REFUTED** — the family spans 5 structures × 24 coordinates (~120 points), not one 49-point grid, and all 16 simulations sit in the SUBINDUSTRY structure |

## The corrected ranking

On `passed_all_checks`, BRAIN's own verdict:

| family | simulated | passed | rate |
|---|---|---|---|
| `max_reported_pretax_profit_quarterly_estimate/cap` | 38 | 15 | **39.5%** |
| `anl4_…_cfps_high/cap` | 24 | 8 | **33.3%** |
| `liabilities/cap` | 61 | 2 | 3.3% |
| `anl4_…_totgw_median/cap` | 42 | 1 | 2.4% |
| `single_sector_pureplay_company_count/cap` | 16 | **0** | **0.0%** |

The one thing the earlier analysis got right is that `liabilities/cap` is near the
bottom. The families with demonstrated yield are `max_reported_pretax_profit` and
`anl4_…cfps_high`.

## What C3's refutation does and does not touch

The **mechanism** is arithmetic and survives: `fitness = Sharpe × √(returns /
max(turnover, 0.125))` means turnover below 0.125 buys no fitness, and at that floor the
Sharpe needed for fitness ≥ 1.0 falls to roughly the `LOW_SHARPE` bar itself.

What is refuted is the **strength of the evidence** offered for it. The gap between
passing and failing turnover is 0.139 vs 0.20 — real, but a third of what was claimed,
and partly an artefact of 147 synthetic rows sitting in the pass band.

**Do not build a strategy on the floor without first checking how low-turnover alphas
fare on `LOW_SUB_UNIVERSE_SHARPE`.** That check sank the last recommendation and has
never been measured against turnover.

## Changes made

- `scripts/diagnose_settings.py` now treats `passed_all_checks` as authoritative, names
  the specific checks BRAIN failed an alpha on, and prints an explicit warning that
  reconstruction sees four of eight checks and overstates.
- `docs/BRAIN_API.md` marks the `LOW_SUB_UNIVERSE_SHARPE` limit as **contested** —
  recorded there as 0.01 from a single TUTORIAL-account observation, observed as 0.80 in
  stored check payloads.
- `scripts/purge_synthetic_alphas.py` reports fields that have been simulated and never
  produced a book. The audit found **686 alphas** referencing the two dead fields against
  only 44 simulated — the queued remainder is the larger waste.

## Open, and not to be guessed at

1. **What is the real `LOW_SUB_UNIVERSE_SHARPE` limit?** One authenticated run settles it.
   It gates a check that has already invalidated one recommendation.
2. **Does low turnover trade against sub-universe Sharpe?** If it does, the 0.125-floor
   idea is worth less than it looks.
3. **Why do `fnd6_newqv1300_*` evaluate to nothing** despite 0.5 stated coverage? 686
   alphas are queued behind the answer.
