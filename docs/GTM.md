# Fastest path to an actual business

**Founder's answer, 2026-08-19.** Companion to `docs/FOUNDER_REVIEW.md`.
Numbers below are from `docs/INVENTORY.md` (2026-08-15), which supersedes `report.md`.

---

## The number that decides everything

| Metric | Value |
|---|---|
| Alphas generated | 4,857 |
| Simulated | 531 |
| Submitted (marked locally) | 3 |
| **Accepted by BRAIN** | **unknown — 0 outcomes recorded** |
| Fields ever touched | **29 of 6,583** (0.44%) |
| Share of alphas from 12 fields / one operator (`ts_zscore`) | **94.9%** |

You do not know your hit rate. The funnel terminates at a human pressing `s` in
your own console; nothing records what BRAIN did next. Every version of this
business — the map, the tool, the subscription — is a claim about that number.

**You cannot sell a map when you have not confirmed your own digging works.**
Fixing this is a day of work and it is the gate on everything below.

---

## Why the current plan is the slowest path, not the fastest

`alphaproductstrategy.md` proposes: give away the machine → members become scouts →
sell the aggregated map. The economics are sound and the trust analysis is better
than most founders manage. But it has a cold start it never addresses:

> No members → no scout data → no map → no reason to become a member.

Every network-effect product dies here unless the founder seeds the network alone.
Seeding this one means 250–300 members' worth of coverage from one account — at
0.44% catalog coverage after five weeks, that is years, not months.

So the plan as written has a time-to-first-revenue measured in quarters, and it is
gated on an assumption `alphaproductstrategy.md` §9 admits is unverified.

---

## The unlock: you already own a map that needs zero members

The plan treats "the map" as something that only exists once customers feed it.
That is true of *one layer* of it. It is not true of the others.

| Layer | What it is | Members needed | Status |
|---|---|---|---|
| **L1 — Static crowding** | `user_count` / `alpha_count` per field, 6,583 fields, 33 datasets | **none** | **you have this today** |
| **L2 — Crowding over time** | Who is moving into which datasets, week over week | **none** | starts accruing the day you cron the fetcher |
| **L3 — Scout hit-rates** | What was tried and failed — which BRAIN does not expose | **many** | the actual network effect |

L1 is BRAIN's own metadata, already fetched, already in your database. The
distribution alone is a product: median field has 16 users, Q3 is 158, the max is
48,210. `pv1` averages 18,485 users/field against 131 for `fundamental2`. Anyone
mining BRAIN would pay to know that, and it requires no other customer.

L2 is the one that is **perishable and cheap**. `data_field_snapshots` exists and
`fetch_brain_catalog.py` writes it — but only when you run it. Every day it does not
run is a day of history that can never be reconstructed, by you or by a competitor.
In six months a daily snapshot is an asset nobody can catch up to. **Cost: one cron
line. Start it today, before anything else in this document.**

L3 is your plan. It is v3, not v1.

**That reordering is the whole answer to "fastest."** You do not need the network to
start selling.

---

## The plan

### Week 1 — three things, all cheap, all in parallel

1. **Close the funnel.** Record BRAIN's accept/reject for the 3 submitted alphas.
   The `platform_outcomes` and `submission_attempts` tables already exist. Until
   this number is real you are selling a hypothesis.
2. **Cron the catalog fetch, daily.** Starts L2 accruing. Ten minutes of work; the
   only asset here that compounds and the only one that punishes delay.
3. **Talk to 10 BRAIN consultants.** Two questions: *what do you earn from BRAIN in a
   year?* and *what would you pay for a live map of where the ground is taken?* This
   is the highest-information week available to you and it costs nothing.

### Weeks 2–5 — buy the right to make a claim

Run the loop for **breadth, not depth**. 94.9% of your alphas are one operator over
12 fields — that is a sweep, not a search, and it is why 4,857 alphas produced 3
submissions. Go wide: 50+ fields across the six unexplored datasets the allocator
already names.

One metric: **distinct, uncorrelated alphas confirmed accepted by BRAIN.** Not
simulated, not promoted, not marked — accepted.

That number is simultaneously your product's core claim, your marketing, your
pricing power, and your own income. Nothing else you can do in four weeks produces
all four.

### Weeks 4–6 — first revenue, no network required

- **Publish a free crowding report.** The L1 distribution, the 170x gap between
  `pv1` and `fundamental2`, the observation that delay-0 is ~18x less crowded. This
  is genuinely novel, nobody else has published it, and it is your demand test: if a
  free report earns no audience, a ₹15,000/month one earns no customers.
- **Sell L1+L2 as a data product.** Low price, one-time or cheap monthly, no
  support burden, no password ever touched, no hosted simulation. Ships this month.
- **Waitlist for the live map.** Convert readers into the 10–20 design partners who
  become your first scouts — which is how L3 gets seeded without the cold start.

### Month 3+ — the business you actually described

With scouts feeding L3 and a confirmed acceptance rate, ₹15,000/month is defensible
and the 250–300 cap becomes a marketing asset rather than a limitation. This is the
same destination as `alphaproductstrategy.md`; it just arrives via revenue instead
of via a build.

---

## Bootstrap revenue nobody has counted

The fastest revenue in this project requires no customer at all: **run the machine
on your own account and take BRAIN's consultant payouts.** No legal exposure, no
product work, no marketing, no support.

It is gated on account level. `docs/BRAIN_API.md` records this account as
`level: NONE`, `permissions: ["TUTORIAL"]` — which means no earnings, USA/delay-1
only, and the 18x-less-crowded delay-0 catalogue locked. Meanwhile
`docs/INVENTORY.md:363` calls it a consultant account. **Resolve that contradiction
this week.** If it is TUTORIAL, climbing BRAIN's own ladder is a higher-return
activity than any feature in the backlog, because it unlocks both the income and the
uncrowded data the entire strategy depends on.

---

## Two things that can kill this — test them early

**1. The affordability trap.** BRAIN payouts are heavily skewed. The consultants who
can comfortably pay ₹1.8L/year are the top few hundred earners — precisely the ones
who least need your map. The thousands who would benefit most earn too little to
justify it. If your 10 conversations confirm this shape, the business is a low-price
volume product or a services business, not a ₹5 crore subscription. **Find out in
week 1, not month six.**

**2. Legal.** Shipping a local tool that runs under the customer's own credentials is
one thing. **Selling a dataset derived from authenticated scraping of BRAIN's
catalogue is a materially different act**, and it is the thing this plan monetises
first. `alphaproductstrategy.md` §2 already says get a lawyer to read the consultant
agreement. Do it before you sell L1 — that is now the first revenue event, not a
later one.

---

## Monday

1. Cron the catalog fetch. **Today.** It is the only thing that gets worse by waiting.
2. Find out what BRAIN did with your 3 submitted alphas.
3. Resolve the account level question.
4. Book 10 consultant conversations for the week.
5. Point the loop at 50 new fields, not 384 more variants of the same 12.

Everything else — the server, billing, the sync protocol, the desktop installer,
evolution, composites — waits for the number from step 2 and the conversations from
step 4.
