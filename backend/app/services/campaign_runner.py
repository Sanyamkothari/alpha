"""Phase 1 — Resumable Database-Backed Campaign Runner (STRATEGY §6 & Task 4).

Executes unattended overnight multi-territory simulation campaigns with:
- 3-arm budget split (50% exploit, 30% random stratified, 20% plateau fill)
- Checkpointed state in SQLite (campaigns and campaign_tasks tables)
- Safe restart / crash recovery from the exact interrupted task
- 3-concurrent politeness and exponential backoff
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Callable

import structlog
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models.alphas import Alpha
from app.models.campaigns import Campaign, CampaignTask
from app.models.results import AlphaMetric
from app.services.allocator import plan_budget_allocation
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import Candidate, FamilySpec, expand
from app.services.correlation import ensure_alpha_pnl
from app.services.simulation_runner import pending_alpha_ids, run_batch

log = structlog.get_logger("campaign_runner")


def create_nightly_campaign(
    db: Session,
    budget: int = 200,
    *,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    seed: int | None = None,
) -> Campaign:
    """Creates a new database-persisted campaign with 3-arm budget allocation."""
    import random as py_random
    effective_seed = seed if seed is not None else py_random.randint(1, 2**31 - 1)

    plan = plan_budget_allocation(
        db,
        total_simulations=budget,
        region=region,
        universe=universe,
        delay=delay,
        seed=effective_seed,
    )

    name = f"nightly_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    campaign = Campaign(
        name=name,
        status="queued",
        budget_total=budget,
        budget_completed=0,
        seed=effective_seed,
        config_json={
            "region": region,
            "universe": universe,
            "delay": delay,
            "seed": effective_seed,
            "exploit_sims": plan.exploit_simulations,
            "random_stratified_sims": plan.random_stratified_simulations,
            "plateau_fill_sims": plan.plateau_fill_simulations,
            "quartile_boundaries": plan.quartile_boundaries,
        },
    )
    db.add(campaign)
    db.flush()

    for t in plan.tasks:
        territory_key = f"{t.field_code}:{t.operator_family}:{t.horizon_band}@{region}/{universe}/d{delay}"

        ctask = CampaignTask(
            campaign_id=campaign.id,
            arm=t.arm,
            territory_key=territory_key,
            field_code=t.field_code,
            operator_family=t.operator_family,
            wrapper_shape=t.wrapper_shape,
            denominator=t.denominator,
            status="queued",
            alphas_total=t.target_simulations,
            alphas_simulated=0,
            alphas_passed=0,
            quartile=t.quartile,
            error=None,
        )
        db.add(ctask)

    db.commit()
    db.refresh(campaign)
    log.info("campaign_created", campaign_id=campaign.id, tasks=len(plan.tasks), budget=budget, seed=effective_seed)
    return campaign


def execute_campaign(
    campaign_id: int,
    *,
    db_factory: Callable[[], Session] | None = None,
    simulate: bool = True,
) -> dict:
    """Executes or resumes a campaign, checkpointing progress to SQLite after each territory."""
    log.info("campaign_execution_started", campaign_id=campaign_id, simulate=simulate)

    with session_scope() as db:
        campaign = db.get(Campaign, campaign_id)
        if not campaign:
            raise ValueError(f"Campaign #{campaign_id} not found")
        campaign.status = "running"
        config = campaign.config_json or {}
        camp_region = config.get("region", "USA")
        camp_universe = config.get("universe", "TOP3000")
        camp_delay = int(config.get("delay", 1))
        db.commit()

    total_simulated = 0
    total_passed = 0

    while True:
        with session_scope() as db:
            task = (
                db.execute(
                    select(CampaignTask)
                    .where(
                        CampaignTask.campaign_id == campaign_id,
                        CampaignTask.status.in_(["queued", "running"]),
                    )
                    .order_by(CampaignTask.id)
                )
                .scalars()
                .first()
            )
            if not task:
                break
            task_id = task.id
            task_arm = task.arm
            task_field = task.field_code
            task_op = task.operator_family
            task_wrap = task.wrapper_shape
            task_denom = task.denominator
            task_budget = task.alphas_total

            task.status = "running"
            db.commit()

        log.info("campaign_task_started", task_id=task_id, arm=task_arm, field=task_field, op=task_op)

        try:
            horizon_band = None
            if task.territory_key and ":" in task.territory_key.split("@")[0]:
                horizon_band = task.territory_key.split("@")[0].split(":")[-1]
                if horizon_band not in {"short", "medium", "long"}:
                    horizon_band = None

            spec = FamilySpec(
                field_code=task_field,
                denominator=task_denom,
                operator_family=task_op,
                wrapper_shape=task_wrap,
                horizon_band=horizon_band,
                mechanism=f"Campaign Task #{task_id} ({task_arm})",
                grid_mode="standard",
            )
            settings = AlphaSettings(region=camp_region, universe=camp_universe, delay=camp_delay)
            family_key = spec.family_key(settings)

            # 1. Expand Candidates (always round max_candidates UP to whole surface size)
            surface_size = 49
            expansion_candidates = math.ceil((task_budget or surface_size) / surface_size) * surface_size
            with session_scope() as db:
                candidates = expand(
                    db,
                    spec,
                    base_settings=settings,
                    max_candidates=expansion_candidates,
                    arm=task_arm,
                    campaign_task_id=task_id,
                )

            # Check if expansion produced zero candidates (C3)
            if len(candidates) == 0:
                with session_scope() as db:
                    existing_simulated = (
                        db.scalar(
                            select(func.count(distinct(AlphaMetric.alpha_id)))
                            .join(Alpha, Alpha.id == AlphaMetric.alpha_id)
                            .where(Alpha.family_key == family_key)
                        )
                        or 0
                    )
                    t = db.get(CampaignTask, task_id)
                    if t:
                        if existing_simulated >= 1:
                            t.status = "skipped"
                            t.error = "surface already complete"
                        else:
                            t.status = "failed"
                            t.error = "expansion produced no candidates"
                        db.commit()
                log.warning(
                    "campaign_task_zero_candidates",
                    task_id=task_id,
                    family_key=family_key,
                )
                continue

            # 2. Save Candidates into Alpha Library
            created_count = 0
            with session_scope() as db:
                for cand in candidates:
                    try:
                        res = create_alpha(
                            db,
                            cand.expression,
                            cand.settings,
                            family_key=cand.family_key,
                            grid=cand.grid,
                            source="campaign_runner",
                            arm=task_arm,
                            campaign_task_id=task_id,
                        )
                        if res.created:
                            created_count += 1
                    except Exception:
                        pass

            # 3. Simulate Batch on BRAIN
            sim_count = 0
            pass_count = 0
            if simulate:
                ids = pending_alpha_ids(limit=task_budget, family_key=family_key)
                if ids:
                    batch_res = run_batch(ids)
                    sim_count = batch_res.simulated
                    pass_count = batch_res.passed_all_checks
                    # Post-batch PnL fetch for passing alphas
                    with session_scope() as db:
                        for aid in ids:
                            m = db.execute(select(AlphaMetric).where(AlphaMetric.alpha_id == aid)).scalars().first()
                            if m and m.passed_all_checks:
                                try:
                                    ensure_alpha_pnl(db, aid, allow_remote_fetch=True)
                                except Exception as exc:
                                    log.warning("pnl_fetch_failed", alpha_id=aid, error=str(exc))
                elif created_count == 0:
                    with session_scope() as db:
                        existing_simulated = (
                            db.scalar(
                                select(func.count(distinct(AlphaMetric.alpha_id)))
                                .join(Alpha, Alpha.id == AlphaMetric.alpha_id)
                                .where(Alpha.family_key == family_key)
                            )
                            or 0
                        )
                        t = db.get(CampaignTask, task_id)
                        if t:
                            if existing_simulated > 0 and (not candidates or existing_simulated >= len(candidates) or existing_simulated >= 14):
                                t.status = "skipped"
                                t.error = "surface already complete"
                            else:
                                t.status = "completed"
                            db.commit()
                    log.info(
                        "campaign_task_already_simulated",
                        task_id=task_id,
                        family_key=family_key,
                    )
                    continue

            # 4. Checkpoint task and campaign status in SQLite
            with session_scope() as db:
                t = db.get(CampaignTask, task_id)
                if t:
                    t.status = "completed"
                    t.alphas_simulated = sim_count
                    t.alphas_passed = pass_count
                db.flush()
                c = db.get(Campaign, campaign_id)
                if c:
                    c.budget_completed = (
                        db.scalar(
                            select(func.sum(CampaignTask.alphas_simulated)).where(
                                CampaignTask.campaign_id == campaign_id
                            )
                        )
                        or 0
                    )
                db.commit()

            total_simulated += sim_count
            total_passed += pass_count
            log.info(
                "campaign_task_completed",
                task_id=task_id,
                simulated=sim_count,
                passed=pass_count,
            )
        except Exception as exc:
            with session_scope() as db:
                t = db.get(CampaignTask, task_id)
                if t:
                    t.status = "failed"
                    t.error = f"{type(exc).__name__}: {exc}"
                    db.commit()
            log.error(
                "campaign_task_failed",
                task_id=task_id,
                campaign_id=campaign_id,
                error=str(exc),
            )
            continue

    # 5. Mark Campaign Complete
    with session_scope() as db:
        c = db.get(Campaign, campaign_id)
        if c:
            c.status = "completed"
            c.budget_completed = (
                db.scalar(
                    select(func.sum(CampaignTask.alphas_simulated)).where(
                        CampaignTask.campaign_id == campaign_id
                    )
                )
                or 0
            )
            db.commit()

    log.info(
        "campaign_completed",
        campaign_id=campaign_id,
        simulated=total_simulated,
        passed=total_passed,
    )
    return {
        "campaign_id": campaign_id,
        "status": "completed",
        "total_simulated": total_simulated,
        "total_passed": total_passed,
    }


def auto_resume_interrupted_campaigns() -> int:
    """Called on server startup to resume any interrupted campaigns."""
    with session_scope() as db:
        active_ids = [
            c.id
            for c in db.execute(
                select(Campaign).where(Campaign.status.in_(["running", "queued"]))
            )
            .scalars()
            .all()
        ]

    if not active_ids:
        return 0

    log.info("resuming_interrupted_campaigns", count=len(active_ids), campaign_ids=active_ids)
    for cid in active_ids:
        try:
            execute_campaign(cid, simulate=True)
        except Exception as exc:
            log.error("campaign_resume_failed", campaign_id=cid, error=str(exc))
    return len(active_ids)
