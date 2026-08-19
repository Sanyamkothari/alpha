"""Reproduction harness for the integration-review findings."""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import tests.conftest as C
from app.models import Base
from app.models.fields import DataField, Dataset


def build_db(path):
    eng = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(eng)
    SF = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    with SF() as s:
        s.add_all([
            C._op("rank", "cross_section", C._matrix("x", 0)),
            C._op("zscore", "cross_section", C._matrix("x", 0)),
            C._op("normalize", "cross_section", C._matrix("x", 0)),
            C._op("ts_zscore", "time_series", C._matrix("x", 0), C._window("d", 1)),
            C._op("ts_rank", "time_series", C._matrix("x", 0), C._window("d", 1)),
            C._op("ts_delta", "time_series", C._matrix("x", 0), C._window("d", 1)),
            C._op("ts_mean", "time_series", C._matrix("x", 0), C._window("d", 1)),
            C._op("ts_std_dev", "time_series", C._matrix("x", 0), C._window("d", 1)),
            C._op("ts_quantile", "time_series", C._matrix("x", 0), C._window("d", 1)),
            C._op("ts_decay_linear", "time_series", C._matrix("x", 0), C._window("d", 1)),
            C._op("ts_backfill", "time_series", C._matrix("x", 0), C._window("d", 1)),
            C._op("divide", "arithmetic", C._matrix("x", 0), C._matrix("y", 1)),
            C._op("group_rank", "group", C._matrix("x", 0), C._group("g", 1)),
            C._op("group_zscore", "group", C._matrix("x", 0), C._group("g", 1)),
        ])
        ds = Dataset(dataset_code="fnd", name="Fundamentals",
                     region="USA", universe="TOP3000", delay=1)
        s.add(ds); s.flush()
        for fc, ft, uc in [("liabilities", "MATRIX", 130), ("cap", "MATRIX", 900),
                           ("assets", "MATRIX", 140), ("sector", "GROUP", 50)]:
            s.add(DataField(field_code=fc, dataset_id=ds.id, category="fundamentals",
                            field_type=ft, region="USA", universe="TOP3000",
                            delay=1, coverage=0.9, user_count=uc))
        s.commit()
    return eng, SF


print("=" * 70)
print("FINDING 1 — campaign_runner references BatchResult.errored")
print("=" * 70)
import inspect

from app.services import campaign_runner, simulation_runner

src = inspect.getsource(campaign_runner)
has_errored_ref = ".errored" in src
has_errored_attr = hasattr(simulation_runner.BatchResult, "errored")
if has_errored_ref:
    print("  REPRODUCED: campaign_runner still references .errored")
elif has_errored_attr:
    print("  REPRODUCED: BatchResult still provides .errored shim")
else:
    print("  FIXED — campaign_runner uses .simulated/.passed_all_checks and BatchResult has no .errored shim")

print()
print("=" * 70)
print("FINDING 2 — plateau_fill arm expands to zero candidates")
print("=" * 70)
tmp = tempfile.mkdtemp()
eng, SF = build_db(os.path.join(tmp, "t.db"))
from app.services.alpha_library import AlphaSettings
from app.services.constructor import FamilySpec, expand

with SF() as db:
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    spec = FamilySpec(field_code="liabilities", denominator="cap",
                      operator_family="ts_zscore", wrapper_shape="rank",
                      grid_mode="standard")
    for budget in (49, 40, 19, 10):
        exp_candidates = math.ceil(budget / 49) * 49
        n = len(expand(db, spec, base_settings=settings, max_candidates=exp_candidates))
        print(f"  budget={budget:>3} (expanded {exp_candidates:>2})  ->  {n:>3} candidates emitted")

print()
print("=" * 70)
print("FINDING 3 — nightly budget overshoot in plan_budget_allocation")
print("=" * 70)
from app.services.allocator import plan_budget_allocation

with SF() as db:
    for budget in (200, 100, 50, 20):
        plan = plan_budget_allocation(db, total_simulations=budget)
        planned = sum(t.target_simulations for t in plan.tasks)
        arms = {}
        for t in plan.tasks:
            arms[t.arm] = arms.get(t.arm, 0) + t.target_simulations
        print(f"  budget={budget:>3}  declared="
              f"{plan.exploit_simulations}/{plan.random_stratified_simulations}"
              f"/{plan.plateau_fill_simulations}  ->  planned {planned:>3}  {arms}")

print()
print("=" * 70)
print("FINDING 4 — correlation gate vetoes the plateau ridge it depends on")
print("=" * 70)
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric, SimulationImport
from app.services import plateau as P
from app.services.alpha_library import create_alpha
from app.services.pnl_storage import PnLStore

with SF() as db:
    spec = FamilySpec(field_code="liabilities", denominator="cap",
                      operator_family="ts_zscore", wrapper_shape="rank")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    fk = spec.family_key(settings)
    ids = []
    for c in expand(db, spec, base_settings=settings, max_candidates=49):
        r = create_alpha(db, c.expression, c.settings, family_key=c.family_key,
                         grid=c.grid, source="repro")
        ids.append(r.alpha.id)
    db.commit()

    # A healthy broad ridge: every cell strong, siblings share a mechanism.
    rng = np.random.default_rng(7)
    store = PnLStore(base_dir=Path(tmp) / "pnl")
    dates = [f"d{i}" for i in range(1300)]
    common = rng.normal(0.002, 0.01, 1300)
    for aid in ids:
        si = SimulationImport(alpha_id=aid, source="brain_api",
                              raw_payload={}, is_latest=True)
        db.add(si); db.flush()
        db.add(AlphaMetric(simulation_import_id=si.id, alpha_id=aid, sharpe=1.9,
                           fitness=1.2, turnover=0.5, returns=0.2,
                           passed_all_checks=True))
        db.get(Alpha, aid).status = AlphaStatus.PASSED.value
        store.save_pnl(aid, dates, common + rng.normal(0, 0.002, 1300))
    db.commit()

    v = P.evaluate(db, fk, pnl_store=store)
    print(f"  surface points          : {len(v)}")
    print(f"  pass plateau ridge test : {sum(1 for x in v if x.is_plateau)}")
    print(f"  flagged is_correlated   : {sum(1 for x in v if x.is_correlated)}")
    print(f"  PROMOTED                : {sum(1 for x in v if x.promoted)}")
    print(f"  redundant_with set      : {sum(1 for x in v if x.redundant_with is not None)}")
    if v and v[0].correlation_collision:
        print(f"  sample veto reason      : {v[0].correlation_collision}")
    print("  TARGET after fix: exactly 1 promoted representative.")
