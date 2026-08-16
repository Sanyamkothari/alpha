# Is There a Business? — Open Source vs the Bureau

**16 August 2026.** Reads `BUSINESS_MODEL.md`, `VALIDATION_PROTOCOL.md` and `ROADMAP.md` against the
open-source survey in `COMPETITIVE_FEATURE_GAP.md` and the measured state of the code.

Three questions, answered in order:
1. What should we take from the open-source projects?
2. If they match us, why would anyone pay?
3. Is there really a business here?

---

## 0. The correction that reframes question 1

The earlier gap analysis ranked open-source features by engineering value. That was the wrong axis,
and `BUSINESS_MODEL.md` §2 says why:

> **Given away free:** the digging machine, running on the member's own computer
> **Sold:** the map

If the digger is free, then **every open-source feature that improves the digger improves the thing
you are giving away.** Open-source parity on the digger is not a threat to the plan — it is what the
plan already assumes. The correct filter is narrower:

> Does this feature produce more **map fuel** (failure observations, territory coverage), or more
> **members**? If neither, it is a hobby.

Re-ranked on that basis, most of the earlier list drops away.

### Worth taking

| From | What | Why it matters to the *business* |
|---|---|---|
| worldquant-miner | 24/7 unattended Docker operation | The map is built from failures (§3). Failures come from volume. We run at ~13 sims/day against a ~2,800/day platform ceiling — 0.5%. This is the fuel line. |
| wq-alpha-research | `evolve_skill.py` — each simulation distilled into a reusable written rule | **This is a single-user prototype of the map.** The cheapest possible test of the core premise: does pooled learning from failure have value? If it cannot help one researcher, it will not help 250. |
| WQ-Brainn | Multi-region / universe templating | Territory coverage is the binding constraint on the validation study (§2 below). We have mined one region-universe cell. |
| QuantGPT | MCP / agent interface | Distribution. Meets members inside tools they already run, which matters given the acquisition problem in §3. |

### Not worth taking

Offline simulation, genetic programming, IC/Rank IC metrics, CPCV/PBO, alpha ensembling. Each is a
real capability and each improves the free component. Two additionally require changing the frozen
filter stack. **Building any of these now is spending the scarcest resource — founder time — on the
giveaway.**

---

## 1. Does open source actually compete with us?

**On the digger: yes, roughly at parity, and it does not matter.**

**On the bureau: no. Not one of the seventeen projects surveyed pools data across users.** Every one
is a single-user miner. Nobody aggregates what was tried and what failed. Nobody shows one researcher
what another has exhausted. The space `BUSINESS_MODEL.md` describes is empty.

The closest thing is **QuantGPT Cloud**, and it is worth being precise about it. Its docs say
A-grade factors upload automatically for "independent IC/IR verification and out-of-sample tracking."
That is an aggregation layer with a server behind it — the beginning of the right shape. But:

- I could not confirm from public documentation whether it is multi-user, whether users see each
  other's data, or whether any crowding/claiming exists. **Recorded as unverified, not as absent.**
- It aggregates **successes**. `BUSINESS_MODEL.md` §3 identifies the key insight that the map must be
  built from **failures**, because failures are abundant and successes are not. Even the nearest
  competitor is building the version that cold-starts badly.

So the strategic read: the bureau idea is genuinely unoccupied, the nearest neighbour is solving an
easier and less valuable problem, and there is a real first-mover window. That is the good news, and
it is not small.

### The uncomfortable half

The moat is the members, not the software. At zero members the moat is zero. `BUSINESS_MODEL.md`
states this plainly ("a copy of the software is worthless without the members") — which cuts both
ways. Today we own no part of the defensible asset.

And our *current* genuine differentiator is not something anyone buys. The provenance discipline —
append-only snapshots, attempt-level submission records, platform-authoritative outcomes — is real,
and **no surveyed project has anything like it.** But it is a necessary condition for a *trustworthy*
map, not a value proposition. Nobody pays for a database schema. It is table stakes for a bureau that
would survive its first audit, and worth nothing until there is a bureau.

---

## 2. Can the premise be tested right now? No — and the reason is smaller than expected

`ROADMAP.md` frames the blocker as territory count: "~36 dense territories vs ~490 needed."

That is true but it is not the binding constraint. **Study 1 (acceptance) is the *primary* study, and
it cannot be run for a different reason entirely.**

| Requirement | Have |
|---|---|
| Submitted alphas | **7** (6 recorded in `submission_attempts`) |
| Recorded acceptance outcomes | **0** |
| Territories | 149 total; median 2 alphas each; 36 holding >100 |
| Territories for 50% lift detection | ~490 |

Study 1 regresses acceptance on crowding. With zero recorded acceptances **the outcome variable is a
constant.** No sample size fixes that. Ten thousand territories would not fix it. This is not an
underpowered study; it is an undefined one.

Meanwhile Study 2 (promotion) is runnable in principle — but per §1.3 it is explicitly *secondary*
and "not a fallback to reach for if Study 1 disappoints."

### The observations that exist and are not written down

Roughly 27 alphas cleared BRAIN's checks and were rejected on self-correlation. **Those are Study 1
outcomes.** They are negative labels — the exact thing §3 of the business model calls the abundant
input — and they currently exist only in the operator's memory.

This is the single highest-value unrecorded asset in the project. `scripts/record_past_attempts.py`
already accepts `--result rejected --check SELF_CORRELATION`. Memory decays; this is a one-way door.

### One thing the design got right

`VALIDATION_PROTOCOL.md` Step 1 warns that crowding range may be too narrow to test anything, "likely,
since you have been deliberately avoiding crowded ground." `INVENTORY.md` §A6 confirms the worry:
47% of alphas sit in the bottom catalog quartile and 6 of 12 mined fields have `user_count = 0`.

The 30% random-stratified arm exists precisely to fix this, and it is the reason `CLAUDE.md` forbids
touching it. That instinct was correct and should be defended when it becomes inconvenient.

---

## 3. Three honest problems with the business case

None of these is fatal. All three are currently unaddressed.

### 3.1 The market arithmetic implies near-total capture

`BUSINESS_MODEL.md` §7 states the market is "a few hundred people who can afford it," then models
**250 members** at steady state and break-even at **45**.

If the addressable population really is a few hundred, then 250 members is most of the market and
break-even is 15–25% penetration — sold by one person, by hand, to competitive quants whose defining
trait is that they build their own tools.

That is not impossible; niche B2B tools do reach high penetration of small markets. But the plan's
headline revenue depends on a share almost no product achieves. **Before any of it: count the actual
number of Master and Grandmaster consultants.** If it is 400, the model needs rewriting. If it is
4,000, the plan is conservative. Nobody knows which, and it is checkable.

### 3.2 New evidence points toward the weaker version of the product

`BUSINESS_MODEL.md` §9 lists as check 2, and `ROADMAP.md` as "the one external fact still
outstanding," the same question: does BRAIN check correlation against *production* alphas, separate
from your own?

> "If BRAIN only checks you against yourself, members don't block each other, the claims layer loses
> most of its value, and the product becomes exhaustion + fertility only."

We now have ~27 data points, and they all say **SELF**_correlation. That is suggestive, not decisive —
those alphas may never have reached a production-correlation check — but it is the first real evidence
and it points the wrong way.

The deeper issue is what it implies about the shape of the pain. If the binding constraint is
collision with *your own* portfolio, the cure is a **portfolio-aware personal search** — something
that knows your 300 alphas and finds ground orthogonal to them. That is a *tool*. By §2's own
argument, a strong quant rebuilds it in a few weekends.

To be fair to the model: self-correlation binding is entirely **consistent** with its best insight —
that pain scales with portfolio size, so "the people in most pain are the people with the most money."
The alignment survives. What changes is which product relieves it, and the tool-shaped answer is far
easier to copy than the bureau-shaped one.

### 3.3 The neutrality requirement is a real cost, and it lands at the worst time

§5 requires abandoning your own mining at ~60 members. That is the same period when revenue is
thinnest (Year 2, ~₹86 lakh modelled) and when the founder's own alpha income is the only proof the
machine works. The argument for it is correct and the sequencing is brutal. Worth acknowledging
before it arrives rather than during.

---

## 4. Verdict

**Is there a chance? Yes — but nobody can currently tell, and the gap to finding out is
much smaller than the gap to building anything.**

What is genuinely strong:

- The bureau space is empty. Seventeen projects, not one pooling data. That is a real window.
- The strategy documents are more rigorous than most funded startups produce — pre-registered
  hypotheses, a baseline ladder, pre-declared kill thresholds, an explicit unit-of-analysis correction.
- The measurement discipline is a real asset for building a map anyone would trust.
- The cost structure is genuinely excellent, because the expensive part runs on members' machines.

What is genuinely weak:

- Zero accepted alphas after six weeks. The premise "this system produces good alphas" is still
  unproven, and everything else is downstream of it.
- The primary study cannot be run — not for lack of territories, but for lack of any recorded outcome.
- The evidence that does exist points at self-correlation, which favours the tool-shaped product over
  the bureau-shaped one.
- The revenue model assumes a market share that has not been checked against a headcount.

**The honest position: this is not yet a business, and it is not yet *not* a business. It is a
research project whose decisive experiment has not been run, and the thing blocking that experiment
is roughly two hours of data entry plus a five-minute look at a web page.**

That is an unusually good place to be. Most failing startups need a year to learn what this one could
learn this week.

### What to do, in order

1. **Record the ~27 self-correlation rejections** with their failed check. Two hours. One-way door —
   this is memory, and it is the only Study 1 outcome data in existence.
2. **Open BRAIN and read a submitted alpha's checks.** Write down every check name and threshold, and
   specifically whether `PROD_CORRELATION` exists separately from self-correlation. Five minutes. It
   decides whether the claims layer — and much of the bureau premise — is real.
3. **Count the Master and Grandmaster population.** Decides whether §7's revenue model is conservative
   or fantasy.
4. **Then Phase 1 as written.** 40 submissions with outcomes. Nothing else.

Do not build the map. Do not build the fertility model. Do not take the offline simulator or the
genetic search. `ROADMAP.md` §"What not to do now" is correct and this analysis changes none of it.

### One reframe worth keeping for later

The open-source projects are more usefully understood as a **distribution channel than as
competition.** Their users are running simulations and discarding failure data right now — precisely
the map's fuel. A reporting client that rides on tools people already use would invert the acquisition
problem in §3.1.

That is a Phase 4 thought. It is recorded here so it is not lost, and it should be ignored until
Phase 2 returns a number.

---

*Peer capabilities are drawn from public READMEs and are claims, not measurements. Statements about
this project's data come from `INVENTORY.md` (16 Aug 2026) and operator report; `database/wq.db` was
not present in the working copy used for this analysis.*
