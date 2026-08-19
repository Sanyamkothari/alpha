"""The Production Feedback Loop: Out-of-Sample Decay Tracking (F1).

Monitors realized live/paper performance from AlphaProductionSnapshot against in-sample backtest
metrics (AlphaMetric.sharpe), computing realized decay and triggering alerts when degradation
exceeds tolerance thresholds:
    decay_pct = (Sharpe_backtest - Sharpe_production) / Sharpe_backtest

**Decay alone is a number; the filter question is a verdict.** The reason this module
exists is not to report that alphas decay — they do, and STRATEGY.md §6 already
assumes ~26%, a figure we inherited rather than measured. It exists to answer
*does our filter predict decay?* That requires joining each alpha's realised decay to
the statistics it was promoted on: DSR, plateau ratio, PBO, N_eff. If DSR at
promotion time does not correlate with realised out-of-sample decay across our own
alphas, the DSR threshold is decoration and should be changed or dropped.

``build_filter_calibration`` produces that join. It is deliberately descriptive: with
a handful of live alphas the correlation it reports is not yet evidence, and the
function says so rather than implying a verdict it cannot support.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha, AlphaProductionSnapshot
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric

log = structlog.get_logger("feedback_loop")


@dataclass(frozen=True)
class DecayReport:
    alpha_id: int
    expression: str
    family_key: str | None
    backtest_sharpe: float
    production_sharpe: float | None
    production_status: str
    as_of_date: date | None
    decay_pct: float | None  # [0.0, 1.0+]
    is_decayed: bool  # True if decay_pct > max_decay_pct or production_sharpe < min_sharpe
    alert: str | None = None


def evaluate_production_decay(
    db: Session,
    alpha_id: int,
    *,
    max_decay_pct: float = 0.50,
    min_prod_sharpe: float = 0.50,
) -> DecayReport | None:
    """Evaluate one submitted alpha's realized decay against its latest production snapshot."""
    alpha = db.get(Alpha, alpha_id)
    if alpha is None:
        return None

    # Load latest backtest metric
    metric = db.execute(
        select(AlphaMetric)
        .where(AlphaMetric.alpha_id == alpha_id)
        .order_by(AlphaMetric.id.desc())
    ).scalars().first()

    backtest_sharpe = metric.sharpe if (metric and metric.sharpe is not None) else 0.0

    # Load latest production snapshot
    snapshot = db.execute(
        select(AlphaProductionSnapshot)
        .where(AlphaProductionSnapshot.alpha_id == alpha_id)
        .order_by(AlphaProductionSnapshot.as_of_date.desc())
    ).scalars().first()

    if snapshot is None:
        return DecayReport(
            alpha_id=alpha_id,
            expression=alpha.expression,
            family_key=alpha.family_key,
            backtest_sharpe=backtest_sharpe,
            production_sharpe=None,
            production_status="unknown",
            as_of_date=None,
            decay_pct=None,
            is_decayed=False,
            alert="no_production_snapshots",
        )

    prod_sharpe = snapshot.sharpe
    if prod_sharpe is None or backtest_sharpe <= 0:
        decay_pct = None
        is_decayed = False
        alert = "insufficient_data"
    else:
        decay_pct = max(0.0, (backtest_sharpe - prod_sharpe) / backtest_sharpe)
        is_decayed = (decay_pct > max_decay_pct) or (prod_sharpe < min_prod_sharpe)
        alert = (
            f"decay_alert(decay={decay_pct:.1%} > {max_decay_pct:.0%})"
            if decay_pct > max_decay_pct
            else (
                f"low_prod_sharpe({prod_sharpe:.2f} < {min_prod_sharpe:.2f})"
                if prod_sharpe < min_prod_sharpe
                else None
            )
        )

    return DecayReport(
        alpha_id=alpha_id,
        expression=alpha.expression,
        family_key=alpha.family_key,
        backtest_sharpe=backtest_sharpe,
        production_sharpe=prod_sharpe,
        production_status=snapshot.status,
        as_of_date=snapshot.as_of_date,
        decay_pct=decay_pct,
        is_decayed=is_decayed,
        alert=alert,
    )


def evaluate_all_production_alphas(
    db: Session,
    *,
    max_decay_pct: float = 0.50,
    min_prod_sharpe: float = 0.50,
) -> list[DecayReport]:
    """Scan all submitted or tracked alphas and report their production degradation."""
    alphas = db.execute(
        select(Alpha.id).where(Alpha.status == AlphaStatus.SUBMITTED.value)
    ).scalars().all()

    reports: list[DecayReport] = []
    for aid in alphas:
        r = evaluate_production_decay(db, aid, max_decay_pct=max_decay_pct, min_prod_sharpe=min_prod_sharpe)
        if r is not None:
            reports.append(r)
    return reports


# Below this many live alphas, a correlation between predicted and realised quality is
# not evidence of anything. Reported anyway, labelled, so the number of alphas needed
# is visible rather than assumed.
MIN_ALPHAS_FOR_CALIBRATION = 8


@dataclass(frozen=True)
class FilterCalibrationRow:
    """One live alpha, its realised decay, and what the filter believed at promotion."""

    alpha_id: int
    expression: str
    family_key: str | None
    backtest_sharpe: float
    production_sharpe: float | None
    decay_pct: float | None
    # Promotion-time beliefs
    dsr: float | None
    plateau_ratio: float | None
    neighbour_median_sharpe: float | None
    family_pbo: float | None
    neutralization_retention: float | None


@dataclass(frozen=True)
class FilterCalibration:
    rows: list[FilterCalibrationRow]
    n_with_decay: int
    dsr_vs_decay_corr: float | None
    plateau_vs_decay_corr: float | None
    sufficient: bool
    note: str


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx <= 1e-12 or dy <= 1e-12:
        return None
    return num / (dx * dy)


def build_filter_calibration(db: Session) -> FilterCalibration:
    """Join realised decay to promotion-time statistics, per live alpha.

    Re-evaluates each alpha's family so the promotion-time statistics come from the
    same code path that made the decision, rather than from a snapshot that may have
    drifted. This is a read-only analysis; nothing here writes.
    """
    from app.services.plateau import evaluate

    reports = evaluate_all_production_alphas(db)
    verdict_cache: dict[str, dict[int, object]] = {}
    rows: list[FilterCalibrationRow] = []

    for rep in reports:
        verdict = None
        if rep.family_key:
            if rep.family_key not in verdict_cache:
                try:
                    verdict_cache[rep.family_key] = {
                        v.alpha_id: v for v in evaluate(db, rep.family_key)
                    }
                except Exception as exc:  # noqa: BLE001 - one bad family must not stop the sweep
                    log.warning("calibration_family_failed", family=rep.family_key, error=str(exc))
                    verdict_cache[rep.family_key] = {}
            verdict = verdict_cache[rep.family_key].get(rep.alpha_id)

        rows.append(
            FilterCalibrationRow(
                alpha_id=rep.alpha_id,
                expression=rep.expression,
                family_key=rep.family_key,
                backtest_sharpe=rep.backtest_sharpe,
                production_sharpe=rep.production_sharpe,
                decay_pct=rep.decay_pct,
                dsr=getattr(verdict, "dsr", None),
                plateau_ratio=getattr(verdict, "plateau_ratio", None),
                neighbour_median_sharpe=getattr(verdict, "neighbour_median_sharpe", None),
                family_pbo=getattr(verdict, "family_pbo", None),
                neutralization_retention=getattr(verdict, "neutralization_retention", None),
            )
        )

    measurable = [r for r in rows if r.decay_pct is not None]

    def _corr(attr: str) -> float | None:
        pairs = [
            (getattr(r, attr), r.decay_pct)
            for r in measurable
            if getattr(r, attr) is not None and r.decay_pct is not None
        ]
        if len(pairs) < 3:
            return None
        return _pearson([p[0] for p in pairs], [p[1] for p in pairs])

    sufficient = len(measurable) >= MIN_ALPHAS_FOR_CALIBRATION
    note = (
        f"{len(measurable)} alphas with realised decay — enough to read the "
        "predicted-vs-realised relationship."
        if sufficient
        else (
            f"{len(measurable)} alphas with realised decay; need "
            f"{MIN_ALPHAS_FOR_CALIBRATION}. Correlations below are descriptive only "
            "and are not evidence about the filter yet."
        )
    )

    return FilterCalibration(
        rows=rows,
        n_with_decay=len(measurable),
        # A filter that works should show DSR *negatively* correlated with decay:
        # higher confidence at promotion time, less decay realised.
        dsr_vs_decay_corr=_corr("dsr"),
        plateau_vs_decay_corr=_corr("plateau_ratio"),
        sufficient=sufficient,
        note=note,
    )
