# Implementation plan — closing the search gaps

**Companion to** `docs/RESEARCH_2026-08.md`. That memo said *what* is missing.
This says *how*, in what order, at what simulation cost, and what has to be true
before each step is safe to take.

**Audience:** whoever writes the code. Every item names files, line numbers,
schema impact, tests, and an acceptance criterion that can be checked mechanically.

---

## 0. Two findings that reorder the memo

Reading the code closely enough to write the patches turned up two things the memo
did not have. Both are prerequisites, not enhancements, and both change the
recommended order.

### 0.1 The effective-trial-count discount is implemented, tested, and never called

`subperiod.py:40` defines `compute_effective_trials()` — the eigenvalue-based
`N_eff = M² / Σλᵢ²`. It has a test. **Nothing in the production path invokes it.**

`plateau.py:329` calls:

```python
dsr_val = compute_dsr(daily_pnl, daily_sharpes)      # n_eff omitted
```

and `subperiod.py:90` therefore falls through to:

```python
n_trials = n_eff if (n_eff is not None and n_eff >= 1.0) else float(max(1, len(sharpes_clean)))
```

So the DSR is currently deflated by the **raw** trial count. The same is true of
`haircut_bar()` (`plateau.py:235`), which takes `simulated_count` directly.

Why this reorders everything: **the entire plan widens the search grid.** Every new
axis inflates the raw trial count, which raises the DSR bar and the haircut bar,
with no credit for the fact that the new points are near-duplicates of existing
ones. Under the current wiring, adding a truncation axis makes it *harder* to
promote the alpha we already found — a 3× trial inflation on a family whose new
points correlate ~0.95 with the old ones.

`N_eff` is exactly the correction for that: correlated trials count as a fraction
of a trial. Wire it first, and the cost of searching wider becomes proportional to
how much genuinely new information each axis buys. That is the statistically
correct answer and it is already written; it just is not plugged in.

**This is Workstream A2 and it blocks every constructor change.**

### 0.2 Widening a settings tuple does not widen coverage — it collapses it

`constructor.py:286` caps the family at `max_candidates=400`, and
`_emit_surface()` only emits **complete** `window × decay` surfaces
(`constructor.py:275-276`) — 49 points on the standard 7×7 grid. So one `expand()`
call yields at most `400 // 49 = 8 surfaces`.

Those 8 surfaces are drawn from `itertools.product` in declaration order
(`constructor.py:359`):

```python
configs = itertools.product(
    ts_transforms, cross_sections, groups,
    axes.neutralizations, axes.truncations, axes.universes,
)
```

The **last** axis varies fastest. Setting `DEFAULT_TRUNCATIONS = (0.01, 0.04, 0.08)`
and `DEFAULT_UNIVERSES = ("TOP3000","TOP1000","TOP500")` does not give you 9× the
coverage. It gives you the *same first structure* — `ts_zscore` / `rank` /
ungrouped / SUBINDUSTRY — repeated across 9 settings combinations, consuming the
entire budget before the loop ever reaches `ts_rank`. Structural coverage goes from
8 distinct mechanisms to **1**.

This is the difference between the memo's "~1 h, widen a tuple" and the real work.
The constructor needs an explicit two-phase sampler — **structure first, settings
second** — before any settings axis is widened. That is Workstream B1, and it
blocks B2/B3/B4.

---

## 1. Principles this plan is written to

**P1 — Price everything in surfaces.** The budget is ~200 simulations/night
(`docs/PHASE1.md` §7; the binding constraint is platform quota, not our 3-concurrent
cap). One complete 7×7 surface is 49 sims. **The unit of work is ~4 surfaces per
night.** Any proposal that reads "sweep axis X × axis Y" is really "spend N nights",
and must say so.

**P2 — Never break surface integrity.** Plateau analysis is the highest-value test
in the system and it depends on one invariant: *every point sharing a `structure`
tuple differs only in `(window, decay)`.* Any new axis is either (a) part of
`_structure_of()`, creating separate comparable surfaces, or (b) a genuine
neighbourhood coordinate with a defined ladder. There is no third option, and
getting this wrong is silent — you get a surface with three points at the same
coordinate and a neighbour median computed across incomparable alphas.

**P3 — Search width has a statistical price, and the price should be honest.**
More trials is more selection bias. `N_eff` makes the price proportional to the
information gained. Widening the grid without A2 is buying trials on credit.

**P4 — Screen coarse, confirm dense.** A new axis gets validated on a reduced
3×3 sub-grid against a known-good structure (9 sims/level) before it earns a full
7×7 (49 sims). Full factorial across settings is unaffordable and unnecessary.

**P5 — The submission line does not move.** Nothing in this plan touches
`POST /simulations` beyond simulation, and nothing approaches submission.
`tests/test_brain_no_post.py` stays green throughout.

---

# Workstream A — correctness prerequisites (P0)

Nothing else lands until these do. All three are small; A1 and A2 are the ones that
determine whether any number the system prints is meaningful.

## A1 — Establish PnL semantics, then enforce reconciliation

**Problem.** `scripts/backfill_pnl.py:73` stores the BRAIN `daily-pnl` recordset
verbatim:

```python
pnl = np.array([float(r[1]) for r in records], dtype=float)
store.save_pnl(local_alpha.id, dates, pnl)
```

BRAIN's `daily-pnl` recordset is widely reported to be a **cumulative** series.
Nothing in this repo differences it. Every statistic in the system —
`compute_dsr`, `evaluate_subperiod_stability`, `check_portfolio_empirical_correlation`,
and the CSCV work in E1 — treats that array as **daily returns**.

If the series is cumulative, the consequences are not subtle:

| Consumer | Behaviour on a cumulative series |
|---|---|
| `compute_dsr` | mean ≫ 0, std of a trending series → Sharpe wildly overstated |
| `evaluate_subperiod_stability` | both halves trend up → split-half always passes |
| correlation gate | every pair of cumulative curves correlates ~0.9+ → **the gate stops discriminating** |

The third one is the dangerous one: a broken correlation gate does not fail loudly,
it silently rejects everything, and the system looks like it is working.

**There is already a guard, and it is disarmed.** `verify_pnl_reconciliation()`
(`subperiod.py:203`) recomputes annualized Sharpe from the stored array and compares
it to BRAIN's reported figure — exactly the check that would catch this. At
`backfill_pnl.py:78` its result is counted into a stats dict and **never logged,
never raised, never gates the write**:

```python
rec = verify_pnl_reconciliation(local_alpha.id, rep_sr, store, sharpe_tolerance=0.10)
if rec.is_valid:
    stats["reconciled"] += 1
# else: nothing
```

**Work.**

1. **Determine the convention empirically.** One authenticated call, one alpha with
   a known reported Sharpe. Add `scripts/probe_pnl_convention.py`: fetch
   `/alphas/{id}/recordsets/daily-pnl`, compute annualized Sharpe on the raw series
   and on `np.diff`, print both against `is.sharpe`. Whichever matches is the
   convention. Record the answer in `docs/BRAIN_API.md` under the VERIFIED section,
   with the alpha id and date.
2. **Normalize at the storage boundary, not at every consumer.** Add
   `PnLStore.save_pnl(..., cumulative: bool = False)`; when `True`, store
   `np.diff(arr, prepend=arr[0])`. One place converts; every consumer keeps its
   current contract of "this array is daily".
3. **Arm the guard.** In `backfill_pnl.py`, on `not rec.is_valid`: `log.error(...)`
   with both Sharpes, increment `stats["reconciliation_failed"]`, and **do not count
   the alpha as usable**. Add `--strict` to abort the run on the first failure.
4. **Make it a standing invariant.** `plateau.evaluate()` should refuse to promote
   an alpha whose stored PnL does not reconcile with its reported Sharpe within
   tolerance. Append the reason and set `promoted=False`. A promotion resting on a
   PnL series we cannot reconcile is not a promotion.

**Tests** (`tests/test_pnl_semantics.py`, new)
- Synthetic daily series → `save_pnl(cumulative=False)` round-trips unchanged.
- `np.cumsum` of it → `save_pnl(cumulative=True)` recovers the original within 1e-9.
- Reconciliation failure path: stored series with Sharpe 3.0 vs reported 1.5 →
  `is_valid False`, and `evaluate()` emits `promoted=False` with the reason present.

**Acceptance.** `docs/BRAIN_API.md` states the convention with evidence; a
deliberately corrupted PnL series cannot produce a promotion.

**Cost.** 0 simulations. ~1 day including the probe.

**Risk if skipped.** Every statistic downstream is unverified, and E1 would build
PBO on top of an array whose units are unknown.

---

## A2 — Wire `N_eff` into the DSR and the haircut bar

**Problem.** §0.1. The discount exists and is not applied.

**Design.** `compute_effective_trials()` needs a correlation matrix over the
family's candidate PnL vectors. Nothing currently assembles one — and E1 (CSCV)
needs the identical artefact. Build it once.

**New module** `app/services/family_matrix.py`:

```python
@dataclass(frozen=True)
class FamilyPnLMatrix:
    alpha_ids: list[int]      # column order
    dates: list[str]          # row order, the intersected trading calendar
    matrix: np.ndarray        # shape (T, N), daily PnL

def build_family_matrix(
    db: Session,
    family_key: str,
    *,
    pnl_store: PnLStore | None = None,
    min_overlap: int = MIN_COMMON_TRADING_DAYS,
    structure: tuple | None = None,   # None = whole family; set = one surface
) -> FamilyPnLMatrix | None:
    """Date-aligned daily-PnL matrix over every simulated member of a family.

    Returns None when fewer than two members have a reconcilable PnL series.
    Date intersection reuses the logic in correlation.py:88-99 — factor that
    loop out rather than writing a second one.
    """
```

Then in `plateau.evaluate()`:

```python
fam_matrix = build_family_matrix(db, family_key, pnl_store=pnl_store)
if fam_matrix is not None and fam_matrix.matrix.shape[1] >= 2:
    corr = compute_correlation_matrix(fam_matrix.matrix)     # correlation.py:36
    n_eff = compute_effective_trials(corr)
else:
    n_eff = None                                             # falls back to raw count

# ...per point:
dsr_val = compute_dsr(daily_pnl, daily_sharpes, n_eff=n_eff)
bar = haircut_bar(n_eff if n_eff is not None else max(simulated_count, 1))
```

`compute_correlation_matrix` (`correlation.py:36`) already exists and takes a matrix.
Confirm its orientation matches `(T, N)` and fix the transpose in one place if not.

**Cost note.** This requires PnL for **every simulated family member**, not just
promoted ones. Currently `ensure_alpha_pnl()` fetches lazily per alpha
(`correlation.py:123`). A 49-point surface is 49 `recordsets` GETs. Add
`scripts/backfill_pnl.py --family <key>` that walks a family through the existing
polite client, and call it once after each campaign batch rather than lazily inside
`evaluate()` — `evaluate()` must stay a pure local computation.

**Tests** (`tests/test_effective_trials.py`, new; extend `tests/test_plateau.py`)
- 40 identical PnL vectors → `n_eff ≈ 1`; DSR bar barely moves versus a single trial.
- 40 independent random vectors → `n_eff ≈ 40`; DSR materially harsher.
- Regression: with `n_eff` wired, a family widened from 49 → 147 points of highly
  correlated variants does **not** lose its existing promotion.
- `build_family_matrix` returns `None` on a family with one PnL series.

**Acceptance.** `compute_effective_trials` appears in a production call path;
tripling a family with near-duplicate points raises the effective bar by < 0.05
Sharpe.

**Cost.** 0 new simulations (PnL fetches only, on the existing polite client).
~2 days.

---

## A3 — Restore surface integrity before adding axes

**Problem.** `_structure_of()` (`plateau.py:145`) returns:

```python
(grid.get("ts"), grid.get("cs"), grid.get("group"),
 grid.get("neutralization"), grid.get("truncation"))
```

`truncation` is present — so widening truncation is safe today, and correctly
produces separate comparable surfaces. **`universe` is not.** Sweep universe and
three alphas land on the same `(structure, window, decay)` coordinate; `_neighbours()`
(`plateau.py:206`) collects all of them and takes a median across incomparable
alphas. Silent, and it corrupts the system's single most valuable test.

Two supporting defects in the same area:

- **`grid_extra` is inconsistent across layers.** Depth-1 sets `"universe"`
  (`constructor.py:376`); depth-2 (`constructor.py:417`) and `ts_corr`
  (`constructor.py:449`) do not. `_emit_surface` reads
  `grid_extra.get("universe", base_settings.universe)` (`constructor.py:262`), so
  depth-2 silently ignores the universe axis. Fix by having `_emit_surface` require
  the key.
- **Latent surface-killer.** `constructor.py:251`:
  ```python
  if spec.effective_backfill and window < 5 and decay == 0:
      continue
  ```
  This `continue` skips a point, but `_emit_surface` then finds
  `len(surface) != surface_size` and **discards the entire surface**
  (`constructor.py:276`). It is dead today because `min(STANDARD_WINDOWS) == 5`, but
  any future window ladder starting below 5 silently returns zero candidates for
  every fundamental family. Convert to an explicit `expected_size` that accounts for
  filtered points, and add a test pinning the behaviour.

**Work.**
1. Add `grid.get("universe")` and `grid.get("turnover_control")` to `_structure_of()`.
   Both are `None` on existing rows, so historical surfaces keep their identity —
   no backfill needed.
2. Make `universe` a required key in every `grid_extra`.
3. Fix the `expected_size` accounting and pin it with a test.

**Tests** (extend `tests/test_plateau.py`)
- Two universes × one structure → two surfaces, each internally complete; the
  neighbour median of a TOP3000 point never reads a TOP1000 Sharpe.
- Depth-2 candidates carry the grid universe into `AlphaSettings`.
- A window ladder containing a filtered point still emits a surface.

**Acceptance.** No `(structure, window, decay)` coordinate is occupied by more than
one alpha, asserted over a synthetic multi-axis family.

**Cost.** 0 simulations. ~half a day.

---

# Workstream B — the constructor

## B1 — Structure-first sampling with an explicit budget contract

**Problem.** §0.2. The flat `itertools.product` spends the whole budget on the
first structure as soon as any settings axis widens.

**Design.** Separate the two loops and give each an explicit share of the budget.

```python
@dataclass(frozen=True)
class BudgetPolicy:
    """How a family's simulation budget is split between breadth and depth."""
    max_surfaces: int = 8
    structures_first: bool = True     # round-robin structures before settings
    settings_per_structure: int = 1   # >1 only for a confirmed structure
```

Enumeration becomes two nested generators:

```python
def _structure_configs(axes, spec) -> Iterator[StructureKey]:
    """(ts_op, cs_op, group) — what the expression looks like."""

def _settings_configs(axes) -> Iterator[SettingsKey]:
    """(neutralization, truncation, universe, turnover_control) — how it is run."""
```

and the driver interleaves **structure-major**:

```python
for settings_idx in range(policy.settings_per_structure):
    for structure in structures:
        emit_surface(structure, settings[settings_idx])
        if surfaces_emitted >= policy.max_surfaces:
            return
```

With `settings_per_structure=1` (the default) the behaviour is byte-identical to
today's for the current single-valued settings tuples — a safe refactor with a
characterization test. Widening a settings tuple then costs nothing until the caller
explicitly raises `settings_per_structure`.

**Ordering within `_settings_configs` matters** and should be deliberate: the first
entry is the reference configuration (`SUBINDUSTRY`, `0.08`, `TOP3000`, no turnover
control) so that a `settings_per_structure=1` run reproduces the current baseline
exactly. Later entries are the probes.

**New CLI surface** on `scripts/run_family.py`:
```
--structures N          cap distinct structures (default 8)
--settings-per-structure N   default 1
--probe-axis {truncation,universe,turnover}   run the P4 screening design
```

**Tests** (extend `tests/test_constructor.py`, `tests/test_phase1_constructor.py`)
- **Characterization first:** with default axes, the refactored `expand()` returns
  the identical candidate list (expression + settings + grid) as the current one.
  Write this test against the current code, confirm it passes, *then* refactor.
- With 3 truncations and `settings_per_structure=1`, distinct structures emitted is
  still 8 — not 1. This is the regression §0.2 describes; it is the reason the
  refactor exists.
- With `settings_per_structure=3`, each structure appears at 3 settings points and
  the surface count respects `max_surfaces`.

**Acceptance.** A run with every settings axis widened emits the same number of
distinct `(ts, cs, group)` structures as a run with none of them widened.

**Cost.** 0 simulations. ~2 days including the characterization test.

---

## B2 — Truncation as a real axis

**Depends on:** A2, A3, B1.

**Rationale.** `constructor.py:98` pins `DEFAULT_TRUNCATIONS = (0.08,)`. Our only
passing alpha cleared `LOW_FITNESS` at fitness **1.00** against a floor of **1.0**.
Truncation caps per-name weight; lowering it spreads the book, which raises fitness
by raising Sharpe per unit of risk and simultaneously relieves `CONCENTRATED_WEIGHT`.
It is a pure settings change — same expression, same `expression_hash`, different
`AlphaSettings`.

**Work.** `DEFAULT_TRUNCATIONS = (0.08, 0.04, 0.01)` — reference value first, per B1's
ordering rule. No other code change; `truncation` is already in `_structure_of` and
already rides on the alpha row (`alphas.truncation`, `models/alphas.py:73`).

**Screening design (P4).** Do not run 3 truncations × 7×7 (147 sims, three nights).
Run the probe: take the confirmed `liabilities/cap` structure, sweep truncation at
3 levels on the reduced grid `windows=(5,20,60) × decays=(0,4,8)` = **27 sims**.
Read fitness and `CONCENTRATED_WEIGHT` across levels. Promote the winning level to a
full 7×7 (**49 sims**). Total 76 sims — under half a night, against 147 for the
naive sweep.

**Tests.** Constructor emits three distinct `AlphaSettings.truncation` values;
`_structure_of` separates them; the reduced-grid probe emits complete 3×3 surfaces.

**Acceptance.** The probe produces a fitness-vs-truncation table for a fixed
structure, and the best level is carried into the standard axes with the evidence
recorded in the family's notes.

**Cost.** 76 simulations, one evening. ~2 h of code.

**Honest expected value.** This is the highest-probability win in the plan — one
data point says our margin on the binding check is 0.00 — but it is one data point.
The probe is designed to cost less than a night precisely because the prior is thin.

---

## B3 — A second turnover lever

**Depends on:** A2, A3, B1.

**Rationale.** The `liabilities/cap` surface:

| decay | Sharpe | turnover | fitness | verdict |
|---|---|---|---|---|
| 0 | 2.10 | 0.97 | — | FAIL `HIGH_TURNOVER` (ceiling 0.70) |
| 4 | 1.91 | 0.58 | 1.00 | **PASS** |
| 8 | 1.66 | 0.44 | 0.94 | FAIL `LOW_FITNESS` (floor 1.00) |

Decay is our only turnover control, and it is a *signal smoother*: it trades Sharpe
for turnover monotonically. The pass survives in a one-cell gap. `hump`
(`operators.yaml`) is a different mechanism — it suppresses day-to-day *position*
changes below a threshold, leaving the signal's cross-section intact:

```
hump(x, hump=0.01)   # arg 0 matrix, arg 1 float in [0,1], default 0.01
```

Two levers with different Sharpe costs turn a 1-D squeeze into a 2-D region.

**Design decision — `hump` is a structure axis, not a settings axis.** It changes
the expression, therefore `expression_hash`, therefore the alpha row. Placed
anywhere else it would violate P2 exactly as universe does. It is added to
`_structure_of` in A3 as `turnover_control`, and applied **outside** the
cross-sectional wrap, since it acts on final alpha values:

```python
def _apply_turnover_control(node: Node, control: tuple[str, float] | None) -> Node:
    if control is None:
        return node
    op, level = control
    return OperatorCall(op, [node, Number(level, False)])   # is_int=False

# in _depth1_builder, after _wrap_cross_section:
return _apply_turnover_control(_wrap_cross_section(node, _cs, _grp), _tc)
```

```python
DEFAULT_TURNOVER_CONTROLS: tuple[tuple[str, float] | None, ...] = (
    None,                  # reference — keeps the baseline reproducible
    ("hump", 0.01),
    ("hump", 0.05),
    ("hump_decay", 0.05),
)
```

**Explicitly out of scope here: `trade_when`.** It is in the KB and it is unused in
the family grid, but it is not a turnover knob — it is a *conditional mechanism*
("trade only when the trigger holds"), and its economics come from the trigger, not
from the gating. It already has a home in `composite_constructor.py:129`. Lumping it
into a turnover axis would mean sweeping trigger expressions, which is a mechanism
search wearing a settings costume. Leave it where it is.

**Screening design.** Probe on the confirmed structure at the failing corner —
`decays=(0, 1, 2)` × `windows=(5, 10, 20)` × 4 turnover levels = **36 sims**. The
question is narrow and answerable: *at decay 0–2, does hump bring turnover under
0.70 while holding Sharpe above where decay=4 left it?* If yes, promote to a full
7×7 at the winning level (49 sims).

**Tests** (extend `tests/test_constructor.py`; new `tests/test_turnover_control.py`)
- Every emitted expression validates against the KB (`hump` arg-1 float bounds).
- `turnover_control=None` reproduces the pre-change expression byte-for-byte.
- `hump` wraps outside `rank`/`group_*`, never inside.
- `_structure_of` separates hump levels into distinct surfaces.

**Acceptance.** A turnover-vs-Sharpe frontier for one structure across
`(decay × hump)`, showing whether a feasible region exists that decay alone cannot
reach.

**Cost.** 36 probe + 49 confirm = **85 simulations**. ~1 day of code.

**Risk.** `hump`'s exact semantics carry an `# unverified` note in
`operators.yaml` for `hump_decay`'s default `p`. Always emit the level explicitly;
never rely on a default.

---

## B4 — Universe: a tuning axis, and a correction to the memo

**Depends on:** A3 (mandatory — without it this silently corrupts surfaces).

**Correction.** The memo framed universe as "free extra draws off an
already-paid-for mechanism." That is **wrong on the economics**, and the correction
matters. TOP1000 ⊂ TOP3000: the same expression on nested universes produces daily
PnL series that are heavily overlapping and typically correlate far above BRAIN's
0.70 self-correlation gate. You cannot submit both. The correlation gate
(`correlation.py:44`) will correctly reject the second one — the system is not
broken, the memo's reasoning was.

**What the axis is actually worth**, which is still worth having:
1. **`LOW_SUB_UNIVERSE_SHARPE` is a real check we do not model.** It appears in
   `is.checks[]` (`docs/BRAIN_API.md`) and is referenced nowhere in `plateau.py` or
   `subperiod.py`. A signal living in the small-cap tail of TOP3000 fails it; the
   same expression on TOP1000 does not.
2. **Universe is a per-mechanism tuning choice** — pick the best universe for a
   mechanism, not three alphas from one mechanism.

**Work.**
1. `DEFAULT_UNIVERSES = ("TOP3000", "TOP1000", "TOP500")`, reference first.
2. **Wire the check.** Add `LOW_SUB_UNIVERSE_SHARPE` to the pre-declared bar in
   `plateau.evaluate()`. `passed_all_checks` already aggregates BRAIN's verdicts, but
   the *reason* is lost; surface the individual check name in `Verdict.reasons` so
   the report says which bar was missed. This is a reporting fix worth more than
   the axis.
3. **Report it as tuning.** In `scripts/report.py`, group universe variants of one
   mechanism into a single row with the best universe marked, rather than listing
   them as separate candidates. Presenting them as three shortlist entries would
   invite exactly the mistake the memo made.

**Tests.** Universe variants of one structure produce distinct surfaces (A3);
`report.py` collapses them into one ranked row; a synthetic
`LOW_SUB_UNIVERSE_SHARPE` failure surfaces by name in `Verdict.reasons`.

**Acceptance.** A universe sweep on one mechanism yields one recommended universe
with the evidence, not three shortlist entries.

**Cost.** Probe only — reduced 3×3 grid × 3 universes = **27 simulations**.
~half a day of code.

---

# Workstream C — diversity

STRATEGY.md §2 makes the objective *count of alphas subject to pairwise correlation
< 0.7*. Both items here attack that constraint directly, and both are cheap relative
to their effect on the binding constraint.

## C1 — Subtree-frequency novelty prior

**Depends on:** nothing. Can run in parallel with A.

**Rationale.** Frequent-subtree avoidance, from the LLM-MCTS work (AAAI 2026).
The mechanism is decoupled from the MCTS and from the LLM: track which AST subtrees
recur among alphas that already passed, and de-prioritise candidates built from
them. Ours is a **pre-simulation** filter, so it spends the scarce resource — 200
sims/night — on structurally novel candidates.

**We already have the hard part.** `structural_skeleton()`
(`validator/features.py:76`) canonicalises a whole tree: fields → type, windows →
band, constants → `<INT>`/`<NUM>`. Generalise it from the root to every subtree.

```python
# app/validator/features.py
def subtree_skeletons(node: Node, kb: ValidatorKB, *, min_ops: int = 2) -> set[str]:
    """Canonical skeletons of every subtree with at least `min_ops` operators.

    min_ops=2 skips bare fields and single-operator leaves, which are shared by
    everything and carry no novelty signal.
    """
```

Add `feature_json["subtree_hashes"]` — a sorted list of `sha256` digests. This is a
JSON column (`models/alphas.py:80`), so **no migration**; existing rows simply lack
the key, and the scorer treats a missing key as an empty set.

**New module** `app/services/novelty.py`:

```python
def subtree_frequency(db: Session, statuses=(PASSED, SUBMITTED)) -> Counter[str]:
    """How often each subtree hash appears among alphas that got somewhere."""

def novelty_score(candidate_hashes: set[str], freq: Counter[str], corpus_size: int) -> float:
    """Mean inverse document frequency over the candidate's subtrees.

    IDF, not raw count: a subtree in 90% of the corpus should cost far more than
    one in 10%, and the measure must not drift as the corpus grows.
    Returns 1.0 for an empty corpus — cold start must not penalise anything.
    """
```

Wire into `expand()` as a **ranking key on the emitted candidate list**, never as a
hard filter — a hard filter on a 625-alpha corpus would fit noise, which is the
mistake STRATEGY.md §10 warns about. Surface `novelty_score` in the review console
next to `complexity_score` so the operator can see it and disagree.

**A backfill script** `scripts/backfill_subtree_hashes.py` re-extracts features for
existing alphas so the corpus is populated from day one. Idempotent; runs in
seconds on 625 rows.

**Tests** (`tests/test_novelty.py`, new)
- `subtree_skeletons` on `rank(ts_zscore(divide(ts_backfill(x,120),cap),5))` returns
  the expected nested set, and `min_ops` excludes the leaves.
- Two alphas differing only in a window **band** share subtree hashes; differing
  across bands do not.
- Empty corpus → every candidate scores 1.0.
- A candidate whose every subtree appears in every corpus member scores lowest, and
  ranking is a strict ordering (no ties collapsing the list).

**Acceptance.** Re-ranking a real 400-candidate family changes the top-50 ordering,
and the corpus frequency table shows the expected head — `rank(...)`,
`ts_backfill(...)` — as the most common subtrees.

**Cost.** 0 simulations. ~2 days.

---

## C2 — Orthogonalised variants of promoted alphas

**Depends on:** A1 (the correlation gate must be trustworthy first).

**Rationale.** Every alpha rejected at the correlation gate is a simulation spent to
learn "too similar." `regression_neut(y, x)` returns the cross-sectional residual of
`y` on `x` — the component of our signal that the colliding factor *cannot* explain.
That converts a rejection into a candidate. `vector_neut(x, y)` is the projection
form of the same idea.

**Correction to the memo's framing.** It called the existing composite "orthogonal"
a difference rather than a residual. That is accurate — `composite_constructor.py:116`
builds `group_neutralize(zscore(a) − zscore(b), group)`, which removes b's *level*,
not its *explanatory power* — but the two operators are not interchangeable in cost.
Read the signatures: both take a **matrix** as the second argument, meaning the risk
factor must be expressed inline as a sub-expression. That has a real ceiling.

**Two tiers, cheapest first.**

*Tier 1 — standard risk factors.* Neutralise against generic, short expressions.
No dependency on the portfolio, so it applies to any family:

```python
RISK_PROXIES: dict[str, Node] = {
    "size":       OperatorCall("log", [Field("cap")]),
    "momentum":   OperatorCall("ts_mean", [Field("returns"), Number(252.0, True)]),
    "volatility": OperatorCall("ts_std_dev", [Field("returns"), Number(60.0, True)]),
    "liquidity":  OperatorCall("log", [Field("adv20")]),
}
```

Emitted as `regression_neut(<alpha_expr>, <proxy>)`. Short, safe, and each is a
recognised risk exposure a reviewer can reason about.

*Tier 2 — neutralise against the actual colliding alpha.* When the correlation gate
names a collision, inline that alpha's expression as the second argument. Strictly
better targeted, and strictly more expensive: expression length and nesting depth
both roughly double. **BRAIN's expression-length limit is not documented anywhere in
`docs/BRAIN_API.md`.** Gate Tier 2 on `complexity_score` (already computed,
`models/alphas.py:79`) below a threshold calibrated from the first rejections, and
treat the first length-related 400 from `POST /simulations` as the empirical limit —
then record it in `docs/BRAIN_API.md`.

**Where it lives.** `composite_constructor.py`, as a new spec kind operating on a
*promoted alpha* rather than a field pair. Its `family_key` must record the parent
(`parent_id`, `models/alphas.py:62`) so genealogy stays intact.

**Tests** (extend `tests/test_composite_constructor.py`)
- Tier-1 emission validates for all four proxies against the KB.
- Tier-2 emission is skipped above the complexity threshold.
- `parent_id` is set and `tests/test_genealogy.py` lineage CTEs still resolve.
- A residual variant is *not* structurally identical to its parent
  (`structural_hash` differs), so the structural fallback in the correlation gate
  does not reject it on sight.

**Acceptance.** For one promoted alpha, the four Tier-1 residuals simulate, and at
least one has |ρ| below the parent's own correlation with the portfolio. If none
does, that is a real negative result — record it and stop, rather than expanding the
proxy list until something passes.

**Cost.** 4 variants × one 3×3 probe grid = **36 simulations** per promoted alpha.
~2 days of code.

---

# Workstream D — the data surface

## D1 — Vector fields

**Depends on:** B1 (needs the structure/settings split to add a reducer axis
without collapsing coverage).

**Problem.** `field_triage.py:131` filters `DataField.field_type == "MATRIX"`.
VECTOR fields are never triaged, never reach the constructor, and all six
`vec_*` operators are unreachable. The sample catalog is ~4% VECTOR + GROUP, and
vector fields concentrate in the datasets our own crowding table points at:
`news12` (109 users/field), `analyst4` (356), `option9` (595).

**Work.**

1. **Triage.** Widen the filter to `("MATRIX", "VECTOR")` and pass `field_type` into
   the LLM payload, so the mechanism proposal knows it is describing a per-record
   collection (articles, analysts, strikes) rather than a daily scalar.
2. **Constructor.** `_base_node()` (`constructor.py:173`) starts from
   `Field(spec.field_code)` and immediately applies `ts_backfill`/`divide`. Both
   require a **matrix**; the validator will reject them on a vector field — correctly.
   Add the reducer step first:
   ```python
   def _base_node(spec: FamilySpec, kb: ValidatorKB) -> Node:
       if kb.field_type(spec.field_code) == FieldType.VECTOR.value:
           node: Node = OperatorCall(spec.vector_reducer or "vec_avg", [Field(spec.field_code)])
       else:
           node = Field(spec.field_code)
       # ...existing backfill / divide unchanged
   ```
   Note `_base_node` currently takes no `kb`; threading it through is a small
   signature change across three call sites (`constructor.py:370,410,442`).
3. **The reducer is a mechanism axis, not a formatting detail.** This is the quant
   point that makes D1 worth doing:

   | Reducer | Economic meaning on a news field |
   |---|---|
   | `vec_avg` | average tone of the day's coverage |
   | `vec_sum` | tone weighted by volume of coverage |
   | `vec_count` | **attention** — how much was written at all, independent of tone |
   | `vec_max` / `vec_min` | the most extreme single item |

   `vec_count` in particular is a genuinely different signal from `vec_avg`, not a
   variant of it. Add `vec_reducers` to `GridAxes` as part of the **structure** key.

**Tests** (`tests/test_vector_fields.py`, new; extend `tests/test_field_triage.py`)
- Triage returns VECTOR fields and marks them.
- A VECTOR family emits `vec_avg(field)` at the base and validates.
- A MATRIX family is byte-identical to before the change (characterization).
- Each reducer yields a distinct `_structure_of` tuple.

**Acceptance.** One `news12` vector family expands to complete valid surfaces and
simulates without a single validator rejection.

**Cost.** 49 simulations for the first real family. ~3 days of code.

**Risk.** VECTOR semantics carry `# unverified` notes in `operators.yaml`
(`vec_choose` indexing base). Start with `vec_avg`/`vec_sum`/`vec_count`, which are
unambiguous; leave `vec_choose` until the first successful run confirms indexing.

---

## D2 — Event-time templates

**Depends on:** B1.

**Rationale.** `FREQUENCY_BACKFILL` (`constructor.py:95`) approximates staleness with
a fixed carry-forward — "assume it is 120 days stale." `days_from_last_change(x)`
*measures* it: trading days since the field last moved. For quarterly fundamentals
that is the canonical staleness mechanism, and it is economically distinct from the
level of the field — it is a signal about information arrival, not about value.

Templates worth one family each:
- `rank(days_from_last_change(x))` — pure staleness.
- `subtract(x, last_diff_value(x, d))` — change since the last *different* value,
  which for a quarterly field is the actual reporting delta rather than a
  backfill artefact.
- `trade_when(days_from_last_change(x) < k, <signal>, -1)` — trade only on fresh
  information. Note this is the *right* home for `trade_when`, per B3: the
  economics come from the trigger.

**Work.** A `template` field on `FamilySpec` selecting the node builder, so these
are first-class family kinds rather than special cases inside `expand()`.
`days_from_last_change` takes no window argument — the `(window, decay)` surface for
that template is a `(decay)` line, so either pair it with a windowed outer operator
or declare a reduced surface shape explicitly. **Do not silently emit a
one-dimensional surface into a system whose plateau test assumes two dimensions**;
that is a P2 violation. Recommended: always compose with a windowed outer operator
(`ts_rank(days_from_last_change(x), w)`), which restores the 2-D surface honestly.

**Tests.** Each template validates; the composed form yields a complete 7×7 surface;
the un-composed form is rejected at construction with a clear error rather than
emitting a degenerate surface.

**Acceptance.** One event-time family on a quarterly fundamental completes a surface.

**Cost.** 49 simulations. ~2 days.

---

# Workstream E — statistics

## E1 — PBO via CSCV

**Depends on:** A1 (hard — the PnL units must be known), A2 (reuses
`build_family_matrix`).

**Rationale.** DSR asks *"is this point real given N trials?"* — per-point. PBO asks
*"within this family, does in-sample rank predict out-of-sample rank at all?"* —
per-family. A family with high PBO is one where the whole surface is noise and the
winner is whichever point got lucky, which no per-point statistic can detect. Recent
comparative work recommends reporting both.

**Method** (Bailey, Borwein, López de Prado, Zhu). Given the `(T, N)` matrix from
A2: split T into S even blocks; for each of the `C(S, S/2)` ways to choose half the
blocks as in-sample, pick the candidate with the best IS Sharpe, find its rank among
all N candidates out-of-sample, map to relative rank `ω ∈ (0,1)`, and take
`λ = log(ω / (1−ω))`. `PBO = P(λ ≤ 0)` — the frequency with which the IS winner
lands in the bottom half OS.

**Implementation note that makes it cheap.** The naive form recomputes Sharpe over
half the series for every combination — with S=16 that is `C(16,8) = 12,870`
combinations × N candidates. Don't. Precompute per-block `n`, `Σx`, `Σx²` per
candidate — an `(S, N)` array each. Any block subset's mean and variance is then an
O(1) combination of block aggregates, so the whole computation is a handful of
vectorised numpy reductions over a `(12870, N)` index array. Sub-second for N=49.

```python
# app/services/pbo.py
@dataclass(frozen=True)
class PBOResult:
    pbo: float                 # P(logit <= 0)
    n_candidates: int
    n_blocks: int
    n_combinations: int
    median_logit: float
    degraded: bool             # True when N or T fell below the reliable range

def compute_pbo(matrix: np.ndarray, *, n_blocks: int = 16) -> PBOResult:
    """Probability of Backtest Overfitting via CSCV.

    matrix: (T, N) daily PnL, columns = candidates.
    Requires N >= 4 and T >= 2 * n_blocks; below that, returns degraded=True and
    the caller must not gate on it.
    """
```

**Reporting, not gating — at first.** Add PBO to the family header in
`scripts/report.py` and to the UI family view. **Do not gate promotions on it until
we have PBO values for at least ten real families.** A threshold chosen before we
have seen the distribution is a number invented to look rigorous. Once ten families
exist, pick the threshold from the empirical distribution and record the reasoning.

**Explicitly not applicable: purging and embargo.** Those correct label leakage when
features and labels overlap in time under supervised cross-validation. We are
resampling a realised PnL series with no labels and no feature windows. Importing
them here would be cargo-culting a technique whose preconditions we do not meet, and
the plan should say so in the module docstring so nobody adds them later.

**Tests** (`tests/test_pbo.py`, new)
- Pure-noise matrix (N=50 iid gaussian columns) → PBO ≈ 0.5 within tolerance.
- One column with a genuine constant edge plus 49 noise columns → PBO well below 0.5.
- Block aggregation matches a brute-force recomputation on a small case (S=6, N=5).
- `degraded=True` for N=2 and for T < 2·S; the report renders it as "insufficient"
  rather than printing a number.

**Acceptance.** `python -m scripts.report` prints a PBO per family alongside the DSR,
and the noise-matrix test pins ≈0.5.

**Cost.** 0 simulations (PnL fetches only). ~3 days.

---

## E2 — Perturbation-extended robustness check

**Depends on:** A3, B2/B4 (needs adjacent settings points to exist).

**Rationale.** Our plateau test is already a perturbation-fidelity test — it asks
whether a result survives a small change in its neighbourhood — restricted to two
axes. A mechanism should also survive a one-step change in *neutralization*; an
overfit should not.

**Design decision — do not widen the surface.** Adding neutralization to the
neighbourhood would mean removing it from `_structure_of`, which merges surfaces
that are genuinely not comparable. Instead add a **separate, second-order check**
that reads across surfaces at matched coordinates:

```python
NEUTRALIZATION_LADDER = ("NONE", "MARKET", "SECTOR", "INDUSTRY", "SUBINDUSTRY")

def neutralization_robustness(
    db: Session, family_key: str, point: SurfacePoint
) -> tuple[float | None, str]:
    """Sharpe ratio of `point` against the same (window, decay) one step along the
    neutralization ladder. Returns (ratio, reason); None when the neighbour was
    never simulated — absence is not evidence of fragility.
    """
```

Report it as a column; make it advisory. It becomes a promotion criterion only after
we have seen it on enough families to know its distribution — same discipline as E1.

**Tests.** Matched-coordinate lookup finds the right neighbour; a missing neighbour
returns `None` and never contributes a failure reason.

**Acceptance.** The report shows a neutralization-robustness column for families that
have adjacent neutralization surfaces.

**Cost.** 0 new simulations (reads points B2/B4 already produce). ~1 day.

---

# Workstream F — the feedback loop

## F1 — Out-of-sample decay tracking

**Depends on:** A1.

**Rationale.** This is the only item in the plan that tells us whether the filter
*works*. We store `is` metrics only. Once an alpha is submitted and live, BRAIN
exposes `os` metrics. Nothing compares realised out-of-sample decay against what the
filter predicted. STRATEGY.md §6 asserts ~26% decay; that number is currently
inherited, not measured on our own alphas.

**Work.** Extend `scripts/sync_submission_outcomes.py` to pull `os` metrics for
submitted alphas into `AlphaProductionSnapshot` (already exists,
`models/alphas.py:109`), then a report section: for each submitted alpha, IS Sharpe,
OS Sharpe, realised decay, and the filter's verdict at promotion time (DSR, PBO,
plateau ratio, neutralization robustness).

The payoff is the correlation between *predicted* and *realised* quality. If DSR at
promotion time does not correlate with realised OS decay across our own alphas, the
DSR threshold is decoration and we should say so and change it. That is worth more
than any single new axis in this plan.

**Tests.** Snapshot ingestion is idempotent; the decay report handles an alpha with
no OS data yet without failing.

**Acceptance.** A table of every submitted alpha with IS Sharpe, OS Sharpe, realised
decay, and its promotion-time statistics.

**Cost.** 0 simulations. ~1 week, and it only becomes informative once several
alphas have been live for a quarter — which is the reason to start the plumbing now
rather than when the data would have been useful.

---

# Sequencing

Sim costs are one-off probes unless noted. "Nights" assumes 200 sims/night.

| Phase | Items | Code | Sims | Gate to the next phase |
|---|---|---|---|---|
| **0 — Trust** | A1, A2, A3 | ~4 d | 0 | PnL convention documented; `N_eff` in the call path; no duplicate surface coordinates |
| **1 — Constructor** | B1, then B2 | ~3 d | 76 (½ night) | Structure count invariant to settings width; truncation/fitness table exists |
| **2 — Turnover** | B3 | ~1 d | 85 (½ night) | A feasible turnover region exists that decay alone cannot reach — or a recorded negative |
| **3 — Diversity** | C1, C2, B4 | ~5 d | 63 (⅓ night) | Novelty re-ranking changes the shortlist; ≥1 residual variant beats its parent's correlation |
| **4 — Data** | D1, D2 | ~5 d | 98 (½ night) | A vector family and an event-time family each complete a surface |
| **5 — Statistics** | E1, E2 | ~4 d | 0 | PBO on ten families; distribution inspected before any threshold is chosen |
| **6 — Feedback** | F1 | ~1 w | 0 | IS-vs-OS table for every submitted alpha |

Phases 1–4 are independently shippable and each answers one question. Phase 0 is not
optional and is not shippable in pieces.

**Total new simulation cost through phase 4: ~322** — under two nights of budget, for
work the memo's naive reading would have spent six nights on.

---

# Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | PnL is cumulative; every statistic to date is wrong | Medium | **Severe** — invalidates DSR, subperiod, correlation gate, and the two promotions | A1 probe before anything else. Treat existing promotions as provisional until it lands. |
| R2 | B1's refactor changes candidate output subtly | Medium | High — silently different families | Characterization test written and passing *before* the refactor |
| R3 | `N_eff` proves so forgiving that the bar stops binding | Low | Medium | `N_eff` is bounded to `[1, M]` by construction (`subperiod.py:58`); assert the bound in the test |
| R4 | Adding axes inflates trials faster than `N_eff` discounts | Medium | Medium | P4 screening: probe on reduced grids, promote only winners |
| R5 | Tier-2 orthogonalisation exceeds an undocumented expression limit | Medium | Low | Gate on `complexity_score`; record the first 400 as the empirical limit |
| R6 | Vector operator semantics differ from the KB's unverified notes | Medium | Medium | Start with `vec_avg`/`vec_sum`/`vec_count`; hold `vec_choose` |
| R7 | PBO threshold chosen before its distribution is known | Medium | Medium | Report-only until ten families exist; threshold from data, reasoning recorded |
| R8 | Novelty prior fits noise on a 625-alpha corpus | Medium | Low | Ranking key, never a hard filter; visible in the console so the operator can override |
| R9 | Per-family PnL backfill multiplies API calls | High | Low | Existing polite client, batch after campaigns, never inside `evaluate()` |

---

# What this plan deliberately does not do

- **No GFlowNet.** It is the right long-run answer to the mode-seeking/mode-covering
  mismatch, and it is a new dependency, a learned model, and a training loop — for a
  system whose corpus is 625 alphas and whose bottleneck is 200 simulations/night.
  C1 buys a meaningful share of the diversity benefit for two days of work and no new
  dependency. Revisit when the corpus is an order of magnitude larger.
- **No LLM-MCTS.** Same reasoning. C1 extracts the transferable mechanism; the search
  wrapper needs a corpus we do not have.
- **No AlphaEval adoption.** Four of its five dimensions need local price data BRAIN
  does not expose. E2 takes the one idea that transfers. Adopting the framework
  wholesale would mean building a data pipeline to serve an evaluation metric — the
  tail wagging the dog.
- **No purging or embargo** (see E1).
- **No change to the submission line.** `tests/test_brain_no_post.py` stays green.

---

# Appendix — file-level change map

| File | Workstream | Change |
|---|---|---|
| `scripts/probe_pnl_convention.py` | A1 | **new** — one-shot convention probe |
| `app/services/pnl_storage.py` | A1 | `save_pnl(..., cumulative: bool)` |
| `scripts/backfill_pnl.py` | A1, A2 | arm reconciliation; `--strict`; `--family` |
| `app/services/subperiod.py` | A1 | reconciliation reason surfaced to `evaluate()` |
| `app/services/family_matrix.py` | A2 | **new** — `build_family_matrix` |
| `app/services/correlation.py` | A2 | factor out the date-intersection loop (`:88-99`) |
| `app/services/plateau.py` | A2, A3, B4, E2 | wire `n_eff`; `_structure_of` + universe/turnover; check names in reasons; robustness column |
| `app/services/constructor.py` | A3, B1–B4, D1, D2 | structure/settings split; new axes; vector base node; templates |
| `scripts/run_family.py` | B1 | `--structures`, `--settings-per-structure`, `--probe-axis` |
| `app/validator/features.py` | C1 | `subtree_skeletons`; `feature_json["subtree_hashes"]` |
| `app/services/novelty.py` | C1 | **new** — frequency table + IDF scorer |
| `scripts/backfill_subtree_hashes.py` | C1 | **new** — idempotent backfill |
| `app/services/composite_constructor.py` | C2 | residual variants of promoted alphas |
| `app/services/field_triage.py` | D1 | MATRIX+VECTOR; field type into the prompt |
| `app/services/pbo.py` | E1 | **new** — CSCV |
| `scripts/report.py` | B4, E1, E2, F1 | universe collapsing; PBO; robustness; IS-vs-OS |
| `scripts/sync_submission_outcomes.py` | F1 | ingest `os` metrics |

**No Alembic migration is required by any item in this plan.** Everything new lands
in `alphas.feature_json` (`models/alphas.py:80`, a JSON column) or in modules that
compute from stored artefacts. The one table F1 needs —
`AlphaProductionSnapshot` — already exists.
