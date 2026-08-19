"""Sharpe, significance and Deflated Sharpe Ratio.

AlphaNova admits a signal to the quality set only if it "demonstrates a
statistically significant positive Sharpe" and "passes automated checks for
data leakage and overfitting". The first is a t-test; the second is what DSR
is for -- a raw Sharpe selected as the best of many trials is biased upward,
and DSR deflates it by the expected maximum of that trial set.

``compute_dsr`` and ``compute_effective_trials`` are ported from the sibling
BRAIN research engine (``backend/app/services/subperiod.py``), which
implements Bailey & Lopez de Prado (2014). The maths is platform-agnostic;
only the units change (there, daily PnL; here, per-period signal returns).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

EULER_MASCHERONI = 0.5772156649015328606065120900824024310421

# Below this many periods, no statistic here is trustworthy.
MIN_PERIODS = 30


@dataclass(frozen=True)
class SharpeTest:
    """Outcome of the "statistically significant positive Sharpe" gate."""

    sharpe: float
    """Per-period (non-annualised) Sharpe ratio."""
    t_stat: float
    p_value: float
    """One-sided p-value against H0: Sharpe <= 0."""
    n_periods: int
    significant: bool

    def annualised(self, periods_per_year: float) -> float:
        return self.sharpe * math.sqrt(periods_per_year)


def sharpe_ratio(returns: np.ndarray) -> float:
    """Per-period Sharpe of a return series. 0.0 when undefined."""
    arr = np.asarray(returns, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return 0.0
    std = float(np.std(arr, ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(np.mean(arr) / std)


def test_sharpe_significance(returns: np.ndarray, alpha: float = 0.05) -> SharpeTest:
    """One-sided t-test that the Sharpe ratio is positive.

    Uses t = SR * sqrt(T), the standard large-sample statistic for an i.i.d.
    return series. It ignores autocorrelation, so a signal whose returns are
    strongly serially correlated will look more significant than it is --
    ``evaluate_stability`` is the cross-check for that.
    """
    arr = np.asarray(returns, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    t = len(arr)
    sr = sharpe_ratio(arr)

    if t < MIN_PERIODS:
        return SharpeTest(sharpe=sr, t_stat=0.0, p_value=1.0, n_periods=t, significant=False)

    t_stat = sr * math.sqrt(t)
    p_value = float(stats.t.sf(t_stat, df=t - 1))
    return SharpeTest(
        sharpe=sr,
        t_stat=float(t_stat),
        p_value=p_value,
        n_periods=t,
        significant=bool(p_value < alpha and sr > 0),
    )


def compute_effective_trials(correlation_matrix: np.ndarray) -> float:
    """Effective number of independent trials from a correlation matrix's eigenvalues.

    N_eff = (sum lambda_i)^2 / sum(lambda_i^2) = M^2 / sum(lambda_i^2)

    Ten variants of one idea are not ten trials. Feeding N_eff to ``compute_dsr``
    instead of a raw count is what stops DSR over-deflating a correlated family.
    """
    cm = np.asarray(correlation_matrix, dtype=np.float64)
    if cm.ndim != 2 or cm.shape[0] != cm.shape[1]:
        return float(max(1, len(cm)))

    m = cm.shape[0]
    if m <= 1:
        return 1.0

    eigenvals = np.linalg.eigvalsh(cm)
    eigenvals = np.clip(eigenvals, a_min=0.0, a_max=None)
    sum_sq = float(np.sum(eigenvals**2))
    if sum_sq <= 0:
        return float(m)
    return max(1.0, min(float(m), float((m**2) / sum_sq)))


def compute_dsr(
    returns: np.ndarray,
    trial_sharpes: list[float] | np.ndarray,
    n_eff: float | None = None,
) -> float:
    """Deflated Sharpe Ratio -- Bailey & Lopez de Prado (2014), equation 8.

    ``returns`` are this signal's per-period returns. ``trial_sharpes`` are the
    Sharpes of every variant tried while arriving at it; their dispersion sets
    how high the selection bias reaches. Pass ``n_eff`` from
    ``compute_effective_trials`` when those variants are correlated.

    Returns a probability in [0, 1] that the true Sharpe exceeds the expected
    maximum of the trial set. Reports 0.0 rather than raising when the input is
    too short or degenerate to say anything.
    """
    arr = np.asarray(returns, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    t = len(arr)
    if t < MIN_PERIODS:
        return 0.0

    std = float(np.std(arr, ddof=1))
    if std <= 1e-12:
        return 0.0

    sr = float(np.mean(arr) / std)
    gamma_3 = float(stats.skew(arr))
    gamma_4 = float(stats.kurtosis(arr, fisher=False))  # Pearson kurtosis (normal = 3)

    sharpes_clean = [float(s) for s in trial_sharpes if math.isfinite(float(s))]
    n_trials = n_eff if (n_eff is not None and n_eff >= 1.0) else float(max(1, len(sharpes_clean)))
    sigma_sr = float(np.std(sharpes_clean, ddof=1)) if len(sharpes_clean) > 1 else 0.0

    if sigma_sr <= 1e-12 or n_trials <= 1.0:
        sr_star = 0.0
    else:
        p1 = stats.norm.ppf(1.0 - (1.0 / n_trials))
        p2 = stats.norm.ppf(1.0 - (1.0 / (n_trials * math.e)))
        sr_star = sigma_sr * ((1.0 - EULER_MASCHERONI) * p1 + EULER_MASCHERONI * p2)

    denom_sq = 1.0 - gamma_3 * sr + ((gamma_4 - 1.0) / 4.0) * (sr**2)
    if denom_sq <= 0:
        return 0.0

    z = (sr - sr_star) * math.sqrt(t - 1.0) / math.sqrt(denom_sq)
    return float(np.clip(float(stats.norm.cdf(z)), 0.0, 1.0))


@dataclass(frozen=True)
class StabilityVerdict:
    passed: bool
    first_half_sharpe: float
    second_half_sharpe: float
    rolling_positive_ratio: float
    reasons: tuple[str, ...] = ()


def evaluate_stability(
    returns: np.ndarray,
    *,
    window: int = 26,
    min_periods: int = MIN_PERIODS,
    rolling_positive_floor: float = 0.55,
) -> StabilityVerdict:
    """Split-half and rolling-window consistency.

    A signal that earns everything in one stretch and nothing afterwards will
    still post a decent full-sample Sharpe, and will then die in the one-month
    live window that decides the cycle. Both halves should work.
    """
    arr = np.asarray(returns, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    t = len(arr)
    reasons: list[str] = []

    if t < min_periods:
        return StabilityVerdict(False, 0.0, 0.0, 0.0, (f"only {t} periods (< {min_periods})",))

    mid = t // 2
    h1, h2 = sharpe_ratio(arr[:mid]), sharpe_ratio(arr[mid:])
    if h1 <= 0 or h2 <= 0:
        reasons.append(f"half-sample Sharpe not positive in both halves ({h1:.2f}, {h2:.2f})")

    if t >= window:
        sums = np.convolve(arr, np.ones(window), mode="valid")
        positive_ratio = float(np.mean(sums > 0))
    else:
        positive_ratio = 1.0 if float(np.sum(arr)) > 0 else 0.0
    if positive_ratio < rolling_positive_floor:
        reasons.append(
            f"only {positive_ratio:.0%} of {window}-period windows positive (< {rolling_positive_floor:.0%})"
        )

    return StabilityVerdict(
        passed=not reasons,
        first_half_sharpe=h1,
        second_half_sharpe=h2,
        rolling_positive_ratio=positive_ratio,
        reasons=tuple(reasons),
    )
