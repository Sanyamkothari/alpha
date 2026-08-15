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


def ensure_alpha_pnl(db: Session, alpha_id: int, pnl_store: PnLStore | None = None) -> bool:
    """Ensure an alpha's daily PnL series is cached in PnLStore, fetching from BRAIN if needed."""
    store = pnl_store or get_pnl_store()
    if store.load_pnl(alpha_id) is not None:
        return True

    # Try to find a known remote brain_id from simulation_imports
    from app.models.results import SimulationImport

    sim_import = (
        db.execute(select(SimulationImport).where(SimulationImport.alpha_id == alpha_id))
        .scalars()
        .first()
    )
    remote_id = None
    if sim_import and isinstance(sim_import.raw_payload, dict):
        remote_id = sim_import.raw_payload.get("id") or sim_import.raw_payload.get("alpha_id")
    if not remote_id:
        alpha = db.get(Alpha, alpha_id)
        if alpha and hasattr(alpha, "brain_id"):
            remote_id = getattr(alpha, "brain_id", None)
    if not remote_id:
        return False

    try:
        from app.services.brain import BrainClient

        with BrainClient() as brain:
            pnl_resp = brain.get_json(f"/alphas/{remote_id}/recordsets/daily-pnl")
            records = pnl_resp.get("records", [])
            if records:
                dates = [str(r[0]) for r in records]
                pnl = np.array([float(r[1]) for r in records], dtype=float)
                store.save_pnl(alpha_id, dates, pnl)
                return True
    except Exception as exc:
        log.warning("on_demand_pnl_fetch_failed", alpha_id=alpha_id, remote_id=remote_id, error=str(exc))

    return False


def compute_max_self_correlation_with_submitted(
    db: Session,
    alpha_id: int,
    *,
    pnl_store: PnLStore | None = None,
    min_overlap: int = MIN_COMMON_TRADING_DAYS,
) -> tuple[float | None, int | None, str]:
    """Compute max correlation against confirmed submitted alphas (submission_attempts with result='submitted').

    Returns (max_correlation, target_alpha_id, method), where method is 'empirical' or 'structural_proxy'.
    Never returns blank/unknown if a comparison target exists.
    """
    from app.models.alphas import SubmissionAttempt

    store = pnl_store or get_pnl_store()
    candidate = db.get(Alpha, alpha_id)
    if candidate is None:
        return None, None, "none"

    # Confirmed submitted alphas via submission_attempts (User Directive 1: Single Source of Truth)
    submitted_alphas = list(
        db.execute(
            select(Alpha)
            .join(SubmissionAttempt, SubmissionAttempt.alpha_id == Alpha.id)
            .where(SubmissionAttempt.result == "submitted", Alpha.id != alpha_id)
        )
        .scalars()
        .all()
    )

    if not submitted_alphas:
        return 0.0, None, "none"

    # Ensure candidate PnL is present (User Directive 2)
    ensure_alpha_pnl(db, alpha_id, store)
    cand_pnl_data = store.load_pnl(alpha_id)

    max_corr = 0.0
    colliding_alpha_id: int | None = None
    had_empirical_match = False

    if cand_pnl_data is not None:
        cand_dates, cand_pnl = cand_pnl_data
        cand_date_map = dict(zip(cand_dates, cand_pnl))

        for sub_alpha in submitted_alphas:
            ensure_alpha_pnl(db, sub_alpha.id, store)
            sub_pnl_data = store.load_pnl(sub_alpha.id)
            if sub_pnl_data is None:
                continue

            sub_dates, sub_pnl = sub_pnl_data
            sub_date_map = dict(zip(sub_dates, sub_pnl))

            common_dates = sorted(set(cand_dates).intersection(sub_dates))
            if len(common_dates) < min_overlap:
                continue

            c_vec = np.array([cand_date_map[d] for d in common_dates], dtype=np.float64)
            s_vec = np.array([sub_date_map[d] for d in common_dates], dtype=np.float64)

            rho = abs(compute_pairwise_correlation(c_vec, s_vec))
            had_empirical_match = True
            if rho >= max_corr:
                max_corr = rho
                colliding_alpha_id = sub_alpha.id

    if had_empirical_match:
        return max_corr, colliding_alpha_id, "empirical"

    # Fallback to structural proxy check (User Directive 2)
    is_struct_corr, struct_collision = check_structural_proxy(db, alpha_id, portfolio=submitted_alphas)
    target_id = submitted_alphas[0].id if submitted_alphas else None
    if is_struct_corr:
        # Near-duplicate / same family structural proxy estimate
        return 0.85, target_id, "structural_proxy"

    # Default proxy baseline for different structure
    return 0.20, target_id, "structural_proxy"
