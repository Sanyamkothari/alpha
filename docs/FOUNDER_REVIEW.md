# Founder review — Alpha Research Engine

**Reviewer stance:** founder/CEO, reviewing for capital allocation. Written 2026-08-19
against `claude/project-founder-review-onz9hw` @ `983c134`.

**Method:** read STRATEGY.md, alphaproductstrategy.md, docs/, and the code; built the
environment and ran the suite (194 tests, all pass), plus `ruff`, `black --check`, `mypy`.

---

## Verdict in one paragraph

The engineering is better than the business case, and the business case is better
than the evidence. This is a genuinely well-built research instrument — the
compiler, the API research, and the decision hygiene are the work of someone who
knows what they're doing. But after ~17,000 lines and seven "Done" milestones, the
measured output is **one distinct alpha**, and the product strategy prices a
₹15,000/month subscription on the promise of 10–20 accepted alphas per member per
year. That is a 15x gap between what the machine has demonstrated and what the
business plan sells. **Stop building features. Prove the loop on one account.**

Three findings would each independently stop me from funding the next phase:

1. The shipped shortlist violates the project's own objective function (§2).
2. The flagship unattended runner crashes the first time it does real work (§3).
3. Two "Done" milestones are not wired into anything (§4).

None are hard to fix. All of them being simultaneously true is the signal: the
project is measuring itself by features written, not by alphas accepted.

---

## 1. What is genuinely good — the part worth protecting

I want to be specific here, because the fix list below is long and the good parts
are the reason the fix list is worth doing.

**The API research is a real asset.** `docs/BRAIN_API.md`'s verified section —
cookie auth not Bearer, no `status` key during polling, the terminal condition
being the presence of `alpha`, `margin` as a fraction not bps, the hard concurrency
cap of 3 — is a week of somebody else's debugging, captured. The finding that
delay-0 is *readable but not simulatable* on this account level, and that universe
does not partition the catalog, are the kind of facts that quietly waste a month if
you assume otherwise.

**The safety boundary is engineered, not asserted.** `tests/test_brain_no_post.py`
does not just check that no submission function exists; it enforces that only one
package may import an HTTP client, that that package may POST only to a declared
whitelist, and that no write table for submissions can enter the schema.
`docs/DECISIONS.md` D1 then explains why the rule was *narrowed* rather than
deleted. That is exactly right, and it is rarer than it should be.

**The comments carry the non-obvious.** `normalize_is_block` explains why it is
now a no-op (double-conversion risk). `jobs.py` states the cost of not using Celery
in the same breath as the choice. `_neighbours` explains why "adjacent" means one
ladder step. This is a codebase someone else could pick up.

**The statistics are real.** `compute_dsr` is a faithful Bailey & López de Prado
implementation — skew/kurtosis correction, Euler–Mascheroni expected maximum, the
right variance term. `verify_pnl_reconciliation` recomputes Sharpe from the stored
daily series and compares it to what BRAIN reported. Most people who claim "honest
filtering" have neither.

Keep all of it. The problems below are about wiring and focus, not competence.

---

## 2. The shortlist does not enforce the objective function

STRATEGY.md §2 states the objective precisely, and correctly:

> maximize: count of alphas clearing the bar
> subject to: pairwise correlation < 0.7 against everything already accepted
> **Diversity is the objective function, not a post-filter.**

The shipped shortlist is a post-filter, and it is not applied within the shortlist
at all. `report.py:121-125` and `routers/ui.py:122-148` both collect promoted
verdicts across families, sort by Sharpe, and cut at 15/25. The correlation gate
(`correlation.py:44`) compares each candidate against alphas already in
`SUBMITTED` or `PASSED` status — that is, against the *existing* portfolio. Two
candidates promoted in the same run are never compared to each other.

`report.md`, the committed output, shows exactly this failure:

| # | Sharpe | expression |
|---|---|---|
| 1 | 1.91 | `rank(ts_zscore(divide(ts_backfill(liabilities,120),cap),5))` |
| 2 | 1.82 | `rank(ts_zscore(divide(ts_backfill(liabilities,120),cap),5))` |

**The same expression, twice.** Same family, adjacent decay settings, near-certainly
>0.95 correlated with each other. The system's entire published track record — "2
clearing every BRAIN check" — is one idea counted twice. An operator working the
morning queue as designed submits both, burns two of a weekly submission quota, and
gets one rejected for self-correlation. The tool actively directs them into the
mistake it exists to prevent.

**Fix:** greedily deduplicate the shortlist before display. Walk it in Sharpe order,
and admit each candidate only if its correlation against every already-admitted
member of *this run* is below threshold — the same `compute_pairwise_correlation`
already in `correlation.py`, with the structural-hash fallback already in
`plateau.check_portfolio_correlation` when PnL is missing. This is perhaps 30 lines
and it is the single highest-value change in the repository.

**Second-order fix:** the report should count *distinct* alphas, not rows. Until it
does, every number in every status table is inflated by an unknown factor.

---

## 3. The unattended overnight runner crashes on first real work

`campaign_runner.py` is the "self-sustaining machine" — resumable, checkpointed,
3-arm budget split, crash recovery. It is the feature that turns this from a tool
you drive into a machine that runs.

```python
# campaign_runner.py:184
sim_count = len(ids) - len(batch_res.errored)
```

`BatchResult` (`simulation_runner.py:38-42`) has fields `simulated`, `failed`,
`passed_all_checks`, `errors`. There is no `errored`. This raises `AttributeError`
on the first task that actually simulates, mid-campaign, after the alphas have been
created but before the checkpoint is written.

It survived because the only test, `test_execute_campaign_dry_run`, calls
`execute_campaign(cid, simulate=False)`. The `simulate=True` path — the entire point
of the module — has no test at all. `mypy` catches this in under a second; it is
error #14 of 24.

Twenty lines above it:

```python
except Exception:
    pass
```

Every failure to create an alpha is swallowed silently. A campaign that creates zero
candidates reports success and checkpoints as `completed`.

**Fix:** the one-character bug, a test that exercises `simulate=True` against a
stubbed `run_batch`, and replace the bare swallow with a counter that surfaces in
the campaign record. Then **put `mypy` and `ruff` in CI** — see §5.

---

## 4. Two "Done" milestones are not connected to the product

The README status table marks Stage 6 — "Diversity-capped allocator · Multi-armed
bandit (Thompson/UCB) with 20% dataset crowding cap" — as **Done**, and the
architecture diagram puts "MAB Dataset Allocator" as the second box in the pipeline,
feeding everything downstream.

`app/services/allocator_bandit.py` is imported by nothing outside its own tests.
Every live caller — `campaign_runner.py:23`, `report.py:23`, `routers/ui.py:31` —
uses `app/services/allocator.py`, a different module with a different arm split
(`exploit/random_stratified/plateau_fill` vs the bandit's
`explore/plateau/evolution`). The live allocator does enforce `MAX_DATASET_SHARE =
0.20`, so the diversity cap is real; the bandit is not.

Similarly, `compute_effective_trials` — the eigenvalue-based N_eff estimator that
would make the DSR haircut honest — is fully implemented, tested, and never called.
`compute_dsr` takes `n_eff` as an optional parameter and `plateau.evaluate` never
passes it, so the deflation uses `len(family_sharpes)`: the trial count of *one
family*, not the ~295 lifetime simulations that actually generated the winner. The
haircut is therefore materially too generous, in the one place the project stakes
its credibility on being conservative.

**This is the finding I'd weight most heavily as an investor**, above the crash. The
README says a thing is done; the code says it was written and never plugged in.
Once that is true twice, I stop being able to read the status table, and the status
table is how I know what I own.

**Fix:** either wire them (`n_eff` first — it is a correctness issue, not a feature)
or delete them and correct the README. Both are fine. Leaving them is not.

---

## 5. Quality gates exist but nothing enforces them

194 tests, all passing, fast, no network. Good. And:

| Gate | Configured | Result |
|---|---|---|
| `pytest` | ✅ | 194 pass |
| `ruff` | ✅ | **108 errors** |
| `black --check` | ✅ | **47 files would reformat** |
| `mypy` | ✅ | **24 errors in 12 files** |
| CI | ❌ | no `.github/` at all |

Every gate is configured in `pyproject.toml` and none of them runs. That is why §3
shipped. Among the mypy errors, at least three are latent runtime failures beyond
the campaign crash: `alpha_library.py:157` references an unimported `Iterable`;
`plateau.py:246,273` annotate against undefined `PnLStore` and a `PlateauPoint` type
that does not exist (it is `SurfacePoint`); `proxy_calibration.py:140-149` compares
an AST `Node` with `>=`.

**Fix:** a ten-line GitHub Actions workflow running all four. Half a day to clear the
backlog, and §3 becomes structurally impossible.

---

## 6. The strategy — where I push back

`alphaproductstrategy.md` is a good document. "Give away the machine, sell the map"
is a genuinely sharp reframe of the cannibalization problem that STRATEGY.md §8
correctly identifies and then shrugs at. The scarcity argument (300 × ₹15,000 beats
1,000 × ₹4,000, and delivers) is right. The trust problem in §7 is one most founders
would not have thought to raise about their own product.

Three things I would not sign off on.

**The keystone assumption is unverified, and the document knows it.** §9 says the
entire capacity model — and therefore the price, the cap, and the defensibility —
rests on whether one member's accepted alpha consumes ground for everyone. If BRAIN
only checks self-correlation, there is no shared map to sell. The document says this
is answerable in five minutes. **Then answer it before writing another line of
product code.** `docs/BRAIN_API.md` already records that `SELF_CORRELATION` returns
`PENDING` at simulation time, which is the neighbouring fact; the production-portfolio
question is still open, and everything downstream is contingent on it.

**The machine has not earned the promise the subscription makes.** The plan assumes a
member needs 10–20 accepted alphas per year to renew. This instance, run by the
person who built it, with full attention and no support burden, has produced **one
distinct alpha that clears BRAIN's checks** (§2 — the "2" is one idea double-counted).
That is not an indictment of the approach; it is a sample size of one family. But it
means the central product claim is currently unevidenced by a factor of ~15, and no
amount of additional tooling changes that. The only thing that does is running the
loop.

**The account level is a live blocker nobody has priced.** `docs/BRAIN_API.md` §93
records this account as `level: NONE`, `permissions: ["TUTORIAL"]`. That means:
USA/delay-1 only; non-USA regions return zero datasets; and **delay-0 — measured at
~18x less crowded, 27 users/field vs 493 — is readable but not simulatable.** Rule 1
of the strategy is "data is the edge, not the formula," and the most valuable
uncrowded data is behind an account gate that no amount of engineering opens. Note
also that `docs/INVENTORY.md:363` refers to "the user's BRAIN consultant account,"
which contradicts the probed `level: NONE`. Reconcile that; if the tool is
architected for consultant-tier access it does not have, several capacity assumptions
move.

---

## 7. Scope, honestly

Git history shows the entire 17,000-line system was committed across **two days**
(2026-08-14 and 08-15). Understanding that explains a lot of the above: the feature
surface massively outran the validation surface, and the README status table is a
record of things written rather than things working.

Held against the loop in STRATEGY.md §7, roughly this much is load-bearing:

| Component | LOC (approx) | In the nightly loop? |
|---|---|---|
| Validator / compiler / operator KB | ~1,500 | ✅ the moat |
| Constructor + allocator + simulator + plateau/DSR/correlation | ~2,500 | ✅ the loop |
| Web console + jobs + routers | ~3,000 | ✅ the morning pass |
| `evolution.py`, `composite_constructor.py`, `genealogy` | ~700 | ❌ manual scripts only |
| `allocator_bandit.py` | ~150 | ❌ dead |
| `proxy_calibration.py`, production snapshots, submission attempts | ~700 | ❌ speculative |
| Desktop packaging (PyInstaller) | ~300 | ❌ single-user tool |

Evolution and composites are Phase-1 features for a system that has not yet
exhausted Phase-0's simplest question: *does one family per day, on uncrowded data,
produce accepted alphas?* Genetic search over a population seeded from one winner is
a way to generate 400 correlated variants of that winner — the exact failure mode
STRATEGY.md §2 warns about.

Desktop packaging deserves a specific call: it is distribution infrastructure for a
product with one user, and `alphaproductstrategy.md` §4 has the machine running
locally under the customer's own credentials — so it is the *right* long-term
architecture. It is just three phases early.

---

## 8. What I would do

**This week — make the existing numbers trustworthy.**

1. Deduplicate the shortlist by correlation (§2). Recount `report.md`. Publish the
   honest number, which is 1.
2. Fix `batch_res.errored`, add a `simulate=True` test, remove the bare `except:
   pass` (§3).
3. CI running `pytest`, `ruff`, `black --check`, `mypy` (§5). Clear the backlog.
4. Pass `n_eff` into `compute_dsr` (§4). Expect the DSR bar to get harder and the
   surviving count to drop. That is the correct direction.
5. Either wire `allocator_bandit.py` or delete it, and make the README table true.

**This month — one question, answered with runs.**

Run the loop, unattended, every night, for four weeks. One target only:
**how many distinct, uncorrelated alphas clear every BRAIN check?** Not simulations
per day, not families expanded, not plateau ratios. Distinct accepted alphas. The
allocator already names six unexplored datasets in `report.md`; work them.

- ≥8 in four weeks → the machine works. Then answer the §9 production-correlation
  question and start on the map.
- 1–3 → the filter is honest and the ground is thinner than modelled. Interesting,
  but not a ₹15,000/month product yet.
- 0 → the one winner was luck, and the whole thesis needs rework before anything
  else gets built.

**Freeze until that returns:** evolution, composites, desktop packaging, production
snapshots, any multi-tenant work. Not because they are bad — because four weeks of
run data changes what they should be, and building them first means building them
twice.

**Before any product code:** get the §9 answer, and get the account level resolved.
Both are cheap. Both invalidate large amounts of work if they come back wrong.

---

## 9. The one thing I would frame and hang on the wall

STRATEGY.md §1 diagnoses the previous version's failure as *building the machine
instead of running it* — 1,900 lines serving a rig that produced 51 alphas and zero
passes. The diagnosis is exactly right and unusually honest about one's own work.

This version has 17,000 lines, 194 tests, seven "Done" milestones, a desktop
installer, a genetic search engine, and **one alpha**.

The failure mode did not get fixed. It got better funded.

That is not a reason to stop — the instrument is good and the thesis is plausible.
It is a reason to stop building and start running. Everything in §8 is in service of
that one sentence.
