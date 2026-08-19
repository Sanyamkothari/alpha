"""Perturbation Robustness and Fragility Check (E2).

Evaluates the local parameter stability of an elected alpha point (w0, d0) on a 2D surface
by measuring the performance drop to its 4-connected coordinate neighbors:
    fragility = max(0, (Sharpe_peak - median(Sharpe_nbrs)) / Sharpe_peak)

Plateau ridges have low fragility (< 0.35) and high neighbor median Sharpe (>= 1.25).
Isolated lucky spikes have high fragility (>= 0.35) and fail promotion.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from app.services.plateau import SurfacePoint


@dataclass(frozen=True)
class PerturbationResult:
    peak_sharpe: float
    neighbour_sharpes: list[float]
    neighbour_median_sharpe: float
    fragility: float  # [0.0, 1.0+]
    is_robust: bool  # True if fragility <= max_fragility and median >= min_median_sharpe
    reason: str | None = None


def check_perturbation_robustness(
    peak: SurfacePoint,
    surface_points: Sequence[SurfacePoint],
    *,
    max_fragility: float = 0.35,
    min_median_sharpe: float = 1.25,
) -> PerturbationResult:
    """Assess whether an elected peak represents a stable ridge or an isolated spike."""
    if peak.sharpe is None or peak.sharpe <= 0:
        return PerturbationResult(
            peak_sharpe=peak.sharpe or 0.0,
            neighbour_sharpes=[],
            neighbour_median_sharpe=0.0,
            fragility=1.0,
            is_robust=False,
            reason="non_positive_peak_sharpe",
        )

    # Derive axes for this structure
    matching_points = [p for p in surface_points if p.structure == peak.structure]
    windows = sorted({p.window for p in matching_points if p.window is not None})
    decays = sorted({p.decay for p in matching_points if p.decay is not None})

    if peak.window not in windows or peak.decay not in decays:
        return PerturbationResult(
            peak_sharpe=peak.sharpe,
            neighbour_sharpes=[],
            neighbour_median_sharpe=0.0,
            fragility=1.0,
            is_robust=False,
            reason="peak_not_in_axes",
        )

    w_idx = windows.index(peak.window)
    d_idx = decays.index(peak.decay)

    nbr_sharpes: list[float] = []
    for p in matching_points:
        if p.sharpe is None or (p.window == peak.window and p.decay == peak.decay):
            continue
        pw_idx = windows.index(p.window)
        pd_idx = decays.index(p.decay)
        # 4-connected neighbors: |dw| + |dd| == 1
        if abs(pw_idx - w_idx) + abs(pd_idx - d_idx) == 1:
            nbr_sharpes.append(p.sharpe)

    if not nbr_sharpes:
        return PerturbationResult(
            peak_sharpe=peak.sharpe,
            neighbour_sharpes=[],
            neighbour_median_sharpe=0.0,
            fragility=1.0,
            is_robust=False,
            reason="no_valid_neighbours",
        )

    med_nbr = float(statistics.median(nbr_sharpes))
    fragility = max(0.0, float((peak.sharpe - med_nbr) / peak.sharpe))

    is_robust = (fragility <= max_fragility) and (med_nbr >= min_median_sharpe)
    reason = None if is_robust else (
        f"fragile_peak(fragility={fragility:.2f} > {max_fragility:.2f})"
        if fragility > max_fragility
        else f"low_neighbour_median({med_nbr:.2f} < {min_median_sharpe:.2f})"
    )

    return PerturbationResult(
        peak_sharpe=peak.sharpe,
        neighbour_sharpes=nbr_sharpes,
        neighbour_median_sharpe=med_nbr,
        fragility=fragility,
        is_robust=is_robust,
        reason=reason,
    )
