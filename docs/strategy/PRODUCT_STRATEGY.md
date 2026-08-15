# Alpha — From Working Tool to Sellable Product

**A plain-language strategy document**
Prepared 14 August 2026

---

## The one-paragraph version

You have built a very good digging machine for a gold rush. Selling digging machines will not work — free ones already exist, and if everyone digs in the same place nobody gets paid. But you are the only person who can see where all the machines are digging, so you can sell **the map**. Give the machine away, charge for the map. Cap membership at roughly 250–300 people, because the map stops being true above that. That is a real business worth roughly ₹4–6 crore a year at the top end, run by a very small team.

Everything below explains that, and what to check before believing it.

---

## 1. Where you are today

You have a working system that finds trading signals ("alphas") on the WorldQuant BRAIN platform. Two things make it unusual:

**It digs in the right places.** Instead of competing with 18,000 people over the same obvious price-and-volume data, it hunts through less-crowded datasets — company fundamentals, news sentiment, analyst estimates, options data.

**It can tell real gold from fake gold.** This is the rarer skill. If you test 500 ideas, some will look brilliant purely by luck. Your system uses several statistical checks — the plateau test, the Deflated Sharpe Ratio, split-period consistency — to throw those out. Most people mining for alphas cannot do this, and it is the reason their hit rates are so poor.

You are at the point where the machine works for **you**. That is not yet a product, and the gap between the two is the subject of this document.

---

## 2. Three problems with the obvious plan

The obvious plan is: host it online, let people sign up, charge monthly. Here is why that specific plan fails.

### Problem one — you would be operating on land you do not own

If customers hand you their BRAIN passwords and you run simulations for them from your servers, your business exists entirely at WorldQuant's discretion. They can end it with a single email. Worse, your customers get their accounts banned, not you — so the damage lands on the people who trusted you.

It is also technically obvious from their side: hundreds of accounts all making requests from the same small set of IP addresses is exactly the pattern that gets flagged.

**And note the incentive:** WorldQuant pays consultants for alphas. They have a direct financial reason to police tools that industrialise this. This is not a rule you can quietly hope goes unenforced.

> **Action:** Have a lawyer read the BRAIN consultant agreement and terms of use before you spend money building anything hosted. This document is strategy, not legal advice.

### Problem two — the machine itself is already free

There are at least half a dozen public, free BRAIN mining tools on GitHub. Several already use AI to generate expressions. "I will sell you an automated alpha miner" is not a business, because the thing you are selling has a price of zero elsewhere.

What those free tools do *not* have is the fake-gold detector, or any awareness of what other people are doing. Those are your two real assets.

### Problem three — your customers would compete with each other

This is the one that matters most, and it is easy to miss.

Your system's correlation check works **within one person's portfolio**. It stops *you* from submitting the same signal twice. It has no idea what anyone else is doing.

So if fifty people run your machine, your allocator sends them all toward the same promising under-mined datasets. They dig up similar signals. Duplicates get rejected.

**The product gets worse as it gets more popular.** That is a business that breaks precisely when it starts working.

---

## 3. The product: give away the digging, sell the map

Problem three is not a flaw to patch. Turned around, it is the entire business.

Every customer running your machine is a scout. Each one reports back: *tried this dataset, this mechanism, found nothing.* Or: *found something good.* You are the only party in the world who can see all of that at once.

That lets you sell something nobody else can build: **a live map of where the ground is already taken.**

- A free GitHub tool cannot build it — it only ever sees one user.
- WorldQuant will not build it — it works against their interest.
- A competitor cannot copy it — they would have to start with zero scouts.

And it compounds: more members means a better map, and a better map is worth more, which attracts more members.

### Why the two halves must ship together

You cannot sell the map without giving away the machine, because **the machine is how the map gets its data.** They are not two products bundled for convenience. They are one product with two faces:

- The **machine** collects information and is the reason people install anything at all.
- The **map** uses that information and is the reason people pay every month.

The fake-gold detector rides along as the second paid component — and unlike the map, it works for any kind of trading research, not just BRAIN. Keep that in mind for later; it is your escape route if BRAIN ever closes.

---

## 4. How to sell it without holding anyone's password

You wanted a hosted subscription business. You can have one, without the legal exposure, by splitting it:

**On the customer's own computer** — the digging machine, running under their own BRAIN account with their own credentials, exactly as your desktop build works today. Nothing changes architecturally. You never see a password. No simulation ever leaves your infrastructure.

**On your servers** — the map, the crowding registry, the field catalogue, the AI triage, the dashboards, billing.

The two talk to each other over a simple connection: the machine reports where it dug and what it found; the map tells it where to go next.

You get recurring revenue, real usage data, updates you control, and a product that cannot be pirated — because the valuable half was never on their computer. And the thing you charge for is precisely the part that cannot be downloaded from GitHub.

---

## 5. How many customers can this actually serve?

This is the question that determines the shape of the whole business, so it is worth doing carefully.

### How much ground exists

BRAIN has roughly **4,367 data fields**. But many are near-duplicates — several slightly different measures of company debt will produce very similar signals, and BRAIN's correlation check treats them as the same thing.

A reasonable haircut puts genuinely distinct fields at **1,000–2,000**. Each of those supports perhaps **2–5** alphas different enough from each other to count separately.

**Estimated total territory: ~5,000 good spots.**

> This is an order-of-magnitude estimate built from your own field counts, not a measured figure. Your database can produce a much better number — see section 9.

### How much 1,000 customers would need

For someone to keep paying every month, they realistically need **10–20 accepted alphas per year**. Below that, the subscription does not justify itself.

| Members | Alphas needed per year | Territory available | Verdict |
|---|---|---|---|
| 1,000 | ~15,000 | ~5,000 | Exhausted in ~4 months |
| 500 | ~7,500 | ~5,000 | Exhausted in ~8 months |
| 300 | ~4,500 | ~5,000 | Works for year one |
| 250 | ~3,750 | ~5,000 | Works, with headroom |

New data does arrive — BRAIN adds datasets — but slowly. Assume it regenerates **10–20% of the ground per year**, which supports perhaps 50–100 members' worth of ongoing demand as older members churn out.

Also remember the other ~15,000 consultants are digging too, without your map, consuming the same ground. That argues for the lower end.

### The answer

**Roughly 250–300 members, steady state.** Not 1,000.

If you sign 1,000, the map runs dry in the first year, most members find nothing, they cancel, and they tell people why. The failure is not gradual — it is a cliff, and it takes your reputation with it.

---

## 6. Why the cap is good news

The instinct is to see a ceiling as a disappointment. It is the opposite.

| Approach | Members | Monthly price | Annual revenue | Does it work? |
|---|---|---|---|---|
| Volume | 1,000 | ₹4,000 | ₹4.8 crore | **No** — product fails, mass churn |
| Scarcity | 300 | ₹15,000 | ₹5.4 crore | **Yes** |

The capped version makes **more money** and actually delivers what it promises.

Scarcity is not damage control here — it is the marketing message:

> *"We accept 300 members. Not one more. The map is worthless if we oversell the ground, and we would rather turn away revenue than sell you a map to a field that is already dug."*

That is a genuinely strong thing to say, and almost nobody in software gets to say it honestly. It justifies premium pricing, creates a waitlist, and — importantly — it is the clean answer to the trust problem in the next section.

Think of it as fishing licences. You do not issue unlimited ones and hope.

---

## 7. The trust problem, and how to solve it

If your map directs 300 people, some get better ground than others. Members will notice, and they will ask the obvious question:

> *"You mine your own account too. Are you sending me to the poor spots and keeping the good ones?"*

That is a fair question, not paranoia. And if you cannot answer it cleanly, the map is worthless — because the map **is** trust. It has no other substance.

Three ways out:

**Publish the allocation rule.** State exactly how fresh ground is assigned — rotation, first-come, whatever you choose — and let members verify it. People accept a rule they can see. They do not accept a black box. *This is the one I would choose.*

**Exclude yourself.** Keep mining your own account, but formally bar yourself from any territory the map is currently assigning to paying members, and say so publicly.

**Stop mining entirely.** The most trustworthy option and the hardest, since that is your income today. Worth reconsidering once subscription revenue exceeds your alpha payouts.

My recommendation is the first two together: publish the rule, exclude yourself from assigned ground, and state both plainly on the website. The enforced membership cap from section 6 is what makes the promise credible — a company chasing unlimited signups could not make it.

---

## 8. What you are really building

Worth being clear about the long game, because it changes what you build first.

**The map** is a BRAIN-specific asset. It is extremely valuable, it has a network effect, and it is capped at a few hundred members. It is also entirely dependent on WorldQuant's continued goodwill.

**The fake-gold detector** is not BRAIN-specific at all. Overfitting is the central problem in every kind of quantitative research. Small funds and proprietary trading desks have the same problem and much bigger budgets.

So: **use the map to build the business, and build the detector so it can outlive BRAIN.** Concretely, keep the statistical filtering code independent of anything BRAIN-specific from the very first day of productisation. It costs almost nothing now and it is your insurance policy — if BRAIN ever shuts you out, you still have a product.

---

## 9. The one fact that could change all of this

Everything in section 5 rests on one assumption I could not verify from public sources:

> **When one person's alpha is accepted, is that ground used up for everybody else?**

The public material I found only describes BRAIN checking your alphas against **your own** previous alphas. If that is the whole story, then your customers do not block each other at all, the capacity cap largely disappears — and so does much of the map's value, since there would be nothing to avoid.

There is a middle case, which I suspect is the real one: BRAIN checks new alphas against its **production portfolio**, which absorbs what has already been accepted from everyone. Under that version, ground genuinely gets consumed and the maths in section 5 holds.

**You can settle this yourself in five minutes.** Open any alpha in your BRAIN account and look at the submission checks. Is there a correlation check against production or platform alphas, separate from your own self-correlation? That single answer determines the size, price, and defensibility of this entire business.

Do this before anything else.

---

## 10. What to do next

### First — two checks, this week, before spending anything

**1. The correlation question above.** Five minutes in your BRAIN account. Everything else depends on it.

**2. Does the machine work for anyone but you?** This is the harder one, and it is the most common way projects like this die.

You have said the alphas are good. The honest question is *whose* judgment is producing them — the machine's, or yours during the morning review? If it is yours, you have a valuable personal tool and not a company, and it is much better to learn that now.

**The test:** give your current desktop build to three other BRAIN consultants. Let them run it for one month with no help from you beyond setup. Compare their acceptance rate before and after.

- If their rate improves → you have a product. Proceed.
- If it does not → the edge is your judgment. That is still worth something, but the business is a different one, and you should stop before building infrastructure.

Do not skip this because you are confident. Confidence is exactly what makes people skip it.

### Then — roughly the first ninety days

| Phase | Focus |
|---|---|
| **Weeks 1–2** | Both checks above. Lawyer reads the BRAIN terms. |
| **Weeks 3–6** | Build the reporting link: machine reports where it dug, server records it. This is the seed of the map, and it works even with three users. |
| **Weeks 7–10** | Turn the recorded data into a real crowding map. Build the allocation rule and publish it. Add accounts and billing. |
| **Weeks 11–12** | Open a waitlist with the cap stated publicly. Onboard the first 20–30 members by hand — you will learn more from watching them than from any amount of planning. |

### Open questions for later

- Exact pricing, once you know how many alphas a typical member actually gets
- Whether to keep mining your own account at all
- Whether the detector becomes a separate product for funds, and when

---

## Summary

| | |
|---|---|
| **Product** | Free digging machine on the customer's computer; paid map and detector on your servers |
| **Charge for** | The map (where ground is taken) and the detector (which signals are real) |
| **Customer** | WorldQuant BRAIN consultants, capped |
| **Size** | 250–300 members, deliberately limited |
| **Price** | Premium — roughly ₹12,000–18,000/month, justified by the cap |
| **Moat** | Network effect: every member makes the map better for the rest |
| **Biggest risk** | WorldQuant's terms of service — get legal advice first |
| **Verify first** | Does one person's accepted alpha consume ground for everyone? |
| **Long game** | The detector is platform-independent — build it that way from day one |

---

*Estimates in this document are order-of-magnitude figures derived from the project's own documentation, not measured results. The capacity model in section 5 should be re-run against your actual field database once the correlation question in section 9 is settled. Nothing here is legal advice.*
