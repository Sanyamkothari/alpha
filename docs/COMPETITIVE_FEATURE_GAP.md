# Feature Gap Analysis — This Project vs Open-Source Alpha Research Tooling

**Date:** 2026-08-16
**Method:** Web survey of public repositories + code inspection of this repo.
**Scope note:** Peer features are taken from their public READMEs/docs. Those are *claims*, not
measurements — the same distinction `CLAUDE.md` insists on for our own numbers. Where a peer
claim conflicts with something we have measured firsthand, this document says so rather than
deferring to the README.

**Verification limit:** `database/wq.db` is not present in this working copy, so every statement
below about *our data* is sourced from `docs/INVENTORY.md` (dated 2026-08-16) or from code, not
from a fresh query. Statements about *our code* were verified directly in this checkout.

---

## 1. The peer set

Three distinct tiers, which matter separately — they are not competing with us on the same axis.

### Tier A — BRAIN-specific tooling (direct comparison)

| Project | What it is |
|---|---|
| [zhutoutoutousan/worldquant-miner](https://github.com/zhutoutoutousan/worldquant-miner) | Local-LLM (Ollama) alpha generator + genetic evolution + web dashboard + auto-submit |
| [Miasyster/QuantGPT](https://github.com/Miasyster/QuantGPT) | MCP toolkit (15 tools) giving an LLM agent an autonomous discover→evaluate→submit loop |
| [jdhruv1503/Brainiac](https://github.com/jdhruv1503/Brainiac) | LangChain agent; ingests research PDFs via RAG, emits Fast Expression, backtests |
| [QuantML-Research/wq-alpha-research](https://github.com/QuantML-Research/wq-alpha-research) | A "self-evolving" agent skill: field search, failure diagnosis, self-correlation, rule learning |
| [dige04/WQ-Brainn](https://github.com/dige04/WQ-Brainn) | Template-driven expression/payload factory, batch simulate + poll |
| [RussellDash332/WQ-Brain](https://github.com/RussellDash332/WQ-Brain) | Submission automation with IS pass-criteria filtering |
| [efJerryYang/worldquant-brain-simulator](https://github.com/efJerryYang/worldquant-brain-simulator) | **Offline** backtester for BRAIN expressions (README admits results diverge from platform) |
| [angel4angelov-glitch/wq-alpha-pipeline](https://github.com/angel4angelov-glitch/wq-alpha-pipeline) | Automated research pipeline built for IQC 2026 |

### Tier B — formulaic alpha mining research frameworks

| Project | Technique |
|---|---|
| [alphagen](https://github.com/ICT-FinD-Lab/alphagen) (KDD'23) | Maskable PPO RL search; optimises a *set* of alphas via IC / Rank IC / **mutual IC** |
| [AlphaForge](https://github.com/DulyHao/AlphaForge) (AAAI'25) | Generative-predictive mining + **dynamic test-time factor combination** over a sliding window |
| [alpha-gfn](https://github.com/nshen7/alpha-gfn) | GFlowNet-based formulaic factor generation |
| [AlphaEval](https://github.com/LeoDingggg/AlphaEval) | Standardised **evaluation harness** for comparing alpha-mining methods |
| gplearn | Symbolic-regression genetic programming (the baseline everyone benchmarks against) |

### Tier C — general quant infrastructure

| Project | Relevance |
|---|---|
| [Microsoft Qlib](https://github.com/microsoft/qlib) | Data layer, Alpha158/360 factor libraries, model zoo, backtest |
| [Microsoft RD-Agent](https://github.com/microsoft/RD-Agent) | LLM hypothesis→experiment→code→feedback loop; **extracts factors from financial reports** |
| [PurgedCV](https://github.com/eslazarev/purged-cross-validation) | Purging, embargoing, CPCV, PBO, PSR/DSR (mlfinlab is now closed-source/paid) |
| alphalens | Standard factor tear-sheet: IC decay, quantile returns, turnover |

---

## 2. Where we are ahead (context for reading the gaps)

Not padding — several gaps below look attractive precisely because we would be adding them to a
base that no peer has. Worth stating so the gap list is not read as "we are behind".

1. **Provenance discipline.** Append-only `alpha_field_snapshot` (5,187 rows), `submission_attempts`
   recording *attempts including failures*, `platform_outcome` derived by a single writer, platform
   treated as authoritative. No surveyed peer separates "I marked it submitted" from "the platform
   agrees it was submitted". This came from a real drift incident and it is our most distinctive asset.
2. **Point-in-time crowding.** We freeze `user_count`/`alpha_count`/`coverage` at alpha creation.
   Every peer reads today's crowding. Retrospective crowding studies are impossible without this,
   which is the whole premise of `VALIDATION_PROTOCOL.md`.
3. **Correct-by-construction expressions.** Deterministic AST constructors against a validated KB
   (105 operators, 213 argument specs, 479 compatibility edges). Peers let the LLM write syntax and
   then repair errors — worldquant-miner explicitly ships a "self-correcting AST with error learning",
   i.e. infrastructure to clean up a problem we designed out.
4. **Honest multiple-testing treatment.** Plateau-over-neighbourhood + Bailey/López de Prado DSR with
   an effective-trials haircut + subperiod split-half. Most Tier A peers threshold on raw Sharpe /
   fitness. QuantGPT is the only peer claiming comparable rigour (4-layer anti-overfit + walk-forward).
5. **Deliberate unbiased sampling.** The 30% random-stratified arm exists to make a later validation
   study possible. Every peer optimises greedily, which is exactly what makes their data unusable for
   inference about what works.
6. **The no-auto-submit invariant, enforced by a test.** Most peers submit automatically. This is a
   deliberate difference, not a missing feature — see §5.

---

## 3. Features we do not have

Ranked by whether they move Phase 1's actual metric — **40 submission attempts with recorded
outcomes** — not by how interesting they are.

### 3.1 On the Phase 1 critical path

**G1 — Pre-submission platform correlation check via the API.**
We compute self-correlation locally from stored PnL vectors (`correlation.py`,
`compute_max_self_correlation_with_submitted`). We never *ask BRAIN* what it thinks. Our
`BrainClient` wraps exactly five GETs: `/alphas/{id}`, `/users/self/alphas`, data fields, data sets,
operators. wq-alpha-research and QuantGPT both do pre-submission correlation management against the
platform's own view.

Why this is the top gap: `CLAUDE.md` lists "Does BRAIN check `PROD_CORRELATION` against the platform
pool, separate from self-correlation?" as an open question *needing a human*, and calls it "the
premise of the entire product plan". It is a **GET**, so it does not touch the no-POST invariant, and
it converts a blocking human question into an automated one. It also directly attacks the
one-submittable-alpha-per-field ceiling by telling us *before* a manual submission whether the
candidate will bounce.

**G2 — Multi-region / multi-universe / multi-delay expansion.**
The constructor already carries `region`/`universe`/`delay` through territory keys, but
`DEFAULT_UNIVERSES = ("TOP3000",)` and defaults are `USA`/`TOP3000`/`d1`. Every territory we have
mined sits in one region-universe cell. *(Unverified against data — the DB is not in this checkout.
`SELECT DISTINCT region, universe, delay FROM alphas` would settle it.)*

Why it matters: self-correlation is computed within a region. The same field in EUR or ASI is a
different alpha for correlation purposes. This is plausibly the cheapest available multiplier on
submittable-alphas-per-field, and it needs no new statistics — only sweeping an axis the code already
models. It respects the frozen-filters rule completely.

**G3 — Unattended, durable campaign execution.**
Jobs are in-process daemon threads with state in a flat `database/jobs.json`; on restart, running jobs
are marked `interrupted` (INVENTORY §B5). No Redis/Celery/arq. worldquant-miner ships Docker
orchestration for 24/7 operation; QuantGPT runs ~15-minute autonomous iteration cycles.

**The important correction here:** the obvious reading is "we are throughput-bound by the 3-concurrent
API cap". That is wrong. `simulation_runner.py` documents the real arithmetic — 3 concurrent at ~90s
gives **~2,800 sims/day**, against measured throughput of **~13/day**. We are running at roughly 0.5%
of the platform ceiling. The bottleneck is not the API and not concurrency; it is that nothing runs
the loop while the human is not watching. That makes durable job execution the highest-leverage
*engineering* gap for Phase 1, and it makes any "raise the concurrency cap" work pointless.

**G4 — A failure-diagnosis feedback loop.**
486 simulations produced 34 BRAIN passes. The ~93% that failed are stored (`simulation_imports`,
`alpha_metrics`) but nothing reads them back into generation. wq-alpha-research's `evolve_skill.py`
distils each simulation into a reusable rule ("fitness failures are often turnover problems in
disguise"); QuantGPT keeps a persistent knowledge base of rules, findings and failures across
sessions. We have `llm_runs` (64 rows) for *field triage only* — nothing that learns from outcomes.

Caveat that must be respected: any such loop must feed the **exploit** arm only. Letting it touch the
random-stratified arm would destroy the unbiased sample and with it the Phase 2 study.

### 3.2 Real gaps, but Phase 2 — do not build now

**G5 — Offline / local simulation.**
efJerryYang's simulator, and all of Tier B via Qlib, evaluate expressions on local cached data. We
cannot evaluate an expression without a BRAIN round-trip. In principle a local pre-screen could rank
candidates before spending platform budget. Two reasons this is *not* urgent: (a) that project's own
README states results "are still different from the platform's", so it can only ever be a ranker, not
a gate; (b) per G3 we are at 0.5% of our simulation budget, so we have no scarcity to economise
against. Revisit only if throughput ever approaches the cap.

**G6 — IC / Rank IC / mutual IC as first-class metrics.**
No occurrence of information-coefficient computation anywhere in `app/`. We judge candidates by
Sharpe, fitness, and realised PnL correlation. alphagen optimises IC and uses **mutual IC** to enforce
diversity *within* a candidate set; alphalens treats IC decay as the basic diagnostic.

The structural consequence: our only diversity measure is PnL correlation, which requires the alpha to
have *already been simulated*. A signal-level diversity metric would let us prune near-duplicates
before spending simulations — directly relevant to the 4,608-alphas-in-one-shape monoculture. But
adding a metric that gates candidates is a change to the filter stack, which Phase 1 freezes.

**G7 — Walk-forward / purged & embargoed CV / CPCV / PBO.**
We have split-half consistency, rolling 126d positivity, decay checks, and DSR. We do not have
combinatorial purged cross-validation or Probability of Backtest Overfitting. PurgedCV provides these
free and maintained; QuantGPT claims walk-forward validation. **Explicitly out of scope now** — rule 3
freezes the filters, and the current phase exists partly to test them as they stand.

**G8 — Alpha combination / ensembling.**
alphagen produces *synergistic sets* with learned linear weights; AlphaForge dynamically recombines
factors at test time. We emit single expressions only. Partly justified — BRAIN accepts one
expression per alpha — but a weighted blend can be expressed as a single BRAIN expression, so the
capability is not structurally unavailable to us. `composite_constructor.py` is the nearest thing and
has produced 8 alphas, 0 simulated.

**G9 — An evaluation harness.**
AlphaEval exists so that mining methods can be compared on equal terms. We have no way to answer "did
the exploit arm outperform the random arm?" other than ad hoc. Phase 2 will need exactly this, and
`VALIDATION_PROTOCOL.md` is pre-registered — so the harness should be built to match that protocol,
not designed fresh.

### 3.3 Present in peers, but we should probably not want them

**G10 — Automated submission.** worldquant-miner, QuantGPT and WQ-Brain all auto-submit. We forbid it,
enforced by `test_brain_no_post.py`. Given the drift incident, and given that submission is the one
irreversible action in the loop, this is correctly a non-goal.

**G11 — Genetic programming actually running.** gplearn, AlphaForge and worldquant-miner run GP for
real. `evolution.py` exists with tests and has produced **0 rows** (`generation = 0` for every alpha).
Not a missing feature — unexercised code. Worth noting that evolution amplifies whatever the fitness
function rewards, so running it before the filters are validated would be premature.

**G12 — Research-literature ingestion.** RD-Agent extracts factor formulas from financial reports;
Brainiac ingests research PDFs by RAG. We triage fields, never literature. Genuinely novel capability,
but it generates *more hypotheses* — and hypothesis supply is not our constraint. We have 6,583 fields
and have touched 32.

**G13 — Public-project hygiene.** No `LICENSE`, no `.github/` (no CI), no Dockerfile, not
pip-installable; peers are all public repos. Irrelevant while this is a personal research tool, and
`CLAUDE.md` puts productisation behind Phase 2. Listed only for completeness. One sub-item is *not*
cosmetic though: INVENTORY §B3 reports 17 uncommitted files including core services — that is a real
reproducibility defect independent of any packaging question.

---

## 4. Summary matrix

`Y` = present and has produced output · `~` = code exists, unexercised or partial · `N` = absent

| Capability | Us | Tier A (BRAIN) | Tier B (mining) | Tier C (infra) |
|---|---|---|---|---|
| KB-validated deterministic AST construction | **Y** | ~ (LLM writes syntax, repairs after) | Y | — |
| Automated batch simulation on BRAIN | **Y** | Y | — | — |
| Plateau / neighbourhood-median filter | **Y** | N | N | N |
| DSR + multiple-testing haircut | **Y** | ~ (QuantGPT only) | N | Y (PurgedCV) |
| Subperiod / split-half stability | **Y** | ~ (QuantGPT) | N | Y |
| Point-in-time crowding snapshots | **Y** | N | N | N |
| Attempt-level submission ledger + derived outcome | **Y** | N | N | N |
| Unbiased random-stratified sampling arm | **Y** | N | N | N |
| Local PnL self-correlation gate | **Y** | Y | — | — |
| **Platform/prod correlation check via API** | **N** | Y | — | — |
| **Multi-region / universe / delay sweep** | **~** | Y | — | Y |
| **Durable unattended job execution** | **N** | Y | Y | Y |
| **Failure→generator feedback loop** | **N** | Y | Y | Y |
| **Offline local backtest** | **N** | ~ (diverges) | Y | Y |
| **IC / Rank IC / mutual IC** | **N** | ~ | Y | Y |
| **Purged CV / CPCV / PBO** | **N** | ~ | N | Y |
| **Alpha set combination / ensembling** | **~** | N | Y | Y |
| **Standardised eval harness** | **N** | N | Y | ~ |
| Genetic / evolutionary search | **~** (0 rows) | Y | Y | — |
| Literature/report factor extraction | **N** | Y (Brainiac) | N | Y (RD-Agent) |
| Automated submission | **N** (by design) | Y | — | — |

---

## 5. Recommendation

If only one thing gets built: **G1, the platform correlation check.** It is a GET, it preserves every
invariant, it closes a question `CLAUDE.md` currently routes to a human, and it attacks the
one-submittable-alpha-per-field ceiling directly.

If two: add **G3, durable job execution**, on the strength of the 13/day vs 2,800/day arithmetic.

Then **G2, the region/universe sweep**, which is a configuration axis the constructor already models.

Everything else — offline simulation, IC metrics, CPCV, ensembling, evaluation harness — is genuinely
missing but belongs after Phase 1 produces its 40 outcomes. Several of them (G6, G7) would require
changing the frozen filter stack, which the current phase exists to test.

The honest summary of this survey: **on statistical rigour and provenance we lead this field
comfortably; on operational autonomy we are behind almost every peer.** Our gap is not that we
research worse. It is that our loop only runs when a human is sitting in front of it.
