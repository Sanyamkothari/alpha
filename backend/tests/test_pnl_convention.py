"""Unit tests for PnL Convention Detector and Daily Returns Normalizer (W1)."""

from __future__ import annotations

import math

import numpy as np

from app.services.pnl_convention import detect, to_daily


def test_detect_daily_pnl() -> None:
    rng = np.random.default_rng(42)
    daily_rets = rng.normal(0.001, 0.009, 500)
    sharpe = (np.mean(daily_rets) / np.std(daily_rets, ddof=1)) * math.sqrt(252)
    verdict = detect(daily_rets, sharpe)
    assert verdict.convention == "daily"
    assert verdict.is_usable is True
    norm = to_daily(daily_rets, verdict.convention)
    assert np.allclose(norm, daily_rets)


def test_detect_cumulative_pnl() -> None:
    rng = np.random.default_rng(42)
    daily_rets = rng.normal(0.001, 0.009, 500)
    cum_pnl = np.cumsum(daily_rets)
    sharpe = (np.mean(daily_rets) / np.std(daily_rets, ddof=1)) * math.sqrt(252)
    verdict = detect(cum_pnl, sharpe)
    assert verdict.convention == "cumulative"
    assert verdict.is_usable is True
    norm = to_daily(cum_pnl, verdict.convention)
    assert len(norm) == len(cum_pnl) - 1
    assert np.allclose(norm, daily_rets[1:], atol=1e-6)


def test_detect_indeterminate_on_garbage() -> None:
    arr = np.ones(100)
    verdict = detect(arr, 1.5)
    assert verdict.convention == "indeterminate"
    assert verdict.is_usable is False


def test_detect_indeterminate_on_short_series() -> None:
    arr = np.array([1.0, 2.0, 3.0])
    verdict = detect(arr, 1.5)
    assert verdict.convention == "indeterminate"
    assert verdict.is_usable is False


def test_detect_preserves_sign_and_tolerates_noise() -> None:
    rng = np.random.default_rng(123)
    daily_rets = rng.normal(-0.0005, 0.01, 600)
    sharpe = (np.mean(daily_rets) / np.std(daily_rets, ddof=1)) * math.sqrt(252)
    verdict = detect(daily_rets, sharpe)
    assert verdict.convention == "daily"
    assert verdict.is_usable is True
