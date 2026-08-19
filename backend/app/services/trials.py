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
    """Programme-wide trial ledger, both statistics from one object.

    The object is the set of **family-maximum** alphas: for each family, the point
    with the highest Sharpe. That is what selection actually operates on -- nobody
    submits a family's average -- and estimating from it fixes two biases that
    used to pull in opposite directions and land in different gates:

    * ``sigma_sr`` came from per-family *mean* Sharpes. Averaging within a family
      shrinks dispersion by roughly 1/sqrt(n), which understates sigma_SR, lowers
      SR*, and makes the DSR more lenient. Maxima are the right population.

    * ``n_eff`` was a ratio extrapolated from <=100 family representatives out to
      every simulated alpha. But the population being extrapolated to is mostly
      within-family siblings at rho ~ 0.95, whose marginal contribution to the
      effective count is near zero, so the figure came out far too large and the
      bar with it. The effective number of independent MECHANISMS is what a
      "best of everything I tried" bar should deflate for, and it is measured
      directly here rather than scaled.

    ``n_trials`` stays the raw count -- it is reported, not used for deflation.
    """
    store = pnl_store or get_pnl_store()

    total_simulated = (
        db.scalar(
            select(func.count(func.distinct(Alpha.id)))
            .join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
        )
        or 1
    )

    # One row per family: the alpha selection would have picked, and its Sharpe.
    # Ordered so the sample -- and therefore the bar -- is reproducible.
    family_max = db.execute(
        select(
            Alpha.family_key,
            func.max(AlphaMetric.sharpe).label("best_sharpe"),
        )
        .join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
        .where(AlphaMetric.sharpe.is_not(None))
        .group_by(Alpha.family_key)
        .order_by(Alpha.family_key)
    ).all()

    best_sharpes = [float(r[1]) for r in family_max if r[1] is not None]
    if len(best_sharpes) > 1:
        sigma_sr_daily = float(np.std(best_sharpes, ddof=1)) / math.sqrt(TRADING_DAYS_PER_YEAR)
    else:
        # Conservative default cross-family Sharpe dispersion
        sigma_sr_daily = 0.35 / math.sqrt(TRADING_DAYS_PER_YEAR)

    # The alpha_ids behind those maxima, for the correlation matrix.
    rep_ids: list[int] = []
    for family_key, best in family_max:
        if best is None:
            continue
        aid = db.scalar(
            select(Alpha.id)
            .join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
            .where(Alpha.family_key.is_(None) if family_key is None else Alpha.family_key == family_key)
            .where(AlphaMetric.sharpe == best)
            .order_by(Alpha.id)
            .limit(1)
        )
        if aid is not None:
            rep_ids.append(int(aid))

    # Absent measurable PnL, the count of distinct mechanisms is a far better
    # stand-in than the count of alphas: 4,800 alphas in 100 families is not
    # 4,800 independent chances to be fooled.
    n_eff = float(max(1, len(rep_ids)))
    if len(rep_ids) > 1:
        valid_ids, _, matrix = store.get_aligned_matrix(rep_ids, min_overlap=300)
        if matrix.shape[0] > 1 and matrix.shape[1] >= 300:
            corr_mat = np.nan_to_num(np.corrcoef(matrix), nan=0.0)
            measured = compute_effective_trials(corr_mat)
            # Families we could not measure still count as their own trial.
            unmeasured = len(rep_ids) - matrix.shape[0]
            n_eff = max(1.0, float(measured + unmeasured))

    log.debug(
        "ledger_built",
        n_trials=total_simulated,
        families=len(family_max),
        n_eff=round(n_eff, 2),
        sigma_sr_daily=round(sigma_sr_daily, 6),
    )

    return TrialLedger(
        n_trials=total_simulated,
        n_eff=n_eff,
        sigma_sr_daily=sigma_sr_daily,
        window_days=cfg.backtest_days,
    )
