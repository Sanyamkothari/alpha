# Phase 1 Operating Guide

**Sixteen weeks. What you personally do, and when to stop.**

The building is finished. Nothing below is a coding task — it's the part where the machine runs and you find out whether it works. This phase fails through drift and lost momentum far more often than through anything technical.

---

## Before you scale — three blockers

**1. Find your submission quota.** Still unanswered, and it determines whether this plan is feasible. Check your BRAIN account tier and the submission dialog. If it's 3+/week you have room; if it's 1/week, 40 attempts takes ten months and everything below stretches accordingly.

**2. Run `record_past_attempts.py`.** Ten minutes. Write down every submission you've attempted and which check killed it. "2 of 2" and "2 of 15" are completely different businesses, and only you know which one you have.

**3. Ramp, don't jump.** 50/day for three days → confirm no rate limiting, no errors, campaigns resume cleanly after a restart → 100/day for three days → 200/day. Your BRAIN account is the one thing here you cannot replace.

---

## Week 1 — close the loop once, by hand

Before automating anything, prove the whole path works end to end with the new machinery.

Run one small campaign. Open the console. Pick the top candidate. **Check its self-correlation badge.** Press `c`, paste into BRAIN, attempt the submission. Whatever happens, resolve the attempt in the Unresolved queue — including, especially, if it fails.

One recorded attempt through the new pipeline is worth more this week than ten thousand simulations. You are testing the loop, not the alphas.

---

## The weekly rhythm

At 200 simulations/day:

| | Weekly | 16 weeks |
|---|---|---|
| Simulations | ~1,400 | ~22,400 |
| Family runs | ~28 | ~450 |
| Territories | ~84 | ~1,344 |
| **Submission attempts** | **2–3** | **40** |

**The only number that matters is the last row.** Simulations are cheap and easy to accumulate; they can create a comforting illusion of progress. Forty recorded attempts is the deliverable.

### Monday — 20 minutes

Open the Throughput view. Check three things:

- Attempts logged last week. Below 2, diagnose why *this week*, not next month.
- Unresolved attempts. Should be near zero — every unresolved row is data quietly evaporating.
- Failure-by-check. This is your diagnostic panel; see below.

### Daily — 15 minutes

Review the morning queue. Check self-correlation badges before copying anything: **red means it will bounce, so don't spend an attempt on it.** Attempt one submission when a candidate is worth it. Resolve the attempt immediately while BRAIN's response is still on screen.

### Friday — 10 minutes

Confirm the nightly campaign is still running and hasn't silently stalled. Check that the calibration arm is still enabled.

---

## Reading the failure-by-check panel

This is the most valuable thing the system now produces. Whichever check dominates tells you what to fix:

**`SELF_CORRELATION` dominates** → your library is still too homogeneous. Push harder on operator families and wrapper shapes; back off the plateau-fill arm, which deepens rather than widens.

**`PROD_CORRELATION` dominates** → your alphas collide with the *platform's* production pool. Painful in the short term, but it is direct evidence that ground is shared across users — which is the premise the entire map business rests on. Record these carefully.

**`LOW_SHARPE` or `LOW_FITNESS` dominates** → your local filters are too permissive. They are letting through alphas BRAIN rejects, which means the fake-gold detector is not yet detecting.

**Failures are spread evenly** → no systematic problem. Keep going.

---

## Do not switch off the calibration arm

Thirty percent of your budget goes to randomly chosen territory including deliberately crowded fields, and it will produce almost nothing usable. That is its purpose.

It exists so the Phase 2 study has variation to analyse. The first attempt at that study was impossible partly because every alpha sat in the same narrow band of crowding. Around week 6 you will be tempted to reclaim that 30% for "real" research. Don't — that decision would cost you the ability to answer the question this whole phase exists to answer.

---

## Checkpoints

### Week 4 — is the loop turning?

- 8+ attempts recorded?
- 3+ distinct operator families in the library?
- Campaigns surviving restarts unattended?

If attempts are under 4, something structural is wrong. Diagnose before continuing — do not simply push more simulations at it.

### Week 6 — first real analysis

You should have ~500 territories, which is enough to detect a 50% effect. Run the validation protocol early, as a dry run. Not for the answer — to find out whether the analysis pipeline works and whether anything is still missing from the data. Better to discover a gap at week 6 than week 16.

### Week 10 — the honest midpoint

- 25+ attempts recorded?
- What is the pass rate?
- Do alphas that cleared plateau/DSR/subperiod pass BRAIN's gate more often than those that didn't?

That last question tests your filter stack, and it may be the most commercially important thing you learn all phase — it's your platform-independent asset.

### Week 16 — the decision

Run the full protocol. Then answer, in order:

1. **Can you produce alphas that pass BRAIN's gate repeatably?** Under ~10% pass rate: the machine isn't ready and there is no product conversation yet.
2. **Does your filter stack earn its place?** No gap between filtered and unfiltered alphas means the detector isn't detecting.
3. **Does crowding predict passing?** Only now is this answerable.

---

## What would make me say stop

Worth writing these down in advance, while you're clear-headed:

- **Week 8 with under 10 attempts.** The bottleneck isn't the tool, and more building won't fix it.
- **Pass rate under 10% at 25+ attempts.** The machine doesn't work well enough yet. That's a research problem, not a product one.
- **Filtered and unfiltered alphas pass at the same rate.** Your differentiator isn't real, and it's the piece you'd have sold.
- **You stop opening the console for two weeks.** The most common ending, and the one worth naming out loud. If the daily loop doesn't fit your life, the business version won't either — because members will need to do the same thing.

None of these mean the work was wasted. They mean you learned something real for a few months of effort rather than a few years.

---

## What not to do for sixteen weeks

No product features. No billing, accounts, landing page, waitlist, or outreach. No crowding map. No fertility model. No changes to the statistical filters — they must stay fixed so this phase can test them.

If you find yourself with spare energy, spend it on **more submission attempts**, not more code. That is the only scarce resource in this phase.

---

## The one sentence

You have spent five weeks building a machine and two weeks instrumenting it. The next sixteen are about producing forty pieces of evidence — and every one of them requires you personally to paste something into BRAIN, press submit, and write down what happened.

Everything after this is waiting on that.
