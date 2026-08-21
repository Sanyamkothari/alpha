# Competitive Feature Gap — what every rival project has that we don't

**Compiled 21 Aug 2026.** Web survey of every open-source and academic system that
mines alphas for WorldQuant BRAIN (or for factor discovery generally).

**Purpose.** A single shopping list. When Phase 1 ends and we are allowed to build
again, this file says exactly what to take from whom, and what to refuse.

**How to read the verdict column:**

| Verdict | Meaning |
|---|---|
| **TAKE** | Real gap, no invariant conflict, build it |
| **ADAPT** | Good idea, but must be reshaped to fit our constraints |
| **PHASE 2** | Would change a frozen filter — forbidden until Phase 2 passes (`CLAUDE.md` invariant 3) |
| **REFUSE** | Violates a hard invariant. Listed so nobody re-proposes it in six months |
| **HAVE** | Already built here — recorded so we stop counting it as a gap |

**Evidence quality.** Everything below was read from the project's own README/docs
except where marked *(snippet)* — those come from search-result summaries because
`arxiv.org`, `deepwiki.com`, `medium.com` and `worldquant-miner.world` were blocked
by the network egress proxy at compile time. Re-verify anything marked *(snippet)*
before building on it.

---

## 0. Our baseline — what we already have

Recorded so the gaps below are honest deltas, not a wish list.

- Deterministic AST constructor + validator (lexer, parser, KB, type checking) — `backend/app/validator/`
- LLM gateway with provider abstraction (Anthropic, OpenRouter, fake) + response cache — `backend/app/llm/`
- LLM field triage producing economic hypotheses with expected sign — `services/field_triage.py`
- MAB dataset allocator, diversity-capped UCB, budget arms — `services/allocator.py`
- Genetic evolution: population 30, AST mutation + crossover, multi-objective fitness
  (DSR × complexity × turnover × **orthogonality**) — `services/evolution.py`
- Plateau ridge test on the (window × decay) surface — `services/plateau.py`
- Deflated Sharpe Ratio + subperiod consistency — `services/plateau.py`, `services/subperiod.py`
- Self-correlation against own portfolio — `services/correlation.py`
- Field crowding from BRAIN's own user/alpha counts, append-only — `services/field_crowding.py`
- Polite batch simulator, 3 concurrent, backoff, no POST path — `services/simulation_runner.py`
- Append-only snapshots + submission-attempt ledger + platform-authoritative sync
- Territory model (`field × operator_family × horizon_band`)
- 36 test modules, `test_brain_no_post.py` enforcing the no-submit invariant

---

## 1. QuantGPT — `Miasyster/QuantGPT` (445★, MCP + Claude)

Closest architectural twin. Agent-driven factor research with an LLM orchestrator.
Reports 3 factors submitted to BRAIN passing IS (Sharpe 1.77 / 1.69 / 1.60), 370+
cumulative backtests, ~15 min per cycle of 8 candidates, 10–20 concurrent backtests.

| What they have | We have? | Verdict |
|---|---|---|
| **MCP tool surface (15 tools)** — the agent drives research through tools, not a fixed pipeline | No — our LLM is called at two fixed points (triage, slot fill) | **TAKE.** Biggest structural gap. An MCP layer over our existing services costs little and turns a batch pipeline into an interactive research agent |
| **Dual-LLM cross-review** — second model (DeepSeek) reviews the first's reasoning to catch bias | No — single-model, no adversarial check | **TAKE.** Cheap, and directly serves this project's anti-self-deception purpose |
| **Persistent knowledge base the agent reads before designing** | Partial — `validator/kb.py` is syntax knowledge, not research memory | **TAKE.** A "what we already tried and why it failed" store the LLM reads at triage time |
| **Four anti-overfit tests + walk-forward validation** | We have plateau + DSR + subperiod. **No walk-forward / OOS split at all** (`grep` for walk-forward → 0 hits) | **PHASE 2.** Adding a filter is frozen. Log the gap, build after validation |
| **~15 min per full cycle, 10–20 concurrent backtests** | 3 concurrent, ~13 sims/day | **ADAPT.** Their concurrency likely breaches polite-client limits; our cap is deliberate. But our *scheduling* is the bottleneck, not the cap — a continuous runner would close most of it |
| **QuantGPT Cloud** — hosted self-correlation dedup service | No hosted anything | **PHASE 2.** This is the map's first inch, built by someone else, free |
| **Auto-submission to BRAIN** | No, by design | **REFUSE.** Invariant 1 |

---

## 2. worldquant-miner — `zhutoutoutousan/worldquant-miner` (725★, 192 forks, Apache-2.0)

The volume leader. Ollama-based local LLM, Dockerised, GPU support.
Generation Two adds a self-optimising genetic layer.

| What they have | We have? | Verdict |
|---|---|---|
| **Local LLM via Ollama** (llama3.2:3b / llama2:7b / 1.5b–32b variants), GPU support | No — cloud providers only (Anthropic, OpenRouter) | **TAKE.** Marginal cost per hypothesis → zero. Our provider abstraction already exists; this is one new provider class |
| **Cascading LLM fallback chain** (Ollama → DeepSeek → simple template generation) | No — a provider failure stalls the run | **TAKE.** Small, obvious robustness win |
| **Self-correcting AST with error learning** — invalid expressions feed back into the prompt and a KB | Partial — we reject invalid ASTs but never feed the failure back | **TAKE.** Closes our loop; fits invariant 2 exactly (LLM still writes no syntax) |
| **Adaptive self-tuning every 100 simulations** — exploration/exploitation rebalanced on measured performance | No — allocator params are static | **ADAPT.** Careful: this is adjacent to the frozen filters. Tune the *allocator*, never the filters |
| **On-the-fly 1-year fast backtest before the full 5-year run** | No — every candidate gets a full simulation | **TAKE — highest throughput lever on this list.** A cheap pre-screen directly attacks the ~13 sims/day ceiling |
| **Health scoring (0–1) + degradation alerts at 20% drop** | No system-health metric | **TAKE.** Operational, touches no filter |
| **Multi-region: USA, EUR, CHN, ASI, GLB, IND** | Region is a per-alpha field; no multi-region campaign sweep | **TAKE.** Multiplies available territory without touching the statistics |
| **Genetic: tournament selection, elitism (top 10%)** | We have mutation + crossover + multi-objective fitness, but no explicit tournament/elitism | **TAKE.** Small delta on `evolution.py` |
| **Search strategy selection (BFS / DFS / Random)** | No — one traversal strategy | **ADAPT.** Interesting; must not disturb the random stratified arm |
| **Smart duplicate detection before simulating** | Partial — correlation is checked post-simulation | **TAKE.** Pre-simulation dedup saves the scarcest resource we have |
| **Cyberpunk GUI: dashboard, evolution control, config editor, DB browser, monitor** | One 1,451-line `index.html` + `ui.py` | **ADAPT.** Ours is adequate for one researcher; steal the DB browser idea only |
| **Credential handling: in-memory only, excluded from logs, `verify_secrets.py` pre-release scan** | Not audited here | **TAKE.** Cheap hygiene |
| **Continuous 24/7 mining, auto-generation every 6 h, scheduled daily auto-submission** | No scheduler | **TAKE the scheduler, REFUSE the submission half.** Invariant 1 |
| **5,000 simulations/day cap enforcement** | Our cap is politeness-driven, not quota-driven | **HAVE (stricter).** Note the number as a data point on real platform limits |

---

## 3. Microsoft RD-Agent / RD-Agent(Q) — `microsoft/RD-Agent` (14.3k★, MIT)

Not BRAIN-specific, but the most serious engineering in the space.
Reports ~2× annualised return vs benchmark factor libraries with 70%+ fewer factors,
at under $10 per run. 30.22% success rate on MLE-bench (75 Kaggle competitions). *(snippet for metrics)*

| What they have | We have? | Verdict |
|---|---|---|
| **Explicit Research → Development → Feedback loop as separate agents** (hypothesis generator, coder, runner, evaluator, knowledge base) | We have the stages as functions, not as a closed feedback loop — results never re-enter hypothesis generation | **TAKE.** The single most valuable idea in this document. Our loop is open; theirs is closed |
| **Factor–model co-optimisation** | Not applicable — BRAIN supplies the model | **REFUSE (irrelevant)** |
| **"Fewer factors, better performance" as an explicit objective** | Our objective is throughput (40 attempts) | **ADAPT.** Directly relevant to the one-submittable-alpha-per-field problem |
| **Cost accounting per run (<$10)** | `services/spend.py` exists — verify it covers LLM spend end-to-end | **HAVE (verify)** |
| **Docker-reproducible execution environment** | Local only | **TAKE (low priority)** |

---

## 4. QuantaAlpha — `QuantaAlpha/QuantaAlpha` (1.4k★, 281 forks, MIT)

LLM + evolutionary mining. Tested 2022–2025, CN and US markets.
Reported IC 0.0472, Rank IC 0.0459, 4.68% annualised, IR 0.6453, max DD 11.80% on CSI 300. *(snippet for metrics)*

| What they have | We have? | Verdict |
|---|---|---|
| **Diversified planning initialisation** — multiple research directions seeded at once, from one NL description | No — one campaign, one direction at a time | **TAKE.** Directly attacks our single-template problem |
| **Trajectory-level evolution** — whole research *trajectories* evolve, not individual factors | We evolve individual ASTs | **ADAPT.** A trajectory ≈ our territory. Conceptually strong fit |
| **Structured hypothesis–code constraints** | **HAVE** — this is our invariant 2, independently reinvented | **HAVE.** Reassuring: our core design choice is convergent, not unique |
| **Walk-forward validation across a documented regime shift (2023)** | No walk-forward, no regime-shift test | **PHASE 2** |
| **Zero-shot cross-market transfer as an overfitting test** | No | **PHASE 2.** Excellent idea: a factor that survives a market it never saw is hard to fake |
| **QuantaAlpha-claw** — multi-role agent orchestration, announced | No | Watch |

---

## 5. ChenNachuan/WorldQuant (14★, MIT — small but strategically important)

Low-star repo that nonetheless implements the thing we believe is our moat.

| What they have | We have? | Verdict |
|---|---|---|
| **Shared factor pool preventing duplication across team members** + per-member DB state tracking | **No.** This is a working multi-user pooled dedup — a primitive version of "the map" | **PHASE 2 — but read this repo first.** Our entire business thesis assumes nobody pools cross-user data. Someone shipped a team-scale version already |
| **Log+MinMax+Softmax field balancing across 16 datasets / 7,642 fields** — explicitly de-biases away from price/volume | Our allocator is UCB with diversity caps | **ADAPT.** Different mechanism, same goal. Worth benchmarking against ours |
| **Three generation modes: fresh 60% / gene recombination 20% / rescue-pool rehabilitation 20%** | We have fresh + evolution. **No rescue pool** — near-miss alphas are simply lost | **TAKE.** Cheap, and we are throwing away our most informative candidates |
| **Automatic reverse-factor detection** — Sharpe < −0.8 retested with negation | No auto-negation. We ask the LLM for an expected sign and trust it | **TAKE.** A strongly negative Sharpe is a *discovery*, and we currently discard it |
| **Automatic parameter optimisation — 4 representative combinations retried when checks fail** | No retry-on-fail path | **TAKE** |
| **Lark/Feishu bot: remote command control, discovery alerts, periodic summaries** | **No notifications of any kind** (`grep` slack/telegram/feishu/webhook → 0 hits) | **TAKE.** Phase 1 is an operational phase run by a human weekly — alerting is directly on-mission |
| **Daily log rotation, 30-day history** | Not present | **TAKE (hygiene)** |
| **Explicit factor lifecycle states**: pending → unsubmitted → submitted, with failure reasons | **HAVE and better** — our `submission_attempts` ledger records failures too | **HAVE** |

---

## 6. Brainiac — `jdhruv1503/Brainiac` (66★, 5 commits, early-stage)

| What they have | We have? | Verdict |
|---|---|---|
| **Scrapes and analyses financial papers to extract candidate signals** | No — our hypotheses come from LLM priors on field names alone | **TAKE.** A literature-grounded hypothesis source is a genuinely different generator, and mechanism diversity is exactly what Phase 1 needs |
| **RAG over datasets to fetch the most relevant fields per hypothesis** | Field selection is allocator-driven, not semantic | **ADAPT** |
| **Reinforcement learning to refine initial alphas** | Genetic, not RL | **ADAPT (low priority).** RL needs a reward signal we do not have at 486 simulations |
| **Watchdog: folder-drop → auto-backtest** | No | **TAKE (trivial, nice ergonomics)** |

---

## 7. wqb (PyPI 0.2.5, Feb 2025) — the reference API client

| What they have | We have? | Verdict |
|---|---|---|
| **`filter_alphas()`** — server-side filtering by sharpe / fitness / turnover / date | We pull and filter locally | **TAKE.** Less bandwidth, fewer calls |
| **`patch_properties()`** — set alpha name, tags, colour, description on the platform | No — our metadata is local-only | **TAKE.** Also a drift-incident mitigation: platform-side tags are independently verifiable |
| **`concurrent_check()`** — parallel pre-submission checks | Sequential | **TAKE** (GET-only, invariant-safe) |
| **Callback hooks: `on_start` / `on_finish` / `on_success` / `on_failure`** | No hook surface | **TAKE.** Enables the alerting in §5 with no extra plumbing |
| **Expiration-proof auto re-auth** | Verify ours handles mid-run token expiry | **VERIFY** |
| **`submit()`** | Deliberately absent here | **REFUSE.** Invariant 1. Note theirs is "not fully implemented" either |

---

## 8. pypbo (138★, AGPL-3.0, inactive) + De Prado statistics

The whole "fake-gold detector" is library code. Everything we treat as an asset:

| Statistic | We have? | Verdict |
|---|---|---|
| Deflated Sharpe Ratio (DSR) | **HAVE** | — |
| Probability of Backtest Overfitting (PBO) | No | **PHASE 2** |
| Probabilistic Sharpe Ratio (PSR) | No | **PHASE 2** |
| Minimum Track Record Length (MinTRL) | No | **PHASE 2** |
| Minimum Backtest Length (MinBTL) | No | **PHASE 2** |
| Performance degradation (IS vs OOS) | No | **PHASE 2** |
| Stochastic dominance between candidate sets | No | **PHASE 2** |
| Correlation-adjusted effective N (`effective_rank`, Marchenko–Pastur, clustering) | No — our DSR trial count is unadjusted for correlated trials | **PHASE 2 — and this one may matter most.** 4,608 of our alphas share one template. Our effective number of trials is far below our nominal count, which means our DSR haircut is almost certainly mis-calibrated |

> ⚠️ **Licence note:** pypbo is **AGPL-3.0**. Do not vendor its code into a product
> we intend to sell. Reimplement from the Bailey & López de Prado papers instead.

---

## 9. Academic frameworks — ideas, not code to copy

*(All snippet-level. Papers were unreachable at compile time — re-verify before building.)*

| System | The idea worth stealing | Verdict |
|---|---|---|
| **AlphaAgent** | Explicit **regularisation against crowding and alpha decay** built into the search objective | **PHASE 2.** Published prior art on our central claim. Read before we assert novelty anywhere |
| **Hubble** | LLM constrained by a domain-specific operator language + **AST execution sandbox** | **HAVE** — our invariant 2, again convergent |
| **XALPHA** | Memory-driven researcher: hypothesis → code, with persistent research memory | **TAKE** (pairs with §1 KB and §3 feedback loop) |
| **AlphaLogics** | Market-logic-driven multi-agent generation, built for interpretability | **ADAPT.** Interpretable economic mechanism is already our house style |
| **"Not All Factors Crowd Equally"** | Finds crowding is useful for **tail-risk management, not factor selection** | **READ BEFORE PHASE 2 ANALYSIS.** Partially adverse to our thesis. Different setting (institutional factors vs BRAIN's mechanical 0.70 gate) — but `VALIDATION_PROTOCOL.md` must engage with it, not ignore it |

---

## 10. The consolidated steal list, ranked

**Tier 1 — take these first (high value, no invariant conflict):**

1. **Cheap pre-screen backtest** (1-year fast run before the full simulation) — *worldquant-miner*. Biggest throughput lever available.
2. **Closed research→development→feedback loop** — *RD-Agent*. Our results currently never re-enter hypothesis generation.
3. **Local LLM provider (Ollama) + cascading fallback** — *worldquant-miner*. Hypothesis cost → zero.
4. **Reverse-factor auto-detection** (Sharpe < −0.8 → retest negated) — *ChenNachuan*. We are discarding discoveries.
5. **Rescue pool for near-miss alphas** — *ChenNachuan*. We discard our most informative candidates.
6. **Notifications + callback hooks** — *ChenNachuan* + *wqb*. Phase 1 is human-operated weekly; alerting is on-mission.
7. **Pre-simulation duplicate detection** — *worldquant-miner*. Protects the scarcest resource.
8. **Self-correcting AST with error learning** — *worldquant-miner*. Closes our validator loop within invariant 2.

**Tier 2 — take after Tier 1:**

9. MCP tool surface over existing services — *QuantGPT*
10. Dual-LLM adversarial cross-review — *QuantGPT*
11. Multi-region sweep (EUR / CHN / ASI / GLB / IND) — *worldquant-miner*
12. Literature-mining hypothesis source — *Brainiac*
13. Diversified planning initialisation — *QuantaAlpha*
14. `filter_alphas` / `patch_properties` / `concurrent_check` — *wqb*
15. Tournament selection + elitism in `evolution.py` — *worldquant-miner*
16. Health scoring + degradation alerts, log rotation, credential hygiene

**Tier 3 — Phase 2 only (all touch frozen filters or the business thesis):**

17. Walk-forward / OOS validation — *QuantGPT*, *QuantaAlpha*
18. Correlation-adjusted effective-N for the DSR haircut — *pypbo/ml4t-diagnostic* ← **likely our biggest statistical error today**
19. PBO / PSR / MinTRL / MinBTL / performance degradation — *pypbo*
20. Zero-shot cross-market transfer as an overfitting test — *QuantaAlpha*
21. Crowding-aware search regularisation — *AlphaAgent*
22. Cross-user pooled dedup — *ChenNachuan* has a team-scale version working already

---

## 11. Permanent refusals — do not re-propose

These appear in nearly every rival and must stay out regardless of competitive pressure:

| Feature | Present in | Why refused |
|---|---|---|
| **Automated submission to BRAIN** | worldquant-miner (scheduled daily), Brainiac, pyworldquant, QuantGPT | **Invariant 1.** `tests/test_brain_no_post.py` enforces it. Non-negotiable |
| **LLM emitting expression syntax directly** | most naive miners | **Invariant 2** |
| **Tuning the statistical filters to raise pass rates** | implicit in adaptive-tuning systems | **Invariant 3.** Frozen during Phase 1 — the filters are themselves under test |
| **Concurrency above the polite ceiling** (10–20 concurrent) | QuantGPT, worldquant-miner | ToS risk sits on the user's own BRAIN account |
| **Multi-account / credential pooling** | some hosted tools | BRAIN agreement prohibits account sharing |
| **Duplicating a fact already stored elsewhere** | — | The drift incident. See `CLAUDE.md` |

---

## 12. Standing conclusion

Two of our three claimed differentiators are **commodity as of Aug 2026**:
the statistical filter set is library code (pypbo, ml4t-diagnostic), and the
AST-constrained LLM design was independently reinvented by Hubble and by
worldquant-miner's Generation Two.

The third — **pooled cross-user territory data** — remains genuinely unclaimed at
scale, but `ChenNachuan/WorldQuant` already runs a team-scale version, and
QuantGPT Cloud runs hosted dedup. It is unclaimed, not unreachable.

What we actually still hold that nobody in this survey has:

- an **append-only, platform-authoritative outcome ledger** (born from the drift incident)
- a **pre-registered validation protocol** written before the analysis
- the **territory abstraction** as the unit of statistical analysis
- a deliberate **unbiased random stratified arm** — every rival optimises greedily,
  which is exactly what makes their data unusable for a validation study

That last one is the real asset, and it is the one most likely to be discarded by
someone trying to improve throughput. Do not discard it.

---

## Sources

- [Miasyster/QuantGPT](https://github.com/Miasyster/QuantGPT)
- [zhutoutoutousan/worldquant-miner](https://github.com/zhutoutoutousan/worldquant-miner) · [Generation Two docs](https://github.com/zhutoutoutousan/worldquant-miner/blob/master/generation_two/DOCUMENTATION.md)
- [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent)
- [QuantaAlpha/QuantaAlpha](https://github.com/QuantaAlpha/QuantaAlpha) · [paper](https://arxiv.org/pdf/2602.07085)
- [ChenNachuan/WorldQuant](https://github.com/ChenNachuan/WorldQuant)
- [jdhruv1503/Brainiac](https://github.com/jdhruv1503/Brainiac)
- [wqb (PyPI)](https://pypi.org/project/wqb/) · [RussellDash332/WQ-Brain](https://github.com/RussellDash332/WQ-Brain) · [q3yi/worldquant](https://github.com/q3yi/worldquant) · [xiegengcai/world-quant-brain](https://github.com/xiegengcai/world-quant-brain)
- [esvhd/pypbo](https://github.com/esvhd/pypbo) · [Deflated Sharpe Ratio](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio) · [Bailey & López de Prado](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- [AlphaAgent](https://www.researchgate.net/publication/394259787_AlphaAgent_LLM-Driven_Alpha_Mining_with_Regularized_Exploration_to_Counteract_Alpha_Decay) · [XALPHA](https://arxiv.org/pdf/2607.08332) · [AlphaLogics](https://arxiv.org/pdf/2603.20247)
- [Not All Factors Crowd Equally](https://arxiv.org/html/2512.11913v1)
- [BRAIN AI Researcher job posting](https://job-boards.greenhouse.io/worldquant/jobs/4581366006)
