"""Fake BRAIN client for offline integration tests.

Kept strictly under tests/fakes/ to ensure test-only code does not reside in the
privileged backend/app/services/brain directory guarded by test_brain_no_post.py.
"""

from __future__ import annotations

import uuid
import math
import random
from datetime import date, timedelta
from typing import Any


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
        self.simulations_called.append({
            "expression": expression,
            "settings": sim_settings,
            "alpha_id": alpha_id,
        })
        if timing is not None:
            timing["seconds"] = 1.25
        return alpha_id

    def alpha(self, brain_id: str) -> dict[str, Any]:
        """Return detail payload for an alpha."""
        val = sum(ord(c) for c in brain_id)
        is_pass = (val % 3 != 0)
        sharpe = 1.65 if is_pass else 0.45
        fitness = 1.20 if is_pass else 0.30
        turnover = 0.25 if is_pass else 0.85
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
                    {"name": "LOW_SHARPE", "result": "PASS" if is_pass else "FAIL", "value": sharpe, "limit": 1.25},
                    {"name": "LOW_FITNESS", "result": "PASS" if is_pass else "FAIL", "value": fitness, "limit": 1.0},
                    {"name": "HIGH_TURNOVER", "result": "PASS" if is_pass else "FAIL", "value": turnover, "limit": 0.70},
                ],
            },
        }

    def get_json(self, path: str, *args: Any, **kwargs: Any) -> Any:
        """Mock GET JSON for recordsets and endpoints."""
        if "daily-pnl" in path:
            return {"records": self._daily_pnl_records(self._id_from_path(path))}
        return {}

    @staticmethod
    def _id_from_path(path: str) -> str:
        """Pull the alpha id out of ``/alphas/{id}/recordsets/daily-pnl``."""
        parts = [p for p in path.split("/") if p]
        return parts[1] if len(parts) > 1 else ""

    def _daily_pnl_records(self, brain_id: str, n_days: int = 504) -> list[list[Any]]:
        """A PnL series whose annualized Sharpe matches what ``alpha()`` reports.

        The previous fixture served an alternating +/- series for every alpha while
        reporting Sharpe 1.65, so the series and the metric described different
        objects. Nothing caught it until ``ensure_alpha_pnl`` began reconciling, and
        an integration test whose data contradicts itself cannot validate the
        pipeline it is standing in for.
        """
        target_sharpe = self.alpha(brain_id)["is"]["sharpe"]

        # Deterministic pseudo-random walk, then rescaled to hit the target exactly.
        rng = random.Random(sum(ord(c) for c in brain_id) or 1)
        raw = [rng.gauss(0.0, 1.0) for _ in range(n_days)]
        mean = sum(raw) / n_days
        var = sum((x - mean) ** 2 for x in raw) / (n_days - 1)
        std = math.sqrt(var) or 1.0
        centred = [(x - mean) / std for x in raw]  # mean 0, sample std 1

        daily_mean = target_sharpe / math.sqrt(252.0)
        series = [(x + daily_mean) * 1000.0 for x in centred]

        start = date(2024, 1, 1)
        return [[(start + timedelta(days=i)).isoformat(), round(v, 6)] for i, v in enumerate(series)]
