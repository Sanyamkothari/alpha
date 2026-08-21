# Terrain — what ground this system is actually fighting on

**Status:** reconnaissance summary, 21 Aug 2026. Sources cited inline; every number
below is quoted from an existing document in this repo, not re-derived. Where two
documents disagree, both are shown rather than silently reconciled — see §5.

**Why this exists.** The build is finished and the operating guide describes what to
*do* weekly. Neither describes *where* the work happens: which region of the platform
is reachable, who else is standing on it, and which gates can be seen before a
decision versus only after. That gap is not addressed anywhere else in `docs/`.

---

## 1. The reachable board is one square, and it is the most crowded one

Verified against a live session on 2026-08-04 (`docs/BRAIN_API.md` §"Account-level
access"), account `SK11953`, `level: "NONE"`, `permissions: ["TUTORIAL"]`.

| Scope | `GET /data-sets` | `POST /simulations` |
|---|---|---|
| USA / delay 1 | 14 datasets, 4,367 fields | works |
| USA / delay 0 | 11 datasets, 2,121 fields | `400 — "Delay 0 is not available."` |
| EUR / GLB / ASI / CHN / AMR | 0 datasets, every universe tried | nothing to run |

Universe does not partition the catalogue: TOP3000, TOP1000, TOP500, TOP200 and
TOPSP500 all return the identical 4,367 fields. **Only delay changes the ground.**

And the delay dimension is where the crowding lives:

| | avg users/field | `fundamental2` |
|---|---|---|
| delay 1 — reachable | **493** | 131 |
| delay 0 — readable, gated | **27** | 3 |

Roughly **18× less contested territory is visible but not simulatable.**

Every one of the 4,857 alphas in the DB was generated on the contested side of that
line. That is the single most consequential fact about the landscape, and it is
currently recorded only as a two-line aside inside an API reference.

Catalogue-wide crowding is severely right-skewed (`docs/INVENTORY.md` §A6, over the
local 6,583-field catalogue): median 16 users/field, Q3 158, max 48,210. So "average
493" is not a typical field — it is a small number of enormously crowded fields
dragging the mean. 47.4% of alphas generated already sit in the bottom crowding
quartile (`user_count <= 2`). The territory selection is not the problem. **The
delay-1 gate is.**

## 2. The gate that decides everything cannot be seen before deciding

From the verified `is.checks[]` block (`docs/BRAIN_API.md`):

- `LOW_SHARPE` 1.25, `LOW_FITNESS` 1.0, `LOW_TURNOVER` 0.01, `HIGH_TURNOVER` 0.7,
  `LOW_SUB_UNIVERSE_SHARPE` 0.01, `CONCENTRATED_WEIGHT`, `MATCHES_COMPETITION` — all
  returned at simulation time.
- **`SELF_CORRELATION` returns `PENDING`.** BRAIN computes it at *submission* time.
- `GET /alphas/{id}/correlations/prod` → **403 Forbidden** at this account level.

So of the gates that matter, six are measurable for free and unlimited times, and the
two correlation gates — the ones the single-template problem makes binding — are
measurable only by spending a submission attempt.

This is the real shape of the campaign. Simulations are reconnaissance of the cheap
gates. **Submission attempts are the only instrument that reads the expensive ones**,
which is precisely why `CLAUDE.md` insists the metric is attempts and not simulations.
That rule has a physical reason behind it, and the reason is worth stating: you are
not being disciplined for its own sake, you are conserving your only sensor.

## 3. The terrain unlock is much closer than the roadmap treats it

`docs/GOLD_LEVEL_GUIDE.md` and `docs/PHASE1_OPERATING_GUIDE.md` read as two unrelated
plans. Put their numbers side by side and they are the same plan.

| | |
|---|---|
| Current leaderboard score | **2,000** (BRONZE, rank 25,711, 2 alphas counting) |
| Gold threshold | **10,000** |
| Points per alpha accepted into OS | **1,000–2,000** |
| Daily point ceiling | 2,000/day, resets 03:00 EST |
| → alphas still needed for Gold | **roughly 4–8** |
| Phase 1 target | **40 recorded submission attempts** |

At a 20% pass rate, 40 attempts yields 8 accepted alphas. **The evidence Phase 1
exists to produce and the account level that unlocks delay 0 are purchased by the
same forty attempts.** Gold also unlocks other regions and higher concurrency
(`docs/GOLD_LEVEL_GUIDE.md` §1.1).

Two cautions, kept explicit:

- The points-per-alpha figures are community-sourced, not vendor-confirmed. They are
  directly measurable: `GET /users/self/competitions` returns
  `leaderboard.score` and `progress.score.remaining`. One request settles it.
- This does **not** license optimising for points. Phase 1's random stratified arm
  (30%) is deliberately unbiased and is what makes the Phase 2 study possible; farming
  the leaderboard would destroy exactly the variation that arm is buying. The point is
  narrower: the level unlock is a *by-product* of Phase 1 succeeding, not a competing
  objective, and it should be tracked rather than pursued.

## 4. Rate and concurrency limits are known and are not the constraint

- Auth: `POST /authentication`, HTTP Basic → cookie `t=<JWT>`, 201, TTL 14,400 s (4 h).
- Bursting > 5 req/s → `429 API rate limit exceeded`.
- Concurrency capped at 3 simultaneous simulations (`MAX_CONCURRENT_SIMULATIONS`,
  enforced by `_ACCOUNT_SLOTS` semaphore, `docs/OPEN_DECISIONS.md` §3.1).
- One simulation ≳60 s, under 120 s wall-clock.
- Submission quota **4/day, confirmed** (`docs/PHASE1_OPERATING_GUIDE.md`) — ~480
  possible attempts over 16 weeks against a target of 40, about 12× headroom.

Ceiling from concurrency alone: 3 slots × ~86,400 s/day ÷ ~90 s ≈ 2,800 sims/day,
far above the 200/day design target. Current throughput is ~13/day. **Nothing on the
platform side is throttling this. The constraint is operational.**

## 5. Four places the map contradicts itself

`CLAUDE.md` records a drift incident whose lesson was one source of truth per fact.
The same failure has reappeared in the documentation layer. None of these are fixed
here — the platform is authoritative and this document is not — but each needs a
human to resolve, and each is cheap to resolve.

**a. The submitted alphas disagree on identity.** Three of six BRAIN IDs differ
between the two documents that list them:

| | `CLAUDE.md` | `docs/GOLD_LEVEL_GUIDE.md` §3 |
|---|---|---|
| agree | `zqNXMEZE`, `N1bkwYGw`, `xANpg6OW` | same |
| **disagree** | `VkGeJGrM`, `O0GKYG0R`, `QPGpqGbG` | `9qpOZjMq`, `j26KNdKo`, `RRmwqE5b` |

This is the drift incident's exact signature. Resolve from the platform
(`GET /users/self/alphas?status=ACTIVE&stage=OS`) via
`scripts/sync_submission_outcomes.py`, never by picking one document.

**b. A closed question is still listed as open.** `CLAUDE.md`'s open-questions table
leads with "BRAIN submission quota per week — determines whether 40 attempts in 4
months is feasible at all." `docs/PHASE1_OPERATING_GUIDE.md` answers it: 4/day,
confirmed, 12× headroom. The most alarming-looking unknown in the project's front
document has already been retired.

**c. Two field denominators are in circulation.** 6,583 fields / 33 datasets is the
*local catalogue* (`docs/INVENTORY.md`, `docs/PHASE0.md`). 4,367 fields / 14 datasets
is the *live, reachable* count for USA/delay 1 (`docs/BRAIN_API.md`, verified). The
headline "32 of 6,583 (0.49%)" therefore understates coverage of the ground actually
playable, and the two figures should not be used interchangeably.

**d. A local proxy is described as a platform guarantee.** `GOLD_LEVEL_GUIDE.md` §3.1
presents a 6×6 empirical correlation matrix and concludes "ensuring zero
self-correlation failure penalties." The local gate is 0.55 and BRAIN's is 0.70, so
the local one is correctly the stricter — but per §2 above, BRAIN computes
`SELF_CORRELATION` at submission over its own ~2-year PnL window and returns `PENDING`
until then. The matrix is good evidence, not a guarantee, and the wording should say so.

## 6. What is genuinely unknown, ranked by what it would change

| Unknown | How to close it | What it decides |
|---|---|---|
| Does `PROD_CORRELATION` gate against the *platform's* pool, separately from self-correlation? | Currently **403 at this account level** — closeable only by spending attempts and reading the failure-by-check panel | Whether research ground is shared between users. The premise of the entire product plan in `BUSINESS_MODEL.md` |
| Which check killed each past submission attempt | `scripts/record_past_attempts.py` — ten minutes, still unrun | "2 of 2" and "2 of 15" are different businesses |
| Do challenge points accrue per accepted alpha as documented? | One `GET /users/self/competitions` | Whether §3's 4–8 alphas to Gold is real |
| Is delay-0 crowding genuinely lower, or lower because the catalogue is smaller and harder? | Readable now — compare `user_count` distributions, not means, across the 2,121 delay-0 fields | Whether the gated ground is worth the level climb |

The first is not closeable by preparation. The other three cost under an hour combined
and none of them has been done.

---

## 7. The strategic reading

The instinct that prompted this document — that the landscape was never surveyed —
is correct, and the survey above is what was missing. But the remedy it suggests is
the wrong one, and worth naming plainly.

Sun Tzu's claim is not that more preparation wins. It is that battles are decided
before they are fought, by knowing five things — and that one should not commit where
the ground cannot be seen. This project's problem is not insufficient preparation.
Six weeks produced a working AST compiler, a 105-operator knowledge base, a campaign
allocator, a plateau/DSR/subperiod filter stack and full instrumentation. That is a
great deal of preparation. The problem is that all of it was aimed at the one region
of the platform where visibility is worst and competitors are densest, and that the
decisive gate there is one that no amount of further preparation can read.

The correlation gates are only legible from inside the engagement. Forty submission
attempts *is* the reconnaissance — reconnaissance in force, the classical name for
finding out what you're facing by making contact with it. Building more machinery
before spending those attempts is not preparation; it is the specific way this kind
of project fails, and `PHASE1_OPERATING_GUIDE.md` already names it as the most common
ending: *"you stop opening the console for two weeks."*

Know the ground, then move. The ground is now known as well as it can be from outside.
