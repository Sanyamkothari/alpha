"""The Production Feedback Loop: Out-of-Sample Decay Tracking (F1).

Monitors realized live/paper performance from AlphaProductionSnapshot against in-sample backtest
metrics (AlphaMetric.sharpe), computing realized decay and triggering alerts when degradation
exceeds tolerance thresholds:
    decay_pct = (Sharpe_backtest - Sharpe_production) / Sharpe_backtest
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
