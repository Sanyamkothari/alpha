# Implementation Plan — Restoring the Promotion Path

> **Status: executed.** This plan was implemented, then reviewed in
> [../CODE_REVIEW.md](../CODE_REVIEW.md) and hardened further. Baselines quoted below
> (194 tests, 5.9s) are the pre-implementation state and are retained deliberately so
> the plan still reads as it was written. Current: 270 tests, ~35s. Decision D3
> (representative granularity) was resolved in favour of the `structure` tuple — see
> [OPEN_DECISIONS.md](OPEN_DECISIONS.md) §4.


**Scope:** the ten findings from `REVIEW.md`.
**Baseline commit:** `29a64c2`. All file:line references are as of that commit.
**Baseline test suite:** 194 passing, 5.9s, no network.

---

## 1. What this plan is actually fixing

Nine of the ten findings are small and independent. One is not, and it dictates the
shape of everything else: **the pipeline cannot promote an alpha.** Not "rarely
promotes" — cannot. In a controlled end-to-end run (49-point family, passing BRAIN
checks, 1300 days of deliberately independent PnL per alpha) the funnel was:

| Stage | Count |
|---|---|
| Simulated | 49 |
| 1. BRAIN checks | 49 |
| 2. Plateau | 42 |
| 3. Sub-period | 29 |
| 4. DSR | 4 |
| **5. Orthogonal** | **0** |
| **Promoted** | **0** |

Every one of the four DSR survivors died at the orthogonality gate, citing a
collision with "submitted alpha #22" — an alpha whose status is `passed` and which
was never submitted. A direct probe confirmed the shape of it:

```
distinct structural hashes across the 49-cell family: 4
group sizes: [14, 14, 14, 7]
alphas blocked by structural collision: 49 / 49
```

So the plan is organised around that: **Workstream A restores the promotion path**,
and B–D fix the display, telemetry and onboarding defects that sit on top of it.
A is the only workstream with real design content. B–D are mechanical once decided.

### 1.1 Findings → workstreams

| # | Finding | Workstream | Files |
|---|---|---|---|
| 1 | Structural proxy overrides measured PnL; siblings block each other | **A** | `correlation.py`, `plateau.py` |
| 2 | Collision message says "submitted" for a `passed` alpha | **A** | `plateau.py` |
| 3 | Surface axes don't match the constructor grid — 41/49 cells hidden | **B** | `ui.py`, `report.py`, `index.html` |
| 4 | `simulated` counts metric rows, not alphas ("49 alphas · 98 simulated") | **C** | `ui.py`, `spend.py` |
| 5 | `0 sat on a plateau` is a hardcoded literal | **C** | `ui.py`, `index.html` |
| 6 | README setup leaves the DB fieldless without BRAIN credentials | **D** | `README.md` |
| 7 | Missing credentials produce a bare traceback | **D** | `scripts/_cli.py` (new), 6 scripts |
| 8 | Three `file:///Users/sanya/...` links | **D** | `README.md` |
| 9 | Empty DB reports "Everything has been tried" | **D** | `report.py` |
| 10 | favicon 404; modal backdrop inert; doc drift | **C/D** | `main.py`, `index.html`, `README.md` |

### 1.2 Why the test suite is green today

This matters, because it tells us what kind of tests to add.

`tests/test_plateau.py:36` — the `_point()` helper builds every grid point with
`status="rejected"`. The portfolio query at `plateau.py:259` selects
`status IN (SUBMITTED, PASSED)`. **Test siblings are therefore never in the
portfolio, so they can never collide with each other.** In production
`result_import.py:228` transitions any alpha clearing BRAIN's checks to `PASSED`,
which puts all 49 siblings in the portfolio the moment their results land.

The bug lives entirely in the gap between the fixture's status and production's
status. Every new test in §7 sets realistic statuses for exactly this reason.

Finding 3 hides for a related reason: `test_plateau.py` builds its surfaces by
iterating `WINDOW_LADDER`/`DECAY_LADDER`, so the fixtures land on the display axes
by construction. Only the real constructor produces off-axis coordinates.

---

## 2. Design decisions

Four decisions govern the implementation. Each is stated with the alternatives that
were considered and rejected, so a reviewer can disagree with the decision rather
than reverse-engineer it from a diff.

### D1 — Measured evidence always wins; the structural proxy fills gaps only

`correlation.py:115` runs `check_structural_proxy` whenever the empirical pass found
no colliding alpha. That is not a fallback — it is an override. In the reproduction
the candidate had 1300 days of PnL, every portfolio alpha had 1300 days of PnL, and
measured correlations were ≈0.00; the proxy rejected them anyway on the grounds that
their expression skeletons rhymed.

**Decision:** the proxy sees only the portfolio alphas the empirical pass could not
measure — those with no stored PnL, or with fewer than `MIN_COMMON_TRADING_DAYS`
overlapping dates. If every portfolio alpha was measured and cleared, no proxy runs.

*Rejected — delete the proxy.* It is genuinely load-bearing before `backfill_pnl`
has run; a cold-start user has no PnL at all and the proxy is the only guard they
have.

*Rejected — run the proxy only when the candidate has no PnL.* Too coarse in the
other direction: a candidate with PnL still needs proxy protection against portfolio
alphas that have none.

### D2 — Same-family siblings are excluded from the proxy, never from the measurement

A family is a deliberate grid sweep of one mechanism. Its members share a structural
skeleton **by construction** — that is what a family is. Applying a
"same skeleton ⇒ correlated" heuristic inside a family is not a signal, it is a
tautology, and it fires on 100% of pairs.

**Decision:** in `check_portfolio_correlation` skip any portfolio alpha where
`family_key == candidate.family_key and status != SUBMITTED`. A sibling that really
was submitted is a real portfolio position and still blocks. Empirical comparison
against siblings is untouched — if two siblings genuinely correlate at 0.9, the
measured gate still rejects the second, on evidence.

*Rejected — exclude siblings from the portfolio entirely in `evaluate()`.* This is
what the rough draft proposed. It also removes them from the empirical comparison,
which discards real measured evidence, and it leaves nothing to prevent 42
near-identical alphas from promoting together. See D3.

### D3 — Deduplication becomes explicit, not a side effect of the correlation gate

Once D1 and D2 land, the 42 plateau survivors are no longer blocked — and the
morning queue fills with 42 rows of the same mechanism at adjacent window/decay
settings. That is a different failure of the same product promise. Today the
correlation gate performs this deduplication accidentally, by rejecting everything.

**Decision:** add an explicit representative-selection pass at the end of
`evaluate()`. Group the promoted verdicts by `structural_hash` (falling back to the
`SurfacePoint.structure` tuple when a hash is absent), keep the strongest member of
each group, and demote the rest with a legible reason:

> `redundant with #37 — same structural skeleton, kept the stronger point (Sharpe 2.00 vs 1.91)`

Ranking key, deterministic and total: `(-sharpe, -plateau_ratio, alpha_id)`.

On the reproduction family this yields **up to 4 promotions** (one per structural
hash) instead of 0 or 42. Demoted siblings still surface in the near-miss list —
they satisfy `clears_bar` — so nothing disappears; it is reclassified from
"rejected as correlated" to "kept the better twin", which is what actually happened.

This is also the honest reading of the project's own thesis: `STRATEGY.md`'s
"plateau, not peak" says the *ridge* is the mechanism. One submission per ridge.

*Rejected — one representative per family.* Over-merges: a family spans several
`structure` tuples (different `ts`/`cs`/neutralization), which are genuinely
different alphas, not settings of one alpha.

*Rejected — leave dedup to the operator.* Pushes 42 rows of near-duplicates into the
one screen whose entire purpose is to be short.

### D4 — Derive surface axes from the data; keep the constants as an empty-surface fallback

Two grids disagree:

| Source | Windows | Decays | Cells |
|---|---|---|---|
| `constructor.py:56-57` (`STANDARD_*`, the default) | 5, 10, 20, 40, 60, 120, 250 | 0, 1, 2, 4, 6, 8, 16 | 49 |
| `plateau.py:49-50` (`WINDOW_LADDER`/`DECAY_LADDER`) | 5, 10, **22, 63, 126, 252** | 0, **4, 8, 16** | 24 |

The display iterates the second while the cells are keyed by the first: 8 of 49
cells land on the rendered axes, 41 are invisible, and 16 rendered cells can never
be filled because the constructor never emits those coordinates.

**Decision:** `/api/ui/surfaces` and `report._surface_grid()` compute axes as the
sorted set of coordinates actually present in `load_surface(...)`, falling back to
the constants only when the surface is empty.

**Do not change the constants' values.** `_neighbours()` (`plateau.py:209`) already
resolves ladders dynamically and is correct — the constants are used only as a
fallback and by tests. Widening `WINDOW_LADDER` to 7 entries would silently turn the
`test_plateau.py` fixtures from 24 points into 49, crossing `MIN_TRIALS_FOR_DSR = 30`
and flipping those tests from `COLD_START_FALLBACK` into `DSR` mode. That is an
unrelated behavioural change smuggled in via a constant. Instead, rename them to
`FALLBACK_WINDOW_LADDER`/`FALLBACK_DECAY_LADDER` so the role is unambiguous, and
keep backwards-compatible aliases for one release.

**Axes are computed per family, not per structure.** The API returns one `windows`
and one `decays` for the whole family and the frontend renders every structure
against them (`index.html:754`, `758`), so the axes must be the union across all
points in the family.

**Axes must come from `include_unsimulated=True` points.** `ui.py:188` already loads
them that way. If axes were derived from simulated points only, genuine holes would
vanish from the grid and the "fill missing cell" recovery path would have nothing to
offer.

---

## 3. Workstream A — Restore the promotion path

**Findings 1, 2. Files:** `backend/app/services/correlation.py`,
`backend/app/services/plateau.py`.
**Note on imports:** `correlation.py:17` imports from `plateau` at module scope while
`plateau.py:250` imports `correlation` *inside* `evaluate()` to break the cycle.
Preserve that arrangement; do not lift the inner import to module scope.

### A1 — Restrict the structural proxy to unmeasured portfolio alphas (D1)

**File:** `correlation.py`, `check_portfolio_empirical_correlation()` (lines 44–120).

Track which portfolio alphas were genuinely compared. Inside the existing loop over
`portfolio`, after the `len(common_dates) < min_overlap: continue` guard at line 96,
record `measured_ids.add(port_alpha.id)`. Then replace the unconditional fallback at
lines 115–118 with:

```python
    # The proxy is a stand-in for missing evidence, not a veto over evidence we
    # have. Alphas we actually measured and cleared are settled; only the ones we
    # could not measure fall through to the skeleton heuristic.
    unmeasured = [p for p in portfolio if p.id != alpha_id and p.id not in measured_ids]
    if unmeasured:
        is_struct_corr, struct_collision = check_structural_proxy(
            db, alpha_id, portfolio=unmeasured
        )
        if is_struct_corr:
            return True, struct_collision, max_corr

    return False, None, max_corr
```

`measured_ids` is initialised to an empty set before the `if cand_pnl_data is not
None:` block at line 79, so the candidate-has-no-PnL path leaves it empty and every
portfolio alpha correctly falls through to the proxy — the cold-start behaviour is
unchanged.

**Edge cases.** Empty portfolio: `unmeasured` is empty, no proxy call, returns
`(False, None, 0.0)` as before. Candidate PnL present but every portfolio alpha
lacks it: `unmeasured == portfolio`, identical to today. Non-finite correlation:
already neutralised by `compute_pairwise_correlation()` (line 33).

**Do not touch** `compute_max_self_correlation_with_submitted()` (line 164). Its
proxy call at line 235 is scoped to genuinely submitted alphas and is correct.

### A2 — Exclude non-submitted siblings and fix the message (D2, finding 2)

**File:** `plateau.py`, `check_portfolio_correlation()` (lines 102–142).

Inside the `for port_alpha in portfolio:` loop, immediately after the existing
`if port_alpha.id == candidate.id: continue` at line 125:

```python
        # A family is a grid sweep of one mechanism, so its members share a
        # skeleton by construction. Applying "same skeleton => correlated" inside
        # a family fires on every pair and says nothing. A sibling that was really
        # submitted is a real position and still blocks; see D3 for how the family
        # picks its own representative.
        same_family = bool(
            candidate.family_key
            and port_alpha.family_key
            and candidate.family_key == port_alpha.family_key
        )
        if same_family and port_alpha.status != AlphaStatus.SUBMITTED.value:
            continue
```

Then correct the wording at line 135, which currently hardcodes "submitted" for a
portfolio that admits `PASSED` alphas too:

```python
        if cand_struct and port_struct and cand_struct == port_struct and cand_field == port_field:
            kind = (
                "submitted"
                if port_alpha.status == AlphaStatus.SUBMITTED.value
                else "portfolio"
            )
            return True, f"structural correlation collision with {kind} alpha #{port_alpha.id}"
```

**Compatibility check.** `test_plateau.py:124` asserts
`any("collision with submitted alpha" in r ...)`. In that fixture the colliding
alpha has `status="submitted"`, so `kind == "submitted"` and the assertion still
holds. Same for the `family collision with submitted alpha` branch at line 140,
which is already correctly gated on `SUBMITTED`.

### A3 — Explicit representative selection (D3)

**File:** `plateau.py`.

**Schema.** Add one field to `Verdict` (after line 98):

```python
    redundant_with: int | None = None
```

**New helper**, placed after `_neighbours()`:

```python
def _select_representatives(
    db: Session, verdicts: list[Verdict], surface: list[SurfacePoint]
) -> None:
    """Keep one promoted point per distinct structural skeleton.

    A family sweeps one mechanism across settings, so a promoted ridge usually
    arrives as a dozen adjacent points that would all be the same submission. The
    correlation gate used to absorb this implicitly by rejecting all of them; doing
    it here instead keeps the shortlist short AND says out loud what happened.
    Mutates verdicts in place.
    """
    structure_of = {p.alpha_id: p.structure for p in surface}
    groups: dict[object, list[Verdict]] = defaultdict(list)
    for v in verdicts:
        if not v.promoted:
            continue
        alpha = db.get(Alpha, v.alpha_id)
        key = (alpha.feature_json or {}).get("structural_hash") if alpha else None
        groups[key or ("structure", structure_of.get(v.alpha_id))].append(v)

    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda v: (-(v.sharpe or 0.0), -(v.plateau_ratio or 0.0), v.alpha_id)
        )
        keeper = members[0]
        for other in members[1:]:
            other.promoted = False
            other.redundant_with = keeper.alpha_id
            other.reasons.append(
                f"redundant with #{keeper.alpha_id} — same structural skeleton, "
                f"kept the stronger point (Sharpe {keeper.sharpe:.2f} vs {other.sharpe:.2f})"
            )
```

**Call site.** In `evaluate()`, between the end of the per-point loop (line 393) and
the sort at line 395:

```python
    _select_representatives(db, verdicts, surface)
```

It must run *before* the sort, because the sort key reads `v.promoted`.

**Logging.** Extend the `family_evaluated` log (line 398) with
`redundant=sum(1 for v in verdicts if v.redundant_with is not None)` so the
demotions are visible in structured logs rather than inferred.

**Determinism.** `sharpe` may be `None` on a promoted verdict only if
`require_pnl=False` and `passed_all_checks` is true with no Sharpe — the `or 0.0`
guards it, and `alpha_id` makes the ordering total regardless.

### A4 — Surface `redundant_with` in the API

**File:** `ui.py`, `_verdict_row()` (lines 49–95). Add alongside the other
`getattr`-guarded fields:

```python
        "redundant_with": getattr(v, "redundant_with", None),
```

The console needs no change to display it — the reason string already renders in the
near-miss list — but the field lets the UI link to the keeper later.

### A5 — Correct the report's gating breakdown

**File:** `report.py`, lines 134–160.

The final `Promoted` column currently prints `len(s5)`, not the count of
`v.promoted`. Today those agree by accident; after A3 they will not, and the table
would claim 4 promoted while the shortlist shows 1. Add the missing stage and read
the real flag:

```python
        s5 = [v for v in s4 if not v.is_correlated]
        s6 = [v for v in s5 if v.promoted]          # survived representative selection

        add(
            f"| `{family}` | {g_mode} | {sim_display} | {len(s1)} | {len(s2)} | {len(s3)} |"
            f" {len(s4)} | {len(s5)} | {len(s6)} | {sum(1 for v in f_verdicts if v.promoted)} |"
        )
```

Update the header at line 134 and the separator at line 135 to ten columns:

```
| Family | Mode | Simulated | 1. Checks | 2. Plateau | 3. Sub-Period | 4. DSR/Cold-Start | 5. Orthogonal | 6. Representative | Promoted |
|---|---|---|---|---|---|---|---|---|---|
```

### A6 — Acceptance criteria for Workstream A

Re-running the reproduction from §7.3 must produce:

- `alphas blocked by structural collision: 0 / 49` (was 49/49).
- Funnel: `49 → 49 → 42 → 29 → 4 → 4 → ≥1 promoted` (was `… → 0 → 0`).
- No near-miss reason contains the string `submitted alpha #` for an alpha whose
  status is not `submitted`.
- Demoted siblings carry a `redundant with #` reason and remain in the near-miss
  list; the promoted count equals the number of distinct structural hashes among
  the survivors.

---

## 4. Workstream B — Honest plateau surfaces

**Finding 3.** Depends on nothing; can land before or after A.

### B1 — Shared axis derivation

**File:** `plateau.py`. Rename the constants and add one helper next to
`load_surface()`:

```python
FALLBACK_WINDOW_LADDER: tuple[int, ...] = (5, 10, 22, 63, 126, 252)
FALLBACK_DECAY_LADDER: tuple[int, ...] = (0, 4, 8, 16)

# Back-compat aliases — the display no longer uses these. Remove after one release.
WINDOW_LADDER = FALLBACK_WINDOW_LADDER
DECAY_LADDER = FALLBACK_DECAY_LADDER


def surface_axes(points: list[SurfacePoint]) -> tuple[list[int], list[int]]:
    """The coordinates a family actually occupies.

    Derived from the points rather than a constant because the constructor's grid
    is configurable (constructor.STANDARD_* vs WIDE_*) and any fixed ladder will
    disagree with some of them — silently, by dropping cells from the render.
    Callers pass points loaded with include_unsimulated=True so that genuine holes
    keep their coordinates and stay fillable.
    """
    windows = sorted({p.window for p in points if p.window is not None})
    decays = sorted({p.decay for p in points if p.decay is not None})
    return (windows or list(FALLBACK_WINDOW_LADDER), decays or list(FALLBACK_DECAY_LADDER))
```

Keeping this in `plateau.py` gives `ui.py` and `report.py` one definition, and lets
`_neighbours()` (lines 209–213) be refactored onto it later without changing its
behaviour now.

### B2 — `/api/ui/surfaces`

**File:** `ui.py`, lines 185–240.

- Import `surface_axes` alongside the existing `load_surface`.
- After `points = load_surface(db, family, include_unsimulated=True)` (line 188)
  compute `windows, decays = surface_axes(points)`.
- Return `"windows": windows, "decays": decays` at lines 237–238.
- The empty-surface early return (lines 190–195) keeps the fallback constants.

Cell keys (`f"{p.window}:{p.decay}"`, line 207) are unchanged — the axes now agree
with them instead of the other way round.

### B3 — Report ASCII surface

**File:** `report.py`, `_surface_grid()`, lines 65–75.

Replace the two `WINDOW_LADDER`/`DECAY_LADDER` iterations with axes derived from the
points already in hand. Derive from **all** points in the family (matching the API),
not from `sel` (the selected structure), so successive structures print against a
stable grid and are visually comparable:

```python
    windows, decays = surface_axes(points)
    header = "  decay\\win " + "".join(f"{w:>8}" for w in windows)
    lines.append(header)
    for d in decays:
        row = f"  {d:>9} "
        for w in windows:
            p = sel.get((w, d))
            row += f"{p.sharpe:>8.2f}" if p and p.sharpe is not None else f"{'·':>8}"
        lines.append(row)
```

**The loader must change too.** Line 53 currently drops unsimulated points at load
time:

```python
    points = [p for p in load_surface(db, family_key) if p.sharpe is not None]
```

Deriving axes from *that* list would delete the holes' coordinates and the grid
would shrink to only the cells that have results — a surface with four missing
simulations would print as complete. Load everything and let the render decide:

```python
    points = load_surface(db, family_key, include_unsimulated=True)
    if not any(p.sharpe is not None for p in points):
        return ["  (nothing simulated yet)"]
```

The existing `p.sharpe is not None` test in the row f-string already prints `·` for
a hole, so no other change is needed. Note the early-return condition moves from
"no points" to "no *simulated* points" — otherwise a family that is fully emitted
but unsimulated would print an empty 7x7 grid instead of the clearer message.

Width check: 7 windows × 8 columns + 11 leading = 78 characters, still inside a
standard 80-column terminal. A `wide` grid (6 windows) is narrower. No wrapping fix
needed for the shipped grids; note it if a future grid exceeds 8 windows.

### B4 — Frontend: the mini-map is hardcoded to six columns

**File:** `index.html:237`.

```css
.mini .g{display:grid;grid-template-columns:repeat(6,7px);gap:1px}
```

That `6` is `len(WINDOW_LADDER)`. With a 7-window family every mini-map wraps into
the wrong shape — a subtle but total misread of the surface. The table renderer at
lines 754–761 and the neighbour walk at 796–801 are already data-driven and need no
change; only this CSS constant is baked in.

In the mini-map builder (line 1362) set the track count from the data:

```js
    g.style.gridTemplateColumns = `repeat(${p.windows.length},7px)`;
    p.decays.forEach(d=>p.windows.forEach(w=>{ /* unchanged */ }));
```

and drop `grid-template-columns` from the `.mini .g` rule, leaving `display:grid`
and `gap`.

### B5 — Acceptance criteria

- For a default `run_family` family, `/api/ui/surfaces` returns
  `windows == [5,10,20,40,60,120,250]` and `decays == [0,1,2,4,6,8,16]`.
- Every key in every `surfaces[].cells` maps onto the returned axes:
  cells-on-axes goes from **8/49 to 49/49**.
- `scripts.report` prints a 7×7 grid with no `·` for a fully simulated family (was
  6×4 with 16 permanent holes).
- The mini-map renders 7 columns.

---

## 5. Workstream C — Counters and console

### C1 — Separate inventory counters from throughput counters (finding 4)

**Files:** `ui.py`, `spend.py`.

`alpha_metrics` is append-only: it has a `unique` FK to `simulation_imports`
(`results.py:42-44`), so one row is one *import event*, and `load_surface()` takes
the newest row per alpha (`plateau.py:174-176`). Counting rows therefore answers
"how many results were imported", not "how many alphas are simulated".

`report.py:84-97` already does this correctly with
`count(distinct Alpha.id) JOIN AlphaMetric` — which is why the report said 49 while
the console header said 98. **Copy the report's query; do not invent a new one.**

The blanket substitution the rough draft proposed is wrong for half the call sites,
so the rule is:

| Question | Correct count | Sites |
|---|---|---|
| How many alphas exist in state X? (funnel, header) | `count(distinct Alpha.id)` | `ui.py:102, 104`; `ui.py:671, 673` |
| How much work happened in window W? (throughput, budget) | one row per **BRAIN simulation** | `ui.py:725, 731, 740`; `spend.py:149, 151` |

For the throughput and budget sites, row-counting is the right *shape* but the wrong
*filter* — a pasted re-import of an old result is not a simulation the account paid
for. Restrict those to rows whose import came from the runner:

```python
    select(func.count(AlphaMetric.id))
    .join(SimulationImport, SimulationImport.id == AlphaMetric.simulation_import_id)
    .where(SimulationImport.source == ImportSource.BRAIN_API.value)
    .where(AlphaMetric.created_at >= today_start)
```

`spend.py:178` (`passing`) is a funnel counter living in a spend module — move it to
`count(distinct Alpha.id)` with the rest.

**Verification:** import the same result twice; `counts.simulated` must stay at the
number of distinct alphas, and `BRAIN today` must not advance for a `paste` import.

### C2 — Real plateau count (finding 5)

**Files:** `ui.py`, `index.html`.

`index.html:629` prints a literal `0`:

```js
<p>${c.simulated} simulated · ${c.passing} cleared BRAIN's checks · 0 sat on a plateau${...}
```

In the reproduction 42 candidates sat on a plateau, and `is_plateau: true` is
already present in the payload that view fetched.

**Backend** (`ui.py:122-143`): the loop at line 124 already calls `evaluate()` for
every family but `continue`s past anything that is neither promoted nor
`clears_bar`, so count inside the loop *before* that filter:

```python
    plateau_count = 0
    for family in _families(db):
        for v in evaluate(db, family):
            if v.is_plateau:
                plateau_count += 1
            if not (v.promoted or v.clears_bar):
                continue
            ...
```

and add `"plateau": plateau_count` to the `counts` dict at line 137. No extra
`evaluate()` calls — it is the same loop.

**Frontend:** `` `${c.plateau ?? 0} sat on a plateau` ``. Keep `?? 0` so an older
backend still renders.

### C3 — favicon (finding 10)

**File:** `main.py`. The console's only console error is a 404 on `/favicon.ico`.
Add next to the `/` route at line 56:

```python
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        # The console is deliberately asset-free; 204 keeps devtools clean without
        # inventing a binary to serve.
        return Response(status_code=204)
```

Import `Response` from `fastapi`. Register it **before** the `/` route for clarity;
ordering does not matter here since the paths are distinct.

### C4 — Dismissable modal backdrops (finding 10)

**File:** `index.html`. Five `.scrim` overlays exist — `surfaceOverlay` (385),
`outcomeOverlay` (396), `unresolvedOverlay` (428), `rejectionModal` (447),
`helpOverlay` (488) — each with a close function (`closeSurface`, `closeOutcome`,
`closeUnresolvedModal`, `closeRejectionModal`, `closeHelp`). `Escape` works today;
clicking the dimmed backdrop does nothing, and while a modal is open the nav tabs
look clickable but are inert because the scrim intercepts pointer events.

Register one delegated handler near the other bindings (~line 1351):

```js
[["surfaceOverlay",closeSurface],["outcomeOverlay",closeOutcome],
 ["unresolvedOverlay",closeUnresolvedModal],["rejectionModal",closeRejectionModal],
 ["helpOverlay",closeHelp]].forEach(([id,close])=>{
  // Only a click on the scrim itself — a click inside .sheet must not close it.
  $("#"+id).addEventListener("mousedown",e=>{ if(e.target.id===id) close(); });
});
```

Use `mousedown` on the target itself so a text selection that starts inside the
sheet and ends on the backdrop does not dismiss the modal. No change to the keyboard
handler at 1382–1400.

### C5 — Acceptance criteria

- Re-importing a result leaves `counts.simulated` unchanged and does not advance
  `BRAIN today`.
- Header reads `49 alphas · 49 simulated`.
- Zero-survivor line reports the real plateau count (42 in the reproduction).
- No 404 in the browser console on load.
- Clicking the dimmed area closes each of the five modals; clicking inside does not.

---

## 6. Workstream D — Onboarding, CLI ergonomics, docs

### D1 — README setup that works without a BRAIN account (findings 6, 8)

**File:** `README.md`.

Step 3 currently runs migrations → `app.seeds.load_operators` →
`scripts.fetch_brain_catalog`. The last step needs credentials, so a reader without
an account ends up with operators but **no fields and no lookups**, and every
validation fails:

```
POST /api/validate {"expression": "rank(ts_delta(close, 5))"}
→ "field 'close' is not in the catalog for USA/TOP3000/delay1"
```

`app.seeds.seed_all` already loads lookups + operators + the 122-field sample
catalog (verified: 10 datasets, 122 fields), after which the same expression
validates and `run_family --simulate 0` expands 49 candidates entirely offline. Make
it the documented default:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.seeds.seed_all          # offline: operators, lookups, sample catalog

# Optional — replaces the sample catalog with your account's live one:
.venv/bin/python -m scripts.fetch_brain_catalog
```

Add one line stating plainly that everything except simulation works offline on the
sample catalog. This is also the answer to "there is no way to evaluate the tool
before handing it credentials".

Fix the three `file:///Users/sanya/Projects/alpha/...` links (lines 16, 18, 234) to
`docs/DECISIONS.md`, `STRATEGY.md`, `docs/PACKAGING.md`.

Doc drift, same pass: "16 tables" → **21** (22 in SQLite minus `alembic_version`);
"120+ tests" → the measured count after §7 lands (194 today; **do not write a number
you have not run**).

### D2 — One clean CLI error path, not six (finding 7)

**Files:** new `backend/scripts/_cli.py`; six call sites.

`fetch_brain_catalog.py` raises `BrainAuthError` uncaught, so the expected first-run
outcome — no credentials — arrives as a 14-line traceback. The message itself is
good; only the framing is wrong.

Six scripts construct `BrainClient` and share the defect: `backfill_pnl.py`,
`fetch_brain_catalog.py`, `import_brain_alphas.py`, `run_composite.py`,
`run_family.py`, `sync_submission_outcomes.py`. Fix it once:

```python
"""Shared CLI helpers."""
from __future__ import annotations

import functools
import sys

from app.services.brain.client import BrainAuthError, BrainError


def cli_main(fn):
    """Turn expected BRAIN failures into a clean message and exit code 1.

    Credential-less first runs are an expected outcome, not a crash; a traceback
    there reads as "this tool is broken" when the real message is one line long.
    Unexpected exceptions still propagate with their traceback intact.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BrainAuthError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(
                "hint: run `python -m app.seeds.seed_all` to work offline on the "
                "sample catalog instead.",
                file=sys.stderr,
            )
            return 1
        except BrainError as exc:
            print(f"error: BRAIN request failed — {exc}", file=sys.stderr)
            return 1
    return wrapper
```

Decorate each script's `main()` with `@cli_main`. `BrainAuthError` subclasses
`BrainError` (`client.py:53-57`), so ordering of the `except` clauses matters —
keep `BrainAuthError` first.

### D3 — Truthful "what to try next" on an empty catalog (finding 9)

**File:** `report.py:233-234`.

`suggest()` returning `[]` currently prints "Everything has been tried. Add a dataset
or widen the search grid." On a fresh install that is the exact inverse of the truth.
Distinguish "no catalog" from "catalog exhausted":

```python
    if not sug_list:
        has_fields = bool(db.scalar(select(func.count(DataField.id))))
        if not has_fields:
            add(
                "No field catalog loaded. Run `python -m app.seeds.seed_all` for the "
                "offline sample catalog, or `python -m scripts.fetch_brain_catalog` "
                "for your account's live catalog."
            )
        else:
            add("Everything has been tried. Add a dataset or widen the search grid.")
```

`report.py` does **not** import `DataField` today — the crowding table goes through
`dataset_stats()` from `app.services.allocator` (line 23). Add the import
explicitly:

```python
from app.models.fields import DataField
```

### D4 — Acceptance criteria

- A clean clone following the README, with no `.env` credentials, can:
  validate `rank(ts_delta(close, 5))` → `valid: true`; run
  `run_family --field liabilities --denominator cap --simulate 0` → 49 candidates;
  open the console and see a populated allocator panel.
- `python -m scripts.fetch_brain_catalog` with no credentials prints two stderr
  lines and exits 1 — no traceback.
- `python -m scripts.report` on an empty DB recommends seeding.
- No `file://` URLs remain in `README.md`.

---

## 7. Test plan

### 7.1 New file — `backend/tests/test_review_findings.py`

Reuse `tests/conftest.py`'s `db_session` and `client` fixtures and the `_point()`
helper pattern from `test_plateau.py:24`, **but set `status="passed"`** so the
fixtures reproduce production. That single difference is what the current suite is
missing (§1.2).

| # | Test | Asserts |
|---|---|---|
| 1 | `test_measured_correlation_beats_structural_proxy` | Candidate and portfolio alpha share a `structural_hash` and base field, both have ≥`MIN_COMMON_TRADING_DAYS` of independent PnL → not correlated, no collision reason. Pins D1. |
| 2 | `test_structural_proxy_still_applies_to_unmeasured_alphas` | Same setup, portfolio alpha has **no** PnL → collision returned. Pins the cold-start guard D1 must not break. |
| 3 | `test_proxy_runs_only_on_the_unmeasured_subset` | Two portfolio alphas, one measured-and-clean, one unmeasured-and-colliding → collision names the unmeasured one. |
| 4 | `test_passed_siblings_do_not_block_each_other` | 49-point family, all `status="passed"`, shared hash → `check_portfolio_correlation` returns False for every member. Directly pins finding 1 (was 49/49 blocked). |
| 5 | `test_submitted_sibling_still_blocks` | One sibling flipped to `submitted` → the others are blocked. Guards against over-correcting D2. |
| 6 | `test_collision_message_names_portfolio_not_submitted` | Colliding alpha is `passed` → reason contains `portfolio alpha #`, and **not** `submitted alpha #`. Pins finding 2. |
| 7 | `test_family_promotes_one_representative_per_skeleton` | Full family with PnL → `promoted` count equals the number of distinct structural hashes; demoted twins carry `redundant_with` and a `redundant with #` reason; every demoted twin still has `clears_bar`. Pins D3. |
| 8 | `test_representative_selection_is_deterministic` | Two members with identical Sharpe and ratio → the lower `alpha_id` wins, stable across repeated `evaluate()` calls. |
| 9 | `test_surfaces_axes_cover_every_emitted_cell` | Build a family on `constructor.STANDARD_WINDOWS`/`STANDARD_DECAYS`; `GET /api/ui/surfaces` → every `cells` key parses onto the returned axes; `len(windows) == 7`, `len(decays) == 7`. Pins finding 3 (was 8/49). |
| 10 | `test_surfaces_axes_fall_back_when_surface_empty` | Unknown family → axes equal the fallback ladders, `surfaces == []`. |
| 11 | `test_summary_counts_distinct_alphas` | Import two results for one alpha → `counts.simulated == 1`. Pins finding 4. |
| 12 | `test_summary_reports_plateau_count` | Family with a known ridge → `counts.plateau` equals the number of `is_plateau` verdicts and is non-zero. Pins finding 5. |
| 13 | `test_report_empty_catalog_advises_seeding` | `report.build()` with no `DataField` rows → output contains `seed_all`, not "Everything has been tried". Pins finding 9. |
| 14 | `test_favicon_returns_no_content` | `GET /favicon.ico` → 204. |

### 7.2 Existing tests to re-check by hand after each workstream

- `test_plateau.py::test_broad_plateau_is_promoted` — asserts `any(v.promoted)`.
  Survives A3 (the keeper stays promoted) but its fixture uses `status="rejected"`,
  so consider adding a `passed`-status variant rather than editing it.
- `test_plateau.py::test_portfolio_correlation_blocks_promotion` — asserts
  `"collision with submitted alpha"`; the colliding alpha is genuinely `submitted`,
  so A2's wording change keeps it green. **If this test goes red, A2 is wrong** —
  it means the `kind` branch picked the wrong label.
- `test_correlation.py`, `test_proxy_calibration.py` — closest to A1; read them
  before editing, since they may assert the current override behaviour.
- `test_ui.py`, `test_app.py` — surface and summary payload shapes (B2, C1, C2).
- `test_e2e_pipeline.py` — the only existing full-pipeline test; the likeliest place
  for an unexpected interaction with A3.

### 7.3 Reproduction harness

The rough draft referenced a `repro.py` that does not exist in the repo. Add
`backend/scripts/repro_review_findings.py` (dev-only, excluded from packaging) that
performs the exact sequence used to find these bugs, so before/after numbers are
reproducible by anyone:

1. `seed_all`, then `run_family --field liabilities --denominator cap --simulate 0`
   → 49 candidates.
2. `POST /api/alphas/{id}/results` for each, with a plateau centred on window 40–60,
   decay 4–8, and a full passing `checks` block.
3. Write 1300 days of independent PnL per alpha via `PnLStore.save_pnl` (seed the
   RNG per `alpha_id` so runs are comparable).
4. Print: blocked-by-collision count, the gating funnel, `counts` from
   `/api/ui/summary`, and cells-on-axes from `/api/ui/surfaces`.

Expected transition:

| Metric | Before | After |
|---|---|---|
| Blocked by structural collision | 49 / 49 | 0 / 49 |
| Promoted | 0 | ≥1 (one per structural hash) |
| Surface cells on rendered axes | 8 / 49 | 49 / 49 |
| Header counts | 49 alphas / 98 simulated | 49 / 49 |
| "sat on a plateau" | 0 (hardcoded) | 42 |

### 7.4 Manual verification

```bash
cd backend
.venv/bin/python -m pytest                     # expect 194 + new tests, all green
.venv/bin/python -m scripts.repro_review_findings
.venv/bin/python -m scripts.fetch_brain_catalog   # no creds: 2 stderr lines, exit 1
.venv/bin/python -m scripts.report                # empty DB, then seeded DB
.venv/bin/python -m uvicorn app.main:app --port 8123
```

In the browser: no console errors; header counts agree with `scripts.report`; the
heatmap is 7×7; the mini-map has 7 columns; each modal closes on a backdrop click;
the near-miss list shows `redundant with #` reasons.

---

## 8. Sequencing

Four PRs, each independently revertible. A is the only one that changes behaviour
users will notice.

| PR | Contents | Depends on | Risk |
|---|---|---|---|
| **1** | D (README, `_cli.py`, empty-catalog message) + C3 favicon | — | none — docs and error paths |
| **2** | B (axis derivation, API, report, mini-map CSS) | — | low — display only |
| **3** | **A** (A1–A5) + tests 1–8 | — | **high** — changes what gets promoted |
| **4** | C1, C2, C4 + tests 11, 12, 14 | PR 3 for `counts.plateau` wording | low |

Land PR 1 and 2 first: they are safe, and they make PR 3's effect *visible* (without
B, a reviewer cannot see the newly promoted alpha on the heatmap). PR 3 should be
reviewed against the §7.3 numbers, not just a green suite.

If PR 3 must be split further: A1+A2 (unblock) and A3+A5 (dedup) are separable, but
**do not ship A1+A2 alone** — that is the 42-near-duplicate morning described in D3.

---

## 9. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A1+A2 ship without A3 → shortlist floods with near-duplicates | medium | high — the morning queue is the product | Enforced by the PR boundary in §8; test 7 fails without A3 |
| Representative selection hides a genuinely better alpha | low | medium | Keeper is the max on `(sharpe, plateau_ratio)`; demoted twins remain in near-misses with an explicit reason and `redundant_with` pointer |
| Grouping by `structural_hash` over-merges | low | medium | Hash includes the window *bucket*, so fast/slow variants stay distinct — 4 groups over the 49-point family, not 1. Test 7 pins the count |
| Loosening the proxy lets a real duplicate through | low | high — a correlated submission wastes a BRAIN slot | Only measured-and-cleared alphas are exempted; measurement threshold `MIN_COMMON_TRADING_DAYS = 500` is unchanged; test 2 pins the unmeasured path |
| Widening `WINDOW_LADDER` flips existing tests into DSR mode | medium if constants are edited | medium | D4: do not edit values; derive axes instead |
| Throughput filter on `BRAIN_API` undercounts | low | low | Verified: `simulation_runner.py:140` writes `ImportSource.BRAIN_API.value`. Re-confirm if the runner is ever refactored |
| `counts.plateau` costs an extra `evaluate()` pass | low | low | Counted inside the existing loop (C2) — no additional calls |

---

## 10. Out of scope

Deliberately not addressed here, flagged for a later decision:

- **Whether `PASSED` belongs in the portfolio at all.** A `passed` alpha is not a
  position on BRAIN; blocking against it is a pipeline reservation, not a
  correlation fact. This plan keeps the current semantics and only makes the message
  honest (finding 2). Changing it is a product decision.
- **The `STANDARD` (49) vs `WIDE` (24) naming inversion** in `constructor.py:56-64`,
  and the README's "384 for wide grid" which matches neither. Confusing, harmless.
- **Serving a real favicon** rather than 204.
- **Making "Unresolved" a tab rather than a modal in the tab bar** — C4 fixes the
  inert-backdrop half; the information-architecture question stands.

---

## 11. Decisions needed before implementation starts

1. **Representative granularity (D3).** One promoted alpha per *structural hash*
   (≈4 per family, recommended) or per *family* (exactly 1)? This is the single
   choice that most changes how the morning queue feels, and it is yours, not mine.
2. **Throughput semantics (C1).** Should `BRAIN today` count only runner-sourced
   simulations (recommended — it is a budget meter for the 3-concurrent account cap)
   or every imported result?
3. **Fallback-constant rename (D4).** Rename with aliases as proposed, or leave
   `WINDOW_LADDER` named as-is to keep the diff smaller?

Everything else in this plan is determined by the findings and needs no input.
