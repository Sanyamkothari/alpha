"""Phase 3 — Empirical Returns Correlation Engine & Portfolio Gate.

Computes exact Pearson correlation matrices over date-aligned daily PnL vectors.
Guards the portfolio against self-correlated duplicates with an internal threshold (< 0.55),
with fallback to structural hashing when empirical PnL is unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha, SubmissionAttempt
from app.models.enums import AlphaStatus
from app.services.plateau import check_portfolio_correlation as check_structural_proxy
from app.services.pnl_storage import PnLStore, get_pnl_store

log = structlog.get_logger("correlation")

# Stricter internal threshold than BRAIN's 0.70 to preserve safety buffer
INTERNAL_CORRELATION_THRESHOLD = 0.55
MIN_COMMON_TRADING_DAYS = 500


def submitted_portfolio(db: Session, exclude_alpha_id: int | None = None) -> list[Alpha]:
    """Confirmed submissions from submission_attempts (result == 'submitted')."""
    q = (
        select(Alpha)
        .join(SubmissionAttempt, SubmissionAttempt.alpha_id == Alpha.id)
        .where(SubmissionAttempt.result == "submitted")
        .distinct()
    )
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
) -> tuple[bool, str | None, float | None]:
    """Check if candidate collides with any portfolio alpha via empirical PnL correlation.

    Returns (is_correlated, reason_or_collision_desc, max_correlation).
    """
    store = pnl_store or get_pnl_store()
    candidate = db.get(Alpha, alpha_id)
    if candidate is None:
        return False, None, None

    if portfolio is None:
        portfolio = submitted_portfolio(db, exclude_alpha_id=alpha_id)

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


@dataclass
class ClusterAssignment:
    """Where one candidate landed in a mutual-correlation clustering pass."""

    alpha_id: int
    representative_id: int | None  # None => this candidate IS a cluster representative
    correlation: float | None  # max |rho| measured against representatives, None if unmeasured
    # 'empirical' => at least one comparison was actually computed.
    # 'unmeasured' => none was possible: no stored PnL, no representative sharing
    # enough trading days, or nothing ranked ahead of it to compare against. The
    # first candidate in the list is always 'unmeasured' for that last reason.
    method: str


def _series_map(
    store: PnLStore, alpha_ids: Sequence[int]
) -> dict[int, tuple[set[str], dict[str, float]]]:
    """Load each alpha's daily PnL once, as a date set plus a date -> value lookup.

    Built up front because the clustering pass below is O(n^2) in comparisons:
    rebuilding the date map inside the inner loop turns a cheap scan into a
    visible pause on the morning summary.
    """
    out: dict[int, tuple[set[str], dict[str, float]]] = {}
    for aid in alpha_ids:
        loaded = store.load_pnl(aid)
        if loaded is None:
            continue
        dates, arr = loaded
        if len(dates) != len(arr):
            continue
        out[aid] = (set(dates), {d: float(v) for d, v in zip(dates, arr, strict=True)})
    return out


def cluster_by_correlation(
    alpha_ids: Sequence[int],
    *,
    pnl_store: PnLStore | None = None,
    threshold: float = INTERNAL_CORRELATION_THRESHOLD,
    min_overlap: int = MIN_COMMON_TRADING_DAYS,
) -> list[ClusterAssignment]:
    """Group candidates that are the same alpha in BRAIN's eyes.

    Takes no ``Session`` — unlike its siblings in this module it reads nothing
    but stored PnL, and the comparison is between the candidates handed to it
    rather than against anything in the database.

    ``plateau.evaluate`` de-duplicates only *within* one family's structure slice,
    so two promoted candidates built on the same data field with a different
    time-series operator or a different neutralization reach the review queue
    looking independent. On daily PnL — which is what BRAIN's SELF_CORRELATION
    check actually measures — they are frequently the same alpha, and the second
    one submitted is the one that gets rejected.

    Greedy in the order given, so pass ``alpha_ids`` already ranked best-first:
    the first member of a cluster becomes its representative and the rest are
    attributed to it.

    Correlation is never fabricated. A candidate whose PnL is missing, or which
    never reaches ``min_overlap`` common days against any representative, is
    returned as its own representative with ``method='unmeasured'`` — absent
    evidence of collision is not evidence of independence, and the caller is
    told which of the two it has.
    """
    store = pnl_store or get_pnl_store()
    series = _series_map(store, alpha_ids)

    assignments: list[ClusterAssignment] = []
    representatives: list[int] = []

    for aid in alpha_ids:
        candidate = series.get(aid)
        if candidate is None:
            representatives.append(aid)
            assignments.append(ClusterAssignment(aid, None, None, "unmeasured"))
            continue

        cand_dates, cand_map = candidate
        best_rho: float | None = None
        best_rep: int | None = None

        for rep in representatives:
            other = series.get(rep)
            if other is None:
                continue
            _, other_map = other
            common = sorted(cand_dates.intersection(other_map))
            if len(common) < min_overlap:
                continue
            c_vec = np.fromiter(
                (cand_map[d] for d in common), dtype=np.float64, count=len(common)
            )
            o_vec = np.fromiter(
                (other_map[d] for d in common), dtype=np.float64, count=len(common)
            )
            rho = abs(compute_pairwise_correlation(c_vec, o_vec))
            if best_rho is None or rho > best_rho:
                best_rho, best_rep = rho, rep

        if best_rho is not None and best_rep is not None and best_rho >= threshold:
            assignments.append(ClusterAssignment(aid, best_rep, best_rho, "empirical"))
            continue

        representatives.append(aid)
        assignments.append(
            ClusterAssignment(
                aid,
                None,
                best_rho,
                "empirical" if best_rho is not None else "unmeasured",
            )
        )

    log.info(
        "correlation_clustered",
        candidates=len(alpha_ids),
        representatives=len(representatives),
        threshold=threshold,
    )
    return assignments


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
    min_overlap: int = MIN_COMMON_TRADING_DAYS,
) -> tuple[float | None, int | None, str]:
    """Compute max correlation against confirmed submitted alphas (submission_attempts with result='submitted').

    Returns (max_correlation, target_alpha_id, method), where method is 'empirical',
    'structural_proxy', 'unmeasured', or 'none'.
    Returns None for max_correlation when PnL series is unmeasured (never fabricates synthetic 0.20 or 0.85).
    """
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

            rho = abs(compute_pairwise_correlation(c_vec, s_vec))
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
