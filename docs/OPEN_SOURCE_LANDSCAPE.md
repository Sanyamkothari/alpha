# Open-Source Landscape Review — What We Are Not Doing

Survey date: 2026-08-21. Reviewed against the repository at `683fe54`.

> [!NOTE]
> **Superseded in two places by `OPEN_SOURCE_LANDSCAPE_R2.md`.**
> 1. §2's "CANNOT DETERMINE the binding constraint on 13/day" is answered in our own docs:
>    `docs/PHASE1_OPERATING_GUIDE.md:11-15` states the 4/day submission quota is not binding,
>    prescribes a 50→100→200/day ramp, and names diversity as the real constraint.
> 2. §G5's implication that we lack expression dedup is wrong: `backend/app/models/alphas.py:57`
>    declares `expression_hash` with a unique index. What is missing is *semantic* equivalence.
>
> Round 2 also answers two of the three open questions in `CLAUDE.md` (submission quota and
> PROD_CORRELATION semantics) and finds four more dead-code modules beyond `calibrate_proxy_rankings`.

## 0. How to read this

This compares our engine against the open-source and published alpha-mining
ecosystem. It is a **research note, not a work order.** Most of what follows is
out of scope for Phase 1 by `CLAUDE.md` rule 3 (filters are frozen) and by the
Phase 1 rule that the metric is submission attempts, not engineering. Items are
tagged accordingly:

- **[P1-SAFE]** — can be done now without touching a frozen filter or the metric
- **[P2]** — deliberately deferred until Phase 1 produces its 40 outcomes
- **[NO]** — do not adopt; our different assumption is the correct one

Claims about our own code were verified by grep/read at the commit above and are
marked *verified*. Claims that need the production database are marked
**CANNOT DETERMINE — no DB in the working tree** (`find . -name "*.db"` returns
nothing here), and the numbers in `CLAUDE.md` are used as given.

---

## 1. What was surveyed

**Direct comparables — automated formulaic alpha mining**

| Project | What it is |
|---|---|
| [alphagen](https://github.com/RL-MLDM/alphagen/) (KDD'23) | PPO over expression trees; builds a *synergistic pool*, not single alphas |
| [AlphaForge](https://github.com/dulyhao/alphaforge) (AAAI'25) | Generative–predictive: a surrogate predicts fitness so candidates need not be evaluated; dynamic test-time factor combination |
| [AlphaSAGE](https://github.com/BerkinChen/AlphaSAGE) | RGCN encoder over expression graphs + GFlowNet generator; explicitly targets *mode collapse* |
| [AlphaPROBE](https://github.com/gta0804/AlphaPROBE) (Feb 2026) | Models the factor pool as a DAG; Bayesian retriever picks seeds; generator conditions on full ancestral trace |
| [QuantaAlpha](https://github.com/QuantaAlpha/QuantaAlpha) (Feb 2026) | LLM + evolution at the *trajectory* level — mutates the research run, not just the factor |
| [RD-Agent](https://github.com/microsoft/RD-Agent) / [Qlib](https://github.com/microsoft/qlib) | Hypothesis → implementation → backtest → feedback loop with a persistent trace |
| [alpha-gfn](https://github.com/nshen7/alpha-gfn) | GFlowNet formulaic factor generation |

**BRAIN-specific tooling**

[worldquant-miner](https://github.com/zhutoutoutousan/worldquant-miner),
[Brainiac](https://github.com/jdhruv1503/Brainiac),
[WQ-Brain](https://github.com/RussellDash332/WQ-Brain),
[WQ-Brainn](https://github.com/dige04/WQ-Brainn),
[wq_new](https://github.com/TonyMa1/wq_new).

**Statistical / infrastructure tooling**

[arch](https://github.com/bashtage/arch) (SPA, StepM, MCS),
[pypbo](https://github.com/esvhd/pypbo) (PBO via CSCV),
[alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded),
[skfolio](https://github.com/skfolio/skfolio),
[PySR](https://github.com/MilesCranmer/PySR), gplearn, DEAP,
[NautilusTrader](https://github.com/nautechsystems/nautilus_trader),
[pysystemtrade](https://github.com/pst-group/pysystemtrade),
Optuna / Ray Tune (ASHA, Hyperband).

---

## 2. The one difference that explains most of the others

**Everyone else has a free oracle. We have an expensive one.**

Every project above computes factor values on a local price/fundamental panel and
scores them with IC or Rank IC in milliseconds. Their search algorithms — PPO,
GFlowNets, genetic programming — are all designed around an evaluation that costs
approximately nothing, so they can afford 10⁴–10⁶ evaluations per run.

Our evaluation is a BRAIN simulation: external, rate-limited, opaque, and per
`CLAUDE.md` running at **~13/day against a 200–500/day target**.

*Verified:* the string `IC` / `information_coefficient` / `rank_ic` does not appear
anywhere in `backend/app/**.py`. We have no local factor-value computation, no
local returns panel, and `pandas` is not a dependency (`pyproject.toml`). Our only
quality signal is what BRAIN hands back.

Two consequences follow, and they run in opposite directions:

1. **We cannot copy their search algorithms.** A PPO or GFlowNet policy needs
   thousands of reward samples to move. At 13/day, an RL run would take years.
   Anyone proposing "let's add RL alpha mining" has not costed the oracle.
2. **We are therefore obliged to copy their *cost-reduction* work,** which we have
   largely not done. This is where the real gaps are, and they are in §4.

Note also `backend/app/services/simulation_runner.py`, which states the platform
ceiling is ~2,800 sims/day from the 3-concurrent cap, and worldquant-miner
documents a 5,000/day limit. So the 13/day figure is **not** a platform
constraint. **CANNOT DETERMINE** from the working tree what the actual binding
constraint is; `docs/PHASE1_OPERATING_GUIDE.md` records the 4/day *submission*
quota as non-binding, which is a different quantity. This is worth one query
before any throughput work is planned.

---

## 3. Assumption diff

| Dimension | Us | The field | Comment |
|---|---|---|---|
| Definition of a good alpha | Passes BRAIN's `checks[]`, then gets *accepted* | High IC / Rank IC / ARR on a held-out slice | Ours is the harder and more honest target |
| Value of an alpha | **Marginal** — gated on correlation with our own portfolio and (suspected) the platform pool | **Standalone** predictive power | Fundamental. Their metric has no notion of a crowded pool |
| Cost of evaluation | High, external, rate-limited | ≈ free, local, fully observable | Drives every method difference |
| Unit of output | One expression a human submits | 50–100 factors fed into LightGBM | They never face a submission gate |
| Search space | 6,583 platform fields × 102 operators | OHLCV + a few derived features | Our space is far wider and barely touched (0.49% of fields) |
| Who writes syntax | Deterministic AST constructors, always | The LLM or the policy net, then repair invalid output | **We are stronger here** |
| Overfitting control | DSR + plateau + subperiod + correlation, pre-registered and frozen | Train/valid/test split; often nothing further | **We are much stronger here** |
| Diversity | Hard constraints in the allocator | An objective term (novelty reward, entropy bonus, pool IC) | They optimise it; we only forbid its absence |
| Pool structure | Flat territory grid + a parent/generation tree (*verified*: `alphas.parent_id`, `alphas.generation`) | AlphaPROBE: an explicit DAG with ancestral-trace conditioning | Their critique lands on us |
| LLM's job | Propose a field and an economic mechanism, once per dataset | Propose, implement, read the result, revise | **Our loop is open** — see G6 |

---

## 4. Gaps — ranked by value per unit of Phase 1 risk

### G1. No surrogate to rank candidates before spending simulation budget — [P1-SAFE]

This is the highest-value gap and it is almost built already.

AlphaForge's central contribution is a *predictive/surrogate model that estimates a
candidate's fitness so the expensive evaluation can be skipped* for most of the
population. That is precisely our situation with a 13/day oracle and (per
`CLAUDE.md`) 4,857 alphas of which 486 are simulated — roughly 4,371 candidates
queued behind a scarce resource, currently ordered by allocator policy rather than
by predicted quality.

*Verified:* `backend/app/services/proxy_calibration.py` already contains
`calibrate_proxy_rankings()`, which measures Spearman rank correlation between
proxy heuristics and real BRAIN metrics over historical payloads. Its only caller
in the entire repository is `tests/test_proxy_calibration.py:109`. **The code
exists and has never run on production data.**

Running it costs zero simulations and is pure measurement — it changes no filter
and no metric. If the rank correlation is even modest, ordering the simulation
queue by predicted quality raises passes-per-simulation with no extra budget,
which is exactly what a 40-attempt target needs. If it is zero, that is a finding
worth having.

### G2. No multi-fidelity screening — [P1-SAFE, needs one design decision]

worldquant-miner runs evolved candidates at a **1-year lookback first and only
promotes survivors to the 5-year validation.** This is successive halving —
the same idea as ASHA/Hyperband in Ray Tune and Optuna's pruners, where cheap
low-fidelity evaluations rule out bad regions before expensive ones are spent.

We simulate everything at full fidelity. *Verified:* `SimulationSettings` is passed
through unchanged; there is no short-window screening pass.

The caveat is real and must be stated: a short-window screen introduces a
selection stage that the frozen plateau/DSR filters do not model, so DSR's
multiple-testing haircut would be counting the wrong N. **Recommended as a
measurement first** — run a screen in parallel with, not instead of, the current
path and record whether short-window rank predicts full-window pass. Substituting
it into the pipeline is [P2].

### G3. Correlation is a gate, never an objective — [P2]

`INTERNAL_CORRELATION_THRESHOLD = 0.55` in `correlation.py` rejects a candidate too
close to the existing portfolio. Everyone else *optimises* the same quantity:

- alphagen scores the **pool**, not the alpha: combine the pool linearly, measure
  IC of the combination against the target, and prefer the alpha that most
  improves it (`calc_pool_IC_ret`).
- AlphaForge reweights the factor set dynamically at test time on recent
  performance.
- skfolio brings the standard portfolio machinery (Ledoit-Wolf shrinkage,
  hierarchical risk parity) to the same problem.

The difference matters because of the yield ceiling `CLAUDE.md` already
identifies: BRAIN's 0.70 self-correlation rule makes the practical yield ~one
submittable alpha per field. A gate can only ever *reject*; a marginal-contribution
objective actively searches for the alpha that adds most to what we already hold.
That is the correct frame for a correlation-constrained submission budget.

We even have the vehicle — `composite_constructor.py` already emits multi-field
composites — but it enumerates a *designed* grid rather than greedily assembling
components by marginal contribution.

Strictly Phase 2: this is a change to a frozen filter's role.

### G4. Diversity is a constraint, not an objective — [P2]

AlphaSAGE's diagnosis is the most directly applicable sentence in the whole
literature: *maximising expected reward drives a policy toward a single mode,
which contradicts the practical need for a diverse portfolio of uncorrelated
alphas.* Their fix is a GFlowNet (samples proportional to reward rather than
argmax) plus explicit novelty weighting (`nov_weight 0.3`) and entropy
regularisation (`entropy_coef 0.01`).

Our monoculture — 4,608 of 5,177 alphas on one template — is textbook mode
collapse arrived at by a different route.

In fairness we are not naive here. `allocator.py` enforces `MAX_DATASET_SHARE`,
forced exploration slots, territory-level exclusion and saturation caps, and
`evolution.py`'s fitness already includes a portfolio-orthogonality term. That is
diversity-as-hard-constraint, which is a defensible and more auditable design than
a tuned reward weight. The gap is that we have no measurement of *achieved*
structural diversity — a novelty score over the expression graph — to tell us
whether the constraints are working. **[P1-SAFE]:** measuring structural novelty
of what we have already generated costs no simulations.

### G5. The factor pool has no global structure — [P2]

AlphaPROBE (Feb 2026) argues that prior work treats factor pools as either
"unstructured collections" or "fragmented parent-child chains", and that both lack
a global structural view, "which leads to redundant search and limited diversity."
Their answer is to model the pool as a DAG and condition generation on a factor's
full ancestral trace.

We are the second case: *verified*, `alphas.parent_id` + `alphas.generation` give a
tree, and the allocator selects a *territory coordinate* rather than a position in
the derivation structure. Whether our search is actually re-deriving equivalent
expressions along different branches is **CANNOT DETERMINE** without the DB — but a
canonical-form hash over the normalised AST would answer it, and we already have
`validator.normalize`.

### G6. The LLM loop is open — [P1-SAFE]

*Verified:* `campaign_runner.py` contains no LLM call at all. Per
`models/prompts.py`, "the LLM fires once per dataset". Nothing feeds simulation
outcomes back to the proposer.

Both agentic systems close this loop:

- **RD-Agent** maintains an explicit *trace* — past designs, their measured
  outcomes, which implementations failed — so later proposals build on earlier
  evidence rather than re-exploring.
- **QuantaAlpha** goes further and evolves the *trajectory*: it localises the
  suboptimal step within a whole mining run and revises that step specifically,
  then recombines high-reward segments across runs.

Our justification for the open loop is sound and should be preserved: an LLM that
sees which alphas passed will start proposing toward the filter, which is
precisely the overfitting this project exists to avoid. But there is a safe middle
ground — feeding back **which fields have been exhausted, which mechanisms
produced degenerate or invalid structure, and which territories are saturated** is
bookkeeping, not outcome-fitting. That reduces wasted proposals without letting
the model tune to the metric.

### G7. Only one overfitting statistic, and we own the data for three more — [P1-SAFE as diagnostics]

We implement DSR (Bailey & López de Prado) with the Euler–Mascheroni expected
maximum, plus split-half and rolling-window stability. That is already ahead of
essentially every open-source project surveyed, none of which do more than a
train/valid/test split.

What is missing is that DSR answers one specific question — *is the best of N
inflated by selection?* — and the neighbouring tools answer different ones:

| Tool | Question it answers | Our input data |
|---|---|---|
| **PBO via CSCV** ([pypbo](https://github.com/esvhd/pypbo)) | Does in-sample rank predict out-of-sample rank? i.e. **does our selection procedure work at all?** | daily PnL vectors — already stored |
| **SPA / StepM / MCS** ([arch](https://github.com/bashtage/arch)) | Which alphas beat a benchmark *after* accounting for the whole search? | same |
| **Purged / embargoed CV, CPCV** | Removes leakage from overlapping label windows | same |

The important point: `pnl_storage.py` already persists date-aligned daily PnL per
alpha, and *verified*, its only consumers are `correlation.py`, `plateau.py` and
the UI. **PBO is computable today on data we already hold, with no new inputs and
no simulations.**

PBO is also the natural instrument for the question `docs/strategy/VALIDATION_PROTOCOL.md`
exists to answer — it directly measures whether the plateau filter's selection
generalises. Computing it as a **read-only diagnostic** does not tune or modify any
frozen filter. Wiring it into the pass/fail cascade would, and is [P2].

### G8. No simplification or Pareto bookkeeping — [P1-SAFE, small]

PySR's search is an *evolve–simplify–optimise* loop and its headline output is a
**complexity-vs-accuracy Pareto front**, not a single winner. We have degeneracy
detection (`is_degenerate_signal`) and bloat caps (depth ≤ 6, nodes ≤ 20), which is
partial — we reject the pathological but never simplify the merely redundant, and
we report a pass/fail cascade rather than a frontier.

For a project whose Phase 1 deliverable is *evidence*, a Pareto front over
(complexity, Sharpe, turnover, crowding) carries strictly more information than a
shortlist, and is a reporting change only.

### G9. Operator coverage — [P1, already the plan]

*Verified:* `operators/operators.yaml` seeds **102 operators** — 29 time-series,
23 arithmetic, 14 group, 12 logical, 10 cross-section, 6 vector, 6
transformational, 2 special. `CLAUDE.md` records one operator family in use before
Phase 1.

No change recommended — breaking the monoculture is what Phase 1 is already for.
Recorded here because it is the single largest quantitative gap against every
comparable, and worth stating plainly: our search space is far larger than theirs
and we have used ~1% of it.

---

## 5. Where we are ahead — do not "fix" these [NO]

1. **No code path submits.** Enforced by `tests/test_brain_no_post.py`. Every
   BRAIN miner surveyed auto-submits. Given that BRAIN penalises correlated
   submissions against your own portfolio, an auto-submitter burns the very quota
   this project is trying to measure.
2. **The LLM never writes syntax.** worldquant-miner's documented pipeline is
   LLM-generates → validator repairs → "self-correcting system learns error
   patterns", i.e. it produces invalid expressions as a matter of course. Our
   deterministic AST emission makes that class of failure unreachable. This is
   the clearest architectural win in the survey.
3. **Pre-registration.** `docs/strategy/VALIDATION_PROTOCOL.md` has no counterpart
   anywhere in this literature. Every paper reports a winning configuration on a
   test set after extensive iteration against that set.
4. **Territory as the unit of statistical analysis.** The papers count near-duplicate
   alphas as independent observations, which inflates every reported number. Our
   `field × operator_family × horizon_band` deduplication is the correct treatment
   and it is not standard practice.
5. **The unbiased random arm.** No surveyed project deliberately spends 30% of
   budget on territory it expects to be unpromising. It is the only reason a
   causal Phase 2 study is possible at all.
6. **Data discipline.** Derived `platform_outcome`, attempt-level recording
   including failures, append-only snapshot series. This is better than anything
   surveyed, most of which keep a CSV of winners.
7. **Measuring against a live platform.** Every academic result above is IC on
   CSI300/500. None faces a submission gate, a crowding pool, or another user's
   correlated portfolio.

---

## 6. What I would actually do

Nothing in the [P1-SAFE] list changes a frozen filter, and nothing consumes
simulation budget — which is the only Phase 1 currency that matters.

1. **Run `calibrate_proxy_rankings()` on the production database.** It is written,
   tested, and has never executed outside a test fixture. Zero simulations. Tells
   us whether we can order 4,371 queued candidates by predicted quality.
2. **Compute PBO from the stored PnL vectors as a read-only diagnostic.** Zero
   simulations, no new data, and it measures whether our selection procedure
   generalises — which is the question Phase 1 is ultimately about.
3. **Establish why throughput is 13/day when our own code says the platform
   ceiling is ~2,800/day.** This is a query, not a build. If the constraint is
   operator cadence rather than the platform, then G1 and G2 are worth much less
   than they look, and the honest answer is that Phase 1's bottleneck is not
   engineering at all.
4. **Add a canonical-AST hash and count true structural distinct-ness.** Uses the
   existing `validator.normalize`. Answers G5's open question and gives the
   diversity measurement G4 lacks.

Everything else — marginal-contribution portfolio selection (G3), diversity as an
objective (G4), DAG-structured search (G5), PBO in the cascade (G7) — is a Phase 2
design input. Recorded here so it is not re-derived later, and deliberately not
started.

---

## 7. Stated absences

- **CANNOT DETERMINE** — no production database in the working tree, so every
  count in this note is from `CLAUDE.md` rather than a query I ran.
- **CANNOT DETERMINE** — whether our search re-derives equivalent expressions
  across branches. Needs the canonical-hash query in §6.4.
- **CANNOT DETERMINE** — the true binding constraint on the 13/day throughput.
- **NOT PRESENT** — no IC/Rank IC computation anywhere in the codebase.
- **NOT PRESENT** — no local data panel, no local factor evaluation, no surrogate
  ranking in production, no multi-fidelity screening, no PBO/SPA/MCS, no
  simplification pass, no LLM outcome feedback.
- Not assessed: BRAIN's `PROD_CORRELATION` semantics, which remain the open
  question in `CLAUDE.md` and which no external source can settle.
