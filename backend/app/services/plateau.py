"""Stage 4 — the honest filter (STRATEGY.md Rule 5).

Mass simulation makes overfitting the *default* outcome, not a risk. A
400-member family will hand you a Sharpe 1.5 by luck alone. Without this module
the rest of the system is a machine for producing confident garbage faster, so
this is the piece not to cut.

Five tests, cheapest first:

1. **Plateau, not peak.** Judge a candidate by the median score of its
   neighbours on the (window, decay) surface. A lone spike surrounded by dead
   neighbours is a coincidence; a broad ridge is a mechanism.

2. **Pre-declared bar.** BRAIN's own ``checks[]``, which the alpha already
   carries. Never re-tuned to fit a result we like.

3. **Deflated Sharpe Ratio (DSR) & Multiple-testing haircut.**
   For family size >= 30, calculates Bailey & Lopez de Prado DSR (>= 0.95) with
   Euler-Mascheroni expected maximum. For cold start (< 30), applies a conservative
   annualized Sharpe hurdle (>= 1.50).

4. **Sub-period stability & decay.**
   Validates split-half consistency, monthly-stepped rolling windows, and recent 252d decay.

5. **Empirical Correlation Gate (< 0.55).**
   Computes exact Pearson correlation over aligned daily PnL vectors against active and submitted
   portfolio alphas, with fallback to structural hashing.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dc_field
from statistics import median
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.results import AlphaMetric

if TYPE_CHECKING:
    from app.services.pnl_storage import PnLStore

log = structlog.get_logger("plateau")

# Legacy fallback coordinate ladders used only when surface is empty.
# In production, _neighbours dynamically derives active ladders from the surface's own points.
WINDOW_LADDER: tuple[int, ...] = (5, 10, 22, 63, 126, 252)
DECAY_LADDER: tuple[int, ...] = (0, 4, 8, 16)

# A candidate must hold at least this fraction of its own score across its
# neighbourhood to count as a plateau rather than a spike.
PLATEAU_RATIO = 0.6

# Baseline sanity floor
BASE_SHARPE_BAR = 1.25
HAIRCUT_PER_LOG10 = 0.10
COLD_START_SHARPE_BAR = 1.50
# When no daily PnL series exists, DSR cannot be computed at all. The fallback
# is a Sharpe hurdle, and it must never be *looser* than the family's own
# multiple-testing haircut — a larger family means more trials, not an easier
# bar. Distinct constant so it can never again be confused with the DSR
# probability thresholds above.
NO_PNL_SHARPE_BAR = COLD_START_SHARPE_BAR
MIN_TRIALS_FOR_DSR = 30
DSR_PROMOTION_THRESHOLD = 0.95
DSR_RE_PROMOTION_THRESHOLD = 0.97


@dataclass
class SurfacePoint:
    alpha_id: int
    expression: str
    window: int
    decay: int
    sharpe: float | None
    fitness: float | None
    turnover: float | None
    passed_all_checks: bool | None
    structure: tuple


@dataclass
class Verdict:
    alpha_id: int
    expression: str
    sharpe: float | None
    fitness: float | None
    neighbour_median_sharpe: float | None
    plateau_ratio: float | None
    is_plateau: bool
    neighbours_simulated: int = 0
    neighbours_possible: int = 0
    clears_bar: bool = False
    haircut_bar: float = 0.0
    is_correlated: bool = False
    correlation_collision: str | None = None
    max_correlation: float | None = None
    correlation_method: str = "none"
    promoted: bool = False
    family_size: int = 0
    dsr: float | None = None
    dsr_passed: bool | None = None
    gate_mode: str = "COLD_START_FALLBACK"
    subperiod_passed: bool | None = None
    redundant_with: int | None = None
    reasons: list[str] = dc_field(default_factory=list)
    # Tier B: recorded for Phase 2, NOT gated in Phase 1
    n_eff_family: float | None = None
    dsr_global_shadow: float | None = None
    shadow_trials: float | None = None


def check_portfolio_correlation(
    db: Session, alpha_id: int, portfolio: list[Alpha] | None = None
) -> tuple[bool, str | None]:
    """Check whether candidate collides structurally with any already-submitted alpha."""
    candidate = db.get(Alpha, alpha_id)
    if candidate is None:
        return False, None

    if portfolio is None:
        from app.services.correlation import submitted_portfolio
        portfolio = submitted_portfolio(db, exclude_alpha_id=alpha_id)

    cand_features = candidate.feature_json or {}
    cand_struct = cand_features.get("structural_hash")
    cand_field = family_field_code(candidate.family_key or "") if candidate.family_key else None

    for port_alpha in portfolio:
        if port_alpha.id == candidate.id:
            continue
        port_features = port_alpha.feature_json or {}
        port_struct = port_features.get("structural_hash")
        port_field = family_field_code(port_alpha.family_key or "") if port_alpha.family_key else None

        # Structural hash match on the same base field => near-certain self-correlation
        if cand_struct and port_struct and cand_struct == port_struct and cand_field == port_field:
            return True, f"structural correlation collision with submitted alpha #{port_alpha.id}"

        # Same family as an already submitted alpha. The portfolio is BUILT from
        # submitted alphas — re-checking status here dropped any row where the
        # status mirror disagreed with platform_outcome, i.e. exactly the rows
        # that most need gating.
        if candidate.family_key and port_alpha.family_key and candidate.family_key == port_alpha.family_key:
            return True, f"family collision with submitted alpha #{port_alpha.id} ({port_alpha.family_key})"

    return False, None


def _structure_of(grid: dict) -> tuple:
    """Everything that is NOT a swept plateau axis. Points sharing a structure
    lie on the same surface and are therefore comparable."""
    return (
        grid.get("ts"),
        grid.get("cs"),
        grid.get("group"),
        grid.get("truncation"),
        grid.get("neutralization"),
    )


def family_field_code(family_key: str) -> str:
    """Extract field_code across legacy ('assets/cap@...') and canonical ('assets:ts_zscore...') keys."""
    raw = family_key.split("@")[0].split("/")[0]
    if ":" in raw:
        return raw.split(":")[0]
    return raw


def load_surface(db: Session, family_key: str) -> list[SurfacePoint]:
    """Pull every simulated alpha in the family, with its coordinates and score."""
    q = (
        select(
            Alpha.id,
            Alpha.expression,
            Alpha.feature_json,
            AlphaMetric.sharpe,
            AlphaMetric.fitness,
            AlphaMetric.turnover,
            AlphaMetric.passed_all_checks,
        )
        .outerjoin(
            AlphaMetric,
            AlphaMetric.alpha_id == Alpha.id,
        )
        .where(Alpha.family_key == family_key)
    )

    out: list[SurfacePoint] = []
    for aid, expr, feat, sharpe, fitness, turnover, passed in db.execute(q).all():
        grid = (feat or {}).get("grid") or {}
        w = grid.get("window")
        d = grid.get("decay")
        if w is None or d is None:
            continue
        out.append(
            SurfacePoint(
                alpha_id=aid,
                expression=expr,
                window=int(w),
                decay=int(d),
                sharpe=float(sharpe) if sharpe is not None else None,
                fitness=float(fitness) if fitness is not None else None,
                turnover=float(turnover) if turnover is not None else None,
                passed_all_checks=bool(passed) if passed is not None else None,
                structure=_structure_of(grid),
            )
        )
    return out


MIN_NEIGHBOURS_TO_JUDGE = 2


def _ladder_neighbours(ladder: list[int] | tuple[int, ...], val: int) -> list[int]:
    if val not in ladder:
        return []
    i = ladder.index(val)
    out = []
    if i > 0:
        out.append(ladder[i - 1])
    if i < len(ladder) - 1:
        out.append(ladder[i + 1])
    return out


def _neighbours(
    point: SurfacePoint, surface: list[SurfacePoint]
) -> tuple[list[SurfacePoint], int]:
    """Adjacent points on the same structural slice. Coordinates derived from surface itself."""
    same_slice = [p for p in surface if p.structure == point.structure]

    active_windows = sorted({p.window for p in same_slice})
    active_decays = sorted({p.decay for p in same_slice})

    w_ladder = active_windows if len(active_windows) >= 2 else WINDOW_LADDER
    d_ladder = active_decays if len(active_decays) >= 2 else DECAY_LADDER

    target_windows = set(_ladder_neighbours(w_ladder, point.window))
    target_decays = set(_ladder_neighbours(d_ladder, point.decay))

    possible = len(target_windows) + len(target_decays)

    found: list[SurfacePoint] = []
    for p in same_slice:
        if p.alpha_id == point.alpha_id:
            continue
        is_w_neighbour = p.decay == point.decay and p.window in target_windows
        is_d_neighbour = p.window == point.window and p.decay in target_decays
        if is_w_neighbour or is_d_neighbour:
            found.append(p)

    return found, possible


def haircut_bar(family_size: int) -> float:
    """Required Sharpe floor."""
    if family_size <= 1:
        return BASE_SHARPE_BAR
    return BASE_SHARPE_BAR + HAIRCUT_PER_LOG10 * math.log10(family_size)


def evaluate(
    db: Session,
    family_key: str,
    portfolio: list[Alpha] | None = None,
    pnl_store: PnLStore | None = None,
    require_pnl: bool = True,
) -> list[Verdict]:
    """Score every simulated point in a family. Promoted ones survived all statistical tests."""
    from app.services.correlation import check_portfolio_empirical_correlation
    from app.services.pnl_storage import get_pnl_store
    from app.services.subperiod import compute_dsr, evaluate_subperiod_stability

    surface = load_surface(db, family_key)
    family_sharpes = [p.sharpe for p in surface if p.sharpe is not None]
    simulated_count = len(family_sharpes)
    bar = haircut_bar(max(simulated_count, 1))

    if portfolio is None:
        from app.services.correlation import submitted_portfolio
        portfolio = submitted_portfolio(db)

    pnl_store = pnl_store or get_pnl_store()

    # Scope DSR activation to slice-level trial count (neighbourhood & multiple testing alignment)
    by_slice: dict[tuple, list[SurfacePoint]] = defaultdict(list)
    for p in surface:
        if p.sharpe is not None:
            by_slice[p.structure].append(p)
    max_slice_trials = max((len(pts) for pts in by_slice.values()), default=0)
    use_dsr = max_slice_trials >= MIN_TRIALS_FOR_DSR
    gate_mode = "DSR" if use_dsr else "COLD_START_FALLBACK"

    # --- Tier B: recorded, NOT gated. See docs/briefs/brief-remediation-2026-08.md W4.
    # Phase 1 freezes the filters; this is measured now so Phase 2 can decide
    # whether the shipped DSR is over-permissive, using real data rather than an
    # argument.
    n_eff_family: float | None = None
    shadow_trials: float | None = None
    global_daily_sharpes: list[float] = []
    family_alpha_ids = [p.alpha_id for p in surface if p.sharpe is not None]
    if len(family_alpha_ids) >= 2:
        from app.services.correlation import compute_correlation_matrix
        from app.services.subperiod import compute_effective_trials

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
                total_simulated = int(
                    db.scalar(select(func.count(distinct(AlphaMetric.alpha_id)))) or len(ids)
                )
                m_family = float(max(1, len(ids)))
                shadow_trials = max(n_eff_family, total_simulated * (n_eff_family / m_family))
                all_sharpes = db.execute(
                    select(AlphaMetric.sharpe).where(AlphaMetric.sharpe.is_not(None))
                ).scalars().all()
                global_daily_sharpes = [float(s) / math.sqrt(252) for s in all_sharpes]

    verdict_map: dict[int, tuple[SurfacePoint, Verdict, bool]] = {}

    for point in surface:
        reasons: list[str] = []
        neigh, possible = _neighbours(point, surface)
        values = [p.sharpe for p in neigh if p.sharpe is not None]
        neigh_median = median(values) if values else None

        ratio = None
        if neigh_median is not None and point.sharpe:
            ratio = neigh_median / point.sharpe

        judgeable = len(values) >= MIN_NEIGHBOURS_TO_JUDGE
        positive = bool(point.sharpe is not None and point.sharpe > 0)
        is_plateau = bool(judgeable and positive and ratio is not None and ratio >= PLATEAU_RATIO)

        if not values:
            reasons.append("no simulated neighbours — surface incomplete")
        elif not judgeable:
            reasons.append(f"only {len(values)} of {possible} neighbours simulated — cannot judge")
        elif not positive:
            reasons.append("non-positive Sharpe — plateau test does not apply")
        elif not is_plateau:
            reasons.append(f"spike: neighbours median {neigh_median:.2f} vs own {point.sharpe:.2f}")

        clears = bool(point.passed_all_checks)
        if not clears:
            reasons.append("fails BRAIN checks")

        above_bar = bool(point.sharpe is not None and point.sharpe >= bar)
        if not above_bar:
            reasons.append(f"below baseline bar {bar:.2f}")

        # Sub-period stability & DSR check: requires daily PnL series
        pnl_data = pnl_store.load_pnl(point.alpha_id)
        dsr_val: float | None = None
        dsr_shadow: float | None = None
        dsr_passed = False
        subperiod_passed = False

        if pnl_data is not None:
            _, daily_pnl = pnl_data
            sub_res = evaluate_subperiod_stability(daily_pnl)
            subperiod_passed = sub_res.passed
            if not subperiod_passed:
                reasons.extend(sub_res.reasons)

            # Daily DSR calculation
            daily_sharpes = [s / math.sqrt(252) for s in family_sharpes]
            dsr_val = compute_dsr(daily_pnl, daily_sharpes)  # GATES (unchanged)
            dsr_shadow = (
                compute_dsr(daily_pnl, global_daily_sharpes, n_eff=shadow_trials)
                if (shadow_trials and global_daily_sharpes)
                else None
            )  # RECORDED ONLY

            if use_dsr:
                alpha_obj = db.get(Alpha, point.alpha_id)
                is_re_promoting = bool(alpha_obj and "watchlist" in (alpha_obj.comments or "").lower())
                target_dsr_hurdle = DSR_RE_PROMOTION_THRESHOLD if is_re_promoting else DSR_PROMOTION_THRESHOLD
                dsr_passed = dsr_val >= target_dsr_hurdle
                if not dsr_passed:
                    reasons.append(f"DSR {dsr_val:.3f} below {target_dsr_hurdle:.2f} threshold")
            else:
                # Cold start mode: conservative hurdle (Sharpe >= 1.50, Fitness >= 1.0)
                dsr_passed = bool(
                    point.sharpe is not None
                    and point.sharpe >= COLD_START_SHARPE_BAR
                    and point.fitness is not None
                    and point.fitness >= 1.0
                )
                if not dsr_passed:
                    reasons.append(f"cold-start Sharpe/Fitness below {COLD_START_SHARPE_BAR:.2f}/1.0")
        else:
            if require_pnl:
                # Hard precondition: daily PnL series is required for statistical gating
                reasons.append("no daily PnL series — subperiod stability and DSR pending")
                subperiod_passed = False
                dsr_passed = False
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

        point_gate_mode = gate_mode if pnl_data is not None else "NO_PNL_FALLBACK"

        # Correlation gate (empirical against confirmed submissions with structural fallback)
        corr = check_portfolio_empirical_correlation(
            db, point.alpha_id, pnl_store=pnl_store, portfolio=portfolio
        )
        if corr.blocking and corr.reason:
            reasons.append(corr.reason)

        survives = (
            clears
            and is_plateau
            and above_bar
            and dsr_passed
            and subperiod_passed
            and not corr.blocking
        )

        v = Verdict(
            alpha_id=point.alpha_id,
            expression=point.expression,
            sharpe=point.sharpe,
            fitness=point.fitness,
            neighbour_median_sharpe=neigh_median,
            plateau_ratio=ratio,
            is_plateau=is_plateau,
            neighbours_simulated=len(values),
            neighbours_possible=possible,
            clears_bar=clears,
            haircut_bar=bar,
            is_correlated=corr.blocking,
            correlation_collision=corr.reason,
            max_correlation=corr.max_correlation,
            correlation_method=corr.method,
            promoted=False,  # Assigned via intra-family representative selection
            family_size=simulated_count,
            dsr=dsr_val,
            dsr_passed=dsr_passed,
            gate_mode=point_gate_mode,
            subperiod_passed=subperiod_passed,
            redundant_with=None,
            reasons=reasons,
            n_eff_family=n_eff_family,
            dsr_global_shadow=dsr_shadow,
            shadow_trials=shadow_trials,
        )
        verdict_map[point.alpha_id] = (point, v, survives)

    # Intra-family redundancy & representative selection (Invariant 8: neighbourhood strength over peak)
    # Group by structure: (ts, cs, group, neutralization, truncation)
    slice_groups: dict[tuple, list[tuple[SurfacePoint, Verdict, bool]]] = defaultdict(list)
    for point in surface:
        if point.alpha_id in verdict_map:
            slice_groups[point.structure].append(verdict_map[point.alpha_id])

    for struct_key, items in slice_groups.items():
        survivors = [item for item in items if item[2]]
        if survivors:
            # Rank candidate representatives by:
            # 1. neighbour_median_sharpe (highest)
            # 2. plateau_ratio (highest)
            # 3. decay (lowest — cheaper turnover)
            # 4. raw sharpe (highest — last tiebreaker)
            survivors.sort(
                key=lambda item: (
                    item[1].neighbour_median_sharpe if item[1].neighbour_median_sharpe is not None else -999,
                    item[1].plateau_ratio if item[1].plateau_ratio is not None else -999,
                    -item[0].decay,
                    item[1].sharpe if item[1].sharpe is not None else -999,
                ),
                reverse=True,
            )
            # Top ranked candidate is promoted
            chosen = survivors[0]
            chosen[1].promoted = True

            # Demote remaining survivors on the same structural slice as redundant with chosen representative
            for redundant in survivors[1:]:
                redundant[1].promoted = False
                redundant[1].redundant_with = chosen[0].alpha_id
                redundant[1].reasons.append(
                    f"redundant with structural representative #{chosen[0].alpha_id} "
                    f"(neigh_median={chosen[1].neighbour_median_sharpe:.2f})"
                )

    return [v for _, v, _ in verdict_map.values()]
