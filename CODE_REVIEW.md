# Code Review — "Restoring the Promotion Path"

**Reviewed:** `claude/project-feature-review-qxc27g` @ `9958c5e`, diffed against `origin/main`.
**Method:** ran the suite, ran the repro harness six times, ran the calibration backtest, and
drove the pipeline end-to-end through the real API and browser with the **default** PnL store.
Findings below are things I reproduced, not things I inferred from reading.

---

## Verdict

**The core fix is real and I verified it independently.** Driving the product the way a user
does — expand a family, import results through `POST /api/alphas/{id}/results`, write PnL to
the default store, load the console — the morning queue now says **"1 survived"** with a fully
rendered 7×7 heatmap. Before this branch the same exercise produced "Nothing survived the
filter" with 41 of 49 cells invisible. All ten findings are addressed, and the new filter
calibrates honestly on synthetic ground truth (FDR 3.3%, signal survival 86.7%).

Three things need attention before this is done. One is a security-grade hole in the
correlation gate that I was able to demonstrate; the other two are that the *verification*
is weaker than the walkthrough claims, in ways that will let a future regression through.

---

## 1. What I verified as genuinely fixed

Each of these I reproduced rather than read:

| # | Finding | Evidence |
|---|---|---|
| 1 | Promotion path restored | End-to-end via API + default store → `shortlist_total: 1`; console header reads `1 promoted` |
| 2 | Collision message wording | `kind = "submitted" if … else "portfolio"` at `plateau.py:144-149` |
| 3 | Surface axes | Browser: heatmap header row has 8 cells (corner + **7** windows); all 49 cells render; API returns 7×7 |
| 4 | Distinct alpha counters | Header reads `49 alphas · 49 simulated` (was `49 · 98`); re-importing a result leaves `simulated` at 49 — **PASS** |
| 5 | Plateau count | `counts.plateau: 49`; `index.html:629` now `${c.plateau ?? 0}` |
| 6 | README seeding | `seed_all` documented as the offline default at README:161 |
| 7 | CLI error path | `fetch_brain_catalog` with no credentials → 2 stderr lines, exit 1, no traceback |
| 8 | Broken `file://` links | 0 remaining |
| 9 | Empty-catalog message | `report.py:257` advises `seed_all` |
| 10 | favicon / modals | Zero browser console errors; backdrop `mousedown` handler at `index.html:1385` |

The A1 change in `correlation.py` (measured-vs-unmeasured split) and the A2 change in
`plateau.py` (sibling exclusion) are implemented exactly as designed. The `is_recalled`
column and its backfill exist in migration `e0a1b2c3d4e5`, so moving the portfolio
definition to attempt-based does not strand existing data. Good.

The EVT haircut deserves a note in its favour: I initially read the bar as a regression
(2.19 for a 49-point family, versus 1.42 under the old formula). That reading was wrong —
the bar scales on **effective** trials, so a realistic family with ~0.9 intra-correlation
gets a bar of 1.38. My first end-to-end test generated independent PnL for all 49 siblings,
which is not what a grid sweep produces. The adaptive bar is the right design.

---

## 2. Must fix

### H1 — `abs()` was dropped from the correlation gate, so a mirror alpha passes as perfectly diversified

`correlation.py` changed `rho = abs(compute_pairwise_correlation(...))` to signed `rho`, in
both `check_portfolio_empirical_correlation()` and
`compute_max_self_correlation_with_submitted()`. The docstring justifies it as "strong
negative correlation is beneficial diversification."

I built the adversarial case — a candidate whose daily PnL is the **exact negation** of a
submitted alpha (true ρ = −1.00):

```
candidate is an exact negation of submitted alpha #1 (true rho = -1.00)
  gate blocked?      False
  reason:            None
  reported max_corr: 0.0
  self_correlation shown to user: 0.0  (target #None, method empirical)
```

Three separate failures in one:

1. **The gate passes it.** A sign-flipped duplicate of something already in the portfolio is
   the most obvious duplicate that exists, and it is now invisible to the check whose entire
   job is catching duplicates.
2. **`max_corr` can never be negative.** It initialises to `0.0` and updates only on
   `rho > max_corr`, so the "maximum correlation" reported is really "maximum *positive*
   correlation". A candidate at −0.95 reports `0.00`.
3. **The console displays `self_correlation: 0.00`** with `method: "empirical"` — the
   strongest possible evidence of diversification — for a perfect mirror. The operator is
   being told the opposite of the truth at the exact moment they decide whether to submit.

The diversification argument is sound in portfolio theory and wrong here, because this gate
is a conservative proxy for BRAIN's own self-correlation limit. BRAIN measures duplication,
not portfolio variance; submitting `X` and `−X` is not diversification to the platform.

**Fix:** gate on `abs(rho)`, and keep the signed value only for display:

```python
rho_signed = compute_pairwise_correlation(c_vec, p_vec)
rho = abs(rho_signed)
if rho > max_corr:
    max_corr, max_corr_signed = rho, rho_signed
    if rho >= thresh:
        colliding_alpha_id = port_alpha.id
```

Report `max_corr_signed` in the message and in `self_correlation` so the operator still sees
"−0.95" rather than "0.95" — the insight is preserved without the hole. Add a regression test
asserting an exactly-negated PnL series is blocked.

---

## 3. The verification is weaker than the walkthrough claims

None of these are wrong code. They matter because they are what will let H1-class bugs back
in after this branch lands.

### M1 — the reproduction harness is not reproducible

`repro_review_findings.py:135`:

```python
rng = np.random.default_rng(abs(hash((w, d, alpha.id, "repro"))) % (2**31))
```

`hash()` on a tuple containing a **string** is salted per process by `PYTHONHASHSEED`. The
same expression returns a different value on every run:

```
821048613
745695855
859581190
```

So every run generates different PnL. Across six runs I measured **Sub-Period: 33, 35, 35,
36, 37, 40** and **Redundant: 10–11**; the walkthrough reports 39 and 11. Nothing is wrong
with the pipeline — the harness is simply not a fixed baseline, so its printed funnel cannot
be used to detect a regression, and the numbers in the walkthrough cannot be reproduced by
anyone including its author.

**Fix:** seed from the integers only — `default_rng(1000 + alpha.id)` — or pass an explicit
`--seed`. Then assert exact counts (`== 12`), not `>= 9`. The wide margins currently hide
the drift.

### M2 — the harness proves the funnel through a store the product never uses

Line 151 evaluates with an explicit temp store:

```python
verdicts = evaluate(db, spec.family_key(), pnl_store=store)   # store = PnLStore(tmp_dir/"pnl")
```

but the two checks that follow — `build_report(db)` and the `/api/ui/surfaces` request — go
through `get_pnl_store()`, the default. With `require_pnl=True` and no PnL there, they
promote nothing. The harness's own log says so:

```
family_evaluated … promoted=1 redundant=11     <- the funnel assertions
family_evaluated … promoted=0 redundant=0      <- the report it then "verifies"
family_evaluated … promoted=0 redundant=0      <- the surfaces API it then "verifies"
```

The report assertion only checks that the 10-column header string is present, and the
surfaces assertion only checks axis lengths and cell count. Neither asserts a promotion. So
the harness prints **"ALL VERIFICATIONS COMPLETED SUCCESSFULLY"** over a report containing
zero promotions.

The end-to-end behaviour *is* correct — I verified it separately through the default store —
but the harness does not demonstrate it, which is the one thing it exists to do.

**Fix:** monkeypatch the default store (`pnl_storage._STORE` / the `get_pnl_store` factory)
for the whole run instead of threading `pnl_store=` into one call, then assert
`"## Promotion shortlist"` in the report contains a row, and that some cell in the surfaces
payload has `promoted: true`.

### M3 — a wall-clock assertion makes the suite machine-dependent

`tests/test_phase5_statistics.py:33`:

```python
assert elapsed < 0.500, f"CSCV took {elapsed:.4f}s >= 0.500s"
```

It passes in isolation and **fails in the full suite on this machine**:

```
AssertionError: CSCV took 0.9559s >= 0.500s
1 failed, 261 passed, 1 warning in 38.62s
```

So the walkthrough's "262 passed in 7.03s" is not a property of the branch — it is a
property of the machine it ran on. (Suite wall-clock here is 38–44s, not 7s.) A timing
assertion in a unit suite will fail on any loaded CI runner.

**Fix:** assert complexity, not seconds — e.g. that 2× the matrix size costs < 3× the time,
or mark it `@pytest.mark.benchmark` and exclude it from the default run. Keep the accuracy
assertions, which are the valuable half of that test.

---

## 4. Should fix

### M4 — representative grouping silently deviates from decision D3

`_select_representatives` groups by `structure_of.get(v.alpha_id)` — the `SurfacePoint.structure`
tuple `(ts, cs, group, neutralization, truncation)` — not by `structural_hash`. The docstring
and the walkthrough both say "structural skeleton", which is what `structural_hash` means.

These are not the same. A single structure holds several structural hashes (in the original
49-point family I measured 4 hashes inside 1 structure). Grouping by structure is strictly
coarser: it promotes **exactly one alpha per family-structure**, which is the option my plan
called out and rejected as over-merging. The repro shows it — `promoted=1, redundant=11` —
and the calibration run demotes up to 47 of 48 survivors.

This may well be the right product call; a one-line morning queue is defensible. But it is
open question §11.1 from the plan, answered in code without being recorded, and described in
the docs as something else. Decide it explicitly, then make the code and the docstring agree.

### L1 — two sources of truth for "is this alpha submitted"

`submitted_portfolio()` now defines portfolio membership by `SubmissionAttempt.result ==
'submitted' AND NOT is_recalled`, with a good comment explaining that `AlphaStatus.PASSED`
describes a simulation, not a position. But `check_portfolio_correlation` still branches on
`port_alpha.status != AlphaStatus.SUBMITTED.value` for both the sibling skip (line 135) and
the message label (line 146).

Today these agree, because `sync_alpha_platform_outcome` sets `status = SUBMITTED` whenever a
submitted attempt exists, and both attempt endpoints call it — I checked. So this is latent,
not live. But it is the same fact stored twice, and the failure mode if they ever drift is
bad: a genuinely submitted sibling gets skipped and stops blocking. Read the attempt, not
the status, in the function whose portfolio is already attempt-defined.

### M5 / L3 — docstrings that describe a different implementation

- `plateau.py:5` says "**Five** sequential gates" and then lists **seven**.
- `plateau.py:13` says the correlation gate is "evaluated on elected representatives", but
  `_select_representatives` runs at line 507, *after* the correlation gate in the per-point
  loop. Every point is correlation-checked; election happens later.
- `correlation.py:74` says insufficient overlap "fails closed (returns unmeasured/blocking)".
  It doesn't block — it routes to the structural proxy, which passes when the skeletons
  differ.

The third one matters most: it describes a safety property the code does not have.

### L2 — lint debt grew

`ruff check` reports **273 errors on this branch vs 233 on `origin/main`** (+40). Pre-existing
debt dominates, so this is not a new problem, but it is moving the wrong way. Of note:
`plateau.py` imports `check_portfolio_empirical_correlation` at module level (line 39) *and*
again inside `evaluate()` (line 338) — F811. The five `F821 undefined-name` hits
(`Session`, `Path`, `Alpha`) are **runtime-safe**, because every affected file has
`from __future__ import annotations`; they are typing hygiene, not latent crashes.

---

## 5. Next plan

Ordered by what protects the operator soonest.

### P0 — close the correlation hole (H1)
1. Gate on `abs(rho)`; carry the signed value through to the message and to
   `self_correlation` for display.
2. Same change in `compute_max_self_correlation_with_submitted` — the displayed number and
   the gated number must come from the same rule.
3. Regression test: exact-negation PnL against a submitted alpha ⇒ blocked, and the reported
   correlation reads −1.00 rather than 0.00.
4. Grep for any other `compute_pairwise_correlation` call sites that compare against a
   threshold; the family clustering matrix is the likely second one.

### P1 — make the verification trustworthy (M1, M2, M3)
5. Seed the harness deterministically; tighten its asserts from `>=` to exact counts.
6. Point the harness at the default PnL store, and assert the *report* and the *surfaces
   payload* each contain a promotion — not just a header and a cell count.
7. Replace the wall-clock assertion with a scaling assertion, or move it behind a benchmark
   marker. Then re-run and publish the real numbers (I measure 261 + 1 timing failure, 38–44s).

### P2 — settle the decisions that are currently implicit (M4, L1, M5, L3)
8. Decide representative granularity: per structure (today, 1 per family) or per structural
   hash (≈4 per family). Record it in `OPEN_DECISIONS.md`, then align code + docstring.
9. Make `check_portfolio_correlation` read submission attempts rather than `Alpha.status`.
10. Correct the three docstrings, especially the "fails closed" claim.

### P3 — calibrate against the portfolio you actually have
11. The synthetic calibration is good work, but you have ~10 real submitted alphas with
    stored PnL and a 10×10 correlation matrix. Run the filter over them: **how many of your
    own accepted alphas would this stack promote today?** A stack that rejects alphas BRAIN
    already accepted is mis-tuned no matter how clean the synthetic scorecard is. That single
    number is the most informative thing left to measure, and it is the natural next use of
    `scripts/calibrate_filter.py`.
12. Then take the lint debt down in one mechanical pass (`ruff check --fix` clears 219 of 273).

---

## 6. Note on the walkthrough itself

Three claims in it do not survive checking, all in the same direction — they describe the
verification as stronger than it is:

- "262 passed in 7.03s" → I measure **261 passed, 1 failed, 38–44s** (M3).
- The repro funnel figures (Sub-Period 39) → **not reproducible**; varies 33–40 by run (M1).
- "one representative per distinct structural skeleton" → the code groups by **structure
  tuple**, which is coarser (M4).

The underlying work is better than the walkthrough needs it to be. Fixing H1 and the
verification gaps would make the claims true as written.
