"""Unit tests for Backfill PnL Matching & Settings Disambiguation (W9)."""

from __future__ import annotations

import numpy as np

from app.models.alphas import Alpha
from app.models.results import AlphaMetric, SimulationImport
from app.services.pnl_storage import PnLStore
from scripts.backfill_pnl import backfill_pnl_from_brain


def test_backfill_pnl_exact_settings_match(db_session, tmp_path):
    store = PnLStore(tmp_path / "pnl")

    # Seed 2 alphas with same expression but different settings (e.g. neutralization)
    a1 = Alpha(
        expression="rank(ts_zscore(close, 20))",
        expression_hash="bf_hash_1",
        neutralization="SUBINDUSTRY",
        decay=0,
        status="passed",
        is_valid=True,
    )
    a2 = Alpha(
        expression="rank(ts_zscore(close, 20))",
        expression_hash="bf_hash_2",
        neutralization="MARKET",
        decay=0,
        status="passed",
        is_valid=True,
    )
    db_session.add_all([a1, a2])
    db_session.flush()

    # Simulation imports
    s1 = SimulationImport(alpha_id=a1.id, raw_payload={"id": "remote_alpha_1"})
    s2 = SimulationImport(alpha_id=a2.id, raw_payload={"id": "remote_alpha_2"})
    db_session.add_all([s1, s2])
    db_session.flush()

    # Metrics with sharpe
    m1 = AlphaMetric(
        alpha_id=a1.id, simulation_import_id=s1.id, sharpe=1.65, fitness=1.2, turnover=0.2
    )
    m2 = AlphaMetric(
        alpha_id=a2.id, simulation_import_id=s2.id, sharpe=1.45, fitness=1.1, turnover=0.3
    )
    db_session.add_all([m1, m2])
    db_session.commit()

    rng = np.random.default_rng(42)
    daily_rets = rng.normal(0.001, 0.009, 252)
    records = [[f"2023-01-{i+1:02d}", float(r)] for i, r in enumerate(daily_rets)]

    brain_alphas = [
        {
            "id": "remote_alpha_1",
            "regular": {"code": "rank(ts_zscore(close, 20))"},
            "settings": {"neutralization": "SUBINDUSTRY", "decay": 0},
            "is": {"sharpe": 1.65},
        },
        {
            "id": "remote_alpha_2",
            "regular": {"code": "rank(ts_zscore(close, 20))"},
            "settings": {"neutralization": "MARKET", "decay": 0},
            "is": {"sharpe": 1.45},
        },
    ]

    class FakeBrain:
        def get_my_alphas(self, limit=100, offset=0):
            return brain_alphas

        def get_alpha_daily_pnl(self, remote_id):
            return records

    matched = backfill_pnl_from_brain(db_session, store, brain_client=FakeBrain())
    assert matched == 2
    assert store.load_pnl(a1.id) is not None
    assert store.load_pnl(a2.id) is not None
