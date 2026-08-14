# Strategy — a machine that produces uncorrelated alphas

**Goal: a self-sustaining system that produces a steady stream of submittable
alphas, requiring only an approve/reject pass from the operator.**

Built for one user first. If it works, it may be given to others — see §8 for the
two things that change and the one that breaks.

---

## 1. Diagnosis — what the database said

Read from `database/wq.db`, 2026-08-03, before the prune:

| Evidence | Number |
|---|---|
| Alphas simulated | 51 |
| Alphas that passed all checks | **0** |
| Best Sharpe / Fitness | 0.93 / 0.26 (bar: 1.25 / 1.00) |
| Distinct fields used across all 51 | **5** — `close`, `volume`, `returns`, `vwap`, `adv20` |
| Fields in the catalog | 122, all invented (a mock; real BRAIN has ~4,000) |
| Fundamental / analyst / news / options alphas ever tried | **0** |

Four structural causes:

1. **Crowded data.** Every alpha was a price-volume transform on USA/TOP3000/
   delay-1 — the most heavily mined space on the platform. `ts_rank(volume, 20)`
   → Sharpe 0.39 is the correct answer for a fully-arbitraged signal, not a bug.
2. **The generator wasn't built to find alphas.** It filled one trial of a
   statistics experiment (sham memory blocks, arm-matched prompt lengths, pinned
   scaffolds). ~1,900 further lines existed only to serve that rig.
3. **Settings were never searched.** Neutralization, decay and truncation move
   Sharpe by 0.3–0.6 on an unchanged expression. Each alpha was run at one point.
4. **The sample was too small to learn from.** 51 results cannot support dedup,
   clustering or priors — all of which were built or scaffolded anyway.

**The asset worth keeping:** the deterministic compiler (`app/validator/`:
lexer → parser → AST → KB validation → features) and the operator knowledge base
(102 operators, 213 argument specs, 479 compatibility edges). That is what lets
everything below run at volume without hallucination.

---

## 2. The objective, stated correctly

Not "find good alphas." BRAIN pays for **uncorrelated** alphas, so:

```
maximize:    count of alphas clearing the bar
subject to:  pairwise correlation < 0.7 against everything already accepted
```

The constraint is the hard part, and it gets harder as you succeed. A 400-member
family optimized purely for Sharpe yields fifty variants of one signal, of which
the platform accepts one. **Diversity is the objective function, not a
post-filter.**

Everything below is organized around that.

---

## 3. Where alphas come from

```
good alpha = uncrowded data × sound structure × right settings × enough draws × honest filter
```

A product, not a sum. Scorecard at the time of the prune:

| Factor | State | Fix |
|---|---|---|
| Uncrowded data | ✗ price-volume only | real ~4k catalog; dataset rotation |
| Sound structure | ✓ validator works | keep the compiler unchanged |
| Right settings | ✗ never varied | settings are a swept axis |
| Enough draws | ✗ 51 lifetime | simulation API, 200–500/day |
| Honest filter | ✗ none | plateau + sub-period + correlation gate |

Four zeros. That is the whole explanation for 0/51.

---

## 4. The five rules

### Rule 1 — Data is the edge, not the formula
Anyone can write `ts_rank(x, 20)`. Almost nobody has *read* 4,000 field
descriptions. Systematic catalog coverage is the durable advantage. Work one
dataset at a time, record hit-rate per dataset, and let that table decide where
the next month goes.

### Rule 2 — Generate families, not alphas
A *family* is one economic mechanism expanded across the full grid:

| Axis | Values |
|---|---|
| ts-transform | `ts_zscore`, `ts_rank`, `ts_delta`, `ts_mean` diff, `ts_regression`, `ts_decay_linear`, `ts_corr` |
| window | 5, 10, 22, 63, 126, 252 |
| cross-section | `rank`, `zscore`, `normalize`, none |
| group | none, `sector`, `industry`, `subindustry` |
| neutralization | NONE, MARKET, SECTOR, INDUSTRY, SUBINDUSTRY |
| decay | 0, 4, 8, 16 |
| truncation | 0.01, 0.05, 0.10 |

200–800 candidates per family, sampled rather than full cross-product, all valid
by construction, zero LLM calls in the inner loop. Settings are part of the idea,
not an afterthought.

### Rule 3 — The LLM proposes mechanisms, never syntax
The LLM fires **once per dataset**, reading field descriptions and proposing
which fields carry an economic mechanism (with expected sign, horizon, and update
frequency, so a quarterly fundamental never gets a 5-day window). Deterministic
code emits every expression.

This is also what makes autonomy affordable: a day producing 400 alphas costs a
handful of LLM calls. If the model wrote each expression, unattended operation
would be both expensive and poisoned by hallucination.

### Rule 4 — Throughput is an engineering goal
200–500 simulations/day through BRAIN's simulation API on the operator's own
account: concurrency cap, exponential backoff, honors `Retry-After`. Queue
overnight, review in the morning.

**Simulation is automated; submission is not, and there is no submission code
path.** See `docs/DECISIONS.md`.

### Rule 5 — Filter honestly, or ship noise
Mass simulation makes overfitting the default outcome. The filter *is* the
product. Four mechanical tests, in order:

- **Plateau, not peak.** Judge a candidate by the median Sharpe of its grid
  neighbours (window ±1 step, decay ±1 step).

  ```
  window:      5    10    22    63   126   252
  Sharpe:    0.3   0.4   1.5   0.4   0.3   0.2   ← spike: coincidence, discard
  Sharpe:    0.9   1.2   1.4   1.3   1.1   0.8   ← plateau: mechanism, promote
  ```

  Same peak, opposite meaning. Highest-value test in the system, free to compute.
- **Sub-period split.** Must hold in both halves of the backtest window.
- **Multiple-testing haircut.** Scale the bar to family size — a winner drawn
  from 500 candidates needs a materially higher threshold than one from 20.
- **Correlation gate.** Reject anything ≥0.7 correlated with an already-accepted
  alpha. This is the constraint from §2, applied at the point of decision.

---

## 5. What replaces operator expertise

| Decision | Automated by |
|---|---|
| Which dataset to mine | bandit over measured hit-rate |
| Which fields carry a mechanism | LLM reads field descriptions |
| Which variants to try | deterministic grid |
| Whether a winner is real | plateau + sub-period, mechanical |
| Which to put forward | correlation gate + ranked queue |
| **Submitting** | **nothing — always the human** |

The operator's job is one approve/reject pass over a ranked shortlist.

---

## 6. The allocator — and why it must not exploit

A naive bandit finds the best dataset and pours everything into it. That is
exactly wrong: it produces mutually-correlated alphas that mostly get rejected.

- Cap any single dataset at ~20% of simulation budget.
- Score candidates on `expected_pass × novelty_vs_portfolio`, never Sharpe alone.
- Keep forced exploration permanently on — signals decay (~26% out-of-sample), so
  a machine that settles into a groove decays with it.

Portfolio diversity is worth more than marginal Sharpe. The allocator is what
turns this from a tool you drive into a machine that runs.

---

## 7. The loop

```
1. allocate      →  bandit picks the next dataset/mechanism, respecting the diversity cap
2. read fields   →  LLM: mechanism? sign? horizon? update frequency?
3. expand        →  constructor emits 200-800 valid candidates
4. simulate      →  batch via BRAIN API, rate-limited, overnight
5. filter        →  plateau → sub-period → haircut → correlation gate
6. report        →  ranked shortlist + updated hit-rate table
7. human         →  approve/reject, then SUBMIT MANUALLY
```

Steps 1, 3, 4, 5, 6 are deterministic code. Step 2 is the only LLM call. Step 7
is the operator.

---

## 8. Going multi-user later

Two things that are cheap now and a rewrite later — do them from the start:

1. **The correlation gate takes a portfolio as an argument, not a global.**
2. **Every alpha records its provenance** — dataset, mechanism, grid coordinates
   (`alphas.family_key` plus `feature_json.grid`). This is the allocator's
   training data and, later, the basis for cross-tenant diversity.

And one that genuinely breaks: **the product cannibalizes itself as it scales.**
Two users running this over the same datasets generate correlated alphas, and
BRAIN rejects duplicates. The more copies in circulation, the less each is worth.
That is a structural property of alpha-generation tooling, not a bug to engineer
around. Fine as a personal instrument; decide deliberately before treating it as
a business.

---

## 9. Metrics

| Metric | Target |
|---|---|
| Simulations/day | 200+ |
| Accepted (uncorrelated) alphas/week | 1+ |
| Plateau ratio — winners surviving the neighbourhood test | > 0.3 |
| Hit-rate per dataset | build the table |
| Datasets covered | growing |

Deliberately absent: prompt eval scores, memory-uplift deltas, knowledge-graph
edge counts. Those measure the machine, not the output.

---

## 10. Build order

| Stage | What | Makes it | State |
|---|---|---|---|
| 1 | Real BRAIN field catalog (replaces the mock) | possible | **done** — 4,367 fields |
| 2 | Batch simulation runner | fast | **done** — 3 concurrent (platform cap) |
| 3 | Family constructor (grid expansion) | productive | **done** — 0 invalid emissions |
| 4 | Filter: plateau + haircut + correlation gate | **trustworthy** | **done** |
| 5 | Allocator: diversity-capped, refuses to over-exploit | **self-sustaining** | **done** |
| 6 | Daily report: ranked shortlist, one approval pass | low-expertise | **done** |

### What the first real run showed (2026-08-04)

The `liabilities/cap` family produced the project's **first alpha to clear every
BRAIN check**, and the mechanism was exactly the one §1.3 predicted:

```
rank(ts_zscore(divide(ts_backfill(liabilities,120),cap),5))   — identical expression

decay=0   Sharpe 2.10   Fitness 0.86   turnover 0.97   FAIL (turnover ceiling 0.7)
decay=4   Sharpe 1.91   Fitness 1.00   turnover 0.58   PASS
decay=8   Sharpe 1.66   Fitness 0.94   turnover 0.44   FAIL (fitness floor 1.0)
```

One settings axis separates pass from fail on an unchanged expression. The
original 51 alphas each sampled a single settings point; they would have hit
decay=0 and recorded the idea as dead.

Crowding, measured from the real catalog, confirmed Rule 1 quantitatively:
`pv1` (price-volume) averages **18,485 users/field** against **131** for
`fundamental2` and **109** for `news12` — a ~170x gap, and every one of the
original 51 alphas lived in the crowded end.

Stage 1 blocks everything — until the catalog is real, every generated alpha
references invented fields and will not simulate. Stage 4 is the one not to cut:
mass simulation with a weak filter does not produce alphas, it produces confident
garbage faster.

Dedup/clustering/priors wait until ~1,000 accumulated results. Below that they
fit noise — which is the mistake this project already made once.

---

## 11. Constraints that hold

- **Never automate submission.** Every alpha is reviewed and submitted by a human.
- **Never bypass auth, rate limits, or platform restrictions.** The simulation
  client is polite: concurrency cap, exponential backoff, honors `Retry-After`.
- Simulation runs on the operator's **own account, for their own research**,
  through the platform's own simulation API.
