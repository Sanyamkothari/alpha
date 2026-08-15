"""Phase 2 — Sub-Period Stability, Regime Decay & Deflated Sharpe Ratio (DSR).

Implements:
1. Deflated Sharpe Ratio (DSR) based on Bailey & Lopez de Prado (2014) operating in
   daily frequency units with Euler-Mascheroni expected maximum SR*.
2. Effective independent trials (N_eff) estimation via correlation matrix eigenvalues.
3. Split-half sign guards and consistency ratios.
4. Monthly-stepped 6-month rolling window positivity checks (>= 75%).
5. Recent regime decay checks (last 252d vs full backtest).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats
import structlog

log = structlog.get_logger("subperiod")

# Euler-Mascheroni constant
EULER_MASCHERONI = 0.5772156649015328606065120900824024310421


@dataclass
class SubPeriodVerdict:
    passed: bool
    h1_sharpe: float | None = None
    h2_sharpe: float | None = None
    split_ratio: float | None = None
    rolling_positive_ratio: float | None = None
    recent_sharpe: float | None = None
    full_sharpe: float | None = None
    reasons: list[str] = field(default_factory=list)


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
    daily_pnl: np.ndarray,
    min_days: int = 252,
    split_ratio_floor: float = 0.40,
    rolling_pos_floor: float = 0.70,
    recent_decay_floor: float = 0.50,
) -> SubPeriodVerdict:
    """Evaluate split-half consistency, monthly-stepped rolling windows, and recent decay."""
    arr = np.asarray(daily_pnl, dtype=np.float64)
    t = len(arr)
    reasons: list[str] = []

    if t < min_days:
        return SubPeriodVerdict(
            passed=False, reasons=[f"insufficient backtest days ({t} < {min_days})"]
        )

    # 1. Full period annualized Sharpe
    full_mean = float(np.mean(arr))
    full_std = float(np.std(arr, ddof=1))
    full_sharpe = (full_mean / full_std * math.sqrt(252)) if full_std > 1e-12 else 0.0

    # 2. Split-Half Test
    mid = t // 2
    h1, h2 = arr[:mid], arr[mid:]
    std1, std2 = float(np.std(h1, ddof=1)), float(np.std(h2, ddof=1))
    sr1 = (float(np.mean(h1)) / std1 * math.sqrt(252)) if std1 > 1e-12 else 0.0
    sr2 = (float(np.mean(h2)) / std2 * math.sqrt(252)) if std2 > 1e-12 else 0.0

    # Sign check: hard reject on opposite or non-positive signs
    split_ratio = None
    if sr1 <= 0.0 or sr2 <= 0.0:
        reasons.append(f"non-positive split-half Sharpe (H1={sr1:.2f}, H2={sr2:.2f})")
    else:
        split_ratio = min(sr1, sr2) / max(sr1, sr2)
        if split_ratio < split_ratio_floor:
            reasons.append(f"split-half ratio {split_ratio:.2f} below floor {split_ratio_floor:.2f}")

    # 3. Monthly-stepped 6-month (126 trading days) Rolling Window
    win_len = 126
    step = 21
    rolling_sharpes: list[float] = []

    if t >= win_len:
        for start in range(0, t - win_len + 1, step):
            sub = arr[start : start + win_len]
            s_std = float(np.std(sub, ddof=1))
            s_sr = (float(np.mean(sub)) / s_std * math.sqrt(252)) if s_std > 1e-12 else 0.0
            rolling_sharpes.append(s_sr)

    pos_count = sum(1 for s in rolling_sharpes if s > 0.0)
    rolling_pos_ratio = (pos_count / len(rolling_sharpes)) if rolling_sharpes else 0.0

    if rolling_sharpes and rolling_pos_ratio < rolling_pos_floor:
        reasons.append(
            f"rolling 6-month positive ratio {rolling_pos_ratio:.1%} below floor {rolling_pos_floor:.1%}"
        )

    # 4. Recent Regime Decay (last 252 days)
    recent_pnl = arr[-252:]
    rec_std = float(np.std(recent_pnl, ddof=1))
    rec_sharpe = (float(np.mean(recent_pnl)) / rec_std * math.sqrt(252)) if rec_std > 1e-12 else 0.0

    if full_sharpe > 0 and (rec_sharpe < recent_decay_floor * full_sharpe):
        reasons.append(
            f"recent 252d Sharpe ({rec_sharpe:.2f}) decayed below {recent_decay_floor:.0%} of full ({full_sharpe:.2f})"
        )

    passed = len(reasons) == 0
    return SubPeriodVerdict(
        passed=passed,
        h1_sharpe=sr1,
        h2_sharpe=sr2,
        split_ratio=split_ratio,
        rolling_positive_ratio=rolling_pos_ratio,
        recent_sharpe=rec_sharpe,
        full_sharpe=full_sharpe,
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
    sharpe_tolerance: float = 0.05,
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
    recomputed_sr = (float(np.mean(arr)) / std * math.sqrt(252)) if std > 1e-12 else 0.0
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

