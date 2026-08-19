# Independent verification task

You are checking someone else's analysis of this database. **Assume it may be wrong.**
Several of these claims were counted by hand from console output rather than queried,
and at least one is likely to be off. Your job is to confirm or refute each one with a
query you ran yourself.

Follow this project's own working style (`CLAUDE.md`):
- **Run queries; do not infer from code.** If you report a number, show the query.
- **Report absences as absences.** `NOT PRESENT` and `CANNOT DETERMINE` are answers.
- Do not tune anything, do not delete anything, do not simulate anything. Read only.

For each claim below, answer exactly one of:
`CONFIRMED` · `REFUTED` (give the real number) · `CANNOT DETERMINE` (say what is missing).

Relevant tables: `alphas` (family_key, decay, truncation, neutralization, status,
feature_json) and `alpha_metrics` (sharpe, fitness, turnover, returns, long_count,
short_count, passed_all_checks, checks).

---

## C1 — Truncation cannot bind on any real family

**Claim:** every family with position counts stored holds at least ~250 positions, so the
largest single position (≈ `2/N` of book for a `rank()` alpha) is at most ~0.8% — below
even the smallest truncation level (0.01). Therefore sweeping truncation is a no-op.

**Check:** min/median of `long_count + short_count` per family. Then `2 / N`.

**Also check the reasoning, not just the number:** is `2/N` actually the right expression
for the maximum weight of a rank-based alpha after neutralization? If the alpha is not
rank-terminated, or if neutralization concentrates weight, this could be wrong. Say so
if it is.

## C2 — The fitness formula is exact

**Claim:** `fitness = Sharpe × sqrt(|returns| / max(turnover, 0.125))` reproduces the
stored `fitness` on real families with mean absolute error ≤ 0.01.

**Check:** recompute per row and compare to stored `fitness`. Report mean and max error.
Exclude the three families named in C5.

## C3 — Passing alphas cluster at low turnover

**Claim:** rows clearing Sharpe ≥ 1.25, fitness ≥ 1.00 and 0.01 ≤ turnover ≤ 0.70 have
**median turnover ≈ 0.14**; rows failing have **median ≈ 0.32**. Roughly half the passing
rows sit at turnover ≤ 0.125.

**Check:** both medians, and the count at or below 0.125.

**Stronger version — please run this instead if you can:** the claim above uses three
bars reconstructed from stored metrics. `alpha_metrics.passed_all_checks` is BRAIN's own
verdict across all eight checks, including ones not reconstructed here
(`CONCENTRATED_WEIGHT`, `LOW_SUB_UNIVERSE_SHARPE`, `MATCHES_COMPETITION`). Redo the
medians using `passed_all_checks = true` and report whether the conclusion survives.
**If it does not survive, that refutes the claim — say so plainly.**

## C4 — Family hit rates (weakest claim, counted by hand)

**Claim:**

| family | simulated | clearing the bars | rate |
|---|---|---|---|
| `single_sector_pureplay_company_count/cap` | 16 | 7 | 44% |
| `max_reported_pretax_profit_quarterly_estimate/cap` | 38 | 15 | 39% |
| `anl4_fs_detail_estimates_basic_qf_delay1_v4_nd_cfps_high/cap` | 24 | 8 | 33% |
| `liabilities/cap` | 61 | 2 | 3% |
| `anl4_fs_detail_estimate_1qf_v4_nd_totgw_median/cap` | 42 | 1 | 2% |

**Check:** counts per family, using `passed_all_checks = true` as the numerator.
These were tallied by eye from console output — treat the individual numbers as
suspect. **The claim that actually matters is the ordering: is `liabilities/cap` really
near the bottom, and is `single_sector_pureplay` really near the top?** Report the
ordering separately from the exact percentages.

## C5 — 147 rows are synthetic test data

**Claim:** `close/cap:ts_zscore:rank`, `close_dedup/cap` and `debug_fam/cap` hold 49
rows each whose metrics are identical across every row, and whose stored `fitness` is
inconsistent with their own `sharpe`/`returns`/`turnover` under the C2 formula
(e.g. 0.50 × sqrt(0.15/0.30) = 0.35, but 1.20 is stored).

**Check:** row counts, distinct metric tuples per family, and the formula mismatch.
Also report `alphas.source` for these rows — if it says `constructor` rather than a test
marker, that is worth knowing.

## C6 — 44 simulations produced no portfolio at all

**Claim:** `fnd6_newqv1300_spcep12/cap` (23 rows) and `fnd6_newqv1300_xoptdq/cap`
(21 rows) have `long_count = short_count = 0` and all-zero sharpe/returns/turnover.

**Check:** confirm counts and zeros. Then answer a question the original analysis did
**not**: does `alpha_metrics.checks` or the raw payload say *why* — an empty field, a
failed simulation, or something else? And do those two fields exist in `data_fields` for
USA / TOP3000 / delay 1 at all?

## C7 — The best family is two-thirds unexplored

**Claim:** `single_sector_pureplay_company_count/cap` has 16 of 49 grid points simulated.

**Check:** distinct `(window, decay)` coordinates present in `feature_json.grid` for that
family, and which are missing. Note that if the family spans more than one structure
(different neutralization / cross-section), 49 is the wrong denominator — report the
per-structure breakdown instead.

---

## Finally

Two open questions the original analysis could not answer. Give your own view:

1. Given the above, is completing `single_sector_pureplay` (≈33 simulations) really the
   best next use of budget, or is there a better call visible in the data?
2. Is there anything materially wrong in this database that none of C1–C7 mentions?

Report refutations plainly and without hedging. A refuted claim is a useful result.
