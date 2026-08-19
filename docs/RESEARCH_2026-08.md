# Research memo — techniques we are not using

**Date:** 2026-08-19 · **Scope:** what exists (platform, operator KB, literature) that this
system does not currently exploit, ranked by expected value per hour of work.

Method: full inventory of `backend/app/services/`, `operators/operators.yaml` (100 operators),
`docs/BRAIN_API.md`, and a literature/community sweep. Every gap below is stated against a
file:line in this repo, so nothing here is a general "you could also try…".

Evidence is labelled:
**[verified-here]** read directly from this codebase ·
**[verified-platform]** confirmed against a live BRAIN session in `docs/BRAIN_API.md` ·
**[community]** reverse-engineered / practitioner sources, unconfirmed on our account ·
**[literature]** peer-reviewed or preprint, not yet validated on BRAIN.

---

## 0. The one-paragraph summary

The statistics are in good shape — DSR with an eigenvalue-based effective trial count, rolling
sub-period stability and recent-decay checks put the filter ahead of most amateur mining rigs.
The **search** is where the gaps are. We sweep 2 axes densely (window, decay), leave 2 axes
pinned to a single value each (truncation, universe), use 7 of 27 time-series operators,
0 of 6 vector operators, and exclude every VECTOR field from triage by an explicit SQL filter.
Separately, the objective declared in STRATEGY.md §2 — *maximise the count of mutually
uncorrelated alphas* — is a mode-covering objective, and every search component we built
(grid, bandit, GA) is mode-seeking with diversity bolted on afterwards as a cap and a
post-filter. §3 below is about that mismatch.

---

## 1. Free axes we own and are not sweeping

### 1.1 Truncation is pinned to one value
`backend/app/services/constructor.py:98` — `DEFAULT_TRUNCATIONS = (0.08,)`.
STRATEGY.md Rule 2 lists truncation as a swept axis with three values; the constructor never
got them. **[verified-here]**

This matters more than it looks. Our one family that cleared every check did so at
`fitness 1.00` against a `LOW_FITNESS` floor of `1.0` **[verified-platform]** — a pass by
0.00. Practitioner consensus is that lowering truncation (0.08 → 0.01) raises fitness by
forcing the book to hold 100+ names instead of concentrating, which also directly attacks the
`CONCENTRATED_WEIGHT` check **[community]**. Truncation is a settings axis: it costs one
simulation per point and no new code beyond widening a tuple.

**Do:** `DEFAULT_TRUNCATIONS = (0.01, 0.04, 0.08)`, and re-run the `liabilities/cap` family —
the surface we already have says the mechanism is real and only settings are failing it.

### 1.2 Universe is pinned to TOP3000
`backend/app/services/constructor.py:101` — `DEFAULT_UNIVERSES = ("TOP3000",)`. **[verified-here]**

`docs/BRAIN_API.md` establishes that universe does *not* partition the field catalog — all of
TOP3000/TOP1000/TOP500/TOP200/TOPSP500 return the identical 4,367 fields at delay 1
**[verified-platform]**. We read that as "universe is not interesting". The opposite follows:
each universe is a **distinct submittable alpha over the same data**, i.e. free draws off an
already-paid-for mechanism, with a different liquidity and breadth profile.

It also bears on a check we do not model at all: `LOW_SUB_UNIVERSE_SHARPE`. Nothing in
`plateau.py` or `subperiod.py` references it. A TOP3000 alpha whose signal lives in the
small-cap tail fails it; the same expression on TOP1000 does not.

### 1.3 `ts_regression` is in the strategy but not in the grid
`DEFAULT_TS_TRANSFORMS` (`constructor.py:66`) is `ts_zscore, ts_rank, ts_delta, ts_mean,
ts_decay_linear, ts_std_dev, ts_quantile`. STRATEGY.md Rule 2's axis table lists
`ts_regression`; the KB has it. It is the only operator in that table missing from the code.
**[verified-here]**

---

## 2. Operator classes in the KB that no expression ever uses

`operators/operators.yaml` holds 100 operators. The constructor and composite constructor
between them reach roughly 20.

### 2.1 Turnover operators — the highest-value gap in this section
Unused: `hump`, `hump_decay`, `bucket`, `tail`, `densify`. `trade_when` exists but only inside
`composite_constructor.py:129`, never in the main family grid. **[verified-here]**

Look at the only real result we have (`report.md`):

```
decay=0   Sharpe 2.10  turnover 0.97  FAIL — HIGH_TURNOVER (ceiling 0.7)
decay=4   Sharpe 1.91  fitness 1.00   PASS
decay=8   Sharpe 1.66  fitness 0.94   FAIL — LOW_FITNESS (floor 1.0)
```

The pass is wedged in a one-cell gap between two failures, and **decay is the only turnover
lever we have**. Decay smooths the signal, so it trades Sharpe for turnover monotonically —
which is exactly the squeeze above. `hump` and `trade_when` reduce turnover by a different
mechanism: they suppress *small position changes* / gate trading on a condition, leaving the
signal itself unsmoothed **[community]**. That is a second, roughly orthogonal turnover axis.
Adding it turns a 1-cell pass region into a 2-D one.

**Do:** add a `turnover_control` axis to `GridAxes`: `(none, hump(x, 0.01), hump(x, 0.05),
trade_when(cond, x, -1))`, swept against decay.

### 2.2 True orthogonalisation operators
Unused: `vector_neut`, `regression_neut`, `group_vector_neut`. **[verified-here]**

What we call the orthogonal composite (`composite_constructor.py:116`) is
`group_neutralize(zscore(a) - zscore(b), group)` — a *difference*, not a residual. It removes
the average of b, not b's explanatory power. `regression_neut(a, b)` is the actual residual.

This is the most direct lever on our stated objective there is: STRATEGY.md §2 says
correlation < 0.7 against everything already accepted is the binding constraint, and
`vector_neut`/`regression_neut` make a candidate **uncorrelated by construction** rather than
by rejection sampling. Every alpha we reject at the correlation gate is a wasted simulation;
this converts some of them into passes.

**Do:** for each promoted alpha, emit a variant neutralised against its own
nearest-correlated portfolio member.

### 2.3 Vector operators, and the field-type filter that hides them
Unused: all six of `vec_avg, vec_sum, vec_max, vec_min, vec_count, vec_choose`. **[verified-here]**

The cause is one line: `backend/app/services/field_triage.py:131` filters
`DataField.field_type == "MATRIX"`. VECTOR fields are never triaged, so they never reach the
constructor, so the vector operators have nothing to operate on. In the 122-field sample
catalog, VECTOR + GROUP is ~8% of fields; `docs/BRAIN_API.md` flags the MATRIX/VECTOR/GROUP
distinction as "load-bearing" **[verified-platform]**.

Vector fields concentrate in exactly the datasets the crowding table says we should be
mining — `news12` (109 users/field), `analyst4` (356), `option9` (595) — where per-record
data (per-article, per-analyst, per-strike) is naturally vector-shaped. We are excluding the
uncrowded end of the uncrowded datasets.

### 2.4 Event-time and distribution-shape families
Unused: `days_from_last_change`, `last_diff_value`, `ts_entropy`, `ts_skewness`, `ts_kurtosis`,
`ts_moment`, `kth_element`, `ts_covariance`, `signed_power`, `winsorize` (in the validator, never
emitted), `one_side`, `rank_by_side`. **[verified-here]**

`days_from_last_change` deserves separate mention: for quarterly fundamentals it *is* the
canonical staleness mechanism, and it is the principled version of what
`FREQUENCY_BACKFILL` (`constructor.py:95`) currently approximates with a fixed
120-day `ts_backfill`. "How long since this number moved" is a signal; "assume it is 120 days
stale" is a workaround.

---

## 3. Search: the mode-seeking / mode-covering mismatch

### 3.1 GFlowNets — the technique that matches our objective function
**[literature]** AlphaSAGE (arXiv 2509.25055) mines alphas with a Generative Flow Network over
expression trees plus an RGCN structure-aware encoder and an explicit novelty reward.
The relevant property: **a GFlowNet samples solutions in proportion to reward rather than
converging on the single best one** — it is mode-covering by construction, where RL and GA are
mode-seeking. The paper reports that the novelty reward raises signal quality *and* tradability
by cutting redundancy, and that swapping a sequence encoder for a GNN was the single largest
ablation lift.

Read STRATEGY.md §2 next to that: our objective is literally *count of alphas subject to
pairwise correlation < 0.7*, and §6 already diagnoses that a naive bandit "finds the best
dataset and pours everything into it… exactly wrong". We answered that with a 20% cap and
forced exploration — a patch on a mode-seeking searcher. GFlowNet is the formulation whose
objective *is* diversity. `alpha-gfn` (github.com/nshen7/alpha-gfn) is a working PyTorch
reference.

This is the largest single idea in this memo and also the largest lift (new dependency, a
learned model, a training loop). It is a Phase-3 item, not a next-sprint one. But the framing
is free and should change how we read `evolution.py` today.

### 3.2 Frequent-subtree avoidance — same benefit, implementable this week
**[literature]** The LLM-MCTS framework in "Navigating the Alpha Jungle" (AAAI 2026,
arXiv 2505.11122) contributes a *frequent subtree avoidance* mechanism: track which AST
subtrees recur across already-found alphas and steer generation away from them, to prevent
formulaic homogenisation.

We can implement the mechanism without the MCTS or the LLM. `app/validator/features.py`
already extracts AST features. Fingerprint the subtrees of every PASSED/SUBMITTED alpha, keep
a frequency table, and penalise candidates whose fingerprints are already common. That is a
**structural novelty prior computable before simulation** — it spends our scarcest resource
(simulation slots, 3 concurrent) on structurally novel candidates. Zero LLM calls, no new
dependency, and it feeds the same objective as §3.1.

### 3.3 LLM-MCTS over the mechanism tree
**[literature]** The same paper's main contribution — an LLM iteratively refining formulas at
MCTS nodes, with backtest metrics as the reward signal — is compatible with Rule 3 (the LLM
proposes mechanisms, deterministic code emits syntax), because the LLM would be choosing
*refinement directions*, not writing expressions. Cost stays bounded: a handful of calls per
tree, not per candidate. Worth prototyping only after §3.2, which captures part of the benefit
for a fraction of the work.

### 3.4 Search memory fed back to the LLM
**[literature]** FactorMiner (arXiv 2602.14670), AlphaMemo (2606.20625), QuantaAlpha
(2602.07085) and AlphaPROBE (2602.11917) all converge on the same structure: a persistent,
structured memory of the search process that conditions the next generation step.

We have the memory — 625 alphas, per-dataset hit rates, failure reasons, campaign tables — and
nothing reads it back into the triage prompt. `field_triage.py` sees field descriptions and
nothing else. Feeding it "in this dataset, these mechanisms were tried, these failed and on
which check" is a prompt change, not an architecture change.

### 3.5 Combining proven alphas, not just fields
**[literature]** AlphaForge (AAAI 2025) is explicitly two-stage: mine factors, then
*dynamically combine* them with time-varying weights.

`composite_constructor.py` blends **fields**. Nothing combines **alphas that already passed**.
A composite of survivors is a different object from a composite of raw fields — its components
have independently demonstrated a plateau — and combination typically raises Sharpe and lowers
turnover simultaneously, which is precisely the corner §2.1 shows us stuck in. It also maps
onto BRAIN's own SuperAlpha concept **[community]**.

---

## 4. Statistics: what to add to an already-strong filter

### 4.1 PBO via CSCV — complementary to the DSR we have
**[literature]** We compute DSR with an eigenvalue-based effective trial count
(`subperiod.py:40,65`) — genuinely good. The complement is the **Probability of Backtest
Overfitting** via Combinatorially Symmetric Cross-Validation (Bailey, Borwein, López de Prado,
Zhu). Recent comparative work in a synthetic controlled environment finds combinatorial purged
approaches materially reduce measured overfitting versus walk-forward and K-fold, and
recommends **reporting PBO and DSR together** rather than choosing.

The reason to want it here: DSR asks "is *this point* real given N trials"; PBO asks "does
in-sample rank within *this family* predict out-of-sample rank at all" — it can tell us a whole
surface is noise, which no per-point statistic can. And CSCV consumes exactly the artefact we
already store: the family PnL matrix (N candidates × T days) in the PnL store. No new data,
no new simulations, ~100 lines.

**Explicitly not applicable:** purging and embargo. Those correct label leakage in supervised
CV; we are resampling a realised PnL series. Adopting them here would be cargo-culting.

### 4.2 Perturbation-fidelity as a generalised plateau test
**[literature]** AlphaEval (KDD 2026, arXiv 2508.13174) proposes backtest-free evaluation over
five dimensions — predictive power, stability, robustness to perturbation, financial logic,
diversity — with metrics including Relative Rank Entropy and a Perturbation Fidelity Score.

Most of it needs local price data we do not have (BRAIN gives metrics and PnL, not raw data),
so **do not adopt AlphaEval wholesale**. One idea does transfer for free: our plateau test *is*
a perturbation-fidelity test, restricted to two axes. We already simulate neutralization and
(soon) truncation and universe points. Extending the neighbourhood definition in
`plateau.py:206` from `(window ±1, decay ±1)` to include a neutralization step would test
robustness to a perturbation the mechanism should survive and an overfit should not.

### 4.3 Surrogate-guided grid completion
The plateau-fill arm (`docs/PHASE1.md` §5) spends 20% of the budget on incomplete surfaces
chosen heuristically. A cheap surrogate — GP or gradient-boosted regressor over
(window, decay, neutralization, truncation) fitted per family — predicts which unsimulated cell
is most informative, i.e. Bayesian-optimisation-style acquisition rather than "fill the holes".
Successive-halving over structures on top of that would cut wasted draws further.

STRATEGY.md §10 says to wait for ~1,000 results before fitting priors. That is right for
clustering across families; a 2-D smoother *within* one 7×7 surface needs far fewer points, and
we already have 295 simulated. These are different sample-size questions and should not share
a rule.

---

## 5. Platform surface we are not touching

| # | Thing | Status | Note |
|---|---|---|---|
| 5.1 | **Multi-alpha simulation** — batch ~10 sims per POST | **[community]** | Community wrappers use `to_multi_alphas` (default batch 10). Consultant-gated; we are `level: NONE`/`TUTORIAL`. This is the throughput ceiling, and it is bought with account level, not code. |
| 5.2 | **Delay 0** | **[verified-platform]** | Catalog fully readable, **~18x less crowded** (27 vs 493 avg users/field), `POST /simulations` returns 400. The single highest-value locked door in the platform. |
| 5.3 | **PPAC** (Power Pool Alpha Correlation) | **[community]** | A gate *distinct* from self-correlation. `correlation.py:164` models self-correlation vs our own submitted alphas only. Community tooling computes PPAC locally from PnL — same data we already hold. |
| 5.4 | `/alphas/{id}/correlations/self` and prod-correlation endpoints | **[community]** | We use `recordsets` for PnL (`scripts/backfill_pnl.py`) but never the correlation endpoints. Worth one probe on our session to see what level gates them. |
| 5.5 | **OS metrics / realised decay** | **[verified-here]** | We store `is` only. Once alphas are live, `os` metrics exist. Nothing compares realised out-of-sample decay against what our filter predicted — that is the feedback loop that would calibrate every threshold in `plateau.py`, and it is the one measurement that tells us whether the filter works. |
| 5.6 | `MATCHES_COMPETITION` | **[verified-platform]** | Appears in `is.checks[]`; nothing in our filter reads it. |

**On 5.2 and 5.1 together:** both unlock with account level, which is earned by submitting
accepted alphas. That is a real strategic point — a machine optimised purely for
alphas-per-week under current permissions is not the same machine as one optimised to reach
the permission tier where the data is 18x less crowded and throughput is 10x higher.

---

## 6. Recommended order

Rough estimates; the first three are settings and tuple edits.

| Priority | Item | § | Effort | Why first |
|---|---|---|---|---|
| 1 | Truncation axis (3 values) | 1.1 | ~1h | Our only pass sits at fitness 1.00 vs a 1.0 floor. |
| 2 | Turnover operators (`hump`, `trade_when`) as a grid axis | 2.1 | ~1d | Breaks the decay/Sharpe squeeze that is failing neighbours of our one real winner. |
| 3 | Universe axis + wire `LOW_SUB_UNIVERSE_SHARPE` | 1.2 | ~half-day | Free extra draws on mechanisms already paid for. |
| 4 | Frequent-subtree novelty prior | 3.2 | ~2d | Diversity gain before simulation; no new dependency. |
| 5 | `regression_neut` / `vector_neut` variants of promoted alphas | 2.2 | ~2d | Converts correlation-gate rejections into passes. |
| 6 | PBO via CSCV alongside DSR | 4.1 | ~2d | Family-level overfitting detection from data already stored. |
| 7 | Drop the MATRIX-only triage filter; vector operator templates | 2.3 | ~3d | Opens the uncrowded end of the uncrowded datasets. |
| 8 | Composites of *passed alphas* | 3.5 | ~3d | Raises Sharpe and cuts turnover together. |
| 9 | Search memory into the triage prompt | 3.4 | ~1d | Prompt change; makes 625 accumulated results actually inform generation. |
| 10 | Surrogate-guided grid completion | 4.3 | ~1w | Better use of the plateau-fill arm's 20%. |
| 11 | OS-decay feedback loop | 5.5 | ~1w | The measurement that validates or refutes the whole filter. |
| 12 | GFlowNet generator | 3.1 | Phase 3 | The objective-matched searcher. Big lift, biggest ceiling. |

---

## Sources

- [AlphaSAGE: Structure-Aware Alpha Mining via GFlowNets](https://arxiv.org/abs/2509.25055) · [alpha-gfn reference implementation](https://github.com/nshen7/alpha-gfn)
- [Navigating the Alpha Jungle: An LLM-Powered MCTS Framework for Formulaic Factor Mining (AAAI 2026)](https://ojs.aaai.org/index.php/AAAI/article/view/37069)
- [AlphaForge: A Framework to Mine and Dynamically Combine Formulaic Alpha Factors (AAAI 2025)](https://dl.acm.org/doi/10.1609/aaai.v39i12.33365)
- [AlphaEval: A Comprehensive and Efficient Evaluation Framework for Formula Alpha Mining (KDD 2026)](https://dl.acm.org/doi/10.1145/3770855.3817727) · [code](https://github.com/LeoDingggg/AlphaEval)
- [FactorMiner: A Self-Evolving Agent with Skills and Experience Memory](https://arxiv.org/pdf/2602.14670) · [AlphaMemo](https://arxiv.org/pdf/2606.20625) · [QuantaAlpha](https://arxiv.org/pdf/2602.07085) · [AlphaPROBE](https://arxiv.org/pdf/2602.11917)
- [The Probability of Backtest Overfitting — Bailey, Borwein, López de Prado, Zhu](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
- [Backtest Overfitting in the Machine Learning Era: A Comparison of Out-of-Sample Testing Methods](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110) · [purged-cross-validation implementation](https://github.com/eslazarev/purged-cross-validation)
- [Self-Correlation Analysis — world-quant-brain (DeepWiki)](https://deepwiki.com/xiegengcai/world-quant-brain/4.1-self-correlation-analysis) · [Alpha Generation and Simulation (multi-alpha batching)](https://deepwiki.com/xiegengcai/world-quant-brain/3-alpha-generation-and-simulation)
- [wq-alpha-research — community BRAIN mining skill](https://github.com/QuantML-Research/wq-alpha-research)
- [WorldQuant BRAIN Consultant Program (multi-simulation, SuperAlphas)](https://worldquantbrain.com/consultant)
- [WorldQuant BRAIN simulation settings — truncation & turnover practice](https://medium.com/@mapongo/worldquant-brain-how-to-apply-the-simulation-environment-settings-9dc232831bb6)
