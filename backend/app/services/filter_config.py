"""Declared, versioned configuration object for all statistical and gating thresholds.

Consolidates all magic numbers across the pipeline (plateau, stability, multiple testing,
correlation, reconciliation) into a single auditable operating point with a cryptographic
fingerprint (STRATEGY.md Rule 5).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


TRADING_DAYS_PER_YEAR: int = 252


@dataclass(frozen=True)
class FilterConfig:
    # Plateau shape
    plateau_ratio: float = 0.60
    min_neighbours_to_judge: int = 2

    # Baseline promotion bar
    target_sharpe: float = 1.25
    backtest_days: int = 1236

    # Stability (Lo 2002 standard error Z-score thresholds)
    split_half_z_floor: float = -2.0
    recent_decay_z_floor: float = -2.0
    rolling_pos_floor: float = 0.70
    min_split_half_sr_floor: float = -0.5  # Reject if either half is significantly negative

    # Multiple testing & DSR
    dsr_threshold: float = 0.95
    dsr_re_promotion_threshold: float = 0.97

    # Sub-universe robustness (LOW_SUB_UNIVERSE_SHARPE).
    #
    # BRAIN does not apply a fixed bar here. Measured across 393 stored check payloads,
    # the limit it applies is 0.433 x the alpha's OWN Sharpe (sd 0.016, 93% of rows
    # within 0.02). That makes this a *robustness* test, not a quality one: a stronger
    # signal raises the bar in proportion and cannot outrun it.
    #
    # Empirically derived from our own results, NOT documented by the platform - so it
    # is used to warn, never to reject. Being wrong about it in the rejecting direction
    # would silently discard good alphas, which is far worse than letting a doomed one
    # through to a simulation we have already paid for.
    subuniverse_ratio: float = 0.433
    subuniverse_ratio_margin: float = 0.05  # slack before we say anything

    # Correlation & portfolio gates
    portfolio_corr_threshold: float = 0.55
    sibling_cluster_threshold: float = 0.90
    min_common_days: int = 500

    # Reconciliation
    sharpe_reconciliation_tolerance: float = 0.10  # accommodate BRAIN sqrt(250) vs local sqrt(252)

    def fingerprint(self) -> str:
        """Stable short hash of the operating point, stamped onto every Verdict
        and printed in the report header.
        """
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


DEFAULT_FILTER_CONFIG = FilterConfig()
