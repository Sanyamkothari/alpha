# Open-Source Landscape Review — Round 2

Survey date: 2026-08-21. Companion to `OPEN_SOURCE_LANDSCAPE.md`, which it corrects in two places.

Method: nine parallel domain sweeps, then four adversarial verification passes under distinct
lenses (external-claim, repo-claim, constraint, phase-discipline). 96 findings raised, 75 sent to
verification, **47 confirmed, 23 plausible, 3 refuted**. Only confirmed findings are reported below
unless flagged otherwise. Every external claim carries a URL that was fetched; every claim about our
code carries a path that was read.

Tags as before: **[P1-SAFE]** costs no simulation budget and touches no frozen filter · **[P2]**
deferred · **[NO]** do not adopt.

---

## 0. Corrections to Round 1

**The 13/day throughput question is already answered in our own docs.** Round 1 flagged it as
CANNOT DETERMINE. `docs/PHASE1_OPERATING_GUIDE.md:11-15` states the submission quota is 4/day and
explicitly *not* binding (~480 possible attempts against a target of 40), and prescribes a deliberate
ramp — 50/day → 100/day → 200/day, gated on confirming no rate limiting. It names the real constraint:
"upstream candidate diversity, coordinate exploration, and avoiding self-correlation collisions."
13/day is a historical average across the build phase, not a ceiling. **This makes the diversity
findings below more central, not less.**

**Round 1's claim that we have no expression dedup was wrong.** `backend/app/models/alphas.py:57`
declares `expression_hash` with a unique index. Exact-string dedup exists; what is missing is
*semantic* equivalence (§5.2).

---

## 1. Two of the three open questions in CLAUDE.md now have answers

`CLAUDE.md` lists three questions "needing a human". Two are substantially answerable from public
sources. Neither answer is first-hand, and both should be confirmed against our own account before
being relied on — but both are far better than the current blank.

### 1.1 "BRAIN submission quota per week" — the platform publishes it as a number [P1-SAFE]

`GET /alphas/{alpha_id}/check` returns an `is.checks[]` array that includes:

```json
{"name": "REGULAR_SUBMISSION", "result": "PASS", "limit": 4, "value": 0}
```

`limit` is the ceiling, `value` the count already consumed. Confirmed in a captured response body in
[untuitivist/wqb_cli's API inventory](https://github.com/untuitivist/wqb_cli), implemented as
`_is_regular_submission_quota_full(name, data) → limit > 0 and value >= limit` in
[Cgodking/Alpha-agent](https://github.com/Cgodking/Alpha-agent), and independently visible in two ACE
bootcamp notebooks showing the same check at limit 4.00.

*Verified about us:* `backend/app/models/enums.py:61` enumerates 12 check names and does **not**
include `REGULAR_SUBMISSION`. A grep of `backend/app` for `/check` returns nothing — we never call
that endpoint. Our client covers 7 endpoints; the community inventory documents ~60.

This corroborates the "4/day confirmed" already in our operating guide, and upgrades it from a
human's note to a value the machine can read before every attempt. Caveat, stated by the verifier:
every observation comes from other people's accounts at unknown tiers, and the reset window is
genuinely unsettled (one source models a 48h rolling window, the same source says per-calendar-day).

### 1.2 "Does BRAIN check PROD_CORRELATION against the platform pool?" — yes, separately [P1-SAFE]

Two distinct endpoints exist, per [ace_lib.py](https://github.com/lavender1203/worldquant-alpha-aiac):

- `GET /alphas/{id}/correlations/self` — returns `records` with one row **per one of your own alphas**.
- `GET /alphas/{id}/correlations/prod` — returns a frame whose column is `alphas`, a **count per
  correlation bucket**. `check_prod_corr_test()` computes
  `value = prod_corr_df[prod_corr_df.alphas > 0]['max'].max()` and passes if `value <= 0.7`.

That the prod endpoint returns counts-per-bin rather than identifiable rows means it is an
**anonymised histogram of your correlation against other users' live production book**. The two
appear as independent entries in the same `checks[]` array with independent values — one captured
alpha shows `SELF_CORRELATION` PASS 0.1465 alongside `PROD_CORRELATION` PASS 0.6514.

**So research ground is shared between users, which is the premise the entire product plan rests on.**

*Verified about us:* `docs/BRAIN_API.md` already records this endpoint returning 403 on our Tutorial
account. `enums.py:66` has the enum member; nothing populates it.

The honest limit: this is inferred from a client library's DataFrame construction, not from a response
body we have seen, and it 403s at our tier — so we still cannot pre-screen against the production pool.
What changes is that the *premise* is no longer unverified folklore.

The third question (past attempts and which check failed) is unchanged: run
`scripts/record_past_attempts.py`.

---

## 2. The most consequential findings are about Phase 1's own statistical design

These do not require adopting anything external. They are arithmetic about the plan we already have,
and they are the findings most likely to change what happens next.

### 2.1 The futility rule kills a working machine about a third of the time [P1-SAFE]

`docs/PHASE1_OPERATING_GUIDE.md:120-125` lists as a stop condition: **"Pass rate under 10% at 25+
attempts."** `:29` ("The weekly rhythm") establishes a weekly review, which turns that point estimate into a sequential
rule applied at every n from 25 to 40.

Exact binomial computation. At n=25 the rule fires when X ≤ 2. P(X ≤ 2 | n=25): **0.537 at p=0.10,
0.254 at p=0.15**, 0.098 at p=0.20. Applied repeatedly across n ∈ [25, 40] (200k paths, fixed seed):

| True pass rate | P(stop for futility) |
|---|---|
| 0.15 | **0.335** |
| 0.20 | 0.134 |
| 0.25 | 0.044 |

**A genuinely 15%-effective system gets shut down one time in three.** The rule has no error rate
attached to it anywhere in the document.

The counter-argument is real and worth stating: a 15% pass rate may not be worth four more months
regardless of certainty, so this may be an economic threshold wearing statistical clothing. If so it
should say that, because as written it reads as a measurement.

### 2.2 The ±15% figure is right only at p = 0.5, and the roadmap table is computed at 92.8% confidence [P1-SAFE]

`CLAUDE.md:63`, `docs/PHASE1.md:5`, `docs/briefs/brief-phase1.md:13` and `docs/strategy/ROADMAP.md:82-87`
all assert ±15% at n=40 with no stated p and no stated interval method.

Computed independently, 95% two-sided half-widths at n=40:

| p̂ | Wald | Wilson | Clopper-Pearson |
|---|---|---|---|
| 0.50 | 15.5% | **14.8%** | 16.2% |
| 0.25 | 13.4% | 13.0% | 14.3% |
| 0.10 | 9.3% | 9.6% | — |

So ±15% is the **worst case**, and at a plausible low pass rate the estimate is meaningfully tighter
than advertised. At p̂=0.10 the interval is [0.040, 0.231] — a 5.8× ratio, not a symmetric ±.

Separately: ROADMAP's table (21/36/81 submissions for ±20/15/10%) fits `n = 0.81/m²` exactly.
`0.81 = z²·0.25` implies **z = 1.800, i.e. 92.8% confidence, not 95%**. The correct 95% figures are
n=43 (Wald) or n=39 (Wilson). This is a rounding-level error and changes nothing operationally, but
the number is quoted in four documents as though it were exact.

### 2.3 A sequential test could answer the phase question in ~12 attempts instead of 40 [P1-SAFE]

Wald SPRT for H₀: p=0.10 vs H₁: p=0.40 at α=β=0.05. Log-likelihood increments +1.386 per pass,
−0.406 per fail; boundaries ±2.944. **Average sample number: 11.7 under H₀, 8.5 under H₁.**
Eight consecutive failures alone crosses the lower boundary.

My own fixed-n arithmetic agrees on the shape — one-sided 95% Clopper-Pearson upper bounds:

| attempts | 0 passes | 1 | 2 |
|---|---|---|---|
| 14 | rules out p ≥ 20% | | |
| 22 | | rules out p ≥ 20% | |
| 29 | rules out p ≥ 10% | | |

**But those fixed-n numbers do not survive the weekly peek**, and this is the correction that matters.
Peeking at a fixed-n 95% CI weekly over 16 weeks gives **19–27% non-coverage**. The honest instrument
is an anytime-valid confidence sequence (Howard/Ramdas; Robbins/Lai mixture martingale), which costs
about **1.36× the width**. Under it, with zero passes the 95% upper bound reaches 0.10 only at
**t=69** attempts and 0.05 at t=157 — so "the rate is below 10%" is *not* honestly available at n=25
at all, which is exactly what §2.1 found from the other direction.

Practical note from the sweep: `confseq` has no wheel for Python 3.11+ and pulls pandas/matplotlib,
and Python group-sequential tooling is thin. The Beta-mixture confidence sequence is ~20 lines of
numpy; adopting a dependency is not the move.

### 2.4 The 40 trials are not iid, so "the true pass rate" is not an identified parameter [P1-SAFE]

*Verified:* `correlation.py:27` `submitted_portfolio()` selects on `SubmissionAttempt.result ==
'submitted'`; `correlation.py:177` and `:57` take the **max** over that portfolio against
`INTERNAL_CORRELATION_THRESHOLD = 0.55`. The portfolio only grows, and a max over a growing set is
monotone non-decreasing — **so every success mechanically lowers the pass probability of every later
attempt.** CLAUDE.md's own "roughly one submittable alpha per field" is the same statement.

There is therefore no fixed p, and S₄₀/40 estimates neither the initial nor the terminal rate.
`VALIDATION_PROTOCOL.md:15-29` corrects for clustering *within* a territory but not for this
sequential self-inhibition *across* attempts.

How much this bites depends on whether attempts draw from fresh ground; at 0.49% field coverage it
may be negligible over 40 attempts. **CANNOT DETERMINE without the DB** — but the estimand should be
stated as a running average of conditional means rather than a constant.

### 2.5 Two different quantities are both called "the pass rate" [P1-SAFE]

The phase measures *BRAIN accepts the submission*. The business question is *the alpha earns*. `CLAUDE.md`
already records 6 submitted, **0 known accepted/paid**. These are different Bernoulli parameters and the
documents use one name for both.

---

## 3. Dead code that changes the meaning of a result

Round 1 found `calibrate_proxy_rankings()` had no caller outside its own test. That was not an
isolated case. This is the project's own stated concern — *"distinguish code exists from code runs
from code has been used"* — and it now has five instances.

| Module | Status | Consequence |
|---|---|---|
| `subperiod.compute_effective_trials()` | callers: `tests/test_subperiod.py` only | **DSR is deflated by a raw count of near-duplicate grid points** (§3.1) |
| `evolution.compute_evolutionary_fitness()` | callers: own test only | GP has no fitness gradient (§3.2) |
| `allocator.DiscountedThompsonSampler` | callers: tests only; §148 labels it "Backward Compatibility" | `test_e2e_pipeline.py:153-157` tests a path production never takes |
| `allocator.SimulationBudgetOrchestrator` | as above | its `daily_budget=15` default is *not* the 13/day explanation |
| `proxy_calibration.calibrate_proxy_rankings()` | callers: own test only | the surrogate study was built and never run |
| point-in-time crowding lookup | zero production callers | silently substitutes present-day values for history |

### 3.1 DSR's multiple-testing haircut is counting the wrong N — inside a frozen filter [P1-SAFE to measure]

*Verified:* `subperiod.py:40` defines `compute_effective_trials(correlation_matrix)` implementing
N_eff = M²/Σλᵢ² from the eigenvalues. Its only callers are `tests/test_subperiod.py:37,42,49`.
`plateau.py:326` calls `compute_dsr(daily_pnl, daily_sharpes)` **with no `n_eff` argument**, so
`subperiod.py:90` falls through to `n_trials = max(1, len(sharpes_clean))` — the raw count of
simulated family members.

Given the monoculture (4,608 of 5,177 alphas on one template), those trials are near-perfectly
correlated, so the raw count overstates the number of independent trials. This is precisely what
López de Prado & Lewis's ONC clustering exists to fix, and the file **already implements the
correction it does not use**.

This sits inside a frozen filter, so: do not change it. But computing both numbers side by side on
existing data is measurement, costs no simulations, and tells you how large the discrepancy is. The
verifier's objection stands — with 486 simulated alphas spread across families, most families will
have too few PnL-bearing members for a stable correlation matrix, so N_eff may be too noisy to use.
That is itself worth knowing.

### 3.2 The genetic programming loop has no fitness-based selection, and has never run [P1-SAFE]

*Verified:* `evolution.py:208` is `parent_alpha, parent_node = random.choice(parent_asts)` — uniform.
The parent list comes from `scripts/run_evolution.py` ordering by Sharpe and truncating to 16. So the
scheme is truncation-to-16 then **uniform sampling**, with no gradient inside the 16, no elitism, no
aging replacement, and no population persisting between runs. `compute_evolutionary_fitness()` — the
DSR × complexity × turnover × orthogonality function — is called only by its own test.

Compare PySR's `reg_evol_cycle`: probabilistic tournament (`tournament_selection_n=15`,
`tournament_selection_p=0.982`, place k drawn with weight p(1−p)ᵏ), crossover at 0.2, and replacement
of the **oldest** member by birth timestamp rather than the worst — aging replacement being precisely
what stops one early winner's lineage from taking over.

**But the refutation pass found the decisive fact:** `docs/INVENTORY.md:39` records
`SELECT COUNT(*) FROM alphas WHERE generation > 0;` → **0 rows**. The evolution module has never
produced a single alpha. Fixing its selection pressure is therefore not urgent; knowing it is inert
is what matters, and any future claim resting on "we evolve alphas" is currently false.

---

## 4. What the architecture family does that we do not

Round 1 established that we belong to the LLM-proposes / deterministic-evaluator-scores family.
Round 2 identifies the specific mechanism that family runs on, which we do not.

### 4.1 Score-conditioned prompting is the core mechanism, and our LLM sees no scores [P1-SAFE]

[FunSearch](https://github.com/google-deepmind/funsearch)'s `config.py`: `functions_per_prompt=2`,
`num_islands=10`, `samples_per_prompt=4`. `programs_database.py` samples two prior implementations,
**sorts them by score in ascending order** (`indices = np.argsort(scores)`), and inserts them into the
skeleton — so the LLM literally reads `priority_v0` (worse), then `priority_v1` (better), and writes
`v2`. AlphaEvolve generalises this; its prompt includes "Rendered evaluation results: usually this
will include a program, the result of executing that program, and the scores assigned by the evaluate
function." Its ablation "no context in the prompt" is one of only five run, and it degrades results
materially.

*Verified about us:* `field_triage.py` is the only LLM call site in the app. It sends a static
`SYSTEM_PROMPT` plus `field_code: description` strings at `temperature=0.0` and returns one boolean
per field. `campaign_runner.py` has zero LLM references. **No simulated alpha, Sharpe, DSR, plateau
result or BRAIN failure reason is ever placed in an LLM context anywhere in the repo.**

Sharper still: `field_triage._pending()` filters to `classification_confidence is None`, so a field
triaged "usable" that then produced 50 dead simulations is **structurally unable to be revisited** —
the open loop is enforced by the query, not merely unimplemented.

The argument against, which I think is strong: our LLM's output surface is one bit per field
(`allocator.py:491` consumes `> 0.5`), so feeding it outcomes can only re-rank fields — it cannot
change expression structure, which is where the monoculture actually lives. And with 486 sims over 32
fields, per-field outcome counts are tiny and mostly zero; a model shown "0 passed of 12" will
narrativise noise. That is exactly the failure `field_triage.py:15-25` explicitly refuses. Feeding
outcomes back also makes field selection outcome-dependent, contaminating the Phase 2 causal study
for every field the exploit arm touches — though not the random stratified arm.

**Recommendation: do not close this loop during Phase 1.** Record it as the main Phase 2 design input.

### 4.2 Our "territory" is a genotype, not a behaviour — and we already store the phenotype [P1-SAFE]

This is the sharpest structural finding of the round.

In MAP-Elites and its descendants, the archive is indexed by a **behaviour descriptor measured from
the evaluation output**. [pyribs](https://github.com/icaros-usc/pyribs)' `ProximityArchive` makes it
explicit: novelty ρ(x) = mean Euclidean distance in measure space to the k nearest archive members;
insert only if ρ(x) > `novelty_threshold`; with `local_competition=True` a below-threshold solution
may still replace its nearest neighbour if its objective is higher.

*Verified about us:* `constructor.py:238-251` `canonical_territory_key(field_code, operator_family,
horizon_band, region, universe, delay)` — **every axis is a design choice known before simulation.**
The genuine behaviour vector exists: `pnl_storage.py:64` `get_aligned_matrix()` returns date-aligned
daily PnL and `correlation.py:49` turns it into a pairwise distance. Their only joint caller is a
**display endpoint**, `routers/ui.py:630-655`, restricted to `status IN (SUBMITTED, PASSED)`.

So we hold the ideal behaviour descriptor and use it exclusively as a post-hoc rejection gate. Nothing
selects, archives, or explores by it.

Two supporting findings sharpen the arithmetic: the territory space has ~138,000 cells against a
lifetime budget of ~1,500 evaluations — a coverage ceiling near 1%, which is the exact failure
CVT-MAP-Elites was invented for — and the allocator never reads back any per-territory outcome, so
territory functions as an *exclusion key*, not an archive.

Related and independently interesting: FunSearch dedups by **score signature**, not source text —
programs with identical behaviour collapse into one cluster and get one vote, not 384. Our
`expression_hash` dedups by string, so 384 near-duplicate grid points still count as 384.

**This is Phase 2 work** — it would change what the allocator optimises. But the *measurement* is
P1-SAFE and worth doing: compute archive coverage and behavioural novelty from PnL vectors we already
hold, and find out whether the 30% random arm is actually reaching distinct behaviour or just
distinct coordinates.

### 4.3 Numerai already ran our exact design and deleted it [P2]

Numerai's "originality" check was a hard correlation rejection gate — structurally identical to our
0.55 threshold. They **removed it**, replacing it with a continuous contribution score.

Their MMC has a closed form we could apply to our stored PnL today
([numerai-tools/scoring.py:292-370](https://github.com/numerai/numerai-tools/blob/master/numerai_tools/scoring.py)):
rank → gaussianise → `orthogonalize(v, u) = v - np.outer(u, (v.T @ u)/(u.T @ u))` → score the residual.
The docstring states the identity that makes it genuinely marginal: MMC is 100% correlated with
`pearson(t, 0.999·meta_model + 0.001·predictions) − pearson(t, meta_model)` — the derivative of pool
quality with respect to admitting this contributor.

*Verified about us:* `correlation.py:39` and `:56` compute only `abs(rho)` per submitted alpha and
keep the **max**. Nothing in `backend/app/` computes a residual — grep for
`orthogonal|residual|lstsq|neutraliz` returns only BRAIN-side `group_neutralize` AST emission and the
`neutralization` simulation setting.

A related finding: max-pairwise and joint-residual are not the same test. **A candidate can pass every
pairwise check at 0.55 and still be almost perfectly explained by a linear combination of the
incumbents.**

Why it may not transfer: MMC's identity holds because Numerai has a target and a pool it actually
trades. We have neither — BRAIN decides on its own number against alphas we cannot see, and our pool
is 6 alphas, a 6-dimensional span estimated from vectors needing 500+ overlapping days
(`correlation.py:24`). Strictly Phase 2, and it changes a frozen filter's role.

---

## 5. Platform and mechanics

### 5.1 Findings we can act on cheaply [P1-SAFE]

- **Multi-simulation.** BRAIN accepts a **list of 2–10 simulations in a single POST /simulations**,
  returning one Location whose completion carries a `children` array. Our `client.simulate()` posts a
  single dict and hard-caps at `MAX_CONCURRENT_SIMULATIONS = 3`. Caveat: those bounds are ace_lib's
  client-side guardrails, not a documented server contract, and our account did observe a 429 on a 4th
  concurrent simulation.
- **`OPTIONS /simulations`** returns the full account-specific settings tree — free config discovery,
  where our preflight burns a simulation slot.
- **HTTP 201 on submit does not mean live.** Status can remain UNSUBMITTED when SELF_CORRELATION
  fails, so the platform must be re-polled for ACTIVE. Given the drift incident, this matters.
- **The check taxonomy is larger than we model** — at least 16 entries with dynamic per-alpha limits
  against our 12, and `docs/BRAIN_API.md` hardcodes `LOW_SHARPE=1.25`.
- **Published base rates** (community, single-source): pass rate by data type — fundamental ~40%,
  mixed 12.7%, pure technical 5.3%; failure causes dominated by LOW_SHARPE at 90.7%.

### 5.2 The claim that most directly threatens our diversification strategy [P1-SAFE to test]

A 361-star community skill states flatly that **varying window, decay, neutralization or universe does
not produce a low-self-correlation alpha — only a fundamentally different data source does.** It
quantifies: same-signal-cluster pairs correlate 0.74–0.84 on daily returns, cross-cluster 0.59–0.67,
with 0.3–0.6 the target band for genuine diversification. It also warns that cumulative PnL curves
show universal pairwise correlation > 0.90 and must never be used for this.

*Verified about us:* `docs/GOLD_LEVEL_GUIDE.md` §2 presents exactly the disputed levers as its first
two expansion frameworks, including the unsourced claim that different universes qualify as distinct
submittable alphas. `constructor.py` encodes them as a 245-point settings grid per structural config.

**If this is right, a large fraction of our grid is generating alphas that cannot be submitted
together.** It is one practitioner's undocumented experience — precisely the folklore this project
exists to resist — and it conflicts with our own 6×6 matrix in `GOLD_LEVEL_GUIDE.md` §3.1. But we can
settle it from data we already hold: correlate stored daily PnL across alphas that differ *only* in
settings. Zero simulations. This is the single highest-value measurement in the report.

### 5.3 Semantic equivalence [P2]

Exact-string dedup via `expression_hash` misses re-spellings. eggp (e-graph GP, GECCO '25) reports
that a large share of GP offspring are re-spellings of already-visited expressions, and equality
saturation catches them. We cannot use output-vector dedup — that needs local evaluation we do not
have — so an e-graph over the AST is the only available route. Not urgent while evolution is inert.

---

## 6. Provenance — where the drift incident's lesson is not yet fully applied

The project's central lesson was: the platform is authoritative, and local state must not be able to
drift from it. Three findings show the perimeter is incomplete.

1. **There is no `brain_id` column anywhere** [P1-SAFE]. `SubmissionAttempt` has
   `alpha_id, attempted_at, result, failed_check, check_detail, notes, is_recalled`. The join key back
   to the platform is recovered by `sync_submission_outcomes._extract_brain_id()` via three fallbacks,
   the first being `re.search(r"alpha\s+([A-Za-z0-9]+)", alpha.comments)` — **a regex over free-text
   prose.** With 6 submissions a human can repair any mismatch; at 40 that is a real risk, and it is
   the same class of failure as the original incident.
2. **Daily PnL vectors are overwritten in place** with no version, checksum or fetch timestamp
   [P1-SAFE] — and these are the numeric evidence all three frozen filters consume. A silently
   changed vector changes a DSR verdict with no trace.
3. **The pre-registered protocol can be silently rewritten** [P1-SAFE]. It has one reconstructed
   commit, no tag, no signature. `VALIDATION_PROTOCOL.md` itself records "Restored from
   `alphahandoff.zip`" — so it has *already* been through a rewrite. A pre-registration you can
   `git commit --amend` is weaker than one you cannot; OpenTimestamps or a signed tag costs minutes.

Also confirmed: **GP runs are unseeded and unrecorded**; campaign runs capture a seed
(`campaign_runner.py:52`) but no code version, so neither is exactly replayable.

The append-only snapshots are **not bitemporal** [P2] — they carry valid time only, and the unique
constraint makes appending a retroactive correction structurally impossible.

---

## 7. Where we are ahead — confirmed again [NO]

The phase-discipline and constraint verifiers independently reinforced Round 1's list. Additions:

- **No auto-submit.** `ALLOWED_POST_PATHS = {'/authentication', '/simulations'}` enforced by test.
  Both community reference clients (`wqb`, `ace_lib`) ship submit paths; adopting either wholesale
  would import an auto-submit path into the one codebase built to forbid it.
- **No pandas in the BRAIN client** — deliberate, and it keeps the dependency surface honest.
- **SRBench's verdict favours our instincts**: classical GP (Operon) beats neural and transformer
  symbolic regression on real black-box data and matches gradient boosting with simpler models. The
  fashionable end of this literature is not where the wins are.

---

## 8. What I would do, in order

Everything here costs zero simulations and touches no frozen filter.

1. **Test the settings-diversification claim (§5.2) against stored PnL.** If varying window/decay/
   neutralization does not decorrelate, a large part of the constructor grid is producing
   unsubmittable siblings, and that is the binding constraint the operating guide already names.
2. **Call `GET /alphas/{id}/check` and record `REGULAR_SUBMISSION`** (§1.1). Answers an open question
   with a number, per attempt, and costs one GET.
3. **Fix the futility rule (§2.1).** As written it discards a working system a third of the time.
   Replace the bare threshold with an anytime-valid bound; it is ~20 lines of numpy.
4. **Add a `brain_id` column and stop parsing prose (§6.1)** — before the attempt count grows.
5. **Run the two dormant studies:** `calibrate_proxy_rankings()`, and N_eff vs raw trial count
   side-by-side (§3.1). Both are measurement, both were already built.
6. **Timestamp the pre-registration (§6.3).**

Deliberately not started: score-conditioned prompting (§4.1), behaviour-descriptor archives (§4.2),
MMC-style marginal scoring (§4.3), semantic dedup (§5.3). All are Phase 2 design inputs, recorded so
they are not re-derived.

---

## 9. Refuted, and stated absences

**Refuted by verification** (recorded so they are not raised again):

- *"Parent selection needs a novelty bonus"* — correctly described, wrong priority: `generation > 0`
  returns 0 rows, so the GP loop has never run.
- *"We need a visited-set hash for expression dedup"* — `alphas.py:57` `expression_hash` already
  exists with a unique index.
- *"Batch active learning implies 99.5% diversity weight at our coverage"* — the formula was quoted
  correctly from modAL but the comparison to our situation was invalid.

**Absences:**

- **CANNOT DETERMINE** — no production DB in the working tree; every count is from `CLAUDE.md`.
- **CANNOT DETERMINE** — whether the settings-diversification claim (§5.2) holds on our data. That is
  recommendation 1.
- **CANNOT DETERMINE** — whether §2.4's self-inhibition materially biases the pass-rate estimate.
- **NOT FIRST-HAND** — §1.1 and §1.2 rest on community captures, not our own account. `/correlations/prod`
  403s at Tutorial tier.
- **NOT PRESENT** — no interval estimation, sequential test, or stopping rule anywhere in
  `backend/app/`; no residualisation; no behaviour-indexed archive; no `brain_id`.
