# Revised Roadmap

**Based on the project inventory and operational progress (Updated 20 August 2026)**
Supersedes the sequencing in the business model and validation protocol.

---

## Progress & Operational Evidence

| Milestone Metric | Baseline (15 Aug 2026) | Verified State (20 Aug 2026) | Status |
|---|---|---|---|
| **Simulated Alphas** | 486 alphas | **695 distinct alphas (740 backtests)** | 178 cleared BRAIN checks |
| **Recorded Submissions** | 0 tracked / 3 unverified | **17 recorded attempts (10 active in OS)** | 10 submissions accepted into Out-of-Sample |
| **Stored Daily PnL** | 369 vectors | **390 daily vectors** in `database/pnl/` | Auto-differenced with sidecar metadata |
| **Point-in-Time Crowding** | Overwritten on fetch | **6,268 snapshots stamped** at creation | `alpha_field_snapshot` preserved |
| **Statistical Hardening** | Basic plateau & DSR | **EVT Gumbel, Lo SE Z-tests, CSCV, Ridge Clustering** | Multi-testing haircuts institutionalized |
| **Test Suite** | 176 tests | **262 tests passing (100%)** | Clean CI run in ~7.60s |

**The operational imperative:** Phase 0 instrumentation is complete, and the system is actively generating evidence toward the 40-submission Phase 1 milestone. 10 alphas are currently active in Out-of-Sample on WorldQuant BRAIN.

---

## Phase 0 — Instrumentation (Completed)


Everything here is cheap, and every day of delay destroys data you cannot get back.

### 0.1 Commit your work — today, before anything else

Seventeen untracked files, including `subperiod.py`, `correlation.py`, `pnl_storage.py`, `allocator.py`, `composite_constructor.py`, `evolution.py`, and seven test files.

**The core statistical work — the part that is actually yours — exists on exactly one disk.** Commit and push. Two minutes.

### 0.2 Record acceptance outcomes

Your funnel currently ends at "user pressed `s`." There is no field anywhere for whether BRAIN accepted the alpha.

**Without this you can never learn whether any of this works.** Not in six months, not ever. Every submission you make until this exists is a lost observation.

Minimum viable version:

```
alphas.platform_outcome     accepted | rejected | pending
alphas.outcome_date
alphas.outcome_note         rejection reason if BRAIN gives one
```

Plus one keystroke in the console to set it, and a backfill for the three already submitted. Half a day at most.

### 0.3 Snapshot crowding with an as-of date

`fetch_brain_catalog.py` deletes and replaces. So the moment you refresh the catalog, the crowding history you would need to test the map's core premise is gone.

Two changes:

**Stop deleting.** Add `as_of_date` and append rather than replace, so you accumulate revisions.

**Stamp every alpha at creation** with the field's `user_count`, `alpha_count`, and coverage *at that moment*. This is the more important of the two — it makes every future alpha a valid observation for the study, regardless of what happens to the catalog.

A day's work that converts an impossible study into a possible one.

### 0.4 Fix the three install-breakers

- Commit the `numpy` and `scipy` declarations in `pyproject.toml`
- Fix the default database path divergence (`~/.alpha-research/` vs the project directory)
- Fix `/api/system/modules`, which reports six working modules as unimplemented

Any of these would break another person's install. All three are under an hour combined.

---

## Phase 1 — Months 1–4: use your own tool

This is now the whole job. Not product, not map, not network.

### The goal

**Find out whether you can produce accepted alphas repeatably.** Everything downstream is unanswerable until this is answered.

### Target: 40 submissions with recorded outcomes

Why 40: it estimates your true acceptance rate to roughly ±15%. That is enough to tell a 10% rate from a 40% one, which is the distinction that decides whether there is a business.

| Confidence | Submissions needed |
|---|---|
| ±20% (very rough) | 21 |
| **±15% (rough — the target)** | **36** |
| ±10% (usable) | 81 |

Your current rate is 0.57 submissions/week. At that pace, 40 takes **16 months**. To get there in four you need about **2.3 per week** — a fourfold increase.

### Three things have to widen at once

**Throughput.** 13 simulations/day → 150–200/day. The runner already supports it; you simply have not been using it. This is the easiest of the three.

**Territory.** 29 fields → 300+, across many datasets. At 0.44% coverage you have barely sampled your own catalog, and you cannot make claims about which ground is fertile having stood on almost none of it.

**Structure.** One operator family → many. Every alpha you have used `ts_zscore`. Your operator KB has 105 operators and 479 compatibility edges, and you have exercised a tiny corner of it. Wire the composite constructor and the evolution engine into the reachable CLI path — they are written and tested but only callable from library code, so in practice they do not exist.

### What to measure the whole time

Because Phase 0 instrumented it, this accumulates for free:

- Acceptance rate overall, and by dataset, by crowding level, by mechanism
- Which filter stages actually predict acceptance — **your plateau/DSR/subperiod stack is itself unvalidated.** With 11 alphas having passed DSR and zero known outcomes, you do not yet know whether your fake-gold detector detects fake gold
- Simulations consumed per accepted alpha — this becomes the "hours saved" number the product would sell on

That second one deserves emphasis. The detector is your platform-independent long-term asset, and it is currently an untested hypothesis. Phase 1 tests it.

### The one external fact still outstanding

Open BRAIN, look at any submitted alpha's checks, and write down whether there is a correlation test against **production or platform alphas** separate from self-correlation. Your agent could not do this; it needs your logged-in session. Five minutes, and it determines whether the "claims" layer of the map has any value at all.

---

## Phase 2 — Month 4: the decision point

With 40 outcomes recorded, ask three questions in order.

**1. Can you produce accepted alphas repeatably?**

- Acceptance rate under ~10% → the machine does not work well enough yet. Keep improving it. There is no product conversation.
- 10–30% → it works. Continue.
- Over 30% → it works well. Move faster.

**2. Does your filter stack earn its place?**

Compare acceptance rates for alphas that passed plateau/DSR/subperiod against those that did not. If there is no gap, your detector is not detecting, and that is a research problem to fix before it is a product to sell.

**3. Only now — does crowding predict acceptance?**

By this point you have point-in-time crowding on every alpha (from 0.3), hundreds of territories (from Phase 1), and real outcomes (from 0.2). The validation protocol becomes runnable. Run it as written.

---

## Phase 3 — Months 5–9: three other researchers

Unchanged from the earlier plan, but now it is properly sequenced — you are testing whether a machine *known to work for you* also works for others, rather than testing two unknowns at once.

Note that in its current state the three-user test would fail for reasons that have nothing to do with your ideas: uncommitted files, missing dependencies, and a database path that needs an environment variable. Phase 0.4 exists to prevent that.

---

## Phase 4 — Month 9 onward: the product

The business model document stands. The bureau structure, the map-and-digger split, the neutrality requirement, the tier pricing, the capacity analysis — none of that is invalidated by the inventory. It was simply about six months early.

Pick it up again when Phase 2 says yes.

---

## What not to do now

No billing. No accounts. No network backend. No fertility model. No landing page. No waitlist. No pricing page. No outreach to potential members.

And specifically: **do not build the map yet.** You would be building a map of territory you have not explored, from outcomes you have not recorded, for members you cannot yet promise anything to.

---

## The honest summary

You have built a genuinely capable research machine in five weeks — a working compiler, 105 seeded operators, a full multi-stage statistical filter, LLM triage, a desktop build, and 176 passing tests. That is real and it is the hard part.

What you do not have is any evidence that it works. Three submissions and zero recorded outcomes cannot tell you, and no amount of strategy can substitute for the measurement.

So the next six months are not a product-building phase. They are **the phase where you become the first proof that your own tool works.** Every alpha you submit with its outcome recorded is one data point toward a company. Right now you have zero.

Start recording today. The instrumentation is a week; the evidence is four months; the business is on the other side of it.

---

*Sequencing revised against the project inventory of 15 August 2026. The business model and validation protocol remain valid as written — this document changes only when they apply.*
