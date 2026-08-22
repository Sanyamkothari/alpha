"""PnL Convention Detector and Daily Returns Normalizer (W1)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ConventionVerdict:
    convention: str  # "daily" | "cumulative" | "indeterminate"
    reported_sharpe: float
    sharpe_as_daily: float
    sharpe_as_cumulative: float
    rel_error_daily: float
    rel_error_cumulative: float
    is_usable: bool


def _calc_annualized_sharpe(series: np.ndarray) -> float:
    """Compute annualized Sharpe from a 1D daily returns array."""
    if len(series) < 10:
        return 0.0
    mean = float(np.mean(series))
    std = float(np.std(series, ddof=1))
    if std < 1e-15:
        return 0.0
    return (mean / std) * math.sqrt(252)


def detect(
    raw_series: np.ndarray, reported_sharpe: float, tolerance: float = 0.25
) -> ConventionVerdict:
    """Detect whether raw_series is daily or cumulative PnL by matching reported Sharpe."""
    arr = np.asarray(raw_series, dtype=np.float64)

    if len(arr) < 20:
        return ConventionVerdict(
            convention="indeterminate",
            reported_sharpe=reported_sharpe,
            sharpe_as_daily=0.0,
            sharpe_as_cumulative=0.0,
            rel_error_daily=float("inf"),
            rel_error_cumulative=float("inf"),
            is_usable=False,
        )

    # Treat as daily returns directly
    sharpe_daily = _calc_annualized_sharpe(arr)

    # Treat as cumulative PnL → diff to get daily
    daily_from_cum = np.diff(arr)
    sharpe_cumulative = _calc_annualized_sharpe(daily_from_cum)

    abs_reported = abs(reported_sharpe) if reported_sharpe != 0 else 1e-10
    rel_error_daily = abs(sharpe_daily - reported_sharpe) / abs_reported
    rel_error_cumulative = abs(sharpe_cumulative - reported_sharpe) / abs_reported

    if rel_error_daily <= tolerance and rel_error_daily < rel_error_cumulative:
        convention = "daily"
        is_usable = True
    elif rel_error_cumulative <= tolerance and rel_error_cumulative < rel_error_daily:
        convention = "cumulative"
        is_usable = True
    else:
        convention = "indeterminate"
        is_usable = False

    return ConventionVerdict(
        convention=convention,
        reported_sharpe=reported_sharpe,
        sharpe_as_daily=sharpe_daily,
        sharpe_as_cumulative=sharpe_cumulative,
        rel_error_daily=rel_error_daily,
        rel_error_cumulative=rel_error_cumulative,
        is_usable=is_usable,
    )


def to_daily(raw_series: np.ndarray, convention: str) -> np.ndarray:
    """Convert raw PnL series to 1D daily returns array based on detected convention."""
    arr = np.asarray(raw_series, dtype=np.float64)
    if convention == "daily":
        return arr
    elif convention == "cumulative":
        return np.diff(arr)
    elif convention == "indeterminate":
        raise ValueError("Cannot normalize PnL series with indeterminate convention")
    else:
        raise ValueError(f"Unknown PnL convention: {convention}")
