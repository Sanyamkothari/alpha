# The Business Model

**Alpha — map + digger**
14 August 2026. Supersedes the earlier strategy document on pricing and market size.

---

## The one-page version

You are not selling software. You are building **a credit bureau for alpha research.**

Every member runs your digging machine on their own computer. The machine automatically reports what it tried and what happened — mostly failures, because 95% of attempts fail. In exchange, each member sees the pooled result: which ground is exhausted, which is being worked right now, and which kinds of ground are currently producing.

No member would share this with a rival directly. They will share it with a neutral third party, because they get back three hundred people's worth of information for one person's worth of contribution. That is exactly how Experian and Equifax work, and it is one of the most durable business models that exists.

**The deal is simple and non-negotiable: report your results, or you don't get the map.**

Three things follow from this, and they are the whole business:

1. The map is built from **failures**, which are abundant — not from successes, which are rare. This solves the cold-start problem.
2. A bureau that also competes with its members is not a bureau. **You will eventually have to stop mining your own account.**
3. First mover takes it. There is room for exactly one of these.

**Numbers:** break-even at ~45 members. Roughly ₹1.8 crore a year at 250 members with 86% margins. Not a venture-scale company — an excellent small one.

---

## 1. The problem this solves, stated properly

WorldQuant pays for alphas that are *different* from what it already has. So the scarce resource on BRAIN is not skill or compute. It is **unexplored ground.**

And here is the thing that makes this a business: **the problem gets worse the more successful you are.**

A beginner with 5 submitted alphas has no correlation problem — everything they find is new to them. A Grandmaster with 300 submitted alphas has a severe one. Almost everything they try now collides with something they already own, or with something already in production. They spend most of their effort rediscovering their own past work.

So the people in most pain are the people with the most money. That alignment is rare and it is the foundation of the business.

---

## 2. Why this is a bureau, not a tool

This distinction decides everything downstream, so it is worth being precise.

**A tool** is something you sell a copy of. Its value is fixed on the day you ship it. Someone can rebuild it. Free versions on GitHub compete with it. Your fake-gold detector — the plateau test, the Deflated Sharpe Ratio, the stability checks — is a tool. It is excellent work, but a strong quant could rebuild it in a few weekends, and your best customers *are* strong quants.

**A bureau** is something you subscribe to. Its value is created by its members and grows every day. It cannot be rebuilt, because a copy of the software is worthless without the members. The map is a bureau.

You need both, but understand which one you are charging for. The digger is how you get data. The map is the business.

### Why members will actually share

This is the question that kills naive versions of this idea, and it deserves a direct answer.

A competitive quant will never voluntarily tell a rival what they are mining. But they will report to a neutral aggregator, for the same reason banks report to credit bureaus despite competing fiercely:

**You get back far more than you put in.** One member contributes their own results and receives 300 members' worth. The trade is overwhelmingly in their favour.

**It is automatic.** The machine reports as it runs. There is no form to fill in, no decision to make, no moment where someone weighs whether to share.

**It is mandatory.** No reporting, no map access. This is not a dark pattern — it is the only structure that works. If reporting were optional, everyone would switch it off and take the map for free, and the map would die. Say this openly in the terms: *"Membership means contributing. That is what makes the map exist."* People understand it immediately, because they understand what they are getting.

**What gets shared is narrow.** You need to know *which field, which mechanism, which settings, and what the outcome was.* You do not need their expression, their full portfolio, or anything they would consider proprietary. Be explicit about the boundary and publish it. This distinction is what makes the whole thing socially acceptable.

---

## 3. Solving the cold start: the map is built from failure

The obvious objection is that the map needs hundreds of members before it is any good, but you cannot charge premium prices until it is good.

That objection dissolves once you see where the data comes from.

**The map is not built from accepted alphas.** Those are rare — perhaps 10–20 per member per year. If that were the input, you really would need hundreds of members and years of time.

**The map is built from simulations.** Every member runs hundreds of backtests a month, and the overwhelming majority fail. Each failure is a data point: *this field, this mechanism, this window — dead.* Negative results are the cheapest and most abundant thing your machine produces, and until now you have been throwing them away.

Run the numbers against an estimated ~5,000 distinct spots of territory:

| Members | Observations per month | Territory coverage |
|---|---|---|
| 3 | ~900 | 0.2× |
| 25 | ~7,500 | **1.5×** |
| 50 | ~15,000 | 3× |
| 100 | ~30,000 | 6× |
| 300 | ~90,000 | 18× |

**At 25 founding members you are already re-covering the entire territory 1.5 times every month.** The map is dense from almost the first day. This is the single most important fact in this document, and it is why the business is feasible for one person to start.

You also begin with three free sources before a single member joins:

- **BRAIN's own crowding data** — user counts and alpha counts per field, which you already collect for all 4,367 fields. A cumulative baseline across all 16,000 consultants.
- **Your own mining history** — every simulation you have already run.
- **Public alpha examples and competition results** — sparse, but free.

**Action:** change the machine to record every simulation outcome permanently, starting now, before you have any members at all. Every day you delay is data you never get back. This is the same work as the experiment-ledger item on your gap list, which is why that item ranked first.

---

## 4. What a member actually receives

The value claim has to be concrete or it cannot be tested. Here is what a member sees when they open the console on a Monday morning.

```
┌────────────────────────────────────────────────────────────┐
│  YOUR MAP — Monday 17 August                               │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ⛔ EXHAUSTED — don't waste simulations here                │
│     fundamental6 / debt-to-equity, 22–63d windows           │
│     Tried 340 times by 23 members. Best Sharpe 0.91.        │
│     Verdict: mined out. Saved you ~340 simulations.         │
│                                                            │
│  🔒 CLAIMED — being worked right now                        │
│     news12 / sentiment-reversal, 5–10d      until 24 Aug    │
│     analyst4 / revision-momentum, 63d       until 19 Aug    │
│                                                            │
│  🌱 FERTILE — hit rate 3.2× baseline this month             │
│     option9 / skew-change, 10–22d windows                   │
│     Only 4 members have touched it. 2 promotions in 14d.    │
│     → Claim this ground   [c]                               │
│                                                            │
│  🆕 NEW GROUND — appeared in the catalog this week          │
│     model16 added 34 fields. Nobody has mined them.         │
│     → 6 mechanisms suggested   [n]                          │
│                                                            │
│  ⚠️  YOUR PORTFOLIO                                         │
│     3 of your 47 submitted alphas now correlate >0.5        │
│     with newly-accepted production alphas. Details ▸        │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

Four distinct kinds of value, in increasing order of how hard they are to copy:

**Exhaustion** — *don't dig here, it's empty.* Saves wasted simulations. Simple, immediately obvious, and the easiest thing to demonstrate in a sales conversation.

**Claims** — *someone is working this right now.* Prevents collisions. This is the piece that makes membership feel like a club with rules rather than a data feed.

**Fertility** — *this kind of ground is producing.* The most valuable and least obvious. With 90,000 observations a month you are not just recording which spots are taken — you are **learning what makes ground fertile in the first place**, and that generalises to data BRAIN has not released yet. This is a genuine model, it improves with scale, and no individual can build it.

**New ground alerts** — *the catalog changed, and you knew first.* Fresh territory is worth the most, and being first to it is worth more still. This is what makes members open the console every single day.

Note that the fertility layer is the real long-term moat and the hardest to fake. Build the first two to get members; the third is what keeps them.

---

## 5. The neutrality requirement

You currently mine your own BRAIN account. That is your income today, and I understand why giving it up feels absurd.

But consider the position from a member's side. They are paying you for advice on where to dig, while you dig the same ground. The first time a member is steered away from a field and later discovers you mined it, the map is dead — and not just for that member. They will post about it, and in a community of a few hundred people that ends the business in a week.

**A credit bureau that also lends money is not a credit bureau.** Neutrality is not a nice-to-have here; it is the product.

The practical sequence:

**Now, while you are still small:** publish the allocation rule so anyone can verify it, and formally exclude yourself from any ground the map is currently assigning to a member. State both on the website in plain language.

**Once subscription revenue covers costs and replaces your alpha income** — roughly 60 members on the model below — **stop mining entirely and announce it loudly.** "We do not mine. We cannot compete with you. That is why you can trust the map."

That announcement is worth more than the mining income it costs you. It is a promise no competitor who is also a consultant can make, and it converts your biggest structural weakness into your strongest marketing claim.

---

## 6. Pricing

WorldQuant has already sorted your customers by ability to pay, so use their tiers. Their published figures:

- **Grandmaster**: "upwards of $8,000 or more" per quarter → roughly $2,700/month
- **Master**: "upwards of $2,000 or more" per quarter → roughly $670/month

(That is WorldQuant's own marketing language and describes the ceiling, not the median. Treat it as optimistic.)

| Tier | Price | Share of their income | Notes |
|---|---|---|---|
| **Grandmaster** | ₹12,000/mo | ~5% | Covered in their first week of a quarter |
| **Master** | ₹4,500/mo | ~8% | Your volume tier |
| **Below Master** | Not sold | — | Cannot afford it, and consumes ground |
| **Scout tier** (later) | Free or ₹500 | — | Only once the map is proven. Contributes data, receives exhaustion warnings only, no claims and no fertility. |

The scout tier is worth planning for but not launching early. It feeds the map cheaply — but every scout still consumes territory, so it must stay small and must never receive the fertility layer, which is what people pay for.

### Entry offers

**Three validation testers:** free. They are doing you a favour, not buying something.

**Founding 25:** half price, locked permanently, in exchange for a monthly feedback call. You are giving up 25 of ~300 slots, so the lifetime discount costs little, and "locked forever" is a real reason to take a risk on something unproven.

**Billing starts at your first accepted alpha.** Offer this to the founding cohort. It removes the entire objection — *if it doesn't work for you, you never pay* — and it points you at the outcome that matters rather than at signups. Make it a start-date trigger, never a percentage of their WorldQuant payouts; taking a cut of their earnings is legally messier and probably touches the BRAIN terms.

---

## 7. The financial picture

### Steady state (~250 members)

| | Members | Price | Monthly |
|---|---|---|---|
| Grandmaster | 50 | ₹12,000 | ₹6,00,000 |
| Master | 200 | ₹4,500 | ₹9,00,000 |
| **Total** | **250** | | **₹15,00,000** |

**₹1.8 crore per year.**

### Costs

| | Monthly |
|---|---|
| Servers and hosting | ₹40,000 |
| LLM triage across all members | ₹60,000 |
| Legal and accounting | ₹40,000 |
| Payment processing (~3%) | ₹45,000 |
| Support tooling and misc | ₹20,000 |
| **Total** | **₹2,05,000** |

**Gross margin: 86%.** Roughly **₹1.55 crore/year** before salaries.

The cost structure is unusually good because **the expensive part runs on members' computers.** They pay for their own compute and their own BRAIN account. You are running a database and a website. This is why one person can operate it.

### Break-even

**About 45 Master-tier members, or 17 Grandmasters.** That is a small enough number to reach by personal outreach — which is fortunate, because at this price point personal outreach is the only channel that will work anyway.

### Honest ramp

| | Members | Revenue |
|---|---|---|
| Year 1 | 28 | ₹18 lakh |
| Year 2 | 135 | ₹86 lakh |
| Year 3 | 250 | ₹1.8 crore |

Year one does not replace a salary. Plan for that.

### What kind of business this is

Be clear-eyed: this is a **profitable small company**, not a venture-scale startup. The market is a few hundred people who can afford it, and the capacity ceiling is real. Investors will not fund it, and you should not want them to — ₹1.5 crore a year for one to three people, at 86% margins, with a genuine moat, is a better life than most funded startups deliver.

The venture-scale version, if you ever want it, is the detector sold to funds — a different business with a different customer. Keep that door open by building the statistical filtering independent of anything BRAIN-specific from day one. It costs almost nothing now.

---

## 8. What kills this

Three things genuinely can, in order of severity.

### WorldQuant builds it themselves

**This is the big one, and it needs stating plainly: WorldQuant already has all the data.** They see every simulation from every consultant. They could publish a crowding map tomorrow and it would be better than yours.

Why haven't they? Because it isn't their priority, redundant consultant effort costs them nothing (they only pay for what they accept), and their product focus is the platform rather than tooling for consultants. But they already publish per-field user counts, so it is a small step for them, not a large one.

Three responses:

**Be faster and finer.** Their data is field-level and historical. Yours is mechanism-level and live. That gap is defensible for a while, but not forever.

**Build the detector as your independent asset.** If BRAIN closes, the fake-gold detector still sells.

**Consider talking to them.** This is worth serious thought once you have proof it works. WorldQuant wants uncorrelated alphas. Your product produces uncorrelated alphas. There is a version where you are a blessed partner or an acquisition rather than a tolerated workaround — which converts your single biggest risk into your single biggest asset. It is a real risk that they simply copy you after the meeting, so do not have that conversation until you have members, data, and evidence. But have it eventually.

### The terms of service

You avoid the worst of it by keeping credentials and simulation on the member's own machine — you never hold a password and never touch BRAIN's API from your servers. That is a genuinely defensible position, but "defensible" is a lawyer's word, not mine.

**Get the BRAIN consultant agreement and terms of use professionally reviewed before you take a single rupee.** This document is strategy, not legal advice.

### The value simply isn't there

The map might save members effort without meaningfully increasing their accepted alphas. That is a real possibility and no amount of good design rules it out.

The test that separates these: does the product move someone **up a tier**, or just make them somewhat more efficient?

- Master → Grandmaster is roughly a $2,000/month income jump. At ₹4,500/month that is a twelvefold return, and nobody hesitates.
- A Grandmaster getting 15% more alphas is worth maybe $130/month. At ₹12,000 they are losing money.

Same product, same price, opposite outcomes. You cannot know which you have until members report real earnings — so measure that from the first cohort, not signups or engagement.

---

## 9. What to do, in order

### This week — two checks that cost nothing

**1. The crowding backtest.** You already have BRAIN's per-field user counts and your own history of what was accepted. Test the premise directly: *do alphas on low-crowding fields get accepted more often than alphas on crowded fields?* If yes, the map's foundation is proven before you write any new code. If the relationship is weak, the map is a beautiful idea built on sand — and you have saved yourself a year.

**2. The correlation question.** Open any alpha in BRAIN and look at the submission checks. Is there a correlation test against production or platform alphas, separate from your own self-correlation? If BRAIN only checks you against yourself, members don't block each other, the claims layer loses most of its value, and the product becomes exhaustion + fertility only. Still a business, but a different one.

Do both before building anything.

### Weeks 1–6 — build the ledger and start collecting

Record every simulation outcome permanently. Make jobs survive a restart. Fix the missing numpy/scipy declaration before anyone else installs it. Add a crude JSON export so testers can send results back.

Nothing here is glamorous, and all of it is the map's foundation.

### Weeks 6–10 — the three-user test

Three consultants, one month, no help beyond setup. Measure three things:

- Did their acceptance rate improve? *(Is the machine good, or is it your judgment?)*
- Did they collide with each other? *(Random collisions at n=3 are near zero, so any collision proves the allocator herds people — which is the disease the map cures.)*
- Did their **earnings** change? *(The only number that justifies the price.)*

### Weeks 10–16 — the first map

Aggregate what you have into the exhaustion layer plus new-ground alerts. Publish the allocation rule. Add accounts and billing. Open a waitlist with the cap stated publicly.

### Months 4–9 — founding 25 and the fertility layer

Recruit by hand, personally, from the top tiers. Twenty-five members gives you 1.5× territory coverage every month, which is enough to build the fertility model — the part nobody can copy.

### Month 9 onward

Grow to break-even at ~45. Then stop mining your own account and announce it. Then grow toward the cap.

---

## 10. The decision points

Four moments where you should be willing to stop or change direction:

| When | Question | If the answer is no |
|---|---|---|
| This week | Does crowding predict acceptance? | The map's premise is wrong. Stop and rethink before building. |
| Week 10 | Did the machine work for people who aren't you? | You have a personal tool, not a company. Valuable, but different. |
| Month 6 | Did founding members' **earnings** rise? | Cut the price to what the value actually supports, or stop. |
| Month 9 | Are you willing to stop mining? | The bureau cannot be trusted while you compete with it. This is the hardest one, and it is not optional. |

---

## Summary

| | |
|---|---|
| **What it is** | A credit bureau for alpha research. Members contribute results, receive the pooled map. |
| **Given away free** | The digging machine, running on the member's own computer with their own account |
| **Sold** | The map: exhausted ground, live claims, fertile ground, new-ground alerts |
| **Why members share** | Mandatory, automatic, and they get 300× what they give |
| **Cold start solved by** | Building the map from failures (abundant), not successes (rare) |
| **Customer** | BRAIN Grandmasters and Masters — a few hundred people, reached by personal outreach |
| **Price** | ₹12,000/mo Grandmaster, ₹4,500/mo Master |
| **Break-even** | ~45 members |
| **Steady state** | ~250 members, ₹1.8 crore/yr, 86% margin |
| **Moat** | Network effect. The map is worthless as software and valuable only as a membership. |
| **Non-negotiable** | You must eventually stop mining. Neutrality is the product. |
| **Biggest risk** | WorldQuant builds it themselves — or, better, partners with you |
| **Prove first** | Does crowding predict acceptance? Do members earn more? |

---

*Financial figures are modelled from WorldQuant's published tier payouts and the project's own field counts. The ~5,000-spot territory estimate is order-of-magnitude and should be recomputed against your actual database. Nothing here is legal or financial advice — have the BRAIN terms professionally reviewed before trading on any of it.*
