"""Clustering and orthogonal representative election engine (STRATEGY.md Rule 2 & 5).

Provides:
1. Intra-family single-linkage clustering: groups surviving ridge points by PnL correlation
   (|rho| >= cfg.sibling_cluster_threshold) and elects the optimal ridge center representative.
2. Inter-family greedy orthogonal batch selection: filters shortlist to ensure no two promoted
   alphas in a batch exceed cfg.portfolio_corr_threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

import numpy as np
import structlog

from app.services.filter_config import DEFAULT_FILTER_CONFIG, FilterConfig
from app.services.pnl_storage import PnLStore

if TYPE_CHECKING:
    from app.services.plateau import Verdict

log = structlog.get_logger("clustering")


@dataclass
class RidgeCluster:
    representative_id: int
    member_ids: list[int]
    intra_max_corr: float
    election_reason: str


def compute_pnl_correlation(
    pnl_store: PnLStore,
    aid1: int,
    aid2: int,
    min_overlap: int = 500,
) -> float | None:
    """Compute Pearson correlation between two alphas from stored PnL vectors."""
    res1 = pnl_store.load_pnl(aid1)
    res2 = pnl_store.load_pnl(aid2)
    if res1 is None or res2 is None:
        return None
    dates1, arr1 = res1
    dates2, arr2 = res2
    common = sorted(set(dates1).intersection(dates2))
    if len(common) < min_overlap:
        return None
    dmap1 = dict(zip(dates1, arr1))
    dmap2 = dict(zip(dates2, arr2))
    v1 = np.array([dmap1[d] for d in common], dtype=np.float64)
    v2 = np.array([dmap2[d] for d in common], dtype=np.float64)
    if len(v1) < 10:
        return None
    std1 = float(np.std(v1))
    std2 = float(np.std(v2))
    if std1 <= 1e-12 or std2 <= 1e-12:
        return 0.0
    corr = np.corrcoef(v1, v2)[0, 1]
    return float(corr) if np.isfinite(corr) else 0.0


def cluster_family(
    candidate_verdicts: Sequence[Verdict],
    pnl_store: PnLStore,
    *,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
) -> list[RidgeCluster]:
    """Single-linkage clustering of surviving family candidates by |PnL correlation|.

    Elects the ridge center representative for each cluster based on:
    1. Highest ridge_score (neighbourhood median / shrunk score)
    2. Higher neighbours_simulated (completeness of surface)
    3. Lowest alpha_id (deterministic tiebreaker)
    """
    if not candidate_verdicts:
        return []

    # Map alpha_id to Verdict
    v_map = {v.alpha_id: v for v in candidate_verdicts}
    alpha_ids = list(v_map.keys())
    n = len(alpha_ids)

    if n == 1:
        aid = alpha_ids[0]
        return [
            RidgeCluster(
                representative_id=aid,
                member_ids=[aid],
                intra_max_corr=1.0,
                election_reason="Singleton candidate in family",
            )
        ]

    # Pre-compute pairwise absolute correlations
    corr_matrix: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            aid1, aid2 = alpha_ids[i], alpha_ids[j]
            rho = compute_pnl_correlation(pnl_store, aid1, aid2, min_overlap=cfg.min_common_days)
            if rho is not None:
                corr_matrix[(aid1, aid2)] = abs(rho)
                corr_matrix[(aid2, aid1)] = abs(rho)

    # Disjoint-set union (DSU) for single-linkage clustering
    parent = {aid: aid for aid in alpha_ids}

    def find(x: int) -> int:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for (aid1, aid2), abs_rho in corr_matrix.items():
        if abs_rho >= cfg.sibling_cluster_threshold:
            union(aid1, aid2)

    # Group by connected component root
    clusters_raw: dict[int, list[int]] = {}
    for aid in alpha_ids:
        r = find(aid)
        clusters_raw.setdefault(r, []).append(aid)

    clusters: list[RidgeCluster] = []
    for members in clusters_raw.values():
        # Elect representative
        def rep_sort_key(aid: int) -> tuple:
            v = v_map[aid]
            r_score = v.ridge_score if v.ridge_score is not None else (v.neighbour_median_sharpe or v.sharpe or -99.0)
            nb_med = v.neighbour_median_sharpe if v.neighbour_median_sharpe is not None else -99.0
            n_sim = v.neighbours_simulated
            # Deterministic tie break on alpha_id
            return (r_score, nb_med, n_sim, -aid)

        sorted_members = sorted(members, key=rep_sort_key, reverse=True)
        rep_id = sorted_members[0]
        rep_v = v_map[rep_id]

        max_intra_corr = 0.0
        if len(members) > 1:
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    m1, m2 = members[i], members[j]
                    c = corr_matrix.get((m1, m2), 0.0)
                    if c > max_intra_corr:
                        max_intra_corr = c

        r_score = rep_v.ridge_score if rep_v.ridge_score is not None else (rep_v.neighbour_median_sharpe or rep_v.sharpe or 0.0)
        reason = (
            f"Elected ridge center: ridge_score={r_score:.2f}, "
            f"neighbours={rep_v.neighbours_simulated}/{rep_v.neighbours_possible}"
        )
        if len(members) > 1:
            reason += f" across {len(members)} cluster siblings (max intra-rho={max_intra_corr:.2f})"

        clusters.append(
            RidgeCluster(
                representative_id=rep_id,
                member_ids=sorted_members,
                intra_max_corr=max_intra_corr,
                election_reason=reason,
            )
        )

    return clusters


def select_orthogonal_batch(
    promoted_verdicts: Sequence[Verdict],
    pnl_store: PnLStore,
    *,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
) -> tuple[list[Verdict], list[Verdict]]:
    """Greedy orthogonal subset selection across a batch/shortlist of promoted alphas.

    Returns:
        (accepted_verdicts, deferred_verdicts)
    """
    if not promoted_verdicts:
        return [], []

    # Sort descending by ridge_score (or neighbour median / sharpe)
    sorted_verdicts = sorted(
        promoted_verdicts,
        key=lambda v: (
            v.ridge_score if v.ridge_score is not None else (v.neighbour_median_sharpe or v.sharpe or -99.0),
            v.sharpe or -99.0,
        ),
        reverse=True,
    )

    accepted: list[Verdict] = []
    deferred: list[Verdict] = []

    for cand in sorted_verdicts:
        is_correlated = False
        colliding_id: int | None = None
        max_rho = 0.0

        for acc in accepted:
            rho = compute_pnl_correlation(pnl_store, cand.alpha_id, acc.alpha_id, min_overlap=cfg.min_common_days)
            # Gate on positive signed correlation against already accepted shortlist members
            if rho is not None and rho >= cfg.portfolio_corr_threshold:
                is_correlated = True
                colliding_id = acc.alpha_id
                max_rho = rho
                break

        if is_correlated and colliding_id is not None:
            # Mark as deferred rather than permanently dropped
            deferred.append(cand)
        else:
            accepted.append(cand)

    return accepted, deferred
