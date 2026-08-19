"""Combinatorially Symmetric Cross-Validation (CSCV) and PBO Engine (E1).

Computes the Probability of Backtest Overfitting (PBO) across a family of N strategies over T days
using sub-second vectorized numpy operations:
1. Divide T into S contiguous subperiods (S even, default S = 16).
2. Form C = comb(S, S/2) symmetric train/test partitions (comb(16, 8) = 12,870).
3. In-sample: Select best strategy n* = argmax Sharpe_IS.
4. Out-of-sample: Evaluate rank and return of n* on test partition.
5. PBO = fraction of splits where the IS winner lands in the bottom half OOS
   (relative rank <= 0.5, equivalently logit lambda <= 0). ``pbo_loss`` reports the
   stricter "IS winner actually lost money OOS" variant separately.

Degradation
-----------
CSCV is only meaningful when there are enough strategies to rank and enough days
per block to estimate a Sharpe from. Below either floor the result is returned with
``degraded=True`` and ``pbo=nan`` — never ``0.0``, which would read as *the best
possible score* for a family that is simply too small to evaluate.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
import numpy as np


# CSCV needs enough strategies for a rank to carry information, and enough days per
# block that a block Sharpe is an estimate rather than noise. S=16 with a 20-day floor
# implies ~640 trading days (~2.5 years) — comfortably inside BRAIN's 2019-onward window.
MIN_STRATEGIES = 4
MIN_BLOCK_LEN = 20


@dataclass(frozen=True)
class CSCVResult:
    pbo: float  # Probability of backtest overfitting (rank-based: P(OOS rank <= 0.5))
    pbo_loss: float  # Fraction where OOS Sharpe <= 0
    n_splits: int
    n_strategies: int
    n_subperiods: int
    median_oos_sharpe: float
    median_oos_rank: float
    degradation_pct: float  # (IS_Sharpe - OOS_Sharpe) / IS_Sharpe
    degraded: bool = False  # True when the input was too small to evaluate
    degraded_reason: str | None = None

    @property
    def reportable(self) -> bool:
        """Whether ``pbo`` is a number a reader may act on."""
        return not self.degraded and math.isfinite(self.pbo)


def compute_pbo_cscv(
    matrix: np.ndarray,
    *,
    n_subperiods: int = 16,
    trading_days_per_year: float = 252.0,
) -> CSCVResult:
    """Vectorized CSCV / PBO computation on N x T matrix.

    Args:
        matrix: N x T numpy array (N strategies, T time points).
        n_subperiods: Number of equal subperiods S (must be even, >= 4).
        trading_days_per_year: Annualization factor (default 252).
    """
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError(f"Expected 2D matrix (N x T), got {matrix.shape}")

    n_strategies, t_days = matrix.shape
    block_len = t_days // n_subperiods if n_subperiods > 0 else 0

    degraded_reason: str | None = None
    if n_subperiods < 4 or n_subperiods % 2 != 0:
        degraded_reason = f"n_subperiods must be even and >= 4, got {n_subperiods}"
    elif n_strategies < MIN_STRATEGIES:
        degraded_reason = f"only {n_strategies} strategies (need >= {MIN_STRATEGIES} to rank)"
    elif block_len < MIN_BLOCK_LEN:
        degraded_reason = (
            f"block length {block_len} < {MIN_BLOCK_LEN} days "
            f"({t_days} days over {n_subperiods} subperiods)"
        )

    if degraded_reason is not None:
        return CSCVResult(
            pbo=float("nan"),
            pbo_loss=float("nan"),
            n_splits=0,
            n_strategies=n_strategies,
            n_subperiods=n_subperiods,
            median_oos_sharpe=float("nan"),
            median_oos_rank=float("nan"),
            degradation_pct=float("nan"),
            degraded=True,
            degraded_reason=degraded_reason,
        )

    # Divide T into S contiguous slices and precompute subperiod sum and sum of squares
    usable_t = block_len * n_subperiods
    trimmed = matrix[:, :usable_t].reshape(n_strategies, n_subperiods, block_len)

    # Subperiod statistics: shape (N, S)
    sub_sums = np.sum(trimmed, axis=2)  # N x S
    sub_sq_sums = np.sum(trimmed**2, axis=2)  # N x S

    # Generate all combinations of S choose S/2
    k = n_subperiods // 2
    comb_indices = list(itertools.combinations(range(n_subperiods), k))
    n_splits = len(comb_indices)

    # Pre-build boolean selection matrix: shape (C, S)
    mask_is = np.zeros((n_splits, n_subperiods), dtype=bool)
    for i, idxs in enumerate(comb_indices):
        mask_is[i, idxs] = True
    mask_oos = ~mask_is

    # IS & OOS sums: (N, C)
    is_sum = np.dot(sub_sums, mask_is.T)
    is_sq = np.dot(sub_sq_sums, mask_is.T)
    oos_sum = np.dot(sub_sums, mask_oos.T)
    oos_sq = np.dot(sub_sq_sums, mask_oos.T)

    n_days_is = k * block_len
    n_days_oos = (n_subperiods - k) * block_len

    # Vectorized mean & variance: (N, C)
    is_mean = is_sum / n_days_is
    is_var = np.maximum(1e-12, (is_sq / n_days_is) - (is_mean**2))
    is_sharpe = (is_mean / np.sqrt(is_var)) * math.sqrt(trading_days_per_year)

    oos_mean = oos_sum / n_days_oos
    oos_var = np.maximum(1e-12, (oos_sq / n_days_oos) - (oos_mean**2))
    oos_sharpe = (oos_mean / np.sqrt(oos_var)) * math.sqrt(trading_days_per_year)

    # For each partition c in C: best strategy in IS
    best_is_idx = np.argmax(is_sharpe, axis=0)  # shape (C,)
    c_indices = np.arange(n_splits)

    selected_is_sharpes = is_sharpe[best_is_idx, c_indices]
    selected_oos_sharpes = oos_sharpe[best_is_idx, c_indices]

    # Out-of-sample ranks (1 to N, where 1 is worst, N is best)
    # argsort of argsort gives rank 0 to N-1
    oos_ranks = np.argsort(np.argsort(oos_sharpe, axis=0), axis=0)  # (N, C)
    selected_oos_ranks = (oos_ranks[best_is_idx, c_indices] + 1.0) / (n_strategies + 1.0)  # relative rank in (0, 1)

    pbo_rank = float(np.mean(selected_oos_ranks <= 0.5))
    pbo_loss = float(np.mean(selected_oos_sharpes <= 0.0))
    median_oos = float(np.median(selected_oos_sharpes))
    median_rank = float(np.median(selected_oos_ranks))
    mean_is = float(np.mean(selected_is_sharpes))
    mean_oos = float(np.mean(selected_oos_sharpes))

    degradation = float((mean_is - mean_oos) / max(1e-6, abs(mean_is)))

    return CSCVResult(
        pbo=pbo_rank,
        pbo_loss=pbo_loss,
        n_splits=n_splits,
        n_strategies=n_strategies,
        n_subperiods=n_subperiods,
        median_oos_sharpe=median_oos,
        median_oos_rank=median_rank,
        degradation_pct=degradation,
    )
