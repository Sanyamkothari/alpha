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
    method: str  # 'empirical' | 'structural_proxy' | 'unmeasured' | 'none'
    measured_pairs: int
    skipped_pairs: int
    portfolio_size: int


def submitted_portfolio(db: Session, exclude_alpha_id: int | None = None) -> list[Alpha]:
    """Alphas confirmed submitted, per the derived platform_outcome column."""
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
    """Check if candidate collides with any portfolio alpha via empirical PnL correlation."""
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

    active_portfolio = [p for p in portfolio if p.id != alpha_id]
    if not active_portfolio:
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

    measured = 0
    skipped = 0
    max_corr: float | None = None
    colliding_alpha_id: int | None = None

    if cand_pnl_data is not None:
        cand_dates, cand_pnl = cand_pnl_data
        cand_date_map = dict(zip(cand_dates, cand_pnl))

        for port_alpha in active_portfolio:
            port_pnl_data = store.load_pnl(port_alpha.id)
            if port_pnl_data is None:
                skipped += 1
                continue

            port_dates, port_pnl = port_pnl_data
            port_date_map = dict(zip(port_dates, port_pnl))

            common_dates = sorted(set(cand_dates).intersection(port_dates))
            if len(common_dates) < min_overlap:
                skipped += 1
                continue

            c_vec = np.array([cand_date_map[d] for d in common_dates], dtype=np.float64)
            p_vec = np.array([port_date_map[d] for d in common_dates], dtype=np.float64)

            rho = abs(compute_pairwise_correlation(c_vec, p_vec))
            measured += 1
            if max_corr is None or rho > max_corr:
                max_corr = rho
                colliding_alpha_id = port_alpha.id
    else:
        skipped = len(active_portfolio)

    # 1. A measured collision blocks.
    if max_corr is not None and max_corr >= threshold:
        return CorrelationVerdict(
            blocking=True,
            reason=(
                f"empirical correlation {max_corr:.2f} with portfolio alpha "
                f"#{colliding_alpha_id} exceeds threshold {threshold:.2f}"
            ),
            max_correlation=max_corr,
            method="empirical",
            measured_pairs=measured,
            skipped_pairs=skipped,
            portfolio_size=len(active_portfolio),
        )

    # 2. The structural proxy is a *supplement*, not a substitute.
    is_struct, struct_reason = check_structural_proxy(db, alpha_id, portfolio=active_portfolio)
    if is_struct:
        return CorrelationVerdict(
            blocking=True,
            reason=struct_reason,
            max_correlation=max_corr,
            method="structural_proxy",
            measured_pairs=measured,
            skipped_pairs=skipped,
            portfolio_size=len(active_portfolio),
        )

    # 3. Nothing measured against a non-empty portfolio => FAIL CLOSED.
    if active_portfolio and measured == 0:
        return CorrelationVerdict(
            blocking=not allow_unmeasured,
            reason=(
                f"correlation UNMEASURED against {len(active_portfolio)} portfolio alphas "
                f"({skipped} pairs lacked {min_overlap}+ common trading days). "
                f"Blocking: an unverified correlation constraint is not a passed one."
            ),
            max_correlation=None,
            method="unmeasured",
            measured_pairs=0,
            skipped_pairs=skipped,
            portfolio_size=len(active_portfolio),
        )

    # 4. Genuinely measured and genuinely clear.
    return CorrelationVerdict(
        blocking=False,
        reason=None,
        max_correlation=max_corr,
        method="empirical" if measured else "none",
        measured_pairs=measured,
        skipped_pairs=skipped,
        portfolio_size=len(active_portfolio),
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
    from app.models.results import AlphaMetric, SimulationImport
    from app.services.brain import BrainClient
    from app.services.pnl_convention import detect, to_daily

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
        with BrainClient() as brain:
            pnl_resp = brain.get_json(f"/alphas/{remote_id}/recordsets/daily-pnl")
            records = pnl_resp.get("records", [])
            if not records:
                return False

            dates = [str(r[0]) for r in records]
            raw = np.array([float(r[1]) for r in records], dtype=float)

            metric = db.execute(
                select(AlphaMetric)
                .where(AlphaMetric.alpha_id == alpha_id)
                .order_by(AlphaMetric.id.desc())
            ).scalars().first()
            if metric is None or metric.sharpe is None:
                log.warning("pnl_fetch_no_local_sharpe", alpha_id=alpha_id)
                return False

            verdict = detect(raw, float(metric.sharpe))
            if not verdict.is_usable:
                log.warning(
                    "pnl_convention_indeterminate",
                    alpha_id=alpha_id,
                    remote_id=remote_id,
                    reported=verdict.reported_sharpe,
                    as_daily=verdict.sharpe_as_daily,
                    as_cumulative=verdict.sharpe_as_cumulative,
                )
                return False

            store.save_pnl(
                alpha_id,
                dates,
                to_daily(raw, verdict.convention),
                convention=verdict.convention,
                reported_sharpe=float(metric.sharpe),
            )
            return True
    except Exception as exc:
        log.warning("on_demand_pnl_fetch_failed", alpha_id=alpha_id, remote_id=remote_id, error=str(exc))

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
        except Exception:
            colliding_id = None
    return v.max_correlation, colliding_id, v.method
