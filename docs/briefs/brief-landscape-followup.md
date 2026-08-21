# Brief — Landscape Follow-Up Implementation Plan

Derived from `docs/OPEN_SOURCE_LANDSCAPE_R2.md`. Written 2026-08-21 against commit `2922e10`.
Alembic head at time of writing: **`c3d4e5f6a1b2`** (`add_seed_to_campaigns`).

---

## 0. The rule this plan obeys

Phase 1's metric is **submission attempts with recorded outcomes**, not simulations and not
engineering. Every workstream below had to answer: *does this increase attempts, protect the record
of attempts, or measure something that decides whether attempts are worth continuing?* Anything that
failed that test is in §8 (Not In This Plan), not in the build.

Three consequences, applied throughout:

- **No workstream consumes simulation budget.** Every study below runs on data already on disk.
- **No workstream modifies a frozen filter** (plateau, DSR, subperiod, correlation). W5 computes a
  second number *alongside* a filter and reports the discrepancy; it does not feed it back.
- **No workstream adds a submission path.** W2 adds a GET. `ALLOWED_POST_PATHS` is unchanged, and
  `tests/test_brain_no_post.py` must stay green without modification.

Standing constraints from `CLAUDE.md` that shape specific designs: migrations via Alembic only with
both directions tested; suite green and under ~5s; one source of truth per fact; and — the one that
changes the most code below — **any new constraint or exclusion must be proven to fire on data
written by the production writer, not by a test fixture.**

---

## 1. Sequencing

```
W0  PnL semantics check ──┬──> W1  Settings-decorrelation study   [decides constructor strategy]
    (prerequisite)        └──> W5b N_eff diagnostic

W2  Submission preflight (GET /check) ──┐
W3  brain_id normalisation             ─┴──> W4  Stopping rule  [W4 reads attempt records]

W5a Proxy calibration      (independent)
W6  Provenance hardening   (independent)
```

**W0 → W1 is a hard dependency and is the reason W1 is not first.** The community claim in §5.2 of
the review is about *daily-return* correlation, and the same source warns that cumulative PnL curves
correlate > 0.90 universally. If our stored vectors are cumulative, W1 measures nothing and would
produce a confident wrong answer. `scripts/verify_pnl_reconciliation.py` already exists to settle
exactly this and has never been run to a recorded conclusion.

Recommended order: **W0, W3, W2, W1, W4, W5, W6.** W3 before W2 because W2 writes platform-sourced
records and should write them against a normalised join key rather than adding to the prose problem.

---

## W0 — Establish what the PnL vectors actually are

**Objective.** Record, as a committed fact, whether `pnl/{alpha_id}.npy` holds discrete daily PnL or
a cumulative curve, and which annualisation constant reconciles with BRAIN's reported Sharpe.

**Why it is first.** Three frozen filters consume these vectors, W1's entire conclusion depends on
their semantics, and the answer is currently unrecorded.

**Work.**
1. Run `python -m scripts.verify_pnl_reconciliation`. It is already written; it needs running.
2. Write the result into `docs/INVENTORY.md` as a row with the query and the measured numbers.
3. If the vectors turn out cumulative, **stop and report** — W1 and the correlation gate's
   interpretation both change, and that is a larger finding than this plan covers.

**Acceptance.** `docs/INVENTORY.md` states daily-vs-cumulative with the reconciliation statistic and
the date measured. No code change.

**Effort.** Under an hour. **Risk.** None (read-only).

---

## W1 — Does varying settings actually decorrelate? [highest value]

**Objective.** Measure, from PnL we already hold, the daily-return correlation between alphas that
differ **only** in a settings axis, per axis: `window`, `decay`, `neutralization`, `universe`,
`region`, `delay`, `truncation`.

**Why it matters more than anything else here.** `constructor.py` sweeps a 245-point settings grid per
structural config, and `docs/GOLD_LEVEL_GUIDE.md` §2 presents cross-universe and neutralization
variation as its first two expansion levers — with the unsourced claim that they "qualify as distinct,
submittable alphas". A 361-star community source states the opposite: only a different data source
decorrelates. `PHASE1_OPERATING_GUIDE.md:11` already names self-correlation collisions as the binding
constraint on attempts. **If the community claim is right, a large fraction of the grid produces
siblings that can never be submitted together, and the cheapest possible fix to throughput is to stop
generating them.** If it is wrong, we have refuted a piece of folklore with our own data.

**Design.** New read-only script `backend/scripts/study_settings_decorrelation.py`.

1. Select alphas having stored PnL and a non-null `family_key`.
2. Group by *structural identity*: `feature_json['structural_hash']` where present, else
   `family_key`. Within a group, members differ only in settings and grid coordinates.
3. For each settings axis A, form all within-group pairs that differ **only** in A (all other axes in
   `{region, universe, delay, neutralization, decay, truncation, window}` equal). Window and decay
   come from the grid coordinates the constructor recorded; the rest are columns on `alphas`.
4. For each pair, load both vectors via `PnLStore.get_aligned_matrix`, require
   `>= MIN_COMMON_TRADING_DAYS` (500) overlapping days, and compute Pearson correlation on the
   **daily** series. Reuse `correlation.compute_pairwise_correlation` — do not reimplement it.
5. Report per axis: n pairs, median |ρ|, IQR, the fraction below 0.70 (BRAIN's gate) and below 0.55
   (ours), and the count dropped for insufficient overlap.
6. Also report the **within-group baseline**: correlation between pairs differing in *nothing* but the
   grid point, which bounds what any settings axis could achieve.

**Output.** A markdown table to stdout and `docs/audits/settings-decorrelation.md`. No DB write, no
schema change, and no change to `constructor.py` in this workstream — the decision follows the data
and belongs in a separate change.

**Tests.** `tests/test_study_settings_decorrelation.py`:
- Pair-selection logic: a fixture group where two alphas differ in exactly one axis is selected for
  that axis and for no other; one differing in two axes is selected for neither.
- The 500-day minimum drops short-overlap pairs rather than silently correlating them.
- **Production-writer rule:** the fixture must build its alphas through `alpha_library.create_alpha`
  and its structural hashes through the same function the constructor uses — not by hand-writing a
  `structural_hash` string. If grouping only works on hand-built identifiers, the study is measuring
  the fixture.

**Acceptance.** A committed table with per-axis n and median |ρ|, and an explicit verdict sentence:
*"Varying {axis} produces median |ρ| = X over n = Y pairs; {above/below} BRAIN's 0.70 gate."*
If any axis has n < 30 pairs, report it as underpowered rather than reporting a median.

**Risks.** The likely failure is **n too small** — 486 simulated alphas across many families may not
contain enough single-axis-differing pairs with 500+ overlapping days. If so, the honest output is
"CANNOT DETERMINE, n = X", and the next step is to *design* a small decorrelation experiment rather
than infer from an accidental sample. Do not lower the 500-day minimum to manufacture pairs.

**Effort.** ~1 day. **Rollback.** Delete the script; nothing else is touched.

---

## W2 — Submission preflight: read the platform's own quota and checks

**Objective.** Before each attempt, read `GET /alphas/{alpha_id}/check` and record the full `checks[]`
array, including `REGULAR_SUBMISSION {limit, value}`.

**Why.** It answers an open question in `CLAUDE.md` with a number rather than an inference, tells the
operator whether an attempt is even possible before spending one, and captures which check failed —
directly feeding the "2-of-2 vs 2-of-15" question the same file raises. It is one GET.

**Design.**

1. **Client.** Add `BrainClient.alpha_checks(alpha_id: str) -> dict` in
   `app/services/brain/client.py`, using the existing `get_json`. It is a GET; `ALLOWED_POST_PATHS`
   is untouched.
2. **Enum.** Extend `SubmissionCheckName` in `app/models/enums.py` with `REGULAR_SUBMISSION` and the
   other names observed in the wild. Keep `OTHER` as the catch-all — the taxonomy is larger than we
   model and will keep growing, so the parser must never raise on an unknown name.
3. **Storage — the design decision.** Do **not** add a `quota_limit` column. Quota is a derived
   reading of a platform response, and `CLAUDE.md`'s drift lesson says the response is the fact.
   Add an append-only snapshot table:

   ```
   alpha_check_snapshots
     id, alpha_id FK -> alphas.id, observed_at (server_default now), raw_checks (JSON), source
   ```

   Quota, pass/fail, and failed-check name are **derived** from `raw_checks` by one function,
   `brain.checks.parse_checks(raw) -> CheckSnapshot`, exactly as `platform_outcome` is derived from
   `submission_attempts`. Nothing writes a quota integer to a column.
4. **Surfacing.** `report.py` gains a "submission window" line: remaining quota
   (`limit - value`) as of the most recent snapshot, with its timestamp. The operator's weekly review
   gets a real number instead of an assumption.

**Migration.** New revision `d1e2f3a4b5c6_add_alpha_check_snapshots`, `down_revision =
"c3d4e5f6a1b2"`. `upgrade()` creates the table; `downgrade()` drops it. Both directions tested.

**Tests.**
- `tests/test_brain_no_post.py` must pass **unmodified**. Add an assertion that `alpha_checks` issues
  a GET and that `/alphas/{id}/check` is not in `ALLOWED_POST_PATHS`.
- `parse_checks` on a captured real-shaped payload returns `limit=4, value=N` and the failed-check
  name; on a payload containing an unknown check name it maps to `OTHER` and does not raise.
- **Production-writer rule:** the fixture payload must be fed through the same `parse_checks` the
  runner calls, and the snapshot row must be written by the production write path, not by the test
  inserting a row directly.
- Schema test extended (`tests/test_schema.py`) for the new table.

**Acceptance.** For one real alpha, a snapshot row exists containing `REGULAR_SUBMISSION`, and
`report.py` prints remaining quota with an observation timestamp.

**Risks.** The endpoint may 403 or 404 at Tutorial tier — the review's evidence is from other
people's accounts. **Handle this as a first-class outcome:** on a non-200, record nothing, log at
warning, and have `report.py` print `quota: CANNOT DETERMINE (HTTP 403)`. Do not invent a fallback
estimate. If it 403s, W2 stops there and the finding ("not available at our tier") is the deliverable.

**Effort.** ~1 day including the migration. **Rollback.** `alembic downgrade`; the client method is
inert if unused.

---

## W3 — Normalise the BRAIN alpha id, delete the regex

**Objective.** Stop recovering the platform join key by regex over free-text prose.

**Why.** `sync_submission_outcomes._extract_brain_id()` tries, in order: a regex over
`alphas.comments`, a whitespace-split of an `AlphaStatusHistory.note` containing the substring
"brain sim", then `SimulationImport.raw_payload["id"]`. That is three sources of one fact with prose
ranked first — the precise shape of the drift incident, in the one place that reconciles against the
platform. At 6 submissions a human repairs a mismatch by hand; at 40 they will not notice one.

**Design — and why this does not violate "one source of truth".** The authoritative value already
exists: `SimulationImport.raw_payload["id"]`, written from the platform response. This workstream does
not add a second source; it **promotes** the existing one to a typed, indexed column extracted by the
production writer, and removes the two prose-derived fallbacks that are the genuine duplicates.

1. Add `simulation_imports.brain_alpha_id: str | None`, indexed.
2. Extract it in `app/services/result_import.py` — **the production writer** — at import time, from
   the same payload, via one function `extract_brain_alpha_id(payload) -> str | None`.
3. Backfill in the migration from `raw_payload` JSON for existing rows.
4. Rewrite `_extract_brain_id` to read the column, with `raw_payload` as the single fallback for
   pre-backfill rows. **Delete the comments regex and the status-note split.**
5. Where a row cannot be resolved, keep the existing behaviour: count it `unresolved`, log, continue.
   Never guess.

**Migration.** `e2f3a4b5c6d7_add_brain_alpha_id`, `down_revision = "d1e2f3a4b5c6"`. `upgrade()` adds
the column, creates the index, and backfills with a `json_extract`-based UPDATE. `downgrade()` drops
index then column. Test both, and test that upgrade→downgrade→upgrade preserves the backfill.

**Tests.**
- **Production-writer rule, the important one here:** the test must import a simulation result through
  `result_import.import_result` and then assert `brain_alpha_id` is populated — *not* construct a
  `SimulationImport` with the field set. The whole point is that the writer populates it.
- A row whose payload lacks an id leaves the column NULL and is reported `unresolved`.
- Backfill correctness on a fixture row created before the column existed.

**Acceptance.** `grep -n 're\.search' scripts/sync_submission_outcomes.py` returns nothing, and a
reconciliation run over existing data resolves **at least as many** alphas as before. That last clause
is a genuine regression gate: if the regex was resolving alphas the column cannot, the diagnosis was
incomplete — stop and report rather than lose resolutions.

**Effort.** ~1 day. **Rollback.** Downgrade; the old fallback chain must be restorable in one revert.

---

## W4 — Replace the futility rule with a bound that has an error rate

**Objective.** Give the weekly review an honest stopping instrument, and stop discarding a working
system a third of the time.

**Why.** `PHASE1_OPERATING_GUIDE.md:125` says stop if "pass rate under 10% at 25+ attempts". Checked
weekly (`:29`), that is a sequential rule applied at every n ∈ [25, 40], and it fires with probability
**0.335 when the true rate is 15%**. No error rate is stated anywhere.

**Scope note — this is not a frozen filter.** The freeze covers the per-alpha statistical filters
(plateau, DSR, subperiod, correlation). This is programme-level monitoring of the phase itself. It
touches none of those modules and changes no alpha's verdict.

**Design.** New module `backend/app/services/phase_metrics.py`, pure functions, numpy + scipy only
(both already dependencies — **do not add `confseq`**: no wheel for 3.11+, and it pulls pandas and
matplotlib into a codebase that deliberately has neither).

```
wilson_interval(k, n, alpha=0.05)              -> (lo, hi)
clopper_pearson_interval(k, n, alpha=0.05)     -> (lo, hi)
beta_mixture_confidence_sequence(k, n, alpha)  -> (lo, hi)   # anytime-valid
futility_verdict(k, n, threshold, alpha)       -> Verdict     # STOP | CONTINUE | UNDECIDED
```

The confidence sequence is the Robbins/Lai Beta(1,1)-mixture martingale: include m in the interval
while `E_t(m) = B(1+k, 1+n-k) / (B(1,1) · m^k (1-m)^(n-k)) < 1/alpha`. It is roughly 20 lines and
costs about 1.36× the fixed-n width — the price of being allowed to look weekly.

`futility_verdict` returns **STOP only when the anytime-valid upper bound falls below the threshold.**
Under that rule, with zero passes the bound reaches 0.10 at n=69, not n=25 — so the honest verdict at
n=25 is UNDECIDED, and the function must be able to say so.

**Surfacing.** `report.py` prints, for the current attempt count: k/n, the Wilson interval, the
anytime-valid interval, and the verdict. It must print both — the gap between them is the cost of
peeking, and the operator should see it.

**Docs.** Rewrite the stop conditions in `PHASE1_OPERATING_GUIDE.md`:
- Replace "Pass rate under 10% at 25+ attempts" with "the anytime-valid 95% upper bound on the pass
  rate falls below 10%", and state the n at which that first becomes possible.
- Add a sentence acknowledging §2.4 of the review: attempts are not iid, because the correlation gate
  makes pass probability monotone non-increasing, so the reported figure estimates a running average
  of conditional means, not a constant.
- If the 10% threshold is an *economic* decision rather than a statistical one, say so explicitly and
  keep it — but then it should not be justified by a pass-rate measurement.

**Tests.** Known-value tests against published binomial intervals; the CS interval strictly contains
the Wilson interval at the same (k, n); `futility_verdict(0, 25, 0.10)` returns UNDECIDED and
`futility_verdict(0, 69, 0.10)` returns STOP; monotonicity — the CS upper bound is non-increasing in n
at fixed k.

**Acceptance.** `report.py` shows both intervals and a verdict; every stop condition in the operating
guide carries an explicit error rate.

**Effort.** ~1 day including the doc rewrite. **Rollback.** Pure addition; delete the module and
revert the doc.

---

## W5 — Run the two studies that were built and never executed

Both are measurement. Neither changes a filter. Neither costs a simulation.

### W5a — Proxy calibration

`proxy_calibration.calibrate_proxy_rankings()` measures Spearman rank correlation between proxy
heuristics and real BRAIN metrics. Its only caller is its own test.

**Work.** Add `backend/scripts/calibrate_proxy.py` (a thin CLI in the style of
`verify_pnl_reconciliation.py`), run it over all available simulation payloads, and commit the
`CalibrationReport` into `docs/audits/proxy-calibration.md` with the sample size.

**Why it is worth a day.** If the rank correlation is materially positive, the ~4,371 unsimulated
candidates can be *ordered* by predicted quality at zero simulation cost — raising
passes-per-simulation without raising the simulation count. If it is zero, that is a real finding and
closes off a whole class of future proposals.

**Acceptance.** A committed report with n and the measured coefficient. **No** change to queue
ordering in this workstream — that is a separate decision that needs the number first.

### W5b — N_eff versus raw trial count (diagnostic only)

`subperiod.compute_effective_trials()` implements the correlation-adjusted trial count
(N_eff = M²/Σλᵢ²). `plateau.py:326` calls `compute_dsr(daily_pnl, daily_sharpes)` without it, so
`subperiod.py:90` falls back to `n_trials = max(1, len(sharpes_clean))` — a raw count of
near-duplicate grid points.

**Work.** `backend/scripts/diagnose_effective_trials.py`: for each family with enough PnL-bearing
members, build the correlation matrix, compute N_eff and the raw count, and report the ratio and the
resulting DSR under each.

**Explicitly out of scope: wiring N_eff into `compute_dsr`.** That is a frozen filter. This produces a
number and a document; what to do about it is a Phase 2 decision.

**Expect it to be inconclusive, and report that.** With 486 simulated alphas spread over many
families, most will have too few members for a stable eigenvalue estimate. Families below a stated
minimum must be reported as skipped with the count, not silently excluded.

**Effort.** ~1 day for both. **Rollback.** Delete the scripts.

---

## W6 — Provenance hardening

**W6a — PnL vectors are overwritten in place.** `PnLStore.save_pnl` writes `{alpha_id}.npy` and
`{alpha_id}_dates.json` with no version, checksum, or fetch timestamp — and these vectors are the
numeric evidence all three frozen filters consume. A silently changed vector changes a DSR verdict
with no trace.

*Work.* Extend `save_pnl` to write a sidecar `{alpha_id}_meta.json` containing `sha256`,
`fetched_at`, `n_days`, `first_date`, `last_date`. On re-save, compare the digest: identical content
is a no-op; **differing content logs a warning naming both digests and requires an explicit
`overwrite=True`**. Add `PnLStore.verify(alpha_id)` re-checking the digest, and call it from
`scripts/verify_pnl_reconciliation.py`.

*Test.* Save, mutate the `.npy` on disk, assert `verify()` fails. Assert a second `save_pnl` with
changed content and `overwrite=False` raises rather than silently replacing.

**W6b — The pre-registration can be silently rewritten.** `VALIDATION_PROTOCOL.md` is a pre-registered
protocol with one reconstructed commit, no tag and no signature — and it already records "Restored
from `alphahandoff.zip`", so it has been through a rewrite once.

*Work.* Record the SHA-256 of the file in `docs/DECISIONS.md` with a date, create an annotated tag
`preregistration-v2` at the commit containing it, and push the tag.

*State the limitation honestly in the doc:* a tag can be moved and a repo can be force-pushed, so this
makes tampering **evident to a careful reader**, not impossible. A third-party anchor (OpenTimestamps,
or a signed tag) is the stronger form and takes minutes more; recommend it, and if it is skipped, say
in `DECISIONS.md` that it was skipped and why.

**W6c — Reproducibility gap, recorded not fixed.** Campaign runs capture a seed
(`campaign_runner.py:52`) but no code version; GP runs are unseeded entirely. Since
`docs/INVENTORY.md:39` records `generation > 0` → 0 rows — the GP loop has never produced an alpha —
**fixing GP seeding is not worth doing now.** Add one row to `docs/INVENTORY.md` noting both facts so
the gap is on the record, and revisit if evolution is ever switched on.

**Effort.** ~1 day for W6a; under an hour for W6b and W6c. **Rollback.** W6a is additive; the sidecar
is ignored by readers that do not know about it.

---

## 7. Definition of done

| # | Workstream | Done when | Costs sims? | Touches frozen filter? |
|---|---|---|---|---|
| W0 | PnL semantics | `INVENTORY.md` states daily-vs-cumulative with the statistic | No | No |
| W1 | Settings decorrelation | `audits/settings-decorrelation.md` has per-axis n, median \|ρ\|, verdict | No | No |
| W2 | Submission preflight | A real `REGULAR_SUBMISSION` snapshot exists; `report.py` prints remaining quota | No | No |
| W3 | brain_id | No regex in `sync_submission_outcomes.py`; resolution count not reduced | No | No |
| W4 | Stopping rule | Both intervals + verdict in `report.py`; every stop condition carries an error rate | No | No |
| W5 | Dormant studies | Two committed audit docs with sample sizes | No | No (diagnostic only) |
| W6 | Provenance | PnL digests written and verified; pre-registration hashed and tagged | No | No |

Across all seven: suite green, under ~5s, both migration directions tested.

---

## 8. Not in this plan, and why

These are the review's Phase 2 items, recorded so they are not re-derived, and deliberately not
started.

- **Score-conditioned prompting** (feeding simulation outcomes back to the LLM). This is the core
  mechanism of the FunSearch/AlphaEvolve family and we do not use it. It stays closed during Phase 1
  for a specific reason: it makes field selection outcome-dependent, which contaminates the Phase 2
  causal study for every field the exploit arm touches. The open loop is currently a feature.
- **Behaviour-descriptor archives** (MAP-Elites-style indexing on PnL rather than design coordinates).
  Changes what the allocator optimises. The *measurement* — archive coverage and behavioural novelty
  from stored PnL — is P1-safe and folds naturally into W1's machinery if that study finds enough
  pairs.
- **MMC-style marginal contribution** replacing the max-pairwise correlation gate. Changes a frozen
  filter's role. Numerai deleted the equivalent hard gate in favour of a continuous score, which is
  the strongest external evidence we have about our own design — but it is a Phase 2 argument.
- **Semantic expression dedup** (e-graphs). `expression_hash` already handles exact strings, and the
  GP loop that would generate re-spellings has never run.
- **Multi-simulation batching** (2–10 per POST). Throughput work, and throughput is not the binding
  constraint — `PHASE1_OPERATING_GUIDE.md:11` says diversity is. Revisit only if W1 shows the grid is
  not the problem.
- **Anything touching billing, accounts, crowding maps, or the network layer.** Out of scope until
  Phase 2 passes.

---

## 9. Honest risks in this plan

1. **W1 may be underpowered.** The most valuable study may return "n too small". That is a real
   possible outcome, and the plan must not respond by weakening the 500-day overlap minimum. The
   fallback is to *design* a decorrelation experiment, which does cost simulations and is a separate
   decision.
2. **W2 may 403 at our tier.** Then the deliverable is the negative result, not the feature.
3. **W3's regression gate could fail.** If the regex currently resolves alphas the promoted column
   cannot, the diagnosis was incomplete — stop and report rather than accept fewer resolutions.
4. **W4 changes a stop condition, which is a business decision as much as a statistical one.** The
   implementation should present both framings and let the operator choose; a correct bound that makes
   the project harder to stop is not automatically an improvement.
5. **Two facts this plan acts on are not first-hand** — the quota limit and PROD_CORRELATION semantics
   come from community captures on other people's accounts. W2 is designed so our own account settles
   the first. The second remains unverified at Tutorial tier, and no workstream here depends on it.
