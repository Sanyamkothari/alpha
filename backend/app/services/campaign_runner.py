"""Phase 1 — Resumable Database-Backed Campaign Runner (STRATEGY §6 & Task 4).

Executes unattended overnight multi-territory simulation campaigns with:
- 3-arm budget split (50% exploit, 30% random stratified, 20% plateau fill)
- Checkpointed state in SQLite (campaigns and campaign_tasks tables)
- Safe restart / crash recovery from the exact interrupted task
- 3-concurrent politeness and exponential backoff
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models.alphas import Alpha
from app.models.campaigns import Campaign, CampaignTask
from app.models.results import AlphaMetric
from app.services.allocator import plan_budget_allocation
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import Candidate, FamilySpec, expand
from app.services.simulation_runner import pending_alpha_ids, run_batch

log = structlog.get_logger("campaign_runner")


def create_nightly_campaign(
    db: Session,
    budget: int = 200,
    *,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
) -> Campaign:
    """Creates a new database-persisted campaign with 3-arm budget allocation."""
    plan = plan_budget_allocation(db, total_simulations=budget, region=region, universe=universe, delay=delay)

    name = f"nightly_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    campaign = Campaign(
        name=name,
        status="queued",
        budget_total=budget,
        budget_completed=0,
        config_json={
            "region": region,
            "universe": universe,
            "delay": delay,
            "exploit_sims": plan.exploit_simulations,
            "random_stratified_sims": plan.random_stratified_simulations,
            "plateau_fill_sims": plan.plateau_fill_simulations,
        },
    )
    db.add(campaign)
    db.flush()

    for t in plan.tasks:
        territory_key = f"{t.field_code}" + (f"/{t.denominator}" if t.denominator else "")
        territory_key += f":{t.operator_family}" + (f":{t.wrapper_shape}" if t.wrapper_shape else "")
        territory_key += f"@{region}/{universe}/d{delay}"

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
            error=None,
        )
        db.add(ctask)

    db.commit()
    db.refresh(campaign)
    log.info("campaign_created", campaign_id=campaign.id, tasks=len(plan.tasks), budget=budget)
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

        spec = FamilySpec(
            field_code=task_field,
            denominator=task_denom,
            operator_family=task_op,
            wrapper_shape=task_wrap,
            mechanism=f"Campaign Task #{task_id} ({task_arm})",
            grid_mode="standard",
        )
        settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
        family_key = spec.family_key(settings)

        # 1. Expand Candidates
        with session_scope() as db:
            candidates = expand(
                db,
                spec,
                base_settings=settings,
                max_candidates=task_budget,
                arm=task_arm,
                campaign_task_id=task_id,
            )

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
                sim_count = len(ids) - len(batch_res.errored)
                with session_scope() as db:
                    for aid in ids:
                        m = db.execute(select(AlphaMetric).where(AlphaMetric.alpha_id == aid)).scalars().first()
                        if m and m.passed_all_checks:
                            pass_count += 1

        # 4. Checkpoint task and campaign status in SQLite
        with session_scope() as db:
            t = db.get(CampaignTask, task_id)
            if t:
                t.status = "completed"
                t.alphas_simulated = sim_count
                t.alphas_passed = pass_count
            c = db.get(Campaign, campaign_id)
            if c:
                c.budget_completed += sim_count
            db.commit()

        total_simulated += sim_count
        total_passed += pass_count
        log.info(
            "campaign_task_completed",
            task_id=task_id,
            simulated=sim_count,
            passed=pass_count,
        )

    # 5. Mark Campaign Complete
    with session_scope() as db:
        c = db.get(Campaign, campaign_id)
        if c:
            c.status = "completed"
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
