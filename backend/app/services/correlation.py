"""Phase 3 — Empirical Returns Correlation Engine & Portfolio Gate.

Computes exact Pearson correlation matrices over date-aligned daily PnL vectors.
Guards the portfolio against self-correlated duplicates with an internal threshold (< 0.55),
with fallback to structural hashing when empirical PnL is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha, submitted_alpha_filter
from app.services.plateau import check_portfolio_correlation as check_structural_proxy
from app.services.pnl_storage import PnLStore, get_pnl_store

log = structlog.get_logger("correlation")

# Stricter internal threshold than BRAIN's 0.70 to preserve safety buffer
INTERNAL_CORRELATION_THRESHOLD = 0.55
MIN_COMMON_TRADING_DAYS = 500


@dataclass(frozen=True)
class CorrelationVerdict:
    """Outcome of the portfolio correlation gate.

    ``blocking`` is what the caller gates on. It is True both when a real
    collision was measured AND when the correlation could not be measured at
    all against a non-empty portfolio — an unmeasured constraint must fail
    closed, not open. ``max_correlation`` is None when nothing was measured;
    it is never 0.0-as-a-stand-in.
    """

    blocking: bool
    reason: str | None
    max_correlation: float | None
    method: str  # "empirical" | "structural_proxy" | "unmeasured" | "none"
    measured_pairs: int
    skipped_pairs: int
    portfolio_size: int


def _date_map(pnl_tuple: tuple[list[str], np.ndarray] | None) -> dict[str, float] | None:
    if pnl_tuple is None:
        return None
    dates, values = pnl_tuple
    if len(dates) != len(values):
        return None
    return dict(zip(dates, values, strict=True))


def submitted_portfolio(db: Session, exclude_alpha_id: int | None = None) -> list[Alpha]:
    """Query confirmed submitted portfolio alphas via SSOT submitted_alpha_filter()."""
    q = select(Alpha).where(submitted_alpha_filter())
    if exclude_alpha_id is not None:
        q = q.where(Alpha.id != exclude_alpha_id)
    return list(db.execute(q).scalars().all())


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
    allow_unmeasured: bool = False,
) -> CorrelationVerdict:
    """Check if candidate collides with any portfolio alpha via empirical PnL correlation.

    Fails closed when correlation cannot be measured against a non-empty portfolio
    unless allow_unmeasured=True (e.g. for exploratory diagnostic queries).
    """
    store = pnl_store or get_pnl_store()
    candidate = db.get(Alpha, alpha_id)
    if candidate is None:
        return CorrelationVerdict(
            blocking=False,
            reason=None,
            max_correlation=None,
            method="none",
            measured_pairs=0,
            skipped_pairs=0,
            portfolio_size=0,
        )

    if portfolio is None:
        portfolio = submitted_portfolio(db, exclude_alpha_id=alpha_id)
    else:
        portfolio = [p for p in portfolio if p.id != alpha_id]

    port_size = len(portfolio)
    if port_size == 0:
        return CorrelationVerdict(
            blocking=False,
            reason=None,
            max_correlation=None,
            method="none",
            measured_pairs=0,
            skipped_pairs=0,
            portfolio_size=0,
        )

    cand_pnl_data = store.load_pnl(alpha_id)
    cand_date_map = _date_map(cand_pnl_data)

    max_corr: float | None = None
    colliding_alpha_id: int | None = None
    measured_pairs = 0
    skipped_pairs = 0

    if cand_date_map is not None:
        for port_alpha in portfolio:
            if port_alpha.id == alpha_id:
                continue

            port_pnl_data = store.load_pnl(port_alpha.id)
            port_date_map = _date_map(port_pnl_data)
            if port_date_map is None:
                skipped_pairs += 1
                continue

            # Intersect dates
            common_dates = sorted(cand_date_map.keys() & port_date_map.keys())
            if len(common_dates) < min_overlap:
                skipped_pairs += 1
                continue

            c_vec = np.array([cand_date_map[d] for d in common_dates], dtype=np.float64)
            p_vec = np.array([port_date_map[d] for d in common_dates], dtype=np.float64)

            rho = abs(compute_pairwise_correlation(c_vec, p_vec))
            measured_pairs += 1
            if max_corr is None or rho > max_corr:
                max_corr = rho
                if rho >= threshold:
                    colliding_alpha_id = port_alpha.id

        if colliding_alpha_id is not None:
            return CorrelationVerdict(
                blocking=True,
                reason=f"empirical correlation {max_corr:.2f} with portfolio alpha #{colliding_alpha_id} exceeds threshold {threshold:.2f}",
                max_correlation=max_corr,
                method="empirical",
                measured_pairs=measured_pairs,
                skipped_pairs=skipped_pairs,
                portfolio_size=port_size,
            )

        if measured_pairs > 0 and skipped_pairs == 0:
            return CorrelationVerdict(
                blocking=False,
                reason=None,
                max_correlation=max_corr,
                method="empirical",
                measured_pairs=measured_pairs,
                skipped_pairs=skipped_pairs,
                portfolio_size=port_size,
            )

    # Fallback to structural proxy check if empirical measurement wasn't possible or clean
    is_struct_corr, struct_collision = check_structural_proxy(db, alpha_id, portfolio=portfolio)
    if is_struct_corr:
        return CorrelationVerdict(
            blocking=True,
            reason=struct_collision,
            max_correlation=max_corr,
            method="structural_proxy",
            measured_pairs=measured_pairs,
            skipped_pairs=skipped_pairs,
            portfolio_size=port_size,
        )

    # Fail-closed check when unmeasured
    if measured_pairs == 0 and not allow_unmeasured:
        return CorrelationVerdict(
            blocking=True,
            reason=f"unmeasured correlation against {port_size} portfolio alphas (missing PnL or insufficient overlap < {min_overlap}d)",
            max_correlation=None,
            method="unmeasured",
            measured_pairs=0,
            skipped_pairs=skipped_pairs,
            portfolio_size=port_size,
        )

    return CorrelationVerdict(
        blocking=False,
        reason=None,
        max_correlation=max_corr if measured_pairs > 0 else None,
        method="empirical" if measured_pairs > 0 else ("unmeasured" if port_size > 0 else "none"),
        measured_pairs=measured_pairs,
        skipped_pairs=skipped_pairs,
        portfolio_size=port_size,
    )


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
        log.warning(
            "on_demand_pnl_fetch_failed", alpha_id=alpha_id, remote_id=remote_id, error=str(exc)
        )

    return False


def compute_max_self_correlation_with_submitted(
    db: Session,
    alpha_id: int,
    *,
    pnl_store: PnLStore | None = None,
    portfolio: list[Alpha] | None = None,
    min_overlap: int = MIN_COMMON_TRADING_DAYS,
) -> tuple[float | None, int | None, str]:
    """Kept for reporting/UI paths. Delegates to check_portfolio_empirical_correlation."""
    v = check_portfolio_empirical_correlation(
        db,
        alpha_id,
        pnl_store=pnl_store,
        portfolio=portfolio,
        min_overlap=min_overlap,
        allow_unmeasured=True,
    )
    colliding_id = None
    if v.reason and "#" in v.reason:
        try:
            colliding_id = int(v.reason.split("#")[1].split()[0])
        except (IndexError, ValueError):
            pass
    return (v.max_correlation, colliding_id, v.method)
