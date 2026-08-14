"""Stage 4 — the honest filter (STRATEGY.md Rule 5).

Mass simulation makes overfitting the *default* outcome, not a risk. A
400-member family will hand you a Sharpe 1.5 by luck alone. Without this module
the rest of the system is a machine for producing confident garbage faster, so
this is the piece not to cut.

Four tests, cheapest first:

1. **Plateau, not peak.** Judge a candidate by the median score of its
   neighbours on the (window, decay) surface. A lone spike surrounded by dead
   neighbours is a coincidence; a broad ridge is a mechanism.

       window:   5    10    22    63   126   252
       sharpe: 0.3   0.4   1.5   0.4   0.3   0.2   -> spike, discard
       sharpe: 0.9   1.2   1.4   1.3   1.1   0.8   -> plateau, promote

2. **Pre-declared bar.** BRAIN's own ``checks[]``, which the alpha already
   carries. Never re-tuned to fit a result we like.

3. **Multiple-testing haircut.** A winner drawn from 400 candidates needs a
   higher bar than one drawn from 20. Scaled by family size.

4. **Correlation gate.** BRAIN reports ``SELF_CORRELATION`` as ``PENDING`` at
   simulation time — it is only computed on submission, i.e. after the decision
   this filter exists to make. So the gate uses a *local* structural proxy
   instead. Anything else would be reading a field that is always empty.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field as dc_field
from statistics import median

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric

log = structlog.get_logger("plateau")

# Neighbour steps along each swept axis. Ordered so "adjacent" means one step.
WINDOW_LADDER: tuple[int, ...] = (5, 10, 22, 63, 126, 252)
DECAY_LADDER: tuple[int, ...] = (0, 4, 8, 16)

# A candidate must hold at least this fraction of its own score across its
# neighbourhood to count as a plateau rather than a spike.
PLATEAU_RATIO = 0.6

# Multiple-testing: required Sharpe uplift grows with log(family size).
BASE_SHARPE_BAR = 1.25
HAIRCUT_PER_LOG10 = 0.10


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
    reasons: list[str] = dc_field(default_factory=list)


def check_portfolio_correlation(
    db: Session, alpha_id: int, portfolio: list[Alpha] | None = None
) -> tuple[bool, str | None]:
    """Check whether candidate collides structurally with any already-submitted alpha.

    BRAIN accepts at most one variant of a signal. Submitting two alphas with
    the same structural skeleton and base field guarantees a self-correlation
    rejection on the second.
    """
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
    """The data field a family was built on.

    Keys look like ``liabilities/cap@USA/TOP3000/d1`` or ``assets@USA/TOP3000/d1``.
    The config suffix must be stripped BEFORE splitting on "/", otherwise a
    family with no denominator yields ``"assets@USA"`` — a field code that
    matches nothing, so its results silently vanish from dataset hit-rate.
    """
    return family_key.split("@", 1)[0].split("/", 1)[0]


def load_surface(
    db: Session, family_key: str, *, include_unsimulated: bool = False
) -> list[SurfacePoint]:
    """Points on a family's grid.

    By default only simulated points, because that is what the plateau maths
    operates on. ``include_unsimulated=True`` adds the emitted-but-not-yet-run
    candidates with ``sharpe=None`` — the UI needs those to draw a hole in the
    grid and to offer "simulate exactly these four neighbours", which is the
    recovery path out of "surface incomplete".
    """
    join = db.execute(
        select(Alpha, AlphaMetric)
        .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .where(Alpha.family_key == family_key)
        .order_by(AlphaMetric.id)
    ).all()
    rows = [(a, m) for a, m in join if m is not None or include_unsimulated]

    # One point per alpha, keeping the newest metric row. An alpha re-simulated
    # or re-imported has several rows, and without this each copy becomes its own
    # "neighbour" — inflating the multiple-testing bar and letting a point act as
    # its own supporting evidence. Ordering by metric id means the last write per
    # alpha wins.
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


# A plateau claim needs at least this many simulated neighbours. Two, not three:
# a corner cell of a COMPLETE surface has only two possible neighbours, and
# requiring three would make every corner permanently unjudgeable — while the
# highest-Sharpe cell is very often a corner.
MIN_NEIGHBOURS_TO_JUDGE = 2


def _neighbours(point: SurfacePoint, surface: list[SurfacePoint]) -> tuple[list[SurfacePoint], int]:
    """Simulated neighbours one step away, and how many COULD exist.

    The second number is the honest denominator. A (5, 0) corner has two
    possible neighbours, not four, so reporting "1 of 4 simulated" would send the
    operator hunting for two cells that do not exist.
    """
    try:
        wi = WINDOW_LADDER.index(point.window)
        di = DECAY_LADDER.index(point.decay)
    except ValueError:
        return [], 0
    wanted = set()
    for step in (-1, 1):
        if 0 <= wi + step < len(WINDOW_LADDER):
            wanted.add((WINDOW_LADDER[wi + step], point.decay))
        if 0 <= di + step < len(DECAY_LADDER):
            wanted.add((point.window, DECAY_LADDER[di + step]))
    found = [
        p
        for p in surface
        if p.structure == point.structure and (p.window, p.decay) in wanted and p.sharpe is not None
    ]
    return found, len(wanted)


def haircut_bar(family_size: int) -> float:
    """Required Sharpe, raised for the number of candidates searched.

    One winner out of 20 is mildly interesting; one out of 2,000 is what you
    would expect from noise. Growing the bar with log10(n) is crude but it is
    honest about the direction, and being crude here beats ignoring it.
    """
    if family_size <= 1:
        return BASE_SHARPE_BAR
    return BASE_SHARPE_BAR + HAIRCUT_PER_LOG10 * math.log10(family_size)


def evaluate(
    db: Session, family_key: str, *, portfolio: list[Alpha] | None = None
) -> list[Verdict]:
    """Score every simulated point in a family. Promoted ones survived all tests."""
    surface = load_surface(db, family_key)
    # The haircut must reflect how many candidates were SEARCHED, not how many
    # happen to be simulated so far. Using the simulated count makes the bar rise
    # as a run proceeds, so the same alpha can be promoted at 12 results and
    # rejected at 48 with nothing about it having changed.
    family_size = db.scalar(select(func.count(Alpha.id)).where(Alpha.family_key == family_key)) or 0
    bar = haircut_bar(max(family_size, len(surface)))

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

    verdicts: list[Verdict] = []

    for point in surface:
        reasons: list[str] = []
        neigh, possible = _neighbours(point, surface)
        values = [p.sharpe for p in neigh if p.sharpe is not None]
        neigh_median = median(values) if values else None

        ratio = None
        if neigh_median is not None and point.sharpe:
            ratio = neigh_median / point.sharpe

        # A plateau claim needs enough neighbours to BE a plateau. The median of
        # a single value is that value, so one neighbour at a similar score used
        # to read as a ridge — defeating the exact lone-spike-vs-mechanism
        # distinction this module exists to make.
        judgeable = len(values) >= MIN_NEIGHBOURS_TO_JUDGE
        # Sign matters: with a negative Sharpe the ratio inverts, so a bad alpha
        # whose neighbours are merely worse produces a large positive ratio and
        # would read as a plateau. A plateau is only meaningful above zero.
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

        above_haircut = bool(point.sharpe is not None and point.sharpe >= bar)
        if not above_haircut:
            reasons.append(f"below multiple-testing bar {bar:.2f}")

        is_corr, corr_collision = check_portfolio_correlation(db, point.alpha_id, portfolio=portfolio)
        if is_corr and corr_collision:
            reasons.append(corr_collision)

        promoted = clears and is_plateau and above_haircut and not is_corr
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
                family_size=family_size,
                reasons=reasons,
            )
        )

    # `or -99` would sort a Sharpe of exactly 0.0 below every negative result.
    verdicts.sort(
        key=lambda v: (v.promoted, v.sharpe if v.sharpe is not None else -99), reverse=True
    )
    log.info(
        "family_evaluated",
        family=family_key,
        points=len(surface),
        promoted=sum(1 for v in verdicts if v.promoted),
        bar=round(bar, 3),
    )
    return verdicts
