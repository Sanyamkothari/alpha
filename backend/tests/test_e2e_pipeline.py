"""End-to-End Pipeline Integration Test.

Executes the complete alpha mining and vetting workflow in strict production order:
1. Stage 1 — Multi-Factor Composite Expansion across full (window x decay) surfaces
2. Stage 2 — Structural Degeneracy Pre-Screening
3. Stage 3 — Simulation Import with BRAIN checks[] persistence
4. Stage 4 — Daily PnL Sidecar Persistence (.npy format)
5. Stage 5 — Production Plateau Evaluation & Statistical Gating:
   a. Plateau shape (ratio >= 0.60 across >= 2 simulated neighbours)
   b. Pre-declared checks
   c. Multiple-testing haircut bar
   d. Sub-period stability & regime decay (SR_252 >= 0.65 * SR_full)
   e. Daily Bailey & Lopez de Prado DSR with Euler-Mascheroni expected max
   f. Empirical returns correlation barrier (rho < 0.55)
6. Stage 6 — Operator Decision & Shortlist Hysteresis State Machine
7. Stage 7 — Bloat-Controlled Evolutionary Genetic Search (Generation 1)
8. Stage 8 — Graded Discounted Thompson Sampling Allocator & Lifecycle Budgeting
9. Stage 9 — Executive Daily Report & UI Funnel Telemetry
"""

from __future__ import annotations

import math

import numpy as np
from fastapi.testclient import TestClient

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, ImportSource
from app.services.allocator import (
    DiscountedThompsonSampler,
    SimulationBudgetOrchestrator,
)
from app.services.composite_constructor import CompositeSpec, expand_composite
from app.services.constructor import GridAxes
from app.services.evolution import EvolutionConfig, evolve_generation
from app.services.plateau import evaluate
from app.services.pnl_storage import PnLStore
from app.services.proxy_calibration import is_degenerate_signal
from app.services.report import build
from app.services.result_import import import_result
from app.validator import ValidatorKB


def test_full_alpha_mining_e2e_pipeline(client: TestClient, db_session, tmp_path) -> None:
    # ------------------------------------------------ 1. Stage 1: Composite Constructor
    axes = GridAxes(
        ts_transforms=("ts_zscore",),
        windows=(5, 10, 22),
        cross_section=("rank", None),
        groups=(None,),
        neutralizations=("SUBINDUSTRY",),
        decays=(0, 4),
        truncations=(0.08,),
        universes=("TOP3000",),
    )
    spec = CompositeSpec(
        primary_field="close",
        secondary_field="volume",
        mechanism="momentum_volume_blend",
        mode="blend",
        axes=axes,
    )

    candidates = expand_composite(db_session, spec)
    assert len(candidates) >= 6, f"expected at least 6 surface points, got {len(candidates)}"

    # ------------------------------------------------ 2. Stage 2: Degeneracy Pre-Screening
    kb = ValidatorKB.from_session(db_session)
    for c in candidates:
        deg = is_degenerate_signal(c.expression, kb)
        assert not deg.is_degenerate

    # ------------------------------------------------ 3. Stage 3 & 4: Import & PnL Storage
    store = PnLStore(base_dir=tmp_path / "pnl")
    dates = [f"day_{i:04d}" for i in range(600)]
    np.random.seed(42)

    created_alphas: list[Alpha] = []
    for i, c in enumerate(candidates):
        alpha = Alpha(
            expression=c.expression,
            expression_hash=f"e2e_prod_hash_{i}_{c.expression}",
            family_key=c.family_key,
            region="USA",
            universe="TOP3000",
            delay=1,
            decay=c.settings.decay,
            neutralization=c.settings.neutralization,
            truncation=c.settings.truncation,
            feature_json={"grid": c.grid},
            status=AlphaStatus.TESTING.value,
            is_valid=True,
        )
        db_session.add(alpha)
        db_session.flush()

        # Import mock BRAIN simulation with checks[] payload
        target_sharpe = 2.45 + (0.05 if c.grid["window"] == 10 else -0.05)
        sim_payload = {
            "sharpe": target_sharpe,
            "fitness": 1.25,
            "turnover": 0.18,
            "checks": [
                {"name": "SHARPE", "result": "PASS"},
                {"name": "TURNOVER", "result": "PASS"},
                {"name": "FITNESS", "result": "PASS"},
            ],
            "passedAllChecks": True,
        }
        res_imp = import_result(db_session, alpha, sim_payload, source=ImportSource.PASTE.value)
        assert res_imp.metrics.passed_all_checks is True

        # Generate robust daily PnL matching reported Sharpe
        daily_sr = target_sharpe / math.sqrt(252)
        daily_pnl = np.random.normal(loc=daily_sr * 0.01, scale=0.01, size=len(dates))
        std = float(np.std(daily_pnl, ddof=1))
        if std > 0:
            target_mean = daily_sr * std
            daily_pnl = daily_pnl - np.mean(daily_pnl) + target_mean
        store.save_pnl(alpha.id, dates, daily_pnl)
        created_alphas.append(alpha)

    db_session.commit()

    # ------------------------------------------------ 5. Stage 5: Production Plateau Gating
    verdicts = evaluate(db_session, spec.family_key(), pnl_store=store)
    assert len(verdicts) == len(candidates)
    promoted = [v for v in verdicts if v.promoted]
    assert len(promoted) > 0, "expected candidates to clear the full production gating stack"

    for v in promoted:
        assert v.clears_bar is True
        assert v.is_plateau is True
        assert v.is_correlated is False
        assert v.subperiod_passed is True

    # ------------------------------------------------ 6. Stage 6: Operator Decision
    top_alpha_id = promoted[0].alpha_id
    resp_mark = client.post(f"/api/ui/alphas/{top_alpha_id}/mark", json={"action": "submitted"})
    assert resp_mark.status_code == 200
    assert resp_mark.json()["status"] == AlphaStatus.SUBMITTED.value

    # ------------------------------------------------ 7. Stage 7: Evolution
    evo_cfg = EvolutionConfig(population_size=5, max_depth=6, max_nodes=20)
    children = evolve_generation(db_session, [top_alpha_id], config=evo_cfg)
    assert len(children) > 0
    for child in children:
        assert child.grid["generation"] == 1
        assert child.grid["parent_id"] == top_alpha_id

    # ------------------------------------------------ 8. Stage 8: Bandit Allocator
    budget = SimulationBudgetOrchestrator.get_allocation(passed_alpha_count=1)
    assert budget.mode == "bootstrap"
    assert budget.explore_slots == 2

    bandit = DiscountedThompsonSampler(discount_factor=0.95)
    bandit.update("pv1", 1.0)
    scores = bandit.sample_scores(["pv1"])
    assert "pv1" in scores

    # ------------------------------------------------ 9. Stage 9: Reports & Telemetry
    resp_tel = client.get("/api/ui/telemetry")
    assert resp_tel.status_code == 200
    tel_data = resp_tel.json()
    assert tel_data["funnel"]["generated"] >= len(candidates)
    assert tel_data["funnel"]["submitted_portfolio"] >= 1

    report_md = build(db_session)
    assert "# Alpha research — daily report" in report_md
    assert "## Funnel Telemetry" in report_md
    assert "## Currently submitted on BRAIN" in report_md
