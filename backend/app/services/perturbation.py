"""Neutralization-ladder robustness check (E2).

A mechanism that survives one step along the neutralization ladder is a mechanism;
one that only works at a single neutralization setting is a fit to that setting's
particular residual structure.

**Why this is not the plateau test.** ``plateau._neighbours`` walks ``(window, decay)``
*within* one structure, and ``_structure_of`` includes ``neutralization`` — so the
existing ridge test structurally cannot vary it. This module reads *across* surfaces
at matched ``(window, decay)`` coordinates, which is the perturbation the surface
test cannot reach. An earlier draft of this module re-walked ``(window, decay)``
inside a single structure and was therefore a second, differently-scaled copy of the
plateau test; that is what this replaces.

**Absence is not evidence of fragility.** If the adjacent-neutralization surface was
never simulated, the verdict is ``None`` — not ``False``. Reporting an unmeasured
alpha as fragile would penalise it for a gap in our own coverage, and the whole
budget argument for this system is that coverage is incomplete by design.

Advisory only. Like PBO, this needs its distribution observed across real families
before any promotion threshold based on it means anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

import structlog

from app.services.plateau import SurfacePoint, structure_component, structure_replacing

log = structlog.get_logger("perturbation")

# Ordered weakest → strongest. "One step" means one position along this ladder.
NEUTRALIZATION_LADDER: tuple[str, ...] = (
    "NONE",
    "MARKET",
    "SECTOR",
    "INDUSTRY",
    "SUBINDUSTRY",
)

# A mechanism should retain most of its Sharpe one rung along the ladder. 0.6 mirrors
# plateau.PLATEAU_RATIO so the two robustness notions are on the same scale; it is a
# starting point to be replaced by a measured value, not a calibrated threshold.
DEFAULT_MIN_RETENTION = 0.6


@dataclass(frozen=True)
class NeutralizationRobustness:
    """How much of a point's Sharpe survives one step along the neutralization ladder."""

    peak_sharpe: float | None
    peak_neutralization: str | None
    neighbour_sharpes: dict[str, float]
    median_neighbour_sharpe: float | None
    retention: float | None
    is_robust: bool | None  # None = not measurable from the points we have
    reason: str

    @property
    def measured(self) -> bool:
        return self.is_robust is not None


def adjacent_neutralizations(neutralization: str | None) -> list[str]:
    """The one-step neighbours of a neutralization on the ladder."""
    if neutralization is None:
        return []
    try:
        idx = NEUTRALIZATION_LADDER.index(neutralization)
    except ValueError:
        return []
    out = []
    if idx > 0:
        out.append(NEUTRALIZATION_LADDER[idx - 1])
    if idx + 1 < len(NEUTRALIZATION_LADDER):
        out.append(NEUTRALIZATION_LADDER[idx + 1])
    return out


def check_neutralization_robustness(
    peak: SurfacePoint,
    family_points: Sequence[SurfacePoint],
    *,
    min_retention: float = DEFAULT_MIN_RETENTION,
) -> NeutralizationRobustness:
    """Compare a point against the same (window, decay) at adjacent neutralizations.

    ``family_points`` should span the whole family, not one surface — the comparison
    is across surfaces by construction.
    """
    peak_neut = structure_component(peak.structure, "neutralization")

    if peak.sharpe is None or peak.sharpe <= 0:
        return NeutralizationRobustness(
            peak_sharpe=peak.sharpe,
            peak_neutralization=peak_neut,
            neighbour_sharpes={},
            median_neighbour_sharpe=None,
            retention=None,
            is_robust=None,
            reason="non-positive Sharpe — robustness test does not apply",
        )

    neighbours = adjacent_neutralizations(peak_neut)
    if not neighbours:
        return NeutralizationRobustness(
            peak_sharpe=peak.sharpe,
            peak_neutralization=peak_neut,
            neighbour_sharpes={},
            median_neighbour_sharpe=None,
            retention=None,
            is_robust=None,
            reason=f"neutralization {peak_neut!r} is not on the ladder",
        )

    # Match on every structural axis except neutralization, at the same coordinate.
    wanted = {
        neut: structure_replacing(peak.structure, "neutralization", neut) for neut in neighbours
    }
    found: dict[str, float] = {}
    for point in family_points:
        if point.alpha_id == peak.alpha_id or point.sharpe is None:
            continue
        if point.window != peak.window or point.decay != peak.decay:
            continue
        for neut, target in wanted.items():
            if point.structure == target:
                found[neut] = point.sharpe

    if not found:
        return NeutralizationRobustness(
            peak_sharpe=peak.sharpe,
            peak_neutralization=peak_neut,
            neighbour_sharpes={},
            median_neighbour_sharpe=None,
            retention=None,
            is_robust=None,
            reason=(
                f"no adjacent-neutralization point simulated at "
                f"(window={peak.window}, decay={peak.decay}) — not measured"
            ),
        )

    med = float(median(found.values()))
    retention = med / peak.sharpe
    is_robust = retention >= min_retention
    reason = (
        f"retains {retention:.0%} of Sharpe across {len(found)} adjacent "
        f"neutralization(s) {sorted(found)}"
        if is_robust
        else (
            f"neutralization-fragile: median {med:.2f} vs own {peak.sharpe:.2f} "
            f"({retention:.0%} < {min_retention:.0%})"
        )
    )

    return NeutralizationRobustness(
        peak_sharpe=peak.sharpe,
        peak_neutralization=peak_neut,
        neighbour_sharpes=found,
        median_neighbour_sharpe=med,
        retention=retention,
        is_robust=is_robust,
        reason=reason,
    )
