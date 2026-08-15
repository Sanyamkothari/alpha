"""Phase 3 — Empirical Returns Correlation Engine & Portfolio Gate.

Computes exact Pearson correlation matrices over date-aligned daily PnL vectors.
Guards the portfolio against self-correlated duplicates with an internal threshold (< 0.55),
with fallback to structural hashing when empirical PnL is unavailable.
"""

from __future__ import annotations

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.services.plateau import check_portfolio_correlation as check_structural_proxy
from app.services.pnl_storage import PnLStore, get_pnl_store

log = structlog.get_logger("correlation")

# Stricter internal threshold than BRAIN's 0.70 to preserve safety buffer
INTERNAL_CORRELATION_THRESHOLD = 0.55
MIN_COMMON_TRADING_DAYS = 500


def compute_pairwise_correlation(arr1: np.ndarray, arr2: np.ndarray) -> float:
    """Compute Pearson correlation between two aligned 1D arrays."""
    if len(arr1) != len(arr2) or len(arr1) < 10:
        return 0.0
    res = np.corrcoef(arr1, arr2)
    val = float(res[0, 1])
    return val if np.isfinite(val) else 0.0


def compute_correlation_matrix(matrix: np.ndarray) -> np.ndarray:
    """Compute full (N x N) Pearson correlation matrix from (N x T) returns."""
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] < 10:
        return np.empty((0, 0), dtype=np.float64)
    corr = np.corrcoef(matrix)
    return np.nan_to_num(corr, nan=0.0)


def check_portfolio_empirical_correlation(
    db: Session,
    alpha_id: int,
    *,
    pnl_store: PnLStore | None = None,
    portfolio: list[Alpha] | None = None,
    threshold: float = INTERNAL_CORRELATION_THRESHOLD,
    min_overlap: int = MIN_COMMON_TRADING_DAYS,
) -> tuple[bool, str | None, float | None]:
    """Check if candidate collides with any portfolio alpha via empirical PnL correlation.

    Returns (is_correlated, reason_or_collision_desc, max_correlation).
    """
    store = pnl_store or get_pnl_store()
    candidate = db.get(Alpha, alpha_id)
    if candidate is None:
        return False, None, None

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

    cand_pnl_data = store.load_pnl(alpha_id)

    max_corr = 0.0
    colliding_alpha_id: int | None = None

    if cand_pnl_data is not None:
        cand_dates, cand_pnl = cand_pnl_data
        cand_date_map = dict(zip(cand_dates, cand_pnl))

        for port_alpha in portfolio:
            if port_alpha.id == alpha_id:
                continue

            port_pnl_data = store.load_pnl(port_alpha.id)
            if port_pnl_data is None:
                continue

            port_dates, port_pnl = port_pnl_data
            port_date_map = dict(zip(port_dates, port_pnl))

            # Intersect dates
            common_dates = sorted(set(cand_dates).intersection(port_dates))
            if len(common_dates) < min_overlap:
                continue

            c_vec = np.array([cand_date_map[d] for d in common_dates], dtype=np.float64)
            p_vec = np.array([port_date_map[d] for d in common_dates], dtype=np.float64)

            rho = abs(compute_pairwise_correlation(c_vec, p_vec))
            if rho > max_corr:
                max_corr = rho
                if rho >= threshold:
                    colliding_alpha_id = port_alpha.id

        if colliding_alpha_id is not None:
            return (
                True,
                f"empirical correlation {max_corr:.2f} with portfolio alpha #{colliding_alpha_id} exceeds threshold {threshold:.2f}",
                max_corr,
            )

    # Fallback to structural proxy check
    is_struct_corr, struct_collision = check_structural_proxy(db, alpha_id, portfolio=portfolio)
    if is_struct_corr:
        return True, struct_collision, max_corr

    return False, None, max_corr
