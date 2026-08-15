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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric

if TYPE_CHECKING:
    # Runtime import stays inside evaluate() to keep the module import graph flat.
    from app.services.pnl_storage import PnLStore

log = structlog.get_logger("plateau")

# Neighbour steps along each swept axis. Ordered so "adjacent" means one step.
WINDOW_LADDER: tuple[int, ...] = (5, 10, 22, 63, 126, 252)
DECAY_LADDER: tuple[int, ...] = (0, 4, 8, 16)

# A candidate must hold at least this fraction of its own score across its
# neighbourhood to count as a plateau rather than a spike.
PLATEAU_RATIO = 0.6

# Baseline sanity floor
BASE_SHARPE_BAR = 1.25
HAIRCUT_PER_LOG10 = 0.10
COLD_START_SHARPE_BAR = 1.50
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
    promoted: bool = False
    family_size: int = 0
    dsr: float | None = None
    dsr_passed: bool | None = None
    gate_mode: str = "COLD_START_FALLBACK"
    subperiod_passed: bool | None = None
    reasons: list[str] = dc_field(default_factory=list)


def check_portfolio_correlation(
    db: Session, alpha_id: int, portfolio: list[Alpha] | None = None
) -> tuple[bool, str | None]:
    """Check whether candidate collides structurally with any already-submitted alpha."""
    candidate = db.get(Alpha, alpha_id)
    if candidate is None:
        return False, None

    if portfolio is None:
        portfolio = list(
            db.execute(
                select(Alpha).where(
                    Alpha.status.in_([AlphaStatus.SUBMITTED.value, AlphaStatus.PASSED.value]),
                    Alpha.id != alpha_id,
                )
            )
            .scalars()
            .all()
        )

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

        # Same family as an already submitted alpha
        if candidate.family_key and port_alpha.family_key and candidate.family_key == port_alpha.family_key:
            if port_alpha.status == AlphaStatus.SUBMITTED.value:
                return True, f"family collision with submitted alpha #{port_alpha.id} ({port_alpha.family_key})"

    return False, None


def _structure_of(grid: dict) -> tuple:
    """Everything that is NOT a swept plateau axis. Points sharing a structure
    lie on the same surface and are therefore comparable."""
    return (
        grid.get("ts"),
        grid.get("cs"),
        grid.get("group"),
        grid.get("neutralization"),
        grid.get("truncation"),
    )


def family_field_code(family_key: str) -> str:
    """The data field a family was built on."""
    return family_key.split("@", 1)[0].split("/", 1)[0]


def load_surface(
    db: Session, family_key: str, *, include_unsimulated: bool = False
) -> list[SurfacePoint]:
    """Points on a family's grid."""
    join = db.execute(
        select(Alpha, AlphaMetric)
        .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .where(Alpha.family_key == family_key)
        .order_by(AlphaMetric.id)
    ).all()
    rows = [(a, m) for a, m in join if m is not None or include_unsimulated]

    latest: dict[int, tuple] = {}
    for alpha, metric in rows:
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


MIN_NEIGHBOURS_TO_JUDGE = 2


def _neighbours(point: SurfacePoint, surface: list[SurfacePoint]) -> tuple[list[SurfacePoint], int]:
    """Simulated neighbours one step away, and how many COULD exist."""
    # Dynamically resolve coordinate ladders from the surface points
    windows = sorted({p.window for p in surface if p.window is not None})
    decays = sorted({p.decay for p in surface if p.decay is not None})
    if not windows:
        windows = list(WINDOW_LADDER)
    if not decays:
        decays = list(DECAY_LADDER)

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
        portfolio = list(
            db.execute(
                select(Alpha).where(
                    Alpha.status.in_([AlphaStatus.SUBMITTED.value, AlphaStatus.PASSED.value])
                )
            )
            .scalars()
            .all()
        )

    pnl_store = pnl_store or get_pnl_store()

    # Scope DSR activation to slice-level trial count (neighbourhood & multiple testing alignment)
    by_slice: dict[tuple, list[SurfacePoint]] = defaultdict(list)
    for p in surface:
        if p.sharpe is not None:
            by_slice[p.structure].append(p)
    max_slice_trials = max((len(pts) for pts in by_slice.values()), default=0)
    use_dsr = max_slice_trials >= MIN_TRIALS_FOR_DSR
    gate_mode = "DSR" if use_dsr else "COLD_START_FALLBACK"

    verdicts: list[Verdict] = []

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
            dsr_val = compute_dsr(daily_pnl, daily_sharpes)
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
                subperiod_passed = True
                dsr_passed = bool(
                    point.sharpe is not None
                    and point.sharpe >= (DSR_PROMOTION_THRESHOLD if use_dsr else COLD_START_SHARPE_BAR)
                    and (point.fitness is None or point.fitness >= 1.0)
                )

        # Correlation gate (empirical with structural fallback)
        is_corr, corr_collision, max_corr = check_portfolio_empirical_correlation(
            db, point.alpha_id, pnl_store=pnl_store, portfolio=portfolio
        )
        if is_corr and corr_collision:
            reasons.append(corr_collision)

        promoted = clears and is_plateau and above_bar and dsr_passed and subperiod_passed and not is_corr

        verdicts.append(
            Verdict(
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
                is_correlated=is_corr,
                correlation_collision=corr_collision,
                promoted=promoted,
                family_size=simulated_count,
                dsr=dsr_val,
                dsr_passed=dsr_passed,
                gate_mode=gate_mode,
                subperiod_passed=subperiod_passed,
                reasons=reasons,
            )
        )

    verdicts.sort(
        key=lambda v: (v.promoted, v.sharpe if v.sharpe is not None else -99), reverse=True
    )
    log.info(
        "family_evaluated",
        family=family_key,
        points=len(surface),
        promoted=sum(1 for v in verdicts if v.promoted),
        gate_mode=gate_mode,
    )
    return verdicts
