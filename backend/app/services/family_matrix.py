"""Family PnL Matrix Service — assembles date-aligned daily PnL matrices across family members.

Used to compute empirical cross-candidate correlation matrices, eigenvalue-based effective
trial counts (N_eff), and Combinatorially Symmetric Cross-Validation (CSCV) for PBO.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.results import AlphaMetric
from app.services.pnl_storage import PnLStore, get_pnl_store

log = structlog.get_logger("family_matrix")


@dataclass
class FamilyPnLMatrix:
    """Date-aligned daily PnL matrix across candidates in a family."""

    family_key: str
    alpha_ids: list[int]
    dates: list[str]
    matrix: np.ndarray  # Shape: (N_alphas, T_days)


def build_family_matrix(
    db: Session,
    family_key: str,
    *,
    pnl_store: PnLStore | None = None,
    structure: tuple | None = None,
    min_overlap: int = 252,
) -> FamilyPnLMatrix | None:
    """Assemble a date-aligned daily PnL matrix over simulated family members.

    Returns None if fewer than 2 series exist with sufficient date overlap. Note this
    filters on length and overlap only — reconciliation against the reported Sharpe is
    enforced at the storage boundary (``PnLStore.save_pnl``), so an unreconcilable
    series never reaches the store to be picked up here.
    """
    store = pnl_store or get_pnl_store()

    # Find simulated alphas in this family
    query = (
        select(Alpha.id, Alpha.feature_json)
        .join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
        .where(Alpha.family_key == family_key)
        .distinct()
    )
    rows = db.execute(query).all()
    if len(rows) < 2:
        return None

    # Filter by structure if specified
    from app.services.plateau import _structure_of

    alpha_ids: list[int] = []
    for aid, feat in rows:
        if structure is not None:
            grid = (feat or {}).get("grid") or {}
            if _structure_of(grid) != structure:
                continue
        alpha_ids.append(aid)

    if len(alpha_ids) < 2:
        return None

    valid_ids, dates, matrix = store.get_aligned_matrix(alpha_ids, min_overlap=min_overlap)
    if matrix.shape[0] < 2 or matrix.shape[1] < min_overlap:
        return None

    return FamilyPnLMatrix(
        family_key=family_key,
        alpha_ids=valid_ids,
        dates=dates,
        matrix=matrix,
    )
