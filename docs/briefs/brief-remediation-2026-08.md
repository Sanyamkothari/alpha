# Remediation brief — quant review of 2026-08-21

**Audience:** an engineer or coding agent with no prior context on this repo.
**Source:** `docs/audits/quant-code-review-2026-08-21.md`. Read that first; this
brief is the executable half of it.

---

## 0. Orientation — read this before touching anything

### 0.1 What the system is

A local research engine that generates candidate trading alphas for the
WorldQuant BRAIN platform, filters them statistically, and shows a human a
shortlist. The human submits manually. It is ~6 weeks old, has 4,857 alphas in
the DB, 486 actually simulated, 6 submitted, 0 known accepted.

The project is in **Phase 1**, whose only goal is *40 submission attempts with
recorded outcomes* so the true pass rate can be estimated to ±15%. Simulations
are cheap and are **not** the metric.

### 0.2 Hard invariants — violating any of these fails review

1. **No code path may submit an alpha to BRAIN.** `tests/test_brain_no_post.py`
   enforces this structurally: any non-GET verb outside `app/services/brain/`,
   any raw-HTTP import outside that package, or any POST to a path outside
   `ALLOWED_POST_PATHS = {"/authentication", "/simulations"}` fails the build.
   Nothing in this brief needs a new network verb. If you find yourself adding
   one, stop.
2. **The LLM never writes expression syntax.** Deterministic AST constructors
   emit code. Nothing here touches the LLM path.
3. **The statistical filters are frozen during Phase 1** — plateau, DSR,
   subperiod, correlation. See §0.3, this is the subtle one.
4. **One source of truth per fact.** A two-week data-drift incident is the
   reason: local state was written with no platform verification, and three
   alphas were marked submitted that were not, while two real submissions went
   unrecorded. Consequences now baked in: `platform_outcome` is *derived* from
   `submission_attempts` and never set directly; `submission_attempts` records
   attempts including failures; snapshot tables are append-only.
5. **Migrations via Alembic only**, and test both `upgrade` and `downgrade`.
6. **Prove new constraints fire on data written by the production writer**, not
   by a test fixture. Where a test constructs an identifier, it must construct
   it with the same function production uses.

### 0.3 The freeze, and how to work inside it

CLAUDE.md says: *"The statistical filters are frozen during Phase 1 (plateau,
DSR, subperiod, correlation). They are themselves unvalidated and the current
phase exists partly to test them. Do not tune, improve, or 'fix' them."*

This brief therefore splits into two tiers, and **you must not move an item
between them without a human decision recorded in `docs/DECISIONS.md`**:

| Tier | Meaning | Ships in Phase 1? |
|---|---|---|
| **A** | The code does not do what its own docstring/constant says. Fixing it *restores* documented behaviour. | Yes |
| **B** | The methodology itself would change — different alphas would promote. | No. Implement, compute, record to `Verdict`/logs, **do not gate on it**, default flag OFF. |

Test for which tier something is in: *if I fix this, does a promotion decision
change for a reason the documentation did not already claim?* If yes → Tier B.

Two Tier-A items still change *which data gets collected* (W5, W6 — the budget
arms). Those need a `docs/DECISIONS.md` entry recording the changeover date, so
the Phase 2 analysis can segment campaigns before/after.

### 0.4 Environment

```bash
cd backend
python -m pip install -e ".[dev]"     # pytest is NOT installed by default in a fresh container
python -m pytest -q                    # target: ~194 tests, under ~5 seconds
python -m alembic upgrade head
python -m alembic downgrade -1 && python -m alembic upgrade head   # both directions
```

Current Alembic head is **`c3d4e5f6a1b2`** (`add_seed_to_campaigns`). Any new
migration sets `down_revision = "c3d4e5f6a1b2"` unless a prior work item in this
brief already added one — in which case chain onto that.

The revision-id convention in this repo is a hand-written 12-hex-char string,
not the Alembic default. Follow it.

### 0.5 Working style expected here

- **Run queries; do not infer from code.** If you report a number, show the query.
- **Report absences as absences.** `NOT PRESENT` and `CANNOT DETERMINE` are
  acceptable answers. Inventing a plausible number is not.
- Distinguish *code exists* from *code runs* from *code has been used*.
- If a task turns out larger than described, or the design looks wrong, **stop
  and report** rather than improvising.

### 0.6 Commit discipline

One work item = one commit. Commit message format:

```
fix(<area>): <what changed>

<why, referencing the audit item number>

Tier: A|B
Behaviour change: none | promotion decisions unchanged | data-collection mix changes (see DECISIONS.md)
```

---

## 1. Work items

Dependency order. W1 gates the value of W3/W4; W8 is independent; W5/W6/W7 are
independent of everything else.

```
W1 (PnL convention) ──┬── W3 (fail-closed correlation)
                      └── W4 (N_eff, Tier B)
W2 (unit bug)          independent
W5 (arm arithmetic) ── W6 (quartiles) ── W7 (plateau_fill territory)
W8 (SSOT) ───────────── W8b (migration + guard test)
W9 (backfill mismatch) independent
W10 (hygiene)          last
```

---

## W1 — Establish the daily-PnL convention and normalize at ingest  **[Tier A]**

### Why

This is the highest-value item in the brief. Every statistic in the system
assumes `PnLStore` holds **daily increments**. Nothing verifies it.

`scripts/backfill_pnl.py:71-74` and `app/services/correlation.py:164-169` both
store BRAIN's `/alphas/{id}/recordsets/daily-pnl` payload verbatim:

```python
records = pnl_resp.get("records", [])
if records:
    dates = [str(r[0]) for r in records]
    pnl = np.array([float(r[1]) for r in records], dtype=float)
    store.save_pnl(local_alpha.id, dates, pnl)
```

Downstream, `subperiod.compute_dsr` takes `mean/std` of that array,
`evaluate_subperiod_stability` computes split-half and rolling Sharpes from it,
and `correlation.compute_pairwise_correlation` runs `np.corrcoef` on it.

`scripts/verify_pnl_reconciliation.py:3-5` states the question is **still open**
in its own docstring: *"Empirically tests whether /alphas/{id}/recordsets/daily-pnl
returns discrete daily dollar PnL series (non-cumulative) vs cumulative PnL curves."*

If the endpoint returns a cumulative curve, then Sharpes are computed on a
trending level series (meaningless), and — worse — the correlation gate becomes
a spurious regression: two unrelated alphas both drifting upward correlate near
1.0, so a 0.55 threshold either rejects everything or passes things it must not.

### The approach: decide empirically, do not guess

The DB already contains the answer. `AlphaMetric.sharpe` is BRAIN's own reported
Sharpe for the same alpha. Compute the annualized Sharpe **both ways** and see
which matches. That is a measurement, not a heuristic.

### W1.1 — Add the convention detector

New file `backend/app/services/pnl_convention.py`:

```python
"""Determine, empirically, whether BRAIN's daily-pnl recordset is a series of
daily increments or a cumulative curve — and normalize to daily increments.

Nothing in this repo may assume the answer. The recordset is compared against
the alpha's own BRAIN-reported Sharpe (``alpha_metrics.sharpe``); whichever
interpretation reconciles is the one that is true. If neither reconciles, the
series is UNUSABLE and must not reach a statistical gate — see docs/audits/
quant-code-review-2026-08-21.md §1.1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import structlog

log = structlog.get_logger("pnl_convention")

TRADING_DAYS = 252
# Reconciliation tolerance on annualized Sharpe. 0.05 is the tolerance already
# used by subperiod.verify_pnl_reconciliation; keep the two in step.
SHARPE_TOLERANCE = 0.05

Convention = Literal["daily", "cumulative", "indeterminate"]


@dataclass(frozen=True)
class ConventionVerdict:
    convention: Convention
    sharpe_as_daily: float
    sharpe_as_cumulative: float
    reported_sharpe: float
    n_observations: int

    @property
    def is_usable(self) -> bool:
        return self.convention in ("daily", "cumulative")


def _annualized_sharpe(arr: np.ndarray) -> float:
    if len(arr) < 2:
        return 0.0
    std = float(np.std(arr, ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(np.mean(arr)) / std * math.sqrt(TRADING_DAYS)


def to_daily(arr: np.ndarray, convention: Convention) -> np.ndarray:
    """Normalize a stored series to daily increments.

    ``prepend=0.0`` keeps ``len(out) == len(arr)`` so the caller's date list
    stays aligned. It assumes the cumulative curve starts from zero at
    inception, which is what a PnL curve does.
    """
    if convention == "cumulative":
        return np.diff(np.asarray(arr, dtype=np.float64), prepend=0.0)
    return np.asarray(arr, dtype=np.float64)


def detect(raw: np.ndarray, reported_sharpe: float) -> ConventionVerdict:
    """Compare both interpretations against the platform's own Sharpe."""
    arr = np.asarray(raw, dtype=np.float64)
    sr_daily = _annualized_sharpe(arr)
    sr_cum = _annualized_sharpe(to_daily(arr, "cumulative"))

    d_daily = abs(sr_daily - reported_sharpe)
    d_cum = abs(sr_cum - reported_sharpe)

    ok_daily = d_daily <= SHARPE_TOLERANCE
    ok_cum = d_cum <= SHARPE_TOLERANCE

    if ok_daily and not ok_cum:
        conv: Convention = "daily"
    elif ok_cum and not ok_daily:
        conv = "cumulative"
    elif ok_daily and ok_cum:
        # Degenerate: both reconcile. Pick the closer, but log it — this should
        # not happen with a real series and means the tolerance is too loose.
        log.warning("pnl_convention_ambiguous", d_daily=d_daily, d_cum=d_cum)
        conv = "daily" if d_daily <= d_cum else "cumulative"
    else:
        conv = "indeterminate"

    return ConventionVerdict(
        convention=conv,
        sharpe_as_daily=sr_daily,
        sharpe_as_cumulative=sr_cum,
        reported_sharpe=reported_sharpe,
        n_observations=len(arr),
    )
```

### W1.2 — Normalize at the two ingest boundaries, not downstream

**Do not** normalize inside `PnLStore.load_pnl` or inside the gates. The store
must hold one convention — daily — and every reader can then be left alone.

`backend/scripts/backfill_pnl.py`, replace the save block (currently lines
69-80):

```python
pnl_resp = brain.get_json(f"/alphas/{r_id}/recordsets/daily-pnl")
records = pnl_resp.get("records", [])
if not records:
    stats["failed"] += 1
    continue

dates = [str(r[0]) for r in records]
raw = np.array([float(r[1]) for r in records], dtype=float)

reported = (ra.get("is") or {}).get("sharpe")
if reported is None:
    log.warning("pnl_no_reported_sharpe", alpha_id=local_alpha.id, remote_id=r_id)
    stats["unreconciled"] += 1
    continue

verdict = detect(raw, float(reported))
if not verdict.is_usable:
    log.warning(
        "pnl_convention_indeterminate",
        alpha_id=local_alpha.id,
        remote_id=r_id,
        reported=verdict.reported_sharpe,
        as_daily=verdict.sharpe_as_daily,
        as_cumulative=verdict.sharpe_as_cumulative,
    )
    stats["unreconciled"] += 1
    continue

store.save_pnl(
    local_alpha.id,
    dates,
    to_daily(raw, verdict.convention),
    convention=verdict.convention,
    reported_sharpe=float(reported),
)
stats["saved"] += 1
stats[f"convention_{verdict.convention}"] = stats.get(f"convention_{verdict.convention}", 0) + 1
```

Apply the same shape to `app/services/correlation.py:ensure_alpha_pnl` (lines
160-171). It has no `ra["is"]["sharpe"]` in scope — fetch the local
`AlphaMetric.sharpe` instead:

```python
metric = db.execute(
    select(AlphaMetric).where(AlphaMetric.alpha_id == alpha_id)
    .order_by(AlphaMetric.id.desc())
).scalars().first()
if metric is None or metric.sharpe is None:
    log.warning("pnl_fetch_no_local_sharpe", alpha_id=alpha_id)
    return False
verdict = detect(raw, float(metric.sharpe))
if not verdict.is_usable:
    log.warning("pnl_convention_indeterminate", alpha_id=alpha_id, ...)
    return False
store.save_pnl(alpha_id, dates, to_daily(raw, verdict.convention),
               convention=verdict.convention, reported_sharpe=float(metric.sharpe))
return True
```

### W1.3 — Record provenance in the store

`app/services/pnl_storage.py`. Extend `save_pnl` and add a metadata read. Keep
the `.npy` format; add a sidecar so an existing store is not invalidated.

```python
def save_pnl(
    self,
    alpha_id: int,
    dates: list[str],
    pnl_values: list[float] | np.ndarray,
    *,
    convention: str = "daily",
    reported_sharpe: float | None = None,
) -> None:
    """Save a series of DAILY INCREMENTS for an alpha.

    ``convention`` records what the raw platform payload was before
    normalization — it is provenance, not an instruction. The array written
    here is always daily. Callers normalize with
    ``pnl_convention.to_daily`` before calling.
    """
    arr = np.asarray(pnl_values, dtype=np.float64)
    if len(dates) != len(arr):
        raise ValueError(
            f"date/value length mismatch for alpha {alpha_id}: "
            f"{len(dates)} dates vs {len(arr)} values"
        )
    meta = {
        "convention_at_source": convention,
        "reported_sharpe": reported_sharpe,
        "n": int(len(arr)),
    }
    with self._lock:
        self._cache[alpha_id] = (dates, arr)
        npy_path = self._dir / f"{alpha_id}.npy"
        json_path = self._dir / f"{alpha_id}_dates.json"
        meta_path = self._dir / f"{alpha_id}_meta.json"
        try:
            np.save(npy_path, arr)
            json_path.write_text(json.dumps(dates), encoding="utf-8")
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        except Exception as exc:
            # A failed write must not leave a populated cache claiming success.
            self._cache.pop(alpha_id, None)
            log.warning("pnl_save_failed", alpha_id=alpha_id, error=str(exc))
            raise
```

Note two behaviour changes in that snippet, both deliberate and both worth
calling out in the commit message: the length-mismatch guard, and **the cache is
now evicted and the exception re-raised on a failed write**. Previously a failed
write logged and returned, leaving the in-memory cache populated — so within one
process the PnL looked saved, and after restart it was gone.

Add:

```python
def load_meta(self, alpha_id: int) -> dict | None:
    """Provenance for a stored series, or None for pre-migration series."""
    path = self._dir / f"{alpha_id}_meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("pnl_meta_load_failed", alpha_id=alpha_id, error=str(exc))
        return None
```

### W1.4 — Fix the crash in the reconciliation script

`scripts/verify_pnl_reconciliation.py:60-64` divides by `len(reported)` and runs
`stats.linregress` on possibly-empty arrays. The guard at line 36 only covers
"no `.npy` files at all". Insert after the collection loop:

```python
if not reported:
    print(
        f"CANNOT DETERMINE: {len(alpha_ids)} stored PnL files, "
        f"but none has a matching alpha_metrics row with a Sharpe. "
        f"Nothing to reconcile."
    )
    return {"total_pnl_files": len(alpha_ids), "reconciled": 0, "pass_rate": 0.0}
if len(reported) < 3:
    print(f"CANNOT DETERMINE: only {len(reported)} reconcilable alphas; regression needs >= 3.")
    # still print the per-alpha diffs, skip the regression
```

Then extend the script to print the convention split, which is the actual
deliverable of this work item:

```python
from app.services.pnl_convention import detect
...
conventions: dict[str, int] = {}
for aid in sorted(alpha_ids):
    ...
    loaded = store.load_pnl(aid)
    if loaded and metric and metric.sharpe is not None:
        v = detect(loaded[1], metric.sharpe)
        conventions[v.convention] = conventions.get(v.convention, 0) + 1
print(f"Convention split across stored series: {conventions}")
```

### W1.5 — Backfill the existing store

Existing `.npy` files were written before any of this and may be cumulative.
Write `backend/scripts/renormalize_pnl.py`:

- iterate `store._dir.glob("*.npy")` — **add a public `iter_alpha_ids()` to
  `PnLStore` and use that instead**; reaching into `_dir` from a script is
  already flagged in the audit (§5)
- skip any alpha that already has a `_meta.json` (already normalized)
- for each, load the local `AlphaMetric.sharpe`, run `detect`, and rewrite via
  `save_pnl(..., convention=...)`
- support `--dry-run` (default true) printing the convention split and the
  count that would change, and `--apply` to write
- **back up first**: copy `database/pnl/` to `database/pnl.backup-<date>/`
  (`.gitignore` already excludes `database/pnl.backup-*/`)

### W1.6 — Tests

New `backend/tests/test_pnl_convention.py`:

```python
def test_detects_cumulative_series() -> None:
    rng = np.random.default_rng(7)
    daily = rng.normal(0.0008, 0.01, 1260)
    reported = float(np.mean(daily) / np.std(daily, ddof=1) * math.sqrt(252))
    cumulative = np.cumsum(daily)

    v = detect(cumulative, reported)
    assert v.convention == "cumulative"
    assert np.allclose(to_daily(cumulative, "cumulative"), daily)


def test_detects_daily_series() -> None:
    ...same, passing `daily` itself; assert convention == "daily"


def test_indeterminate_when_neither_reconciles() -> None:
    v = detect(np.arange(500, dtype=float), reported_sharpe=99.0)
    assert v.convention == "indeterminate"
    assert not v.is_usable


def test_to_daily_preserves_length() -> None:
    """Date alignment in the correlation gate depends on this."""
    arr = np.cumsum(np.ones(100))
    assert len(to_daily(arr, "cumulative")) == 100


def test_save_pnl_rejects_length_mismatch(tmp_path) -> None:
    store = PnLStore(tmp_path)
    with pytest.raises(ValueError):
        store.save_pnl(1, ["2020-01-01"], np.array([1.0, 2.0]))
```

**Per invariant 6**, add one test that goes through the *production writer*:
call `correlation.ensure_alpha_pnl` against `tests/fakes/fake_brain_client.py`
(extend the fake at line 76 to serve a cumulative curve) and assert the stored
array is daily and `load_meta()["convention_at_source"] == "cumulative"`.

### W1.7 — Definition of done

- [ ] `python -m scripts.verify_pnl_reconciliation` runs against the real store
      and prints a convention split with no crash
- [ ] The answer is written into `docs/BRAIN_API.md` under its VERIFIED section,
      with the date and the sample size
- [ ] `scripts/verify_pnl_reconciliation.py`'s docstring no longer says
      "empirically tests whether" — it says which it is
- [ ] The audit's §1.1 is struck through with the finding

---

## W2 — A probability is being used as a Sharpe bar  **[Tier A]**

### Why

`app/services/plateau.py:350-356`, the `require_pnl=False` branch:

```python
subperiod_passed = True
dsr_passed = bool(
    point.sharpe is not None
    and point.sharpe >= (DSR_PROMOTION_THRESHOLD if use_dsr else COLD_START_SHARPE_BAR)
    and (point.fitness is None or point.fitness >= 1.0)
)
```

`DSR_PROMOTION_THRESHOLD = 0.95` is a **probability** (the DSR hurdle, from
`plateau.py:53`). `COLD_START_SHARPE_BAR = 1.50` is an **annualized Sharpe**.
They are being selected between and compared against `point.sharpe`.

Two consequences:

1. The bar becomes "Sharpe ≥ 0.95" — below `BASE_SHARPE_BAR = 1.25`, which the
   same function applied twelve lines earlier at `above_bar`.
2. **The direction is inverted.** `use_dsr` is true when
   `max_slice_trials >= MIN_TRIALS_FOR_DSR` (30) — i.e. when the family is large
   and multiple-testing risk is *highest*. That is exactly when this line makes
   the bar *easier*.

No docstring anywhere claims a 0.95 Sharpe hurdle. This is a typo-class defect,
not a threshold choice — hence Tier A.

### The fix

Add a named constant next to the others at `plateau.py:50-56`:

```python
# When no daily PnL series exists, DSR cannot be computed at all. The fallback
# is a Sharpe hurdle, and it must never be *looser* than the family's own
# multiple-testing haircut — a larger family means more trials, not an easier
# bar. Distinct constant so it can never again be confused with the DSR
# probability thresholds above.
NO_PNL_SHARPE_BAR = COLD_START_SHARPE_BAR
```

Replace the branch:

```python
else:
    # No PnL series and the caller opted out of requiring one. DSR is not
    # computable; fall back to a Sharpe/Fitness hurdle that is never looser
    # than the haircut bar this family already has to clear.
    subperiod_passed = True
    fallback_bar = max(NO_PNL_SHARPE_BAR, bar)
    dsr_passed = bool(
        point.sharpe is not None
        and point.sharpe >= fallback_bar
        and (point.fitness is None or point.fitness >= 1.0)
    )
    if not dsr_passed:
        reasons.append(f"no-PnL fallback: Sharpe below {fallback_bar:.2f}")
```

Also set `gate_mode` honestly. Today it is `"DSR"` or `"COLD_START_FALLBACK"`
computed once for the whole family at `plateau.py:283`, but a point with no PnL
was never in DSR mode regardless. Make it per-point:

```python
point_gate_mode = gate_mode if pnl_data is not None else "NO_PNL_FALLBACK"
```
and pass `gate_mode=point_gate_mode` into the `Verdict` at the construction site.

### Tests

Add to `backend/tests/test_plateau.py`:

```python
def test_no_pnl_fallback_bar_never_below_haircut(db_session) -> None:
    """A larger family must not get an easier bar. Regression for the
    0.95-probability-as-Sharpe defect (audit §1.2)."""
    # Build a family of >= MIN_TRIALS_FOR_DSR points on one structural slice,
    # all with sharpe between 0.95 and 1.50, none with stored PnL.
    ...
    verdicts = evaluate(db_session, family_key, require_pnl=False)
    assert not any(v.promoted for v in verdicts)
    assert all(v.gate_mode == "NO_PNL_FALLBACK" for v in verdicts)


def test_larger_family_bar_is_monotone_non_decreasing() -> None:
    """Property: the effective bar must never decrease as trials increase."""
    bars = [haircut_bar(n) for n in (1, 10, 30, 100, 1000)]
    assert bars == sorted(bars)
```

### Definition of done

- [ ] `DSR_PROMOTION_THRESHOLD` appears **only** in comparisons against a `dsr`
      value. Verify: `grep -n "DSR_PROMOTION_THRESHOLD\|DSR_RE_PROMOTION_THRESHOLD" app/services/plateau.py`
      and check every hit is compared to `dsr_val`, never to `sharpe`.
- [ ] Existing tests still pass with no threshold edits.

---

## W3 — Unmeasured correlation must fail closed  **[Tier A]**

### Why

`app/services/correlation.py:40-45`:

```python
def compute_pairwise_correlation(arr1: np.ndarray, arr2: np.ndarray) -> float:
    if len(arr1) != len(arr2) or len(arr1) < 10:
        return 0.0
```

and `check_portfolio_empirical_correlation:95-99`:

```python
common_dates = sorted(set(cand_dates).intersection(port_dates))
if len(common_dates) < min_overlap:
    continue
```

`MIN_COMMON_TRADING_DAYS = 500`. If no pair clears that, the function returns
`max_corr = 0.0` — **indistinguishable from "measured, and genuinely
uncorrelated"**. The candidate then sails through the gate.

The codebase already knows this is wrong. `compute_max_self_correlation_with_submitted`
(same file, lines 175-238) exists specifically to return `None` for unmeasured,
and its docstring promises it *"never fabricates synthetic 0.20 or 0.85"*. But
the function that actually gates promotion — called from `plateau.py:361` — is
the other one. Two functions compute the same quantity with opposite
missing-data semantics, and the gate got the unsafe one.

BRAIN rejects submissions correlating > 0.70 with the user's own alphas, and per
CLAUDE.md the practical yield is roughly one submittable alpha per field. A
false negative here costs a submission slot out of a 40-attempt budget.

### The fix

Return a verdict object rather than a tuple whose third element is ambiguous.
In `app/services/correlation.py`:

```python
@dataclass(frozen=True)
class CorrelationVerdict:
    """Outcome of the portfolio correlation gate.

    ``blocking`` is what the caller gates on. It is True both when a real
    collision was measured AND when the correlation could not be measured at
    all against a non-empty portfolio — an unmeasured constraint must fail
    closed, not open. ``max_correlation`` is None when nothing was measured;
    it is never 0.0-as-a-stand-in.
    """
    blocking: bool
    reason: str | None
    max_correlation: float | None
    method: str  # 'empirical' | 'structural_proxy' | 'unmeasured' | 'none'
    measured_pairs: int
    skipped_pairs: int
    portfolio_size: int
```

Rewrite `check_portfolio_empirical_correlation` to return it. Core logic:

```python
measured = 0
skipped = 0
max_corr: float | None = None
colliding_alpha_id: int | None = None

if cand_pnl_data is not None:
    for port_alpha in portfolio:
        if port_alpha.id == alpha_id:
            continue
        port_pnl_data = store.load_pnl(port_alpha.id)
        if port_pnl_data is None:
            skipped += 1
            continue
        common_dates = sorted(set(cand_dates).intersection(port_dates))
        if len(common_dates) < min_overlap:
            skipped += 1
            continue
        ...
        rho = abs(compute_pairwise_correlation(c_vec, p_vec))
        measured += 1
        if max_corr is None or rho > max_corr:
            max_corr = rho
            colliding_alpha_id = port_alpha.id
else:
    skipped = len(portfolio)

# 1. A measured collision blocks.
if max_corr is not None and max_corr >= threshold:
    return CorrelationVerdict(
        blocking=True,
        reason=(f"empirical correlation {max_corr:.2f} with portfolio alpha "
                f"#{colliding_alpha_id} exceeds threshold {threshold:.2f}"),
        max_correlation=max_corr, method="empirical",
        measured_pairs=measured, skipped_pairs=skipped,
        portfolio_size=len(portfolio),
    )

# 2. The structural proxy is a *supplement*, not a substitute.
is_struct, struct_reason = check_structural_proxy(db, alpha_id, portfolio=portfolio)
if is_struct:
    return CorrelationVerdict(
        blocking=True, reason=struct_reason, max_correlation=max_corr,
        method="structural_proxy", measured_pairs=measured,
        skipped_pairs=skipped, portfolio_size=len(portfolio),
    )

# 3. Nothing measured against a non-empty portfolio => FAIL CLOSED.
if portfolio and measured == 0:
    return CorrelationVerdict(
        blocking=not allow_unmeasured,
        reason=(f"correlation UNMEASURED against {len(portfolio)} portfolio alphas "
                f"({skipped} pairs lacked {min_overlap}+ common trading days). "
                f"Blocking: an unverified correlation constraint is not a passed one."),
        max_correlation=None, method="unmeasured",
        measured_pairs=0, skipped_pairs=skipped, portfolio_size=len(portfolio),
    )

# 4. Genuinely measured and genuinely clear.
return CorrelationVerdict(
    blocking=False, reason=None, max_correlation=max_corr,
    method="empirical" if measured else "none",
    measured_pairs=measured, skipped_pairs=skipped, portfolio_size=len(portfolio),
)
```

Signature gains one keyword:

```python
def check_portfolio_empirical_correlation(
    db: Session,
    alpha_id: int,
    *,
    pnl_store: PnLStore | None = None,
    portfolio: list[Alpha] | None = None,
    threshold: float = INTERNAL_CORRELATION_THRESHOLD,
    min_overlap: int = MIN_COMMON_TRADING_DAYS,
    allow_unmeasured: bool = False,
) -> CorrelationVerdict:
```

`allow_unmeasured=True` is the escape hatch for backfill/reporting paths that
want the number without gating. **It must never be passed from
`plateau.evaluate`.** Grep for it in review.

### Caller update

`app/services/plateau.py:360-366`:

```python
corr = check_portfolio_empirical_correlation(
    db, point.alpha_id, pnl_store=pnl_store, portfolio=portfolio
)
if corr.blocking and corr.reason:
    reasons.append(corr.reason)
...
survives = clears and is_plateau and above_bar and dsr_passed and subperiod_passed and not corr.blocking
```

Extend `Verdict` (`plateau.py:73-95`) so the reason is auditable rather than
just a string in a list:

```python
max_correlation: float | None = None
correlation_method: str = "none"
```

Then collapse the duplication: `compute_max_self_correlation_with_submitted`
(lines 175-238) is now a thin wrapper over the same engine —

```python
def compute_max_self_correlation_with_submitted(...) -> tuple[float | None, int | None, str]:
    """Kept for the reporting/UI path. Delegates to the single implementation."""
    v = check_portfolio_empirical_correlation(..., allow_unmeasured=True)
    return v.max_correlation, colliding_id, v.method
```

Having one implementation is the point of this item; two functions with opposite
missing-data semantics is how the bug happened.

### Expected impact — measure it before you ship

This will block alphas that currently pass. **Quantify it first:**

```bash
cd backend && python - <<'PY'
from app.db import session_scope
from app.services.correlation import submitted_portfolio, check_portfolio_empirical_correlation
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from sqlalchemy import select
with session_scope() as db:
    portfolio = submitted_portfolio(db)
    cands = db.execute(select(Alpha.id).where(Alpha.status == AlphaStatus.PASSED.value)).scalars().all()
    print(f"portfolio={len(portfolio)} candidates={len(cands)}")
    from collections import Counter
    c = Counter()
    for aid in cands:
        v = check_portfolio_empirical_correlation(db, aid, portfolio=portfolio, allow_unmeasured=True)
        c[v.method] += 1
    print(c)
PY
```

If `unmeasured` dominates, W1 has not been run or the backfill is incomplete —
**fix that before shipping W3**, or the gate blocks everything. This is why W1
comes first.

### Tests

```python
def test_unmeasured_correlation_blocks(db_session) -> None:
    """Audit §1.5: a constraint that could not be evaluated is not a passed one."""
    # portfolio alpha WITH stored pnl, candidate WITHOUT
    v = check_portfolio_empirical_correlation(db_session, cand.id, portfolio=[port])
    assert v.blocking
    assert v.method == "unmeasured"
    assert v.max_correlation is None      # never 0.0-as-a-stand-in


def test_insufficient_overlap_blocks(db_session) -> None:
    """499 common days is not 'uncorrelated', it is 'unknown'."""
    # both have pnl, but only 499 overlapping dates vs MIN_COMMON_TRADING_DAYS=500
    v = check_portfolio_empirical_correlation(db_session, cand.id, portfolio=[port])
    assert v.blocking and v.skipped_pairs == 1


def test_empty_portfolio_does_not_block(db_session) -> None:
    """Nothing to collide with is a real pass, not an unmeasured one."""
    v = check_portfolio_empirical_correlation(db_session, cand.id, portfolio=[])
    assert not v.blocking and v.method == "none"


def test_allow_unmeasured_escape_hatch_is_not_used_by_the_gate() -> None:
    """Structural: plateau.evaluate must never pass allow_unmeasured."""
    src = (Path(__file__).parents[1] / "app/services/plateau.py").read_text()
    assert "allow_unmeasured" not in src
```

That last test is the CLAUDE.md invariant-6 pattern: a structural check that
cannot rot.

---

## W4 — Effective trials and the trial universe  **[Tier B — DO NOT GATE]**

### Why

`plateau.py:326` passes `family_sharpes` — the Sharpes of one `family_key` — as
the trial population, and `subperiod.compute_dsr:90-92` uses
`len(sharpes_clean)` as the trial count:

```python
n_trials = n_eff if (n_eff is not None and n_eff >= 1.0) else float(max(1, len(sharpes_clean)))
sigma_sr = float(np.std(sharpes_clean, ddof=1)) if len(sharpes_clean) > 1 else 0.0
```

Two problems. First, the DB holds 4,857 alphas / 486 simulations; the deflation
sees only the current family. Second, the trials it *does* count are the most
correlated ones available — neighbouring `(window, decay)` points on one smooth
surface — so `sigma_sr` is small, so `sr_star` is small, so DSR is inflated.

`compute_effective_trials` (`subperiod.py:41-64`) implements the eigenvalue
participation ratio that addresses exactly this, and **is never called**. `n_eff`
is `None` at every production call site.

### Why this is Tier B and not Tier A

Note the direction carefully, because it is counterintuitive:

- `N_eff < M` always. Substituting `N_eff` for `M` **lowers** the trial count,
  which **lowers** `sr_star`, which **raises** DSR — i.e. makes the gate
  *easier*. Wiring `compute_effective_trials` in naively makes the filter more
  permissive, not less.
- The under-deflation actually comes from the **trial universe** (one family
  instead of the whole research programme) and from `sigma_sr` being measured on
  a tightly-clustered family.

A correct treatment expands the universe *and* deflates it for dependence:

```
n_trials = max(n_eff_family, total_simulated_alphas * (n_eff_family / m_family))
sigma_sr = std of the GLOBAL simulated-Sharpe distribution, not the family's
```

That changes which alphas promote. **It is a methodology change and the filters
are frozen.** Implement it, compute it, record it, do not gate on it.

### The fix

Add to `plateau.evaluate`, computed once per family before the point loop:

```python
# --- Tier B: recorded, NOT gated. See docs/briefs/brief-remediation-2026-08.md W4.
# Phase 1 freezes the filters; this is measured now so Phase 2 can decide
# whether the shipped DSR is over-permissive, using real data rather than an
# argument.
n_eff_family: float | None = None
family_alpha_ids = [p.alpha_id for p in surface if p.sharpe is not None]
if len(family_alpha_ids) >= 2:
    ids, _dates, matrix = pnl_store.get_aligned_matrix(family_alpha_ids)
    if matrix.size:
        corr_m = compute_correlation_matrix(matrix)
        if corr_m.size:
            n_eff_family = compute_effective_trials(corr_m)
            log.info(
                "family_effective_trials",
                family=family_key,
                m=len(ids),
                n_eff=round(n_eff_family, 2),
                independence_ratio=round(n_eff_family / len(ids), 3),
            )
```

Then, per point, compute a **shadow** DSR alongside the gating one:

```python
dsr_val = compute_dsr(daily_pnl, daily_sharpes)                  # GATES (unchanged)
dsr_shadow = compute_dsr(daily_pnl, global_daily_sharpes,        # RECORDED ONLY
                         n_eff=shadow_trials) if shadow_trials else None
```

Add to `Verdict`:

```python
n_eff_family: float | None = None
dsr_global_shadow: float | None = None
shadow_trials: float | None = None
```

**`survives` must not reference any of these three fields.** Add the structural
guard test:

```python
def test_shadow_dsr_is_not_gated() -> None:
    """Phase 1 freeze: the shadow statistic must never reach the survives expression."""
    src = (Path(__file__).parents[1] / "app/services/plateau.py").read_text()
    survives_line = next(l for l in src.splitlines() if l.strip().startswith("survives ="))
    for banned in ("dsr_shadow", "dsr_global_shadow", "n_eff", "shadow_trials"):
        assert banned not in survives_line
```

Also fix the internal inconsistency in `compute_effective_trials` while you are
there (`subperiod.py:56-61`): eigenvalues are clipped at 0 but the numerator
still uses `m**2` rather than `(sum of clipped eigenvalues)**2`. For a valid
correlation matrix the two agree; after clipping they do not.

```python
eigenvals = np.clip(eigenvals, a_min=0.0, a_max=None)
total = float(np.sum(eigenvals))
sum_sq = float(np.sum(eigenvals**2))
if sum_sq > 0 and total > 0:
    n_eff = float((total**2) / sum_sq)
    return max(1.0, min(float(m), n_eff))
```

### Deliverable

A short note in `docs/audits/` reporting, over the current DB: the distribution
of `n_eff/m` per family, and how many currently-promoted alphas would fail the
shadow DSR. That number is the input to a Phase 2 decision, and it is the whole
point of the item.

---

## W5 — Budget arm arithmetic  **[Tier A, but changes the data mix — needs a DECISIONS.md entry]**

### Why

`app/services/allocator.py:877-879` reports:

```python
exploit_simulations=declared_exploit,
random_stratified_simulations=declared_random,
plateau_fill_simulations=declared_plateau,
```

computed at 667-669 from the declared shares. The tasks actually built use
different quantities — `exploit_budget`/`random_budget` at 683-685 — further
quantized by `budget // sims_per_territory` at 694 and 762.

With the defaults (`total_simulations=200`, `sims_per_territory=49`):

| Arm | Declared | Territories built | Actual sims |
|---|---|---|---|
| exploit | 100 (50%) | `max(1, 100//49)` = 2 | 98 |
| random_stratified | 60 (30%) | `max(1, 60//49)` = **1** | 49 |
| plateau_fill | 40 (20%) | loop at 815-830 ends in an unconditional `break` → **1** | 49 |

Total 196; the closure step at 861-864 spreads the remaining 4 round-robin
across arms *regardless of arm*. Realized split ≈ **50 / 25 / 25**, reported as
50 / 30 / 20.

Worse, the entire random-stratified block is guarded at line 737 by:

```python
if all_fields and total_simulations >= (2 * sims_per_territory):
```

**Any campaign under 98 simulations produces zero calibration tasks, silently.**

CLAUDE.md: *"Do not disable or 'improve' the random stratified arm (30% of
budget). It deliberately samples crowded, unpromising territory. Its scientific
value comes precisely from being unbiased — it is what makes the Phase 2
validation study possible."* Underfunding it by integer division is disabling it
by accident.

### The fix

**1. Apportion by largest remainder, per arm, against the declared shares.**

```python
def _apportion(total: int, shares: dict[str, float]) -> dict[str, int]:
    """Largest-remainder apportionment. Sums exactly to ``total``.

    Integer division alone systematically underfunds the smallest arm, which
    here is the calibration arm the Phase 2 study depends on.
    """
    raw = {k: total * v for k, v in shares.items()}
    floors = {k: int(math.floor(x)) for k, x in raw.items()}
    remainder = total - sum(floors.values())
    order = sorted(raw, key=lambda k: (raw[k] - floors[k]), reverse=True)
    for i in range(remainder):
        floors[order[i % len(order)]] += 1
    return floors
```

**2. Never let a task target exceed its arm's budget, and never silently drop an
arm.** Replace `n_rand_territories = max(1, random_budget // sims_per_territory)`
with a helper that also returns the leftover, so a partial territory still gets
simulated rather than rounded away:

```python
def _territories_for(budget: int, per_territory: int) -> list[int]:
    """Split an arm budget into per-territory targets summing exactly to budget.

    A remainder smaller than a full surface becomes a final partial territory
    rather than being discarded — that remainder is what used to vanish.
    """
    if budget <= 0:
        return []
    full, rest = divmod(budget, per_territory)
    out = [per_territory] * full
    if rest:
        out.append(rest)
    return out or [budget]
```

**3. Delete the `total_simulations >= (2 * sims_per_territory)` guard at line 737.**
A 40-sim campaign should produce a 12-sim calibration task, not none.

**4. Report realized, not declared.** At the return (877-879):

```python
realized = {"exploit": 0, "random_stratified": 0, "plateau_fill": 0}
for t in tasks:
    realized[t.arm] = realized.get(t.arm, 0) + t.target_simulations

if realized["random_stratified"] == 0 and total_simulations > 0:
    log.error(
        "calibration_arm_empty",
        total=total_simulations,
        reason="random_stratified produced no tasks — Phase 2 validation study depends on this arm",
    )

return BudgetPlan(
    total_simulations=total_simulations,
    exploit_simulations=realized["exploit"],
    random_stratified_simulations=realized["random_stratified"],
    plateau_fill_simulations=realized["plateau_fill"],
    ...
)
```

**5. Make the closure step arm-aware.** The round-robin at 861-870 must add or
remove within the arm that owns the drift, not across arms. If `_apportion` and
`_territories_for` are used correctly the remainder is zero by construction —
so replace the whole closure block with an assertion:

```python
current_total = sum(t.target_simulations for t in tasks)
if current_total != total_simulations:
    raise AssertionError(
        f"budget closure violated: tasks sum to {current_total}, expected {total_simulations}. "
        f"Per-arm: {realized}"
    )
```

An assertion is correct here: silent rebalancing is what hid the defect.

### Tests

Extend `backend/tests/test_allocator.py`:

```python
@pytest.mark.parametrize("total", [10, 40, 49, 50, 98, 100, 137, 200, 501])
def test_arm_shares_are_within_one_sim_of_declared(db_session, total) -> None:
    """Audit §2.1: the calibration arm must actually receive its 30%."""
    plan = plan_budget_allocation(db_session, total_simulations=total, seed=11)
    assert sum(t.target_simulations for t in plan.tasks) == total
    if total > 0:
        assert plan.random_stratified_simulations >= 1, "calibration arm was dropped"
        assert abs(plan.random_stratified_simulations - 0.30 * total) <= 1


def test_reported_split_matches_tasks(db_session) -> None:
    """BudgetPlan must report what was built, not what was intended."""
    plan = plan_budget_allocation(db_session, total_simulations=200, seed=3)
    by_arm: dict[str, int] = {}
    for t in plan.tasks:
        by_arm[t.arm] = by_arm.get(t.arm, 0) + t.target_simulations
    assert by_arm.get("exploit", 0) == plan.exploit_simulations
    assert by_arm.get("random_stratified", 0) == plan.random_stratified_simulations
    assert by_arm.get("plateau_fill", 0) == plan.plateau_fill_simulations


def test_small_campaign_still_calibrates(db_session) -> None:
    """A 40-sim campaign used to produce ZERO calibration tasks."""
    plan = plan_budget_allocation(db_session, total_simulations=40, seed=1)
    assert any(t.arm == "random_stratified" for t in plan.tasks)
```

### DECISIONS.md entry

Append to `docs/DECISIONS.md`:

```markdown
### D8 — Budget arm apportionment corrected (2026-08-XX)

Campaigns planned before this date received an arm split of approximately
50/25/25 (exploit / random_stratified / plateau_fill) while `BudgetPlan`
reported 50/30/20, because arm budgets were quantized by integer division
against a 49-sim surface and the remainder was redistributed across arms.
Campaigns under 98 simulations received **no** random_stratified tasks at all.

Any Phase 2 analysis over campaigns created before this date must treat the
calibration arm as under-sampled and must not pool them with later campaigns
without segmenting on campaign creation date.
```

---

## W6 — Quartile stratification silently collapses  **[Tier A]**

### Why

`app/services/allocator.py:767`:

```python
pool = q_fields[quartile_idx] or all_fields
```

`user_count` on data fields is heavily zero-inflated. If the 25th and 50th
percentiles are both 0 (computed at 743-747), the assignment chain at 753-761:

```python
if uc <= q_bounds[0]:      q_fields[1].append(f)
elif uc <= q_bounds[1]:    q_fields[2].append(f)
elif uc <= q_bounds[2]:    q_fields[3].append(f)
else:                      q_fields[4].append(f)
```

puts every zero-user field in Q1 and leaves Q2 (and possibly Q3) **empty**. The
`or all_fields` fallback then draws from the whole population while still
labelling the task `quartile=2`.

The result is a dataset that claims stratified crowding coverage and does not
have it — mislabelled, with **no warning logged**. `docs/strategy/VALIDATION_PROTOCOL.md`
is pre-registered on this stratification. CLAUDE.md notes an earlier study was
impossible because all data sat in one narrow band of crowding; this recreates
that condition while reporting the opposite.

Also at line 749, the fallback `q_bounds = [10.0, 100.0, 1000.0]` are magic
constants presented as percentiles.

### The fix

**1. Detect the degenerate case explicitly and report it.**

```python
q_bounds = [
    float(np.percentile(user_counts, 25)),
    float(np.percentile(user_counts, 50)),
    float(np.percentile(user_counts, 75)),
]
if len(set(q_bounds)) < 3:
    log.warning(
        "quartile_boundaries_degenerate",
        bounds=q_bounds,
        n_fields=len(user_counts),
        n_zero=sum(1 for u in user_counts if not u),
        detail="user_count distribution is too concentrated to form 4 distinct "
               "crowding strata; falling back to rank-based bucketing",
    )
```

**2. Use rank-based bucketing when values tie.** Percentile boundaries cannot
separate a distribution with mass at a single value; ranks can.

```python
def _quartile_buckets(fields: list[tuple]) -> dict[int, list[tuple]]:
    """Partition fields into 4 crowding strata of near-equal size.

    Rank-based rather than value-based: user_count is zero-inflated, so
    value boundaries collapse (p25 == p50 == 0) and leave strata empty.
    Ties are broken by field_code so the partition is deterministic for a
    given corpus — reproducibility matters, this feeds a pre-registered study.
    """
    ranked = sorted(fields, key=lambda f: (f[1] or 0, f[0]))
    n = len(ranked)
    out: dict[int, list[tuple]] = {1: [], 2: [], 3: [], 4: []}
    for i, f in enumerate(ranked):
        out[min(4, i * 4 // n + 1)].append(f)
    return out
```

**3. Never fall back to `all_fields`. Raise or skip, loudly.**

```python
pool = q_fields[quartile_idx]
if not pool:
    log.error(
        "quartile_empty",
        quartile=quartile_idx,
        detail="cannot draw a calibration sample for this stratum; "
               "task skipped rather than mislabelled",
    )
    continue    # NOT: pool = all_fields
```

A skipped task is honest. A mislabelled task poisons the study.

**4. Record the realized strata on the plan** so an analysis can verify coverage
after the fact rather than trusting the label:

```python
@dataclass
class BudgetPlan:
    ...
    quartile_sizes: dict[int, int] | None = None      # fields available per stratum
    quartile_method: str = "percentile"               # 'percentile' | 'rank'
```

### Tests

```python
def test_zero_inflated_user_counts_still_stratify(db_session) -> None:
    """Audit §2.2: p25 == p50 == 0 used to collapse Q2/Q3 into the full population."""
    # seed 40 fields: 30 with user_count=0, 10 spread across 5..5000
    plan = plan_budget_allocation(db_session, total_simulations=400, seed=5)
    rs = [t for t in plan.tasks if t.arm == "random_stratified"]
    assert {t.quartile for t in rs} == {1, 2, 3, 4}
    assert plan.quartile_method == "rank"


def test_quartile_label_matches_the_stratum_drawn_from(db_session) -> None:
    """A task labelled Q4 must come from the most-crowded stratum, not the whole pool."""
    plan = plan_budget_allocation(db_session, total_simulations=400, seed=5)
    buckets = _quartile_buckets(...)   # same helper production uses (invariant 6)
    for t in (x for x in plan.tasks if x.arm == "random_stratified"):
        assert t.field_code in {f[0] for f in buckets[t.quartile]}


def test_empty_stratum_skips_rather_than_mislabels(db_session) -> None:
    plan = plan_budget_allocation(db_session, total_simulations=200, seed=9)
    assert all(t.quartile is not None for t in plan.tasks if t.arm == "random_stratified")
```

The second test is the CLAUDE.md invariant-6 pattern: the test constructs its
expectation with **the same helper production uses**, not a reimplementation.

---

## W7 — `plateau_fill` rebuilds a different territory  **[Tier A]**

### Why

`app/services/allocator.py:815-830` picks `fkey` from `incomplete_families`,
extracts only the field code via `family_field_code`, then hardcodes:

```python
operator_family="ts_zscore",
wrapper_shape="rank",
horizon_band="medium",
```

The operator family and horizon of the family it is supposedly completing are
parsed away and discarded. "Complete the surface for `assets:ts_rank:long@…`"
emits work for `assets:ts_zscore:medium@…` — a different territory, and
specifically the single template that 4,608 of 5,177 alphas already occupy.

`parse_territory_signature` (`constructor.py:161`) already returns exactly the
fields needed and is already imported at the top of `allocator.py`.

### The fix

```python
for fkey in incomplete_families:
    count = sim_counts.get(fkey, 0)
    sig = parse_territory_signature(
        str(fkey),
        default_region=region,
        default_universe=universe,
        default_delay=delay,
    )
    # A legacy key sweeps all horizons (horizon_band is None). Completing it
    # means completing the band the existing points actually sit in; 'medium'
    # is a guess, so skip rather than build the wrong territory.
    if sig.horizon_band is None:
        log.info("plateau_fill_skipped_legacy_key", family=fkey,
                 detail="legacy key has no horizon band; cannot target a surface")
        continue
    dscode = field_to_dataset.get(sig.field_code, "fundamentals")
    tasks.append(
        AllocationTask(
            arm="plateau_fill",
            field_code=sig.field_code,
            dataset_code=dscode,
            operator_family=sig.operator_family,
            wrapper_shape=None,      # sub-axis of the territory; let expand() sweep it
            horizon_band=sig.horizon_band,
            denominator=default_denom,
            target_simulations=per_task_target,
            reason=f"Complete surface for {fkey} ({count}/{expected_surface_size} simulated)",
        )
    )
```

**Also remove the unconditional `break` at the end of that loop.** With W5's
`_territories_for` the arm budget is split across as many incomplete families as
it can fund; one hardcoded task is why the arm always produced exactly one.

### The companion defect — `sims_per_territory` is not a surface size

`allocator.py:58-61`:

```python
DEFAULT_SIMS_PER_TERRITORY = 49
SURFACE_SIZE = 49
```

49 is the 7×7 `STANDARD_WINDOWS × STANDARD_DECAYS` grid, from before horizon
banding existed. But `constructor.expand:470` filters windows by band:

```python
band_windows = tuple(w for w in axes.windows if derive_horizon_band(w) == band)
```

Against `STANDARD_WINDOWS = (5,10,20,40,60,120,250)` and
`derive_horizon_band` (short 1–10, medium 11–63, long 64+) that yields:

| Band | Windows | Points per structural slice |
|---|---|---|
| short | 5, 10 | 2 × 7 decays = **14** |
| medium | 20, 40, 60 | 3 × 7 = **21** |
| long | 120, 250 | 2 × 7 = **14** |

`incomplete_families` is `0 < count < sims_per_territory` (line 663). **A
horizon-banded family therefore stays "incomplete" permanently** and keeps
drawing plateau_fill budget it can never satisfy.

Fix by deriving the expected size from the same constants the constructor uses:

```python
from app.services.constructor import STANDARD_DECAYS, STANDARD_WINDOWS, derive_horizon_band


def expected_surface_size(horizon_band: str | None,
                          windows: tuple[int, ...] = STANDARD_WINDOWS,
                          decays: tuple[int, ...] = STANDARD_DECAYS) -> int:
    """Points on one structural slice for a territory.

    Derived from the constructor's own ladders, never hardcoded: horizon
    banding means a 'short' territory has 2 windows, not 7, so a fixed 49
    marks every banded family permanently incomplete.
    """
    if horizon_band is None:
        return len(windows) * len(decays)
    n_w = sum(1 for w in windows if derive_horizon_band(w) == horizon_band)
    return max(1, n_w * len(decays))
```

and use it in the `incomplete_families` comprehension:

```python
incomplete_families = []
for fkey, count in sim_counts.items():
    if family_field_code(str(fkey)) not in valid_matrix_fields:
        continue
    sig = parse_territory_signature(str(fkey), default_region=region,
                                    default_universe=universe, default_delay=delay)
    if 0 < count < expected_surface_size(sig.horizon_band):
        incomplete_families.append(fkey)
```

Note the counts are still not strictly comparable — `sim_counts`
(`allocator.py:637-651`) counts alphas per `family_key` across **all** structural
slices, while `expected_surface_size` is **one** slice. Fixing that properly means
grouping by `feature_json['grid']` structure, which is a larger change. **Record
this as a known limitation in the function's docstring and stop there** — do not
expand scope.

### Tests

```python
def test_plateau_fill_targets_the_family_it_completes(db_session) -> None:
    """Audit §2.4: op family and horizon were parsed then discarded."""
    # seed an incomplete family 'assets:ts_rank:long@USA/TOP3000/d1' with 5 sims
    plan = plan_budget_allocation(db_session, total_simulations=200, seed=2)
    fill = [t for t in plan.tasks if t.arm == "plateau_fill"]
    assert fill, "plateau_fill produced no task for an incomplete family"
    assert fill[0].operator_family == "ts_rank"
    assert fill[0].horizon_band == "long"


@pytest.mark.parametrize("band,expected", [("short", 14), ("medium", 21), ("long", 14)])
def test_expected_surface_size_respects_horizon_banding(band, expected) -> None:
    assert expected_surface_size(band) == expected


def test_banded_family_can_reach_complete(db_session) -> None:
    """A 'short' family with 14 sims is COMPLETE and must stop drawing fill budget."""
    # seed 14 simulated alphas on a short-band family
    plan = plan_budget_allocation(db_session, total_simulations=200, seed=2)
    assert not any(t.arm == "plateau_fill" and t.horizon_band == "short" for t in plan.tasks)
```

---

## W8 — One source of truth for submission state  **[Tier A]**

### Why

CLAUDE.md invariant 4, and the drift incident that produced it. The canonical
derivation already exists: `app/models/alphas.py:sync_alpha_platform_outcome`
derives `Alpha.platform_outcome` from `submission_attempts`, and as a side effect
mirrors it onto `Alpha.status`. So `platform_outcome` is the derived truth and
`status` is a *mirror* — which is why so many modules read the mirror.

Five modules disagree about where submission truth lives:

| Location | Reads submission from | Verdict |
|---|---|---|
| `correlation.py:28-32` | join on `SubmissionAttempt.result == "submitted"` | correct, but re-derives |
| `spend.py:182` | `Alpha.status == AlphaStatus.SUBMITTED.value` | ❌ |
| `constructor.py:503-506` | `filter_by(status=AlphaStatus.SUBMITTED.value)` | ❌ |
| `plateau.py:130-131` | `port_alpha.status == AlphaStatus.SUBMITTED.value` | ❌ |
| `allocator.py:404` **and** `:418` | **both**, appended to one list | ❌❌ |

`allocator.suggest` is the worst: it reads the status column at 404 *and*
`submission_attempts` at 418, pushing both into `submitted_sigs` — so real
submissions are double-counted and status-only rows (exactly the drift failure
mode) are trusted. It also compares against the bare string `"submitted"` rather
than `AlphaStatus.SUBMITTED.value`.

`plateau.py:130` is the sharpest: the portfolio it filters was *already* built
from `submission_attempts` by `submitted_portfolio`, and it then re-filters on
the status column — so a row where the two disagree is **dropped from the
correlation gate**, which is precisely the row you most want gated.

### The fix

**1. One accessor. Put it next to the derivation it trusts,** in
`app/models/alphas.py`:

```python
def submitted_alpha_filter():
    """The ONE predicate for 'this alpha was submitted to BRAIN'.

    Reads ``platform_outcome``, which ``sync_alpha_platform_outcome`` derives
    from ``submission_attempts``. Never read ``Alpha.status`` for this: status
    is a mirror maintained as a side effect, and a mirror that disagrees with
    its source is how the 2026-07 drift incident stayed invisible for two
    weeks. ``platform_outcome`` is indexed; the old join was not.
    """
    return Alpha.platform_outcome == PlatformOutcome.SUBMITTED.value
```

**2. Rewrite `correlation.submitted_portfolio` to use it** — this removes the
join and the `.distinct()`:

```python
def submitted_portfolio(db: Session, exclude_alpha_id: int | None = None) -> list[Alpha]:
    """Alphas confirmed submitted, per the derived platform_outcome column."""
    q = select(Alpha).where(submitted_alpha_filter())
    if exclude_alpha_id is not None:
        q = q.where(Alpha.id != exclude_alpha_id)
    return list(db.execute(q).scalars().all())
```

**3. Delete the redundant re-filter** at `plateau.py:130-131`. The block becomes:

```python
# Same family as an already submitted alpha. The portfolio is BUILT from
# submitted alphas — re-checking status here dropped any row where the
# status mirror disagreed with platform_outcome, i.e. exactly the rows
# that most need gating.
if candidate.family_key and port_alpha.family_key and candidate.family_key == port_alpha.family_key:
    return True, f"family collision with submitted alpha #{port_alpha.id} ({port_alpha.family_key})"
```

**4. `spend.py:180-183`:**

```python
submitted = int(db.scalar(select(func.count(Alpha.id)).where(submitted_alpha_filter())) or 0)
```

**5. `constructor.py:503-506`:**

```python
submitted_slices = {
    (a.family_key, a.neutralization, a.truncation)
    for a in db.execute(select(Alpha).where(submitted_alpha_filter())).scalars()
    if a.family_key
}
```

**6. `allocator.py:395-420` — delete the status branch entirely.** Keep only the
`submission_attempts`-derived path, rewritten against the accessor:

```python
submitted_sigs: list[TerritorySignature] = []
for (fkey,) in db.execute(
    select(Alpha.family_key).where(submitted_alpha_filter(), Alpha.family_key.is_not(None))
).all():
    submitted_sigs.append(parse_territory_signature(
        str(fkey), default_region=region, default_universe=universe, default_delay=delay))
```

and remove `Alpha.status` from the `existing_alphas` select at 383-390 plus the
`if status == "submitted"` block at 404-411.

### W8b — The structural guard

This is the item most likely to regress, because reading `Alpha.status` is the
obvious thing to write. Add `backend/tests/test_submission_source_of_truth.py`:

```python
"""Guardrail: submission truth has exactly one source.

CLAUDE.md invariant 4. The 2026-07 drift incident happened because local
state was written without platform verification; the fix was to DERIVE
platform_outcome from submission_attempts. A module that reads
Alpha.status to answer 'was this submitted?' reintroduces the second
source, silently, and the disagreement is invisible until someone
reconciles by hand.

This test is deliberately structural rather than behavioural: a behavioural
test passes as long as the two columns happen to agree, which they did for
two weeks.
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# The one module allowed to write the mirror is the one that derives it.
ALLOWED = {"models/alphas.py", "routers/ui.py", "services/alpha_library.py"}

PATTERN = re.compile(
    r"status\s*==\s*AlphaStatus\.SUBMITTED"
    r"|filter_by\([^)]*status\s*=\s*AlphaStatus\.SUBMITTED"
    r"|status\s*==\s*[\"']submitted[\"']"
)


def test_no_module_reads_alpha_status_as_submission_evidence() -> None:
    offenders = []
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel in ALLOWED:
            continue
        if PATTERN.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert not offenders, (
        f"{offenders} read Alpha.status as submission evidence. "
        f"Use submitted_alpha_filter() — see CLAUDE.md invariant 4."
    )


def test_derived_and_mirror_agree_on_the_live_corpus(db_session) -> None:
    """Behavioural companion: if these ever disagree, drift has recurred."""
    from sqlalchemy import select
    from app.models.alphas import Alpha
    from app.models.enums import AlphaStatus, PlatformOutcome

    rows = db_session.execute(select(Alpha.id, Alpha.status, Alpha.platform_outcome)).all()
    drift = [
        (aid, st, po) for aid, st, po in rows
        if (st == AlphaStatus.SUBMITTED.value) != (po == PlatformOutcome.SUBMITTED.value)
    ]
    assert not drift, f"status/platform_outcome disagree for {drift[:5]} ({len(drift)} rows)"
```

Note `routers/ui.py` is in `ALLOWED` because it *writes* the status transition
when the operator records a submission (`ui.py:525-539`) — that is the writer,
not a reader. If you find it also *reads* status as evidence, fix it and remove
it from the allowlist.

### Run the drift check against the real DB before and after

```bash
cd backend && python - <<'PY'
from sqlalchemy import select
from app.db import session_scope
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, PlatformOutcome
with session_scope() as db:
    rows = db.execute(select(Alpha.id, Alpha.status, Alpha.platform_outcome)).all()
    drift = [r for r in rows if (r[1] == AlphaStatus.SUBMITTED.value) != (r[2] == PlatformOutcome.SUBMITTED.value)]
    print(f"total={len(rows)} drift={len(drift)}")
    for r in drift[:20]: print(r)
PY
```

**If drift > 0, stop and report it.** That is a live recurrence of the incident
and it is a finding in its own right, not something to fix by picking a side.
`scripts/sync_submission_outcomes.py` treats the platform as authoritative and
is the tool for reconciling — but running it is a human decision.

### Doc-level instance — fix in the same commit

`docs/PHASE1_OPERATING_GUIDE.md:11` states *"Submission quota is 4/day
(confirmed)"* and that quota is not the binding constraint. `CLAUDE.md:84` still
lists *"BRAIN submission quota per week"* as an open question needing a human.
One of the two is stale. Given commit `2275f13` ("docs(operating-guide): confirm
4/day submission quota not binding constraint"), the operating guide is newer —
**remove that row from the CLAUDE.md open-questions table** and note the
confirmed figure instead.

---

## W9 — `backfill_pnl` can attach one alpha's PnL to another  **[Tier A]**

### Why

`backend/scripts/backfill_pnl.py:34-37`:

```python
for a in db_alphas:
    expr_to_alpha[(a.expression.strip(), a.neutralization, a.decay)] = a
    expr_to_alpha[a.expression.strip()] = a
```

Both keys go into **the same dict**, and the expression-only key is overwritten
by whichever of 4,857 alphas comes last. The lookup at line 48:

```python
local_alpha = expr_to_alpha.get((code, neutr, decay)) or expr_to_alpha.get(code)
```

falls back to it. So a remote alpha whose settings match no local row is matched
on expression alone, and its PnL is saved under an arbitrary local alpha id with
**different neutralization and decay** — a different backtest. That PnL then
feeds DSR, subperiod and correlation for the wrong alpha.

This is the drift incident's failure mode (local state written without
verification) in a different column. Given `family_key` includes settings and
the constructor deliberately sweeps neutralization and decay as grid axes, the
collision rate here is not hypothetical.

### The fix

```python
from collections import defaultdict

# Exact match on (expression, neutralization, decay) only. An expression-only
# fallback is NOT safe: the constructor sweeps neutralization and decay as
# grid axes, so one expression maps to many alphas with different backtests,
# and attaching the wrong PnL corrupts every statistic downstream.
exact: dict[tuple, list[Alpha]] = defaultdict(list)
with session_scope() as db:
    for a in db.query(Alpha).all():
        exact[(a.expression.strip(), a.neutralization, a.decay)].append(a)

...

matches = exact.get((code, neutr, decay), [])
if len(matches) != 1:
    log.info(
        "pnl_match_ambiguous" if matches else "pnl_match_none",
        remote_id=ra.get("id"), n_local=len(matches),
        neutralization=neutr, decay=decay,
    )
    stats["unmatched"] += 1
    continue
local_alpha = matches[0]
```

Two further defects in the same file:

**Line 77:** `float((ra.get("is") or {}).get("sharpe", 0.0))` raises `TypeError`
when the key is present with a `None` value — `.get` returns `None`, not the
default. Use:

```python
reported = (ra.get("is") or {}).get("sharpe")
if reported is None:
    stats["no_reported_sharpe"] += 1
    continue
```

**Lines 32-74:** `db_alphas` is read inside `session_scope()` and the ORM
instances are used *after* the block exits. This works only because
`SessionLocal` sets `expire_on_commit=False` (`app/db/session.py:66`) — an
implicit dependency on a session flag, not an intended contract. Capture plain
tuples instead:

```python
with session_scope() as db:
    rows = db.execute(
        select(Alpha.id, Alpha.expression, Alpha.neutralization, Alpha.decay)
    ).all()
exact = defaultdict(list)
for aid, expr, neutr_, decay_ in rows:
    exact[(expr.strip(), neutr_, decay_)].append(aid)
```

Also drop the unused `import math` at line 10.

### Tests

New `backend/tests/test_backfill_pnl_matching.py`:

```python
def test_ambiguous_expression_is_not_matched() -> None:
    """Audit §3.1: two alphas sharing an expression but differing in decay
    must not have one's PnL written onto the other."""
    # two Alpha rows, same expression, decay 0 and 4
    # remote payload with decay=8 (matching neither)
    # assert nothing is written to the store, and stats['unmatched'] == 1


def test_exact_settings_match_is_required() -> None:
    # remote decay=4 matches only the decay=4 row
    # assert store.load_pnl(alpha_decay4.id) is not None
    # assert store.load_pnl(alpha_decay0.id) is None
```

---

## W10 — Hygiene  **[Tier A, one commit, no behaviour change]**

Each of these is a one-to-three-line change. Do them together.

**a. Undefined `Any`.** `app/services/subperiod.py:206` annotates
`pnl_store: Any`, but line 16 is `from typing import Sequence` — `Any` is never
imported. It does not raise today only because `from __future__ import annotations`
(line 12) defers evaluation, but `typing.get_type_hints`, Pydantic, or any
runtime introspection over this signature raises `NameError`.

```python
from typing import Any, Sequence
```

Same latent pattern in two more places — fix all three:
- `app/services/plateau.py:263` — `pnl_store: PnLStore | None` where `PnLStore`
  is imported only inside the function body at line 268. Move to a
  `TYPE_CHECKING` block at module top.
- `backend/tests/conftest.py` — `_isolate_pnl_store(tmp_path: Path, ...)` has
  `from pathlib import Path` **inside** the function body.

**b. Dead RNG.** `app/services/allocator.py:369`:

```python
_rng = rng or (random.Random(seed) if seed is not None else random.Random())
```

`_rng` is never read again. Every choice in `suggest()` is deterministic index
rotation (`len(out) % len(...)` at 545 and 560). The docstring's *"Unseeded calls
use random.Random() for diverse interactive UI recommendations; reproducible
campaigns pass an explicit seed"* is false in both halves, and
`plan_budget_allocation` passes `rng=rng` at 699 to no effect — so the campaign
`seed` column has no effect on the exploit arm.

**Delete the parameters and fix the docstring.** A dead reproducibility knob is
worse than none, because the campaign records a seed implying a replayability it
does not have. If seeded exploration is wanted, that is a separate, specified
change — do not invent it here.

Update the call at 699 and any test that passes `seed=` to `suggest`.

**c. Stale window ladder in evolution.** `app/services/evolution.py:55-62` keys
`_WINDOW_JITTER` on `(5, 10, 22, 63, 126, 252)` — the **WIDE** ladder. Production
default is `STANDARD_WINDOWS = (5,10,20,40,60,120,250)`. The lookup at line 108
is `if val in _WINDOW_JITTER`, so windows 20, 40, 60, 120 and 250 **never
jitter**; only 5 and 10 mutate. Rebuild it from the constructor's ladder:

```python
from app.services.constructor import STANDARD_WINDOWS, WIDE_WINDOWS


def _jitter_candidates(w: int) -> tuple[int, ...]:
    """Neighbouring windows for parameter mutation, derived rather than
    hardcoded: a literal table keyed on one ladder silently no-ops when the
    default ladder changes, which is what happened here."""
    return tuple(sorted({max(2, int(w * f)) for f in (0.6, 0.8, 1.25, 1.6)} - {w}))
```

Add a test asserting every window in `STANDARD_WINDOWS` and `WIDE_WINDOWS`
produces at least two distinct candidates.

**d. Stale docstrings.** Fix these four; each asserts something the code does not do:
- `app/services/brain/client.py:18-20` claims `normalize_is_block` converts
  margin from fraction to bps. It does not — `client.py:104-106` is
  `return dict(is_block)`. The conversion lives in
  `result_import.py:_margin_to_bps`. Point the docstring there.
- `app/services/subperiod.py:8` promises rolling positivity `">= 75%"`; the
  parameter default at line 115 is `0.70`. Pick one — **change the docstring,
  not the constant** (the constant is a frozen filter threshold).
- `app/services/plateau.py:41-45` documents `WINDOW_LADDER`/`DECAY_LADDER`
  fallbacks that no longer match `constructor.STANDARD_WINDOWS`. Either import
  the constructor's constants or state that these are legacy-only fallbacks.
- `app/services/constructor.py:12-14` says structural axes "are sampled". See
  W11 — after that fix the statement becomes true; if W11 is deferred, correct
  the docstring now to say they are truncated.

**e. Private access from a script.** `scripts/verify_pnl_reconciliation.py:29`
does `store._dir.glob("*.npy")`. Add and use:

```python
def iter_alpha_ids(self) -> list[int]:
    """Alpha ids with a stored series, ascending."""
    return sorted(int(p.stem) for p in self._dir.glob("*.npy") if p.stem.isdigit())
```

**f. Repo hygiene.**
- `alphahandoff.zip` (48 KB binary) is tracked at the repo root. `git rm` it;
  add `*.zip` to `.gitignore`.
- `backend/repro.py` is a tracked scratch harness that does
  `import tests.conftest as C` from outside `tests/`. Move it to
  `backend/tests/manual/repro.py` or delete it. Do **not** leave a tracked file
  at the backend root importing test fixtures.

**g. Bare excepts.** There are 36 `except Exception` blocks in `app/` and
`scripts/`. Do **not** sweep them all in this commit. Fix only the one already
covered in W1.3 (`PnLStore.save_pnl` leaving a populated cache after a failed
write) and open an issue listing the rest.

---

## W11 — Structural axes are truncated, not sampled  **[Tier A — constructor is not a frozen filter]**

### Why

`app/services/constructor.py:519-522` (and the identical guards at 550-552 and
595-597):

```python
for ts_op, cs_op, group, neutralization, truncation, universe in configs:
    if (family_key, neutralization, truncation) in submitted_slices:
        continue
    if len(out) + surface_size > max_candidates:
        break
```

`break`, not `continue`, over an `itertools.product`. With `max_candidates=400`
and a 21-point surface, roughly 19 of 700 configs are emitted — and because
`product` varies the **last** axis fastest, they are all a *prefix*:
`neutralizations[0] == "SUBINDUSTRY"` only. `MARKET` and `NONE` never appear.

This directly defeats the module's own stated design (`constructor.py:16-19`):

> *"Settings are part of the family, not a wrapper around it. Neutralization,
> decay and truncation move Sharpe by 0.3–0.6 on an unchanged expression, which
> is the single biggest reason the first 51 alphas failed: each idea was sampled
> at exactly one settings point."*

The code samples at exactly one neutralization point. The constructor is not in
the frozen-filter list, and Phase 1 exists to break the monoculture, so this
ships.

### The fix

Stratify the config list before consuming it, so the budget is spread across
each axis rather than taken off the front:

```python
def _stratified_configs(configs: list[tuple], n_slots: int) -> list[tuple]:
    """Spread a candidate budget across the config axes instead of taking a prefix.

    ``itertools.product`` varies the last axis fastest, so truncating it keeps
    only the first value of every earlier axis — with neutralization as an
    early axis, that means SUBINDUSTRY only. Round-robin by the axes that
    matter most (neutralization first) so a small budget still touches every
    setting rather than one.
    """
    if n_slots >= len(configs):
        return configs
    by_neutral: dict[str, list[tuple]] = {}
    for c in configs:
        by_neutral.setdefault(c[3], []).append(c)   # index 3 == neutralization
    out: list[tuple] = []
    keys = sorted(by_neutral)
    i = 0
    while len(out) < n_slots and any(by_neutral.values()):
        bucket = by_neutral[keys[i % len(keys)]]
        if bucket:
            out.append(bucket.pop(0))
        i += 1
    return out
```

Call it before the loop:

```python
configs = list(itertools.product(
    ts_transforms, cross_sections, groups,
    axes.neutralizations, axes.truncations, axes.universes,
))
n_slots = max(1, max_candidates // max(1, surface_size))
configs = _stratified_configs(configs, n_slots)

for ts_op, cs_op, group, neutralization, truncation, universe in configs:
    if (family_key, neutralization, truncation) in submitted_slices:
        continue
    if len(out) + surface_size > max_candidates:
        break        # now a genuine budget stop, not an axis truncation
```

### Tests

```python
def test_all_neutralizations_are_reached_under_budget(db_session) -> None:
    """Audit §2.6: a prefix of itertools.product is SUBINDUSTRY-only."""
    spec = FamilySpec(field_code="close", horizon_band="medium", operator_family="ts_zscore")
    cands = expand(db_session, spec, max_candidates=400)
    seen = {c.settings.neutralization for c in cands}
    assert len(seen) >= 3, f"only reached {seen}"


def test_budget_is_still_respected(db_session) -> None:
    cands = expand(db_session, FamilySpec(field_code="close"), max_candidates=100)
    assert len(cands) <= 100
```

---

## 2. Definition of done for the whole brief

- [ ] `python -m pytest -q` green, still under ~5 s, count >= 194 + new tests
- [ ] `python -m alembic upgrade head && python -m alembic downgrade -1 && python -m alembic upgrade head` clean
- [ ] `tests/test_brain_no_post.py` still passes (no new network verbs)
- [ ] `tests/test_submission_source_of_truth.py` passes (W8b)
- [ ] The shadow-DSR guard test passes (W4) — no Tier B statistic reaches a gate
- [ ] `docs/DECISIONS.md` has the D8 entry (W5)
- [ ] `docs/BRAIN_API.md` records the PnL convention with a date and sample size (W1)
- [ ] `CLAUDE.md` open-questions table no longer contradicts
      `docs/PHASE1_OPERATING_GUIDE.md` on the submission quota (W8)
- [ ] The audit doc is annotated with which findings are closed and which are
      deferred to Phase 2, with the deferral reason

## 3. What is explicitly out of scope

Do not, without a separate human decision:

- Change any threshold constant in `plateau.py`, `subperiod.py` or
  `correlation.py` — `PLATEAU_RATIO`, `BASE_SHARPE_BAR`, `HAIRCUT_PER_LOG10`,
  `COLD_START_SHARPE_BAR`, `MIN_TRIALS_FOR_DSR`, `DSR_PROMOTION_THRESHOLD`,
  `DSR_RE_PROMOTION_THRESHOLD`, `INTERNAL_CORRELATION_THRESHOLD`,
  `MIN_COMMON_TRADING_DAYS`, or any `evaluate_subperiod_stability` floor.
- Make the plateau test two-sided. A valley currently passes
  (`ratio = neigh_median / sharpe` is unbounded above, `plateau.py:296-303`),
  and representative selection sorts by `neighbour_median_sharpe` first
  (`plateau.py:392-401`), so it actively prefers the valley over the ridge top.
  This is a real defect — audit §3.4 — but fixing it changes which alphas
  promote. **Tier B: implement behind a flag defaulting to OFF, report how many
  historical promotions change, and let a human decide.**
- Rework the margin unit heuristic (`result_import.py:89-104`). The comment
  claims *"The two scales cannot overlap in practice"*; they do — a fraction
  margin of exactly 0.01 is 100 bps and passes through unconverted. Real, but it
  needs a schema decision about recording the arrival scale.
- Touch `config_available` (`brain/client.py:335-360`), which POSTs a real
  simulation for `close` and abandons it without polling. Needs a human call on
  whether preflight should spend a slot at all.
- Fix the capacity arithmetic in `spend.py:35,163` (`DAILY_SIM_CAPACITY` = 2,880/day
  theoretical vs ~13/day measured; `wall_clock_hours` assumes perfect 3-wide
  packing). Reporting-only, no decision depends on it yet.
- Add billing, accounts, multi-user features, a crowding map, a network layer, a
  fertility model, a landing page, or outreach. All out of scope until Phase 2
  passes.
- Build product features of any kind. This is a personal research tool.

## 4. If you get stuck

Stop and report rather than improvising. Specifically, stop if:

- W1's convention detector returns `indeterminate` for most of the corpus. That
  means the reported Sharpe and the recordset cannot be reconciled at all, which
  is a bigger finding than anything in this brief.
- The W8 drift query returns a non-zero count. That is a live recurrence of the
  incident CLAUDE.md documents.
- Any change would require editing a frozen threshold to keep tests green. The
  test is telling you the item is Tier B.
