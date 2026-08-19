"""Phase 2 — Sub-Period Stability, Regime Decay & Deflated Sharpe Ratio (DSR).

Implements:
1. Deflated Sharpe Ratio (DSR) based on Bailey & Lopez de Prado (2014) operating in
   daily frequency units with Euler-Mascheroni expected maximum SR*.
2. Effective independent trials (N_eff) estimation via correlation matrix eigenvalues.
3. Lo (2002) asymptotic standard error significance tests for split-half stability and regime decay.
4. Monthly-stepped 6-month rolling window positivity checks (>= 70%).
5. Strict standing PnL Sharpe reconciliation against reported metrics.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from scipy import stats

from app.services.filter_config import DEFAULT_FILTER_CONFIG, TRADING_DAYS_PER_YEAR, FilterConfig

if TYPE_CHECKING:
    pass

log = structlog.get_logger("subperiod")

# Euler-Mascheroni constant
EULER_MASCHERONI = 0.5772156649015328606065120900824024310421


@dataclass
class SubPeriodVerdict:
    passed: bool
    h1_sharpe: float | None = None
    h2_sharpe: float | None = None
    split_ratio: float | None = None
    split_half_z: float | None = None
    rolling_positive_ratio: float | None = None
    recent_sharpe: float | None = None
    prior_sharpe: float | None = None
    full_sharpe: float | None = None
    recent_decay_z: float | None = None
    reasons: list[str] = field(default_factory=list)


def sharpe_standard_error(sr_daily: float, n_obs: int) -> float:
    """Asymptotic standard error of the annualized Sharpe ratio (Lo 2002).

    Var(SR_daily) ≈ (1 + 0.5 * SR_daily^2) / n_obs
    SE(SR_annual) = sqrt(Var(SR_daily)) * sqrt(TRADING_DAYS_PER_YEAR)
    """
    if n_obs <= 2:
        return 1.0
    var_daily = (1.0 + 0.5 * (sr_daily**2)) / float(n_obs)
    return float(math.sqrt(max(1e-12, var_daily)) * math.sqrt(TRADING_DAYS_PER_YEAR))


def compute_effective_trials(correlation_matrix: np.ndarray) -> float:
    """Estimate the effective number of independent trials (N_eff) from eigenvalues.

    N_eff = (sum(lambda_i))^2 / sum(lambda_i^2) = M^2 / sum(lambda_i^2)
    """
    if correlation_matrix.ndim != 2 or correlation_matrix.shape[0] != correlation_matrix.shape[1]:
        return float(max(1, len(correlation_matrix)))

    m = correlation_matrix.shape[0]
    if m <= 1:
        return 1.0

    try:
        eigenvals = np.linalg.eigvalsh(correlation_matrix)
        eigenvals = np.clip(eigenvals, a_min=0.0, a_max=None)
        sum_sq = float(np.sum(eigenvals**2))
        if sum_sq > 0:
            n_eff = float((m**2) / sum_sq)
            return max(1.0, min(float(m), n_eff))
    except Exception as exc:
        log.warning("neff_eigenval_failed", error=str(exc))

    return float(m)


def compute_dsr(
    daily_pnl: np.ndarray,
    family_daily_sharpes: Sequence[float],
    n_eff: float | None = None,
    sigma_sr_override: float | None = None,
) -> float:
    """Compute Deflated Sharpe Ratio (DSR) using daily non-annualized returns.

    Follows Bailey & Lopez de Prado (2014) equation 8 with Euler-Mascheroni expected maximum.
    """
    arr = np.asarray(daily_pnl, dtype=np.float64)
    t = len(arr)
    if t < 30:
        return 0.0

    std = float(np.std(arr, ddof=1))
    if std <= 1e-12:
        return 0.0

    sr_daily = float(np.mean(arr) / std)

    # Skewness and Kurtosis of daily returns
    gamma_3 = float(stats.skew(arr))
    gamma_4 = float(stats.kurtosis(arr, fisher=False))  # Pearson kurtosis (normal = 3)

    sharpes_clean = [s for s in family_daily_sharpes if math.isfinite(s)]
    n_trials = n_eff if (n_eff is not None and n_eff >= 1.0) else float(max(1, len(sharpes_clean)))
    if sigma_sr_override is not None and sigma_sr_override > 1e-12:
        sigma_sr = sigma_sr_override
    else:
        sigma_sr = float(np.std(sharpes_clean, ddof=1)) if len(sharpes_clean) > 1 else 0.0

    if sigma_sr <= 1e-12 or n_trials <= 1.0:
        # Single trial case
        sr_star = 0.0
    else:
        # Euler-Mascheroni expected maximum
        p1 = stats.norm.ppf(1.0 - (1.0 / n_trials))
        p2 = stats.norm.ppf(1.0 - (1.0 / (n_trials * math.e)))
        sr_star = sigma_sr * ((1.0 - EULER_MASCHERONI) * p1 + EULER_MASCHERONI * p2)

    denom_sq = 1.0 - gamma_3 * sr_daily + ((gamma_4 - 1.0) / 4.0) * (sr_daily**2)
    if denom_sq <= 0:
        return 0.0

    z = (sr_daily - sr_star) * math.sqrt(t - 1.0) / math.sqrt(denom_sq)
    dsr_value = float(stats.norm.cdf(z))
    return float(np.clip(dsr_value, 0.0, 1.0))


def evaluate_subperiod_stability(
    daily_pnl: Sequence[float] | np.ndarray,
    min_days: int = 252,
    split_ratio_floor: float | None = None,
    rolling_pos_floor: float | None = None,
    recent_decay_floor: float | None = None,
    *,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
    split_half_z_floor: float | None = None,
    recent_decay_z_floor: float | None = None,
) -> SubPeriodVerdict:
    """Evaluate split-half consistency, monthly-stepped rolling windows, and recent decay via Z-tests."""
    arr = np.asarray(daily_pnl, dtype=np.float64)
    t = len(arr)
    reasons: list[str] = []

    z_floor_half = split_half_z_floor if split_half_z_floor is not None else cfg.split_half_z_floor
    z_floor_decay = recent_decay_z_floor if recent_decay_z_floor is not None else cfg.recent_decay_z_floor
    pos_floor = rolling_pos_floor if rolling_pos_floor is not None else cfg.rolling_pos_floor

    if t < min_days:
        return SubPeriodVerdict(
            passed=False, reasons=[f"insufficient backtest days ({t} < {min_days})"]
        )

    # 1. Full period annualized Sharpe
    full_mean = float(np.mean(arr))
    full_std = float(np.std(arr, ddof=1))
    full_sr_d = (full_mean / full_std) if full_std > 1e-12 else 0.0
    full_sharpe = full_sr_d * math.sqrt(TRADING_DAYS_PER_YEAR)

    # 2. Split-Half Test (SE-difference hypothesis test)
    mid = t // 2
    h1, h2 = arr[:mid], arr[mid:]
    std1, std2 = float(np.std(h1, ddof=1)), float(np.std(h2, ddof=1))
    sr1_d = (float(np.mean(h1)) / std1) if std1 > 1e-12 else 0.0
    sr2_d = (float(np.mean(h2)) / std2) if std2 > 1e-12 else 0.0
    sr1 = sr1_d * math.sqrt(TRADING_DAYS_PER_YEAR)
    sr2 = sr2_d * math.sqrt(TRADING_DAYS_PER_YEAR)

    se1 = sharpe_standard_error(sr1_d, len(h1))
    se2 = sharpe_standard_error(sr2_d, len(h2))
    se_diff_half = math.sqrt(se1**2 + se2**2)
    split_half_z = (sr2 - sr1) / se_diff_half if se_diff_half > 1e-12 else 0.0

    split_ratio = min(sr1, sr2) / max(sr1, sr2) if (sr1 > 0 and sr2 > 0 and max(sr1, sr2) > 0) else 0.0

    # Sign guard
    if (sr1 / se1 < -1.0) or (sr2 / se2 < -1.0) or sr1 < cfg.min_split_half_sr_floor or sr2 < cfg.min_split_half_sr_floor:
        reasons.append(f"non-positive split-half Sharpe (H1={sr1:.2f}, H2={sr2:.2f})")
    elif split_ratio_floor is not None and split_ratio < split_ratio_floor:
        reasons.append(f"split-half ratio {split_ratio:.2f} below floor {split_ratio_floor:.2f}")
    elif split_half_z < z_floor_half:
        reasons.append(
            f"statistically significant split-half decay (H1={sr1:.2f} -> H2={sr2:.2f}, z={split_half_z:.2f} < {z_floor_half:.2f})"
        )

    # 3. Monthly-stepped 6-month (126 trading days) Rolling Window
    win_len = 126
    step = 21
    rolling_sharpes: list[float] = []

    if t >= win_len:
        for start in range(0, t - win_len + 1, step):
            sub = arr[start : start + win_len]
            s_std = float(np.std(sub, ddof=1))
            s_sr = (float(np.mean(sub)) / s_std * math.sqrt(TRADING_DAYS_PER_YEAR)) if s_std > 1e-12 else 0.0
            rolling_sharpes.append(s_sr)

    pos_count = sum(1 for s in rolling_sharpes if s > 0.0)
    rolling_pos_ratio = (pos_count / len(rolling_sharpes)) if rolling_sharpes else 0.0

    if rolling_sharpes and rolling_pos_ratio < pos_floor:
        reasons.append(
            f"rolling 6-month positive ratio {rolling_pos_ratio:.1%} below floor {pos_floor:.1%}"
        )

    # 4. Recent Regime Decay: compare recent 252d against prior non-overlapping window
    recent_decay_z = None
    rec_sharpe = None
    prior_sharpe = None

    if t >= 504:  # At least 2 years of backtest
        recent_pnl = arr[-252:]
        prior_pnl = arr[:-252]
        rec_std = float(np.std(recent_pnl, ddof=1))
        prior_std = float(np.std(prior_pnl, ddof=1))
        rec_sr_d = (float(np.mean(recent_pnl)) / rec_std) if rec_std > 1e-12 else 0.0
        prior_sr_d = (float(np.mean(prior_pnl)) / prior_std) if prior_std > 1e-12 else 0.0
        rec_sharpe = rec_sr_d * math.sqrt(TRADING_DAYS_PER_YEAR)
        prior_sharpe = prior_sr_d * math.sqrt(TRADING_DAYS_PER_YEAR)

        se_rec = sharpe_standard_error(rec_sr_d, len(recent_pnl))
        se_prior = sharpe_standard_error(prior_sr_d, len(prior_pnl))
        se_diff_decay = math.sqrt(se_rec**2 + se_prior**2)
        recent_decay_z = (rec_sharpe - prior_sharpe) / se_diff_decay if se_diff_decay > 1e-12 else 0.0

        if recent_decay_floor is not None and full_sharpe > 0 and (rec_sharpe < recent_decay_floor * full_sharpe):
            reasons.append(f"decayed below {int(recent_decay_floor * 100)}% of full Sharpe")
        elif recent_decay_z < z_floor_decay:
            reasons.append(
                f"recent 252d Sharpe decayed significantly (prior={prior_sharpe:.2f} -> recent={rec_sharpe:.2f}, z={recent_decay_z:.2f} < {z_floor_decay:.2f})"
            )

    passed = len(reasons) == 0
    return SubPeriodVerdict(
        passed=passed,
        h1_sharpe=sr1,
        h2_sharpe=sr2,
        split_ratio=split_ratio,
        split_half_z=split_half_z,
        rolling_positive_ratio=rolling_pos_ratio,
        recent_sharpe=rec_sharpe,
        prior_sharpe=prior_sharpe,
        full_sharpe=full_sharpe,
        recent_decay_z=recent_decay_z,
        reasons=reasons,
    )


@dataclass
class PnLReconciliationResult:
    alpha_id: int
    is_valid: bool
    recomputed_sharpe: float
    reported_sharpe: float
    sharpe_diff: float
    pnl_sum: float
    error: str | None = None


def verify_pnl_reconciliation(
    alpha_id: int,
    reported_sharpe: float,
    pnl_store: Any,
    sharpe_tolerance: float = 0.10,
) -> PnLReconciliationResult:
    """Standing acceptance check: reconciles loaded daily PnL array against reported metrics."""
    loaded = pnl_store.load_pnl(alpha_id)
    if loaded is None:
        return PnLReconciliationResult(
            alpha_id=alpha_id,
            is_valid=False,
            recomputed_sharpe=0.0,
            reported_sharpe=reported_sharpe,
            sharpe_diff=reported_sharpe,
            pnl_sum=0.0,
            error="no daily PnL array found in store",
        )
    dates, arr = loaded
    if len(arr) < 30:
        return PnLReconciliationResult(
            alpha_id=alpha_id,
            is_valid=False,
            recomputed_sharpe=0.0,
            reported_sharpe=reported_sharpe,
            sharpe_diff=reported_sharpe,
            pnl_sum=float(np.sum(arr)),
            error="insufficient observations in daily PnL array",
        )
    std = float(np.std(arr, ddof=1))
    recomputed_sr = (float(np.mean(arr)) / std * math.sqrt(TRADING_DAYS_PER_YEAR)) if std > 1e-12 else 0.0
    diff = abs(recomputed_sr - reported_sharpe)
    is_valid = diff <= sharpe_tolerance
    return PnLReconciliationResult(
        alpha_id=alpha_id,
        is_valid=is_valid,
        recomputed_sharpe=recomputed_sr,
        reported_sharpe=reported_sharpe,
        sharpe_diff=diff,
        pnl_sum=float(np.sum(arr)),
        error=None if is_valid else f"recomputed Sharpe {recomputed_sr:.4f} deviates from reported {reported_sharpe:.4f}",
    )
