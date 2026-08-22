"""Fake BRAIN client for offline integration tests.

Kept strictly under tests/fakes/ to ensure test-only code does not reside in the
privileged backend/app/services/brain directory guarded by test_brain_no_post.py.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, timedelta
from typing import Any

# Matches app.services.pnl_convention.ANNUALIZATION_DAYS -- the fake daily-pnl
# series below is constructed so its recomputed Sharpe reconciles with the
# Sharpe this fixture reports, the way a real BRAIN alpha's would.
_ANNUALIZATION_DAYS = 252
_PNL_SERIES_LENGTH = 750  # comfortably above correlation.MIN_COMMON_TRADING_DAYS
_PNL_STD = 100.0


def _outcome_for(brain_id: str) -> tuple[bool, float, float, float]:
    """Deterministic pass/fail and metrics, shared by ``alpha()`` and ``get_json()``.

    Both methods must agree on the same alpha's Sharpe, or the two independently
    derive numbers that do not reconcile -- exactly the failure mode
    app.services.pnl_convention.detect() exists to catch.
    """
    val = sum(ord(c) for c in brain_id)
    is_pass = val % 3 != 0
    sharpe = 1.65 if is_pass else 0.45
    fitness = 1.20 if is_pass else 0.30
    turnover = 0.25 if is_pass else 0.85
    return is_pass, sharpe, fitness, turnover


class FakeBrainClient:
    """Mock BRAIN API client returning deterministic simulated results."""

    def __init__(self, *args, **kwargs) -> None:
        self.simulations_called: list[dict[str, Any]] = []
        self._auth_ok = True

    def __enter__(self) -> FakeBrainClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def simulate(
        self,
        expression: str,
        sim_settings: Any = None,
        *,
        poll_seconds: float | None = None,
        max_wait_seconds: float = 900.0,
        timing: dict | None = None,
        **kwargs: Any,
    ) -> str:
        """Simulate an alpha expression and return its remote alpha id."""
        alpha_id = f"fake_{uuid.uuid4().hex[:8]}"
        self.simulations_called.append(
            {
                "expression": expression,
                "settings": sim_settings,
                "alpha_id": alpha_id,
            }
        )
        if timing is not None:
            timing["seconds"] = 1.25
        return alpha_id

    def alpha(self, brain_id: str) -> dict[str, Any]:
        """Return detail payload for an alpha."""
        is_pass, sharpe, fitness, turnover = _outcome_for(brain_id)
        return {
            "id": brain_id,
            "status": "SUBMITTED",
            "stage": "UNSUBMITTED",
            "dateSubmitted": "2026-08-15T12:00:00Z",
            "is": {
                "sharpe": sharpe,
                "fitness": fitness,
                "turnover": turnover,
                "returns": 0.18 if is_pass else 0.04,
                "margin": 0.00045 if is_pass else 0.00005,
                "drawdown": 0.08,
                "checks": [
                    {
                        "name": "LOW_SHARPE",
                        "result": "PASS" if is_pass else "FAIL",
                        "value": sharpe,
                        "limit": 1.25,
                    },
                    {
                        "name": "LOW_FITNESS",
                        "result": "PASS" if is_pass else "FAIL",
                        "value": fitness,
                        "limit": 1.0,
                    },
                    {
                        "name": "HIGH_TURNOVER",
                        "result": "PASS" if is_pass else "FAIL",
                        "value": turnover,
                        "limit": 0.70,
                    },
                ],
            },
        }

    def get_json(self, path: str, *args: Any, **kwargs: Any) -> Any:
        """Mock GET JSON for recordsets and endpoints."""
        if "daily-pnl" in path:
            # path shape: /alphas/{brain_id}/recordsets/daily-pnl
            brain_id = path.strip("/").split("/")[1]
            _, sharpe, _, _ = _outcome_for(brain_id)

            # An alternating two-value series has an exactly controllable sample
            # mean and a sample std (ddof=1) within a fraction of a percent of
            # `_PNL_STD` at this length, which is what lets the reported Sharpe
            # reconcile with the series within pnl_convention.SHARPE_TOLERANCE.
            mean = sharpe * _PNL_STD / math.sqrt(_ANNUALIZATION_DAYS)
            start = date(2024, 1, 1)
            records = [
                [
                    (start + timedelta(days=i)).isoformat(),
                    mean + _PNL_STD if i % 2 == 0 else mean - _PNL_STD,
                ]
                for i in range(_PNL_SERIES_LENGTH)
            ]
            return {"records": records}
        if "check" in path:
            return {
                "checks": [
                    {
                        "name": "REGULAR_SUBMISSION",
                        "result": "PASS",
                        "value": 1.0,
                        "limit": 4.0,
                    }
                ]
            }
        return {}

    def get_submission_check(self, alpha_id: str | None = None) -> dict[str, Any]:
        """Mock platform submission check."""
        return {
            "checks": [
                {
                    "name": "REGULAR_SUBMISSION",
                    "result": "PASS",
                    "value": 1.0,
                    "limit": 4.0,
                }
            ]
        }
