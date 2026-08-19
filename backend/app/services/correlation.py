"""Phase 3 — Empirical Returns Correlation Engine & Portfolio Gate.

Computes exact Pearson correlation matrices over date-aligned daily PnL vectors.
Guards the portfolio against self-correlated duplicates with an internal threshold (rho >= 0.55),
with fallback to structural hashing when empirical PnL is unavailable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha, SubmissionAttempt
from app.models.enums import AlphaStatus
from app.services.filter_config import DEFAULT_FILTER_CONFIG, FilterConfig
from app.services.pnl_storage import PnLStore, get_pnl_store

log = structlog.get_logger("correlation")


def submitted_portfolio(db: Session, exclude_alpha_id: int | None = None) -> list[Alpha]:
    """The only definition of 'portfolio' the correlation gate may use.

    An alpha is in the portfolio iff it has a SubmissionAttempt with
    result == 'submitted' and is_recalled is False.
    AlphaStatus.PASSED means 'BRAIN scored it', which is a property of a simulation,
    not of a portfolio.
    """
    q = (
        select(Alpha)
        .join(SubmissionAttempt, SubmissionAttempt.alpha_id == Alpha.id)
        .where(
            SubmissionAttempt.result == "submitted",
            SubmissionAttempt.is_recalled.is_(False),
        )
        .distinct()
    )
    if exclude_alpha_id is not None:
        q = q.where(Alpha.id != exclude_alpha_id)
    return list(db.execute(q).scalars().all())


def compute_pairwise_correlation(arr1: np.ndarray, arr2: np.ndarray) -> float:
    """Compute signed Pearson correlation between two aligned 1D arrays."""
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
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
    threshold: float | None = None,
    min_overlap: int | None = None,
) -> tuple[bool, str | None, float | None]:
    """Check if candidate collides with any portfolio alpha via empirical PnL correlation.

    Uses signed correlation: strong negative correlation is beneficial diversification and passes.
    Insufficient trading day overlap fails closed (returns unmeasured/blocking).

    Returns (is_correlated, reason_or_collision_desc, max_correlation).
    """
    from app.services.plateau import check_portfolio_correlation as check_structural_proxy

    thresh = threshold if threshold is not None else cfg.portfolio_corr_threshold
    overlap = min_overlap if min_overlap is not None else cfg.min_common_days
    store = pnl_store or get_pnl_store()
    candidate = db.get(Alpha, alpha_id)
    if candidate is None:
        return False, None, None

    if portfolio is None:
        portfolio = submitted_portfolio(db, exclude_alpha_id=alpha_id)

    if not portfolio:
        return False, None, 0.0

    cand_pnl_data = store.load_pnl(alpha_id)

    max_corr = 0.0
    colliding_alpha_id: int | None = None
    measured_ids: set[int] = set()

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
            if len(common_dates) < overlap:
                continue

            measured_ids.add(port_alpha.id)

            c_vec = np.array([cand_date_map[d] for d in common_dates], dtype=np.float64)
            p_vec = np.array([port_date_map[d] for d in common_dates], dtype=np.float64)

            # Gate on signed Pearson correlation (not abs)
            rho = compute_pairwise_correlation(c_vec, p_vec)
            if rho > max_corr:
                max_corr = rho
                if rho >= thresh:
                    colliding_alpha_id = port_alpha.id

        if colliding_alpha_id is not None:
            return (
                True,
                f"empirical correlation {max_corr:.2f} with portfolio alpha #{colliding_alpha_id} exceeds threshold {thresh:.2f}",
                max_corr,
            )

    # The proxy is a stand-in for missing evidence, not a veto over evidence we
    # have. Alphas we actually measured and cleared are settled; only the ones we
    # could not measure fall through to the skeleton heuristic.
    unmeasured = [p for p in portfolio if p.id != alpha_id and p.id not in measured_ids]
    if unmeasured:
        is_struct_corr, struct_collision = check_structural_proxy(
            db, alpha_id, portfolio=unmeasured
        )
        if is_struct_corr:
            return True, struct_collision, max_corr

    return False, None, max_corr


def ensure_alpha_pnl(
    db: Session,
    alpha_id: int,
    pnl_store: PnLStore | None = None,
    *,
    allow_remote_fetch: bool = False,
) -> bool:
    """Check if daily PnL vector is stored locally, optionally fetching from BRAIN if missing."""
    store = pnl_store or get_pnl_store()
    if store.load_pnl(alpha_id) is not None:
        return True

    if not allow_remote_fetch:
        return False

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
    min_overlap: int = 500,
) -> tuple[float | None, int | None, str]:
    """Compute max correlation against confirmed submitted alphas (submission_attempts with result='submitted').

    Returns (max_correlation, target_alpha_id, method), where method is 'empirical',
    'structural_proxy', 'unmeasured', or 'none'.
    Returns None for max_correlation when PnL series is unmeasured (never fabricates synthetic constants).
    """
    from app.services.plateau import check_portfolio_correlation as check_structural_proxy

    store = pnl_store or get_pnl_store()
    candidate = db.get(Alpha, alpha_id)
    if candidate is None:
        return None, None, "none"

    submitted_alphas = submitted_portfolio(db, exclude_alpha_id=alpha_id)
    if not submitted_alphas:
        return 0.0, None, "none"

    cand_pnl_data = store.load_pnl(alpha_id)

    max_corr = 0.0
    colliding_alpha_id: int | None = None
    had_empirical_match = False

    if cand_pnl_data is not None:
        cand_dates, cand_pnl = cand_pnl_data
        cand_date_map = dict(zip(cand_dates, cand_pnl))

        for sub_alpha in submitted_alphas:
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

            rho = compute_pairwise_correlation(c_vec, s_vec)
            had_empirical_match = True
            if rho >= max_corr:
                max_corr = rho
                colliding_alpha_id = sub_alpha.id

    if had_empirical_match:
        return max_corr, colliding_alpha_id, "empirical"

    # Fallback to structural proxy check
    is_struct_corr, struct_collision = check_structural_proxy(db, alpha_id, portfolio=submitted_alphas)
    target_id = submitted_alphas[0].id if submitted_alphas else None
    if is_struct_corr:
        return None, target_id, "structural_proxy"

    return None, None, "unmeasured"
