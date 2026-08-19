"""Stage 4 — the honest filter (STRATEGY.md Rule 5).

Mass simulation makes overfitting the *default* outcome, not a risk.
Five sequential gates, cheapest first:

1. **Plateau, not peak.** Judge a candidate by the median score of its neighbours
   on the (window, decay) surface. A lone spike is a coincidence; a broad ridge is a mechanism.
2. **Pre-declared bar.** BRAIN's own ``checks[]``, which the alpha already carries.
3. **Hard PnL Reconciliation Precondition.** Loaded daily PnL must reconcile with reported Sharpe.
4. **Sub-period stability & Lo (2002) SE Z-tests.** Statistical significance against sampling error.
5. **Deflated Sharpe Ratio (DSR) & EVT Multiple-testing haircut.**
6. **Intra-family clustering & Ridge Center Representative Election.**
7. **Empirical Correlation Gate (< 0.55).** Evaluated on elected representatives against submitted portfolio.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dc_field
from statistics import median
from typing import TYPE_CHECKING, Any, Sequence

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric
from app.services.constructor import (
    STANDARD_DECAYS,
    STANDARD_WINDOWS,
    WIDE_DECAYS,
    WIDE_WINDOWS,
)
from app.services.correlation import (
    check_portfolio_empirical_correlation,
    compute_pairwise_correlation,
    submitted_portfolio,
)
from app.services.filter_config import (
    DEFAULT_FILTER_CONFIG,
    TRADING_DAYS_PER_YEAR,
    FilterConfig,
)
from app.services.pnl_storage import PnLStore, get_pnl_store
from app.services.trials import TrialLedger, build_ledger, expected_max_normal

log = structlog.get_logger("plateau")

FALLBACK_WINDOW_LADDER: tuple[int, ...] = (5, 10, 22, 63, 126, 252)
FALLBACK_DECAY_LADDER: tuple[int, ...] = (0, 4, 8, 16)

# Back-compat aliases — the display no longer uses these. Remove after one release.
WINDOW_LADDER = FALLBACK_WINDOW_LADDER
DECAY_LADDER = FALLBACK_DECAY_LADDER

BASE_SHARPE_BAR: float = DEFAULT_FILTER_CONFIG.target_sharpe
PLATEAU_RATIO: float = DEFAULT_FILTER_CONFIG.plateau_ratio


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
    ridge_score: float | None = None
    plateau_ratio: float | None = None
    is_plateau: bool = False
    neighbours_simulated: int = 0
    neighbours_possible: int = 0
    clears_bar: bool = False
    haircut_bar: float = 0.0
    is_correlated: bool = False
    correlation_collision: str | None = None
    promoted: bool = False
    family_size: int = 0
    dsr: float | None = None
    dsr_passed: bool | None = None
    gate_mode: str = "DSR"
    subperiod_passed: bool | None = None
    redundant_with: int | None = None
    reasons: list[str] = dc_field(default_factory=list)
    config_fingerprint: str = ""


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

        port_features = port_alpha.feature_json or {}
        port_struct = port_features.get("structural_hash")
        port_field = family_field_code(port_alpha.family_key or "") if port_alpha.family_key else None

        # Structural hash match on the same base field => near-certain self-correlation
        if cand_struct and port_struct and cand_struct == port_struct and cand_field == port_field:
            kind = (
                "submitted"
                if port_alpha.status == AlphaStatus.SUBMITTED.value
                else "portfolio"
            )
            return True, f"structural correlation collision with {kind} alpha #{port_alpha.id}"

        # Same family as an already submitted alpha
        if candidate.family_key and port_alpha.family_key and candidate.family_key == port_alpha.family_key:
            if port_alpha.status == AlphaStatus.SUBMITTED.value:
                return True, f"family collision with submitted alpha #{port_alpha.id} ({port_alpha.family_key})"

    return False, None


def _structure_of(grid: dict) -> tuple:
    """Everything that is NOT a swept plateau axis."""
    return (
        grid.get("ts"),
        grid.get("cs"),
        grid.get("group"),
        grid.get("neutralization"),
        grid.get("truncation"),
        grid.get("universe"),
        grid.get("hump"),
    )


def family_field_code(family_key: str) -> str:
    """The data field a family was built on."""
    prefix = family_key.split("@", 1)[0]
    prefix = prefix.split("+", 1)[0]
    prefix = prefix.split(":", 1)[0]
    prefix = prefix.split("/", 1)[0]
    return prefix


def load_surface(
    db: Session, family_key: str, *, include_unsimulated: bool = False
) -> list[SurfacePoint]:
    """Points on a family's grid, selecting the latest metric by max(id)."""
    join = db.execute(
        select(Alpha, AlphaMetric)
        .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .where(Alpha.family_key == family_key)
        .order_by(Alpha.id, AlphaMetric.id.desc().nulls_last())
    ).all()

    # Explicitly pick latest AlphaMetric per Alpha by highest ID
    latest: dict[int, tuple[Alpha, AlphaMetric | None]] = {}
    for alpha, metric in join:
        if alpha.id not in latest:
            if metric is not None or include_unsimulated:
                latest[alpha.id] = (alpha, metric)

    points: list[SurfacePoint] = []
    for alpha, metric in latest.values():
        m_sharpe = metric.sharpe if metric else None
        m_fitness = metric.fitness if metric else None
        m_turnover = metric.turnover if metric else None
        m_passed = metric.passed_all_checks if metric else None
        grid = (alpha.feature_json or {}).get("grid") or {}
        if "window" not in grid:
            continue
        points.append(
            SurfacePoint(
                alpha_id=alpha.id,
                expression=alpha.expression,
                window=int(grid["window"]),
                decay=int(grid.get("decay", 0)),
                sharpe=m_sharpe,
                fitness=m_fitness,
                turnover=m_turnover,
                passed_all_checks=m_passed,
                structure=_structure_of(grid),
            )
        )
    return points


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


def _neighbours(point: SurfacePoint, surface: list[SurfacePoint]) -> tuple[list[SurfacePoint], int]:
    """Simulated neighbours one step away on the surface coordinate grid."""
    windows = sorted({p.window for p in surface if p.window is not None})
    decays = sorted({p.decay for p in surface if p.decay is not None})
    if not windows:
        windows = list(FALLBACK_WINDOW_LADDER)
    if not decays:
        decays = list(FALLBACK_DECAY_LADDER)

    try:
        wi = windows.index(point.window)
        di = decays.index(point.decay)
    except ValueError:
        return [], 0
    wanted = set()
    for step in (-1, 1):
        if 0 <= wi + step < len(windows):
            wanted.add((windows[wi + step], point.decay))
        if 0 <= di + step < len(decays):
            wanted.add((point.window, decays[di + step]))
    found = [
        p
        for p in surface
        if p.structure == point.structure and (p.window, p.decay) in wanted and p.sharpe is not None
    ]
    return found, len(wanted)


def _select_representatives(
    db: Session,
    verdicts: list[Verdict],
    surface: list[SurfacePoint],
    pnl_store: PnLStore | None = None,
    *,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
) -> None:
    """Keep one promoted point per *correlated* ridge cluster. Mutates in place.

    Grouping is by measured daily-PnL correlation, not by structural skeleton.
    The skeleton buckets windows coarsely, so one ridge routinely straddles a
    bucket boundary: grouping on it splits a single mechanism into several groups
    and promotes a near-duplicate out of each. Measured correlation is the thing
    the portfolio actually cares about, and it is already on disk.

    Election is by ``ridge_score`` — the median over the point and its simulated
    neighbours — never by the point's own Sharpe. This is the election that
    decides which single alpha reaches the operator, and the peak of a ridge is
    by construction the point carrying the largest positive error.

    Where a pair cannot be measured, the skeleton is the fallback: an unmeasurable
    pair is grouped rather than assumed independent.
    """
    from app.services.clustering import cluster_family

    promoted = [v for v in verdicts if v.promoted]
    if len(promoted) < 2:
        return

    store = pnl_store or get_pnl_store()
    structure_of = {p.alpha_id: p.structure for p in surface}

    fallback_key: dict[int, object] = {}
    for v in promoted:
        alpha = db.get(Alpha, v.alpha_id)
        skeleton = (alpha.feature_json or {}).get("structural_hash") if alpha else None
        fallback_key[v.alpha_id] = skeleton or ("structure", structure_of.get(v.alpha_id))

    clusters = cluster_family(
        promoted, store, cfg=cfg, unmeasured_fallback_key=fallback_key
    )

    v_by_id = {v.alpha_id: v for v in promoted}
    for cluster in clusters:
        keeper = v_by_id.get(cluster.representative_id)
        if keeper is None:
            continue
        for aid in cluster.member_ids:
            if aid == cluster.representative_id:
                continue
            other = v_by_id.get(aid)
            if other is None:
                continue
            other.promoted = False
            other.redundant_with = keeper.alpha_id
            other.reasons.append(
                f"clustered into representative #{keeper.alpha_id} "
                f"(intra-cluster rho {cluster.intra_max_corr:.2f}) — "
                f"{cluster.election_reason}"
            )


def haircut_bar(
    n_trials_or_ledger: int | TrialLedger,
    *,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
) -> float:
    """Required Sharpe floor based on EVT asymptotic expected maximum."""
    if isinstance(n_trials_or_ledger, TrialLedger):
        n_eff = n_trials_or_ledger.n_eff
        window_days = n_trials_or_ledger.window_days
    else:
        n_eff = float(max(1, n_trials_or_ledger))
        window_days = cfg.backtest_days

    se = math.sqrt(TRADING_DAYS_PER_YEAR / max(252, window_days))
    evt_max = expected_max_normal(n_eff)
    return float(cfg.target_sharpe + se * evt_max)


def evaluate(
    db: Session,
    family_key: str,
    portfolio: list[Alpha] | None = None,
    pnl_store: PnLStore | None = None,
    *,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
    ledger: TrialLedger | None = None,
) -> list[Verdict]:
    """Score every simulated point in a family using the honest statistical gate stack."""
    from app.services.clustering import cluster_family
    from app.services.correlation import (
        check_portfolio_empirical_correlation,
        submitted_portfolio,
    )
    from app.services.pnl_storage import get_pnl_store
    from app.services.subperiod import (
        compute_dsr,
        evaluate_subperiod_stability,
        verify_pnl_reconciliation,
    )

    surface = load_surface(db, family_key)
    family_sharpes = [p.sharpe for p in surface if p.sharpe is not None]
    simulated_count = len(family_sharpes)

    store = pnl_store or get_pnl_store()
    trial_ledger = ledger or build_ledger(db, store, cfg=cfg)

    from app.services.family_matrix import build_family_matrix
    from app.services.correlation import compute_correlation_matrix
    from app.services.subperiod import compute_effective_trials

    fam_matrix = build_family_matrix(db, family_key, pnl_store=store)
    fam_n_eff = None
    if fam_matrix is not None and fam_matrix.matrix.shape[0] >= 2:
        corr_mat = compute_correlation_matrix(fam_matrix.matrix)
        fam_n_eff = compute_effective_trials(corr_mat)

    # The bar deflates for the trials the WINNER was selected from -- every alpha
    # this programme has simulated -- so it takes the programme ledger. Passing the
    # family's own N_eff here is the original F4 defect: 49 points at rho ~= 0.9
    # collapse to N_eff ~= 1.2, and the "multiple-testing bar" lands at 1.38,
    # BELOW the flat 1.25 + 0.10*log10(N) it replaced. fam_n_eff keeps its one
    # legitimate job below: correcting sigma_SR inside the DSR.
    bar = haircut_bar(trial_ledger, cfg=cfg)

    if portfolio is None:
        portfolio = submitted_portfolio(db)

    verdicts: list[Verdict] = []

    for point in surface:
        reasons: list[str] = []
        neigh, possible = _neighbours(point, surface)
        values = [p.sharpe for p in neigh if p.sharpe is not None]
        neigh_median = float(median(values)) if values else None

        ratio = None
        if neigh_median is not None and point.sharpe:
            ratio = neigh_median / point.sharpe

        judgeable = len(values) >= cfg.min_neighbours_to_judge
        positive = bool(point.sharpe is not None and point.sharpe > 0)
        is_plateau = bool(
            judgeable and positive and ratio is not None and ratio >= cfg.plateau_ratio
        )

        # Ridge score: median of own Sharpe and simulated neighbours
        all_local_sharpes = [point.sharpe] + values if point.sharpe is not None else values
        ridge_score = float(median(all_local_sharpes)) if all_local_sharpes else None

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
            reasons.append(f"below EVT haircut bar {bar:.2f}")

        # 3. Hard PnL Reconciliation Precondition
        pnl_data = store.load_pnl(point.alpha_id)
        dsr_val: float | None = None
        dsr_passed = False
        subperiod_passed = False
        reconciled = False

        if pnl_data is not None:
            _, daily_pnl = pnl_data

            # Check Sharpe reconciliation against reported metric
            if point.sharpe is not None:
                rec = verify_pnl_reconciliation(
                    point.alpha_id,
                    point.sharpe,
                    store,
                    sharpe_tolerance=cfg.sharpe_reconciliation_tolerance,
                )
                reconciled = rec.is_valid
                if not reconciled:
                    reasons.append(
                        f"PnL failed Sharpe reconciliation (recomputed {rec.recomputed_sharpe:.2f} vs reported {rec.reported_sharpe:.2f})"
                    )

            # 4. Sub-Period Stability
            sub_res = evaluate_subperiod_stability(daily_pnl, cfg=cfg)
            subperiod_passed = sub_res.passed
            if not subperiod_passed:
                reasons.extend(sub_res.reasons)

            # 5. Deflated Sharpe Ratio (DSR) using program ledger and family N_eff
            daily_sharpes = [s / math.sqrt(TRADING_DAYS_PER_YEAR) for s in family_sharpes]
            dsr_val = compute_dsr(
                daily_pnl,
                daily_sharpes,
                n_eff=fam_n_eff if fam_n_eff is not None else trial_ledger.n_eff,
                sigma_sr_override=trial_ledger.sigma_sr_daily,
            )

            alpha_obj = db.get(Alpha, point.alpha_id)
            is_re_promoting = bool(alpha_obj and getattr(alpha_obj, "is_rewatched", False))
            target_dsr_hurdle = (
                cfg.dsr_re_promotion_threshold if is_re_promoting else cfg.dsr_threshold
            )
            dsr_passed = dsr_val >= target_dsr_hurdle
            if not dsr_passed:
                reasons.append(f"DSR {dsr_val:.3f} below {target_dsr_hurdle:.2f} threshold")
        else:
            reasons.append("no daily PnL series — statistical gating pending")
            subperiod_passed = False
            dsr_passed = False
            reconciled = False

        survives_statistical_gates = (
            clears
            and is_plateau
            and above_bar
            and reconciled
            and dsr_passed
            and subperiod_passed
        )

        is_corr, corr_collision, max_corr = check_portfolio_empirical_correlation(
            db, point.alpha_id, pnl_store=store, portfolio=portfolio, cfg=cfg
        )
        if is_corr and corr_collision:
            reasons.append(corr_collision)

        promoted = bool(survives_statistical_gates and not is_corr)

        v = Verdict(
            alpha_id=point.alpha_id,
            expression=point.expression,
            sharpe=point.sharpe,
            fitness=point.fitness,
            neighbour_median_sharpe=neigh_median,
            ridge_score=ridge_score,
            plateau_ratio=ratio,
            is_plateau=is_plateau,
            neighbours_simulated=len(values),
            neighbours_possible=possible,
            clears_bar=clears,
            haircut_bar=bar,
            is_correlated=is_corr,
            correlation_collision=corr_collision,
            promoted=promoted,
            family_size=simulated_count,
            dsr=dsr_val,
            dsr_passed=dsr_passed,
            gate_mode="DSR",
            subperiod_passed=subperiod_passed,
            redundant_with=None,
            reasons=reasons,
            config_fingerprint=cfg.fingerprint(),
        )
        verdicts.append(v)

    _select_representatives(db, verdicts, surface, store, cfg=cfg)

    verdicts.sort(
        key=lambda v: (
            v.promoted,
            v.ridge_score if v.ridge_score is not None else -99.0,
            v.neighbour_median_sharpe if v.neighbour_median_sharpe is not None else -99.0,
            v.sharpe if v.sharpe is not None else -99.0,
        ),
        reverse=True,
    )
    log.info(
        "family_evaluated",
        family=family_key,
        points=len(surface),
        promoted=sum(1 for v in verdicts if v.promoted),
        redundant=sum(1 for v in verdicts if v.redundant_with is not None),
        gate_mode="DSR",
    )
    return verdicts


# Request-scoped evaluation cache for Decision D3
_EVAL_CACHE: dict[tuple[str, str], list[Verdict]] = {}


def evaluate_families(
    db: Session,
    family_keys: Sequence[str],
    *,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
    pnl_store: PnLStore | None = None,
    portfolio: list[Alpha] | None = None,
    use_cache: bool = True,
) -> dict[str, list[Verdict]]:
    """Batch evaluation of multiple families with request-scoped caching."""
    store = pnl_store or get_pnl_store()
    port = portfolio if portfolio is not None else submitted_portfolio(db)
    ledger = build_ledger(db, store, cfg=cfg)

    fp = cfg.fingerprint()
    results: dict[str, list[Verdict]] = {}

    for fkey in family_keys:
        cache_key = (fkey, fp)
        if use_cache and cache_key in _EVAL_CACHE:
            results[fkey] = _EVAL_CACHE[cache_key]
        else:
            v_list = evaluate(db, fkey, portfolio=port, pnl_store=store, cfg=cfg, ledger=ledger)
            if use_cache:
                _EVAL_CACHE[cache_key] = v_list
            results[fkey] = v_list

    return results


def clear_eval_cache() -> None:
    """Clear request-scoped evaluation cache."""
    _EVAL_CACHE.clear()
