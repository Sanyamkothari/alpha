"""Detect whether a raw PnL series from BRAIN is daily or a cumulative curve.

``/alphas/{id}/recordsets/daily-pnl`` is named for a discrete daily series, but
``scripts/verify_pnl_reconciliation.py`` exists because that has not been taken
on faith: BRAIN dashboard PnL exports are commonly cumulative curves, and this
project has not verified firsthand which shape the endpoint returns in every
case. Storing a cumulative curve as if it were daily silently poisons every
downstream consumer -- DSR, plateau, and the correlation gate all treat each
array entry as one day's PnL.

This module answers the question once, at ingest, by reconciling the raw
series against the alpha's own reported Sharpe under both interpretations and
keeping only the one that actually reconciles. An alpha whose series matches
neither interpretation is reported ``indeterminate`` and must not be stored --
a wrong guess here is worse than no data, because it fails silently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Matches subperiod.verify_pnl_reconciliation's post-hoc acceptance check, so a
# series classified here as "daily" also passes that check downstream.
ANNUALIZATION_DAYS = 252
SHARPE_TOLERANCE = 0.05


@dataclass(frozen=True)
class ConventionVerdict:
    """Outcome of reconciling a raw PnL series against its reported Sharpe."""

    is_usable: bool
    convention: str  # "daily" | "cumulative" | "indeterminate"
    reported_sharpe: float
    sharpe_as_daily: float
    sharpe_as_cumulative: float | None


def _annualized_sharpe(series: np.ndarray) -> float:
    if len(series) < 2:
        return 0.0
    std = float(np.std(series, ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(np.mean(series)) / std * math.sqrt(ANNUALIZATION_DAYS)


def detect(
    raw: np.ndarray, reported_sharpe: float, *, tolerance: float = SHARPE_TOLERANCE
) -> ConventionVerdict:
    """Reconcile ``raw`` against ``reported_sharpe`` under both conventions.

    Tries the series as-is (daily) first, then differenced (cumulative). Picks
    whichever reconciles within ``tolerance``; if both do, prefers daily, since
    that is the endpoint's documented contract. If neither reconciles, the
    series is indeterminate.
    """
    arr = np.asarray(raw, dtype=np.float64)
    sr_daily = _annualized_sharpe(arr)
    sr_cumulative = _annualized_sharpe(np.diff(arr)) if len(arr) >= 2 else None

    daily_ok = abs(sr_daily - reported_sharpe) <= tolerance
    cumulative_ok = sr_cumulative is not None and abs(sr_cumulative - reported_sharpe) <= tolerance

    if daily_ok:
        convention = "daily"
    elif cumulative_ok:
        convention = "cumulative"
    else:
        convention = "indeterminate"

    return ConventionVerdict(
        is_usable=convention != "indeterminate",
        convention=convention,
        reported_sharpe=reported_sharpe,
        sharpe_as_daily=sr_daily,
        sharpe_as_cumulative=sr_cumulative,
    )


def to_daily(raw: np.ndarray, convention: str) -> np.ndarray:
    """Convert ``raw`` to a discrete daily series of the same length as ``raw``.

    A cumulative curve is differenced with an implicit zero baseline before the
    first recorded day, so the output stays aligned with the caller's date
    array -- ``PnLStore.save_pnl`` requires dates and values to match in length.
    """
    arr = np.asarray(raw, dtype=np.float64)
    if convention == "daily":
        return arr
    if convention == "cumulative":
        return np.diff(arr, prepend=0.0)
    raise ValueError(f"cannot convert an indeterminate series to daily (convention={convention!r})")
