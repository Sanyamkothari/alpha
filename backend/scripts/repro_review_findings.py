"""Reproduction and verification script for REVIEW.md findings.

Runs an end-to-end simulation of a 49-point family through constructor expansion,
synthetic simulation import, independent PnL generation, plateau gating with
representative selection, report generation, and surface API checks.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_db
from app.main import app
from app.models import Base
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, ImportSource
from app.models.results import AlphaMetric, SimulationImport
from app.seeds import load_fields, load_lookups, load_operators
from app.services.constructor import (
    STANDARD_DECAYS,
    STANDARD_WINDOWS,
    FamilySpec,
    GridAxes,
    expand,
)
from app.services.plateau import evaluate
from app.services.pnl_storage import PnLStore
from app.services.report import build as build_report
from scripts._cli import cli_main


@cli_main
def main() -> None:
    print("=" * 60)
    print("REVIEW.md Reproduction & Verification Harness")
    print("=" * 60)

    # 1. Setup isolated database
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "repro.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    with session_factory() as db:
        load_lookups.load(db)
        load_operators.load(db)
        load_fields.load(db)
        db.commit()
        print("✓ Database initialized and seeded")

        # 2. Expand a 49-point family on 'close'
        spec = FamilySpec(
            field_code="close",
            operator_family="ts_zscore",
            axes=GridAxes(
                ts_transforms=("ts_zscore",),
                cross_section=("rank",),
                groups=(None,),
                windows=tuple(STANDARD_WINDOWS),
                decays=tuple(STANDARD_DECAYS),
                neutralizations=("SUBINDUSTRY",),
                truncations=(0.08,),
                universes=("TOP3000",),
            ),
        )
        candidates = expand(db, spec)
        print(f"✓ Expanded family: {len(candidates)} candidates")
        assert len(candidates) == 49, f"Expected 49 candidates, got {len(candidates)}"

        # 3. Create Alphas and synthetic simulation metrics with known plateau
        store = PnLStore(tmp_dir / "pnl")
        dates = [f"d_{i:04d}" for i in range(1236)]
        daily_vol = 0.01

        alphas: list[Alpha] = []
        for i, c in enumerate(candidates):
            w = c.grid["window"]
            d = c.grid["decay"]

            # Plateau region: windows 20..60, decays 2..8
            in_plateau = (20 <= w <= 60) and (2 <= d <= 8)
            sharpe = 2.45 if in_plateau else 0.80
            fitness = 1.60 if in_plateau else 0.50

            alpha = Alpha(
                expression=c.expression,
                normalized_expression=c.expression,
                expression_hash=f"repro_hash_{w}_{d}",
                family_key=c.family_key,
                status=AlphaStatus.PASSED.value,
                source="constructor",
                region="USA",
                universe="TOP3000",
                delay=1,
                neutralization="SUBINDUSTRY",
                decay=d,
                truncation=0.08,
                is_valid=True,
                generation=0,
                feature_json={
                    "structural_hash": "repro_shash_close_zscore",
                    "grid": c.grid,
                },
            )
            db.add(alpha)
            db.flush()

            si = SimulationImport(alpha_id=alpha.id, source=ImportSource.BRAIN_API.value, raw_payload={})
            db.add(si)
            db.flush()

            db.add(
                AlphaMetric(
                    simulation_import_id=si.id,
                    alpha_id=alpha.id,
                    sharpe=sharpe,
                    fitness=fitness,
                    turnover=0.18,
                    returns=0.15,
                    passed_all_checks=True,
                )
            )
            db.flush()

            # 4. Generate independent stationary daily PnL vectors matching reported Sharpe
            daily_sharpe = sharpe / math.sqrt(252)
            rng = np.random.default_rng(abs(hash((w, d, alpha.id, "repro"))) % (2**31))
            half = len(dates) // 2
            n1 = rng.normal(0, daily_vol, half)
            n1 = (n1 - np.mean(n1)) / float(np.std(n1, ddof=1)) * daily_vol
            n2 = rng.normal(0, daily_vol, len(dates) - half)
            n2 = (n2 - np.mean(n2)) / float(np.std(n2, ddof=1)) * daily_vol
            noise = np.concatenate([n1, n2])
            pnl = (daily_sharpe * daily_vol) + noise
            store.save_pnl(alpha.id, dates, pnl)
            alphas.append(alpha)

        db.commit()
        print("✓ Created 49 Alpha records and generated independent PnL series")

        # 5. Evaluate family
        verdicts = evaluate(db, spec.family_key(), pnl_store=store)

        simulated_cnt = len(verdicts)
        clears_cnt = sum(1 for v in verdicts if v.clears_bar)
        plateau_cnt = sum(1 for v in verdicts if v.is_plateau)
        subperiod_cnt = sum(1 for v in verdicts if v.subperiod_passed)
        dsr_cnt = sum(1 for v in verdicts if v.dsr_passed)
        corr_cnt = sum(1 for v in verdicts if v.is_correlated)
        promoted_cnt = sum(1 for v in verdicts if v.promoted)
        redundant_cnt = sum(1 for v in verdicts if v.redundant_with is not None)

        print("\n--- Gating Funnel Breakdown ---")
        print(f"1. Simulated:       {simulated_cnt:>2}")
        print(f"2. BRAIN Checks:    {clears_cnt:>2}")
        print(f"3. Plateau Ridge:   {plateau_cnt:>2}")
        print(f"4. Sub-Period:      {subperiod_cnt:>2}")
        print(f"5. DSR:             {dsr_cnt:>2}")
        print(f"6. Correlated:      {corr_cnt:>2}  (Must be 0!)")
        print(f"7. Promoted:        {promoted_cnt:>2}  (Must be >= 1!)")
        print(f"8. Redundant:       {redundant_cnt:>2}  (Demoted plateau twins)")
        print("-------------------------------\n")

        assert simulated_cnt == 49, f"Expected 49 simulated, got {simulated_cnt}"
        assert clears_cnt == 49, f"Expected 49 clearing checks, got {clears_cnt}"
        assert plateau_cnt >= 9, f"Expected >= 9 plateau points, got {plateau_cnt}"
        assert subperiod_cnt >= 9, f"Expected >= 9 subperiod passed, got {subperiod_cnt}"
        assert dsr_cnt >= 9, f"Expected >= 9 DSR passed, got {dsr_cnt}"
        assert corr_cnt == 0, f"Expected 0 correlated collisions, got {corr_cnt}"
        assert promoted_cnt >= 1, f"Expected >= 1 promoted representative, got {promoted_cnt}"
        assert redundant_cnt >= 8, f"Expected >= 8 redundant twins, got {redundant_cnt}"
        print("✓ All funnel asserts passed!")

        # 6. Generate and verify report
        report_text = build_report(db)
        assert "| Family | Mode | Simulated | 1. Checks | 2. Plateau | 3. Sub-Period | 4. DSR/Cold-Start | 5. Orthogonal | 6. Representative | Promoted |" in report_text
        assert "What to try next (allocator suggestions)" in report_text
        assert "seed_all" not in report_text  # catalog was seeded
        print("✓ Report generation passed (10-column table verified)")

        # 7. Verify UI surfaces endpoint via TestClient
        def _get_db():
            with session_factory() as s:
                yield s

        app.dependency_overrides[get_db] = _get_db
        try:
            client = TestClient(app)
            resp = client.get("/api/ui/surfaces", params={"family": spec.family_key()})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["windows"]) == 7
            assert len(data["decays"]) == 7
            assert len(data["surfaces"][0]["cells"]) == 49
            print("✓ Surface API endpoint passed (7x7 axes and 49/49 cells verified)")
        finally:
            app.dependency_overrides.pop(get_db, None)

    print("\n" + "=" * 60)
    print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
