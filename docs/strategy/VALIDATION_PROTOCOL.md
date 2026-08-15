# Validation Study Protocol — v2

**Does research territory predict alpha acceptance?**
Pre-registered design. Supersedes v1 of 14 August 2026.

> [!IMPORTANT]
> **Document Provenance:** Restored from `alphahandoff.zip` (timestamp: `2026-08-15 18:18 UTC`) during code review follow-up F14. This pre-registered protocol defines the hypotheses, statistical power analyses, and primary endpoints for Phase 1.

> **v2 changes:** territory key and crowding metric locked to single definitions; the two studies separated by outcome; unit of analysis corrected from alpha to territory (this changes the power analysis fundamentally); Study B promoted to primary; baseline ladder added; out-of-sample test promoted to decisive; H6 added; economic threshold restated as an assumption.

---

## 0. Read this first — the correction that changes the study

v1 computed power at the level of individual alphas. **That was wrong, and the error was large.**

The hypothesis is about *territory*. Every alpha inside one territory shares the same crowding value, so they are not independent observations. Worse, this system's constructor deliberately generates **200–800 near-duplicate candidates per mechanism** by grid expansion. That is the whole design.

So:

| Alphas in database | Candidates per family | Effective sample size |
|---|---|---|
| 5,000 | ~400 | **~12 territories** |
| 10,000 | ~400 | **~25 territories** |
| 20,000 | ~300 | **~66 territories** |

**10,000 alphas drawn from 25 mechanisms is n = 25, not n = 10,000.**

Recomputed at the territory level, with outcome "did this territory yield at least one hit" at an assumed 30% base rate, p < 0.01, 80% power:

| Relative lift | Territories per group | Total territories needed |
|---|---|---|
| 25% | ~930 | **~1,860** |
| 50% | ~245 | **~490** |
| 100% | ~65 | **~130** |

Three consequences, and they restructure the whole study:

1. **Count your distinct territories before anything else.** That single number, not your alpha count, decides whether Study A is a real test or a screen. If you have mined 40 mechanisms, Study A can only detect enormous effects.
2. **Study B is now the primary study.** BRAIN's 4,367 fields is the only dataset with enough independent units to test the commercially relevant effect size.
3. **The three-researcher test is no longer confirmation — it is the main event.** Three researchers exploring fresh territory is the fastest way to add independent observations, which is the binding constraint.

---

## Part 1 — Pre-registration

*Complete, commit to git, and only then touch the data.*

### 1.1 Territory — locked definition

```
territory_key = field_id + operator_family + horizon_band

  horizon_band: short (1-10d) | medium (11-63d) | long (64d+)
```

**Everything else is a covariate, not part of territory.** Window, decay, neutralization, truncation, universe, region and delay describe *how* a territory was explored, not *which* territory it is. Two researchers using different decay on the same field, operator family and horizon are working the same ground.

This is now fixed. Alternative definitions may be explored, but only reported as exploratory.

### 1.2 Crowding — locked primary metric

**Study B primary:** `unique_researchers_90d` — distinct researchers who explored the territory in the preceding 90 days.

Rationale: 500 experiments by one researcher is not the same signal as 500 experiments by 50 independent researchers.

> **⚠️ This metric is unavailable in Study A, and that matters.** Your own history has exactly one researcher, so `unique_researchers_90d` is constant and undefined as a predictor. This is a real limitation of the metric the critique proposed and it must not be papered over.
>
> **Study A primary crowding metric:** BRAIN's published `user_count` for the field, **as of the simulation date**. It is cruder — field-level rather than mechanism-level — and this weakens Study A. Note it as a limitation rather than pretending the two studies test the same thing.

**Secondary crowding metrics** (reported, never promoted to primary after the fact): experiment count, alpha count, cumulative historical user count, your own prior experiments on the territory.

### 1.3 Two studies, two outcomes — do not mix them

| | **Study 1 — Acceptance** | **Study 2 — Promotion** |
|---|---|---|
| Population | Alphas you submitted | Alphas you simulated |
| Outcome | Accepted / rejected by BRAIN | Passed your filters / did not |
| Strength | The real economic outcome | Far more data; excludes your judgment |
| Weakness | Small; conditioned on your judgment | Proxy outcome, not money |
| Status | **Primary** | **Secondary, pre-registered** |

Study 2 is not a fallback to reach for if Study 1 disappoints. It is a distinct pre-registered hypothesis with its own threshold, reported either way.

### 1.4 Thresholds — decided in advance

| Out-of-sample lift | Verdict |
|---|---|
| ≥ 50% | **Strong pass** — build the network, lead with the number |
| 25–50% | **Pass** — build MVP, price low, re-test at 25 members |
| 10–25% | **Marginal** — find a second value layer before charging |
| < 10% | **Fail** — stop; the organising principle is wrong |
| Underpowered | **Inconclusive** — not a fail; proceed to the researcher test |

**Statistical bar:** p < 0.01, with standard errors clustered by territory.

**The economic assumption behind these numbers.** A Master earns roughly ₹58,000/month and pays ₹4,500, so an ~8% lift in accepted alphas covers the fee *if earnings scale linearly with accepted alphas*.

> **That linearity is an assumption, not a fact.** BRAIN pays on tiers with quality weighting and production-correlation constraints, so the true relationship is probably non-linear and possibly steppy near tier boundaries. The thresholds above are therefore a *business hypothesis*, and must be validated by measuring **actual earnings** in the researcher trial — not inferred from acceptance rates.

### 1.5 Hypotheses

**H1 (primary).** Territory crowding predicts acceptance, after controls.

**H2.** Mechanism-level crowding predicts better than field-level crowding. *(If false, your map can be much simpler and cheaper.)*

**H3.** Similarity to your own prior submitted alphas predicts rejection.

**H4.** Recency matters — territory mined in the last 90 days is more exhausted than territory mined two years ago.

**H5.** The relationship is **non-monotonic**: moderately-crowded territory outperforms both untouched and heavily-mined territory.

> Pre-registered specification, so this cannot become an after-the-fact reading of a chart: fit `accepted ~ log(crowding) + log(crowding)²`. H5 is supported if the quadratic term is significant at p < 0.01 **and** the fitted peak falls strictly inside the observed crowding range.
>
> If H5 holds, the product improves: the advice becomes *"find the optimal research density"* rather than *"avoid crowded ground."* Untouched ground may be untouched because it is worthless.

**H6 (network, tested at the researcher stage).** Researchers using the map produce *less mutually correlated* research than researchers working independently.

> This is a separate business claim from H1 and is testable at n = 3. Even if the map never improves any individual's hit rate, reducing collective overlap is the thing WorldQuant pays for — and it may be the stronger product story.

---

## Part 2 — The baseline ladder

**The single most important addition in v2.** Your model must beat simple heuristics, not random chance. Report every rung:

| | Strategy | Expected lift |
|---|---|---|
| B0 | Random territory | 1.00× by definition |
| B1 | Pick lowest field-level crowding | ____ |
| B2 | Pick highest field-level crowding | ____ |
| B3 | Field crowding + coverage + delay | ____ |
| B4 | Mechanism-level crowding | ____ |
| B5 | Full model (all covariates) | ____ |

The finding is in the **gaps between rungs**, not in B5's absolute number.

- If B5 = 1.57× and B1 = 1.18×, the sophisticated map earns its existence.
- If B5 = 1.35× and B1 = 1.32×, **the product is a one-line heuristic** — which is not a failure, it is excellent news. You would build something far simpler and cheaper, and skip the fertility model entirely.

Do not skip the boring rungs. B1 is the competitor that actually threatens you, because any member can implement it in an afternoon.

---

## Part 3 — Confound controls

Run the primary test three ways: raw, controlled, and out-of-sample.

**C1 — Skill drift over time.** You improved, and may have drifted toward uncrowded fields as you did. Include submission date as a covariate; additionally re-run on the last 12 months alone.

**C2 — Settings quality.** Include neutralization, decay, universe, region.

**C3 — Data quality.** Include field coverage, delay, update frequency. **This is the confounder most likely to be the real story** — uncrowded fields may be uncrowded because their data is poor. If so, that is a better product, not a worse one: rank on data quality *and* crowding.

**C4 — Your own selection.** Study 2 (promotion outcome) removes your judgment from the loop and exists for this reason.

**C5 — Clustering.** Standard errors clustered by territory in both studies; additionally by researcher in Study B if researcher-level data is available. Report alphas, territories, submissions, acceptances, and unique researchers separately — never a single "n".

---

## Part 4 — Method

**Step 0 — Count territories.** Before any analysis. Report the distribution of alphas per territory. If a handful of territories hold most of your alphas, say so prominently; it caps everything that follows.

**Step 1 — Range check.** Plot crowding across your territories. If there is little variation — likely, since you have been deliberately avoiding crowded ground — Study A cannot answer H1 and Study B carries the weight.

**Step 2 — Descriptive.** Acceptance rate by crowding quartile with confidence intervals. **For visualisation only.** Quartiles hide the shape of the relationship, which is what you actually need.

**Step 3 — Continuous model.** `accepted ~ log(crowding) + log(crowding)² + controls`, clustered SEs. Report the crowding coefficient with and without controls; **the gap between them is the headline finding**, because it measures how much of the raw effect was confounding.

**Step 4 — Out-of-sample temporal test. This is the decisive test.**

Train on everything before a cutoff (suggested: 31 Dec 2025). Predict the following six months. Report lift in the top-ranked 20% of territory against every rung of the baseline ladder.

This is decisive because it simulates the actual product experience. Your customer's question is *"it is Monday, where should I dig?"* — so the validation question is **"could a map built from yesterday's data have helped a researcher tomorrow?"** A regression coefficient fitted on the full history cannot answer that. This can.

**Step 5 — Secondary hypotheses.** H2–H5, all reported.

---

## Part 5 — Reporting

```
SAMPLE
  Alphas ____   Territories ____   Alphas/territory: median ____ max ____
  Submitted ____   Accepted ____   Unique researchers ____
  Smallest detectable lift at this n: ____%

PRIMARY — Study 1 (acceptance)
  Crowding coefficient, raw:         ____
  Crowding coefficient, controlled:  ____
  p-value (clustered SE):            ____

OUT-OF-SAMPLE (decisive)   cutoff ____   n after cutoff ____
  B0 random            1.00x
  B1 lowest crowding   ____x
  B2 highest crowding  ____x
  B3 + data quality    ____x
  B4 mechanism-level   ____x
  B5 full model        ____x
  VERDICT (per 1.4):   Strong pass / Pass / Marginal / Fail / Inconclusive

SECONDARY — Study 2 (promotion)     ____
  H2 mechanism > field ____   H3 self-similarity ____
  H4 recency ____             H5 quadratic term ____ , peak at ____

EXPLORATORY (everything not pre-registered above)
  ...
```

Then follow the table in 1.4 without renegotiating it. It exists so that today's you constrains next month's you, who will want the answer to be yes.

---

## Part 6 — The researcher trial

Given the power problem, this is no longer a confirmation step. It is where the independent observations come from.

**Pre-registered:** H1 replication on pooled data, and H6 (collision reduction).

**Measured but not pre-registered** — record prospectively, analyse as exploratory:

| Metric | Why it matters |
|---|---|
| Accepted alphas per month | The outcome that pays |
| **Earnings** | Validates the economic assumption in 1.4 — the only number that tests the price |
| Experiments run | Productivity |
| Experiments avoided | Direct map value |
| Research hours saved | May justify the price even if acceptance barely moves |
| New territory discovered | Network value |
| Pairwise overlap between the three | H6 |
| Retention | Do they keep using it unprompted |

Note the second and fifth rows together: if the map cuts 1,000 wasted experiments to 250 while acceptance only moves 12% → 15%, the product may be valuable through *time saved* rather than *alphas gained*. That is a different pitch and a different price, and you will only find it if you measure it.

---

## Part 7 — The decision tree

| Stage | Result | Decision |
|---|---|---|
| Historical | No signal at all | **Stop the map concept** |
| Historical | Signal, but does not generalise out-of-sample | Do not build the network |
| OOS | Full model does not beat B1 | Build the one-line heuristic instead; skip fertility |
| OOS | < 10% | Reconsider the economics entirely |
| OOS | 10–25% | Test a second value layer before charging |
| OOS | 25–50% | **Build the MVP network** |
| OOS | > 50% | **Go aggressively** |
| Trial | No behavioural or economic change | Stop the network |
| Trial | Outcomes improve | **Scale** |

**The governing principle:** a p-value can tell you the effect probably exists. It cannot tell you a Master will pay ₹4,500 a month for it. Let out-of-sample **economic** lift make that decision.

---

## Part 8 — What to build regardless

Two things have positive expected value under every branch of that tree:

**The experiment ledger.** Record every simulation outcome permanently, starting today. The study needs it, the trial needs it, the network needs it — and if the business dies you still own a much better research system. Data you do not record is gone forever.

**The numpy and scipy declarations.** Ten minutes. Otherwise the three testers' installs break.

Build nothing else — no billing, no accounts, no network backend, no fertility model, no waitlist, no dashboard — until Part 7 has a value in it.

---

## A closing note on this protocol

This is v2, and it is now good enough. The remaining uncertainty is not in the design — it is in your data, and specifically in one number: **how many distinct territories have you actually mined?**

Further refinement of this document has sharply diminishing returns against simply running Step 0. Protocol polish is a comfortable way to avoid finding out. Count the territories, then run it.

---

*Written before the data was examined. Amendments must be dated and recorded below, not made silently.*

**Amendments:**

*v2, 14 Aug 2026 — unit of analysis corrected from alpha to territory; power analysis recomputed; Study B promoted to primary; territory key and crowding metric locked; baseline ladder, H6, and clustered SEs added; economic threshold restated as an assumption requiring independent validation.*
