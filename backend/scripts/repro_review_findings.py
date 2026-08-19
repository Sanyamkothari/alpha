"""Reproduction and verification script for REVIEW.md findings.

Runs an end-to-end simulation of a 49-point family through constructor expansion,
synthetic simulation import, independent PnL generation, plateau gating with
representative selection, report generation, and surface API checks.
"""

from __future__ import annotations

import math
import tempfile
import zlib
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app
from app.models import Base
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, ImportSource
from app.models.results import AlphaMetric, SimulationImport
from app.seeds import load_fields, load_lookups, load_operators
from app.services import pnl_storage
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


def _seed_for(*parts: object) -> int:
    """Deterministic across processes.

    hash() on a tuple containing a str is salted by PYTHONHASHSEED, so seeding from
    it makes this harness generate different PnL on every run — and a reproduction
    that does not reproduce cannot be a regression baseline.
    """
    return zlib.crc32("|".join(str(p) for p in parts).encode()) % (2**31)


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
        # The report and the surfaces API resolve their own store via get_pnl_store().
        # Passing pnl_store= to evaluate() only would leave those two paths with no PnL,
        # so they would promote nothing and "verify" a report that is empty by
        # construction. Redirect the process-wide default instead.
        pnl_storage._default_store = store
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
            rng = np.random.default_rng(_seed_for(w, d, alpha.id, "repro"))
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
        verdicts = evaluate(db, spec.family_key())

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

        # Exact counts, not floors. The seed is fixed, so any movement here is a real
        # change in the filter and should be read as one — a ">= 9" floor would have
        # sat quietly through a drift from 36 to 12.
        expected = {
            "simulated": 49,
            "clears": 49,
            "plateau": 49,
            "subperiod": 36,
            "dsr": 12,
            "correlated": 0,
            "promoted": 1,
            "redundant": 11,
        }
        actual = {
            "simulated": simulated_cnt,
            "clears": clears_cnt,
            "plateau": plateau_cnt,
            "subperiod": subperiod_cnt,
            "dsr": dsr_cnt,
            "correlated": corr_cnt,
            "promoted": promoted_cnt,
            "redundant": redundant_cnt,
        }
        if actual != expected:
            drift = {k: (expected[k], actual[k]) for k in expected if expected[k] != actual[k]}
            raise AssertionError(
                f"funnel drifted from the recorded baseline (expected, actual): {drift}\n"
                "If the change is intended, update the baseline in this file."
            )
        print("✓ All funnel asserts passed (exact match against recorded baseline)")

        # 6. Generate and verify report
        report_text = build_report(db)
        assert "| Family | Mode | Simulated | 1. Checks | 2. Plateau | 3. Sub-Period | 4. DSR/Cold-Start | 5. Orthogonal | 6. Representative | Promoted |" in report_text
        assert "What to try next (allocator suggestions)" in report_text
        assert "seed_all" not in report_text  # catalog was seeded
        # The header rendering alone proves nothing: the report must actually carry the
        # promotion through. Without this the harness happily passes over an empty
        # shortlist, which is exactly the state the review found.
        assert "Nothing survived the filter" not in report_text, (
            "report shows an empty shortlist even though the funnel promoted an alpha"
        )
        promoted_expr = next(v.expression for v in verdicts if v.promoted)
        assert promoted_expr in report_text, (
            f"promoted alpha {promoted_expr!r} is missing from the report shortlist"
        )
        print("✓ Report generation passed (10-column table + promoted alpha present)")

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
            axis_w = set(data["windows"])
            axis_d = set(data["decays"])
            off_axis = [
                k for k in data["surfaces"][0]["cells"]
                if int(k.split(":")[0]) not in axis_w or int(k.split(":")[1]) not in axis_d
            ]
            assert not off_axis, f"cells rendered off the returned axes: {off_axis[:5]}"
            promoted_cells = [
                k for k, c in data["surfaces"][0]["cells"].items() if c.get("promoted")
            ]
            assert len(promoted_cells) == 1, (
                f"expected exactly 1 promoted cell on the surface, got {len(promoted_cells)}"
            )
            print("✓ Surface API passed (7x7 axes, 49/49 cells on-axis, 1 promoted cell)")
        finally:
            app.dependency_overrides.pop(get_db, None)

    print("\n" + "=" * 60)
    print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
