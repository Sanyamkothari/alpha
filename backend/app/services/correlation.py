"""Phase 3 — Empirical Returns Correlation Engine & Portfolio Gate.

Computes exact Pearson correlation matrices over date-aligned daily PnL vectors.
Guards the portfolio against self-correlated duplicates with an internal threshold on
magnitude (|rho| >= 0.55), with fallback to structural hashing when empirical PnL is
unavailable. Correlations are reported signed but always gated on |rho|.
"""

from __future__ import annotations

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha, SubmissionAttempt
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

    Gates on |rho|, reports the signed value. This gate is a conservative proxy for
    BRAIN's own self-correlation limit, and BRAIN measures *duplication*, not portfolio
    variance: submitting X and -X is one idea twice, however well it diversifies in
    theory. Gating on signed rho would let an exact negation of a submitted alpha
    through as the single most diversified candidate on the board.

    The reported correlation keeps its sign so the operator can still tell an
    anti-correlated collision from a co-moving one.

    Portfolio alphas we could not measure (no PnL, or too little date overlap) fall
    through to the structural proxy below. Note that this is a weaker guarantee than
    blocking: the proxy clears an unmeasured pair whose skeletons differ, so an
    unmeasurable collision between two structurally distinct alphas is passed, not
    caught. Backfilling PnL is what closes that gap.

    Returns (is_correlated, reason_or_collision_desc, max_signed_correlation).
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

    # Ranked on magnitude, reported with sign — see the docstring.
    max_abs_corr = 0.0
    max_signed_corr = 0.0
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

            rho_signed = compute_pairwise_correlation(c_vec, p_vec)
            rho = abs(rho_signed)
            if rho > max_abs_corr:
                max_abs_corr = rho
                max_signed_corr = rho_signed
                if rho >= thresh:
                    colliding_alpha_id = port_alpha.id

        if colliding_alpha_id is not None:
            return (
                True,
                f"empirical correlation {max_signed_corr:.2f} with portfolio alpha "
                f"#{colliding_alpha_id} exceeds threshold {thresh:.2f}",
                max_signed_corr,
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
            return True, struct_collision, max_signed_corr

    return False, None, max_signed_corr


def _reported_sharpe(db: Session, alpha_id: int) -> float | None:
    """The most recent BRAIN-reported Sharpe for an alpha, if we have one."""
    from app.models.results import AlphaMetric

    return db.execute(
        select(AlphaMetric.sharpe)
        .where(AlphaMetric.alpha_id == alpha_id)
        .order_by(AlphaMetric.id.desc())
        .limit(1)
    ).scalar_one_or_none()


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
                # Reconcile against the reported Sharpe, exactly as the batch backfill
                # does. Saving without it would record ``reconciled=None`` on the path
                # that runs *during evaluation* — the one place the guard matters most.
                save_res = store.save_pnl(
                    alpha_id,
                    dates,
                    pnl,
                    reported_sharpe=_reported_sharpe(db, alpha_id),
                )
                if not save_res.saved:
                    log.warning(
                        "on_demand_pnl_rejected",
                        alpha_id=alpha_id,
                        remote_id=remote_id,
                        reason=save_res.rejection_reason,
                    )
                    return False
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

    The *strongest* relationship is the one worth showing, so the pair is chosen on
    |rho| and then reported with its sign. Ranking on signed rho would report a -0.98
    mirror of a submitted alpha as 0.00 — the number the operator reads must agree
    with the number the gate acted on.
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

    max_abs_corr = 0.0
    max_signed_corr = 0.0
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

            rho_signed = compute_pairwise_correlation(c_vec, s_vec)
            had_empirical_match = True
            if abs(rho_signed) >= max_abs_corr:
                max_abs_corr = abs(rho_signed)
                max_signed_corr = rho_signed
                colliding_alpha_id = sub_alpha.id

    if had_empirical_match:
        return max_signed_corr, colliding_alpha_id, "empirical"

    # Fallback to structural proxy check
    is_struct_corr, struct_collision = check_structural_proxy(db, alpha_id, portfolio=submitted_alphas)
    target_id = submitted_alphas[0].id if submitted_alphas else None
    if is_struct_corr:
        return None, target_id, "structural_proxy"

    return None, None, "unmeasured"
