"""Program-wide trial ledger and Extreme Value Theory (EVT) multiple-testing corrections.

Maintains the global trial ledger across the entire simulation history (STRATEGY.md Rule 5)
and computes asymptotic expected maximum Sharpe ratios under the Gumbel distribution:
    E[max of N standard normals] ≈ sqrt(2 ln N) - (ln ln N + ln 4pi) / (2 sqrt(2 ln N))
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.results import AlphaMetric
from app.services.filter_config import DEFAULT_FILTER_CONFIG, TRADING_DAYS_PER_YEAR, FilterConfig
from app.services.pnl_storage import PnLStore, get_pnl_store
from app.services.subperiod import compute_effective_trials

log = structlog.get_logger("trials")


@dataclass
class TrialLedger:
    n_trials: int  # Total simulated alphas across the program lifetime
    n_eff: float  # Effective independent trials via cross-family eigenvalue decomposition
    sigma_sr_daily: float  # Cross-family daily Sharpe dispersion
    window_days: int  # Backtest window length in trading days


def expected_max_normal(n: float) -> float:
    """Expected maximum of N independent standard normal variables under Gumbel EVT.

    Formula:
        E[max_N] ≈ sqrt(2 ln N) - (ln ln N + ln 4pi) / (2 sqrt(2 ln N))
    """
    n_val = max(1.0, float(n))
    if n_val <= 1.0:
        return 0.0
    if n_val < 5.0:
        # Small sample empirical interpolation
        return float(0.5 * math.sqrt(2.0 * math.log(n_val)))

    log_n = math.log(n_val)
    sqrt_2_log_n = math.sqrt(2.0 * log_n)
    correction = (math.log(log_n) + math.log(4.0 * math.pi)) / (2.0 * sqrt_2_log_n)
    return float(sqrt_2_log_n - correction)


def build_ledger(
    db: Session,
    pnl_store: PnLStore | None = None,
    *,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
    lookback_days: int = 365,
) -> TrialLedger:
    """Construct program-wide trial ledger across simulated alphas."""
    store = pnl_store or get_pnl_store()

    # Total simulated alphas count
    total_simulated = (
        db.scalar(
            select(func.count(func.distinct(Alpha.id)))
            .join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
        )
        or 1
    )

    # Fetch cross-family simulated alphas to estimate cross-family Sharpe dispersion and N_eff
    family_metrics = (
        db.execute(
            select(Alpha.family_key, func.avg(AlphaMetric.sharpe), func.count(Alpha.id))
            .join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
            .where(AlphaMetric.sharpe.is_not(None))
            .group_by(Alpha.family_key)
        )
        .all()
    )

    sharpes_annual = [float(row[1]) for row in family_metrics if row[1] is not None]
    if len(sharpes_annual) > 1:
        sigma_annual = float(np.std(sharpes_annual, ddof=1))
        sigma_sr_daily = sigma_annual / math.sqrt(TRADING_DAYS_PER_YEAR)
    else:
        # Conservative default cross-family Sharpe dispersion
        sigma_sr_daily = 0.35 / math.sqrt(TRADING_DAYS_PER_YEAR)

    # Compute N_eff over stored PnL vectors (sample representative per family)
    sample_alpha_ids: list[int] = (
        db.execute(
            select(func.max(Alpha.id))
            .join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
            .group_by(Alpha.family_key)
            .limit(100)
        )
        .scalars()
        .all()
    )

    n_eff = float(total_simulated)
    if len(sample_alpha_ids) > 1:
        _, _, matrix = store.get_aligned_matrix(sample_alpha_ids, min_overlap=300)
        if matrix.shape[0] > 1 and matrix.shape[1] >= 300:
            corr_mat = np.nan_to_num(np.corrcoef(matrix), nan=0.0)
            n_eff_sample = compute_effective_trials(corr_mat)
            # Scale sample N_eff to total trials
            ratio = n_eff_sample / float(matrix.shape[0])
            n_eff = max(1.0, float(total_simulated) * ratio)

    return TrialLedger(
        n_trials=total_simulated,
        n_eff=n_eff,
        sigma_sr_daily=sigma_sr_daily,
        window_days=cfg.backtest_days,
    )
