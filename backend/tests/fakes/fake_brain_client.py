"""Fake BRAIN client for offline integration tests.

Kept strictly under tests/fakes/ to ensure test-only code does not reside in the
privileged backend/app/services/brain directory guarded by test_brain_no_post.py.
"""

from __future__ import annotations

import uuid
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
            return {"records": [[f"2026-01-{i+1:02d}", 100.0 * (1 if i % 2 == 0 else -0.5)] for i in range(30)]}
        return {}
