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

---

# Part II: Implementation Plan — Closing the Search Gaps

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
