"""CLI script to manage and execute multi-territory simulation campaigns (Phase 1).

Usage:
    # Create and run a new nightly campaign (default 200 sims budget)
    python -m scripts.run_campaign --nightly

    # Create and run a campaign with custom budget
    python -m scripts.run_campaign --budget 100

    # Expand only without running remote simulations
    python -m scripts.run_campaign --budget 50 --no-simulate

    # Resume an existing or interrupted campaign
    python -m scripts.run_campaign --resume 1

    # List all campaigns and status
    python -m scripts.run_campaign --list
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.db import session_scope
from app.models.campaigns import Campaign
from app.services.campaign_runner import create_nightly_campaign, execute_campaign


def list_campaigns() -> None:
    with session_scope() as db:
        campaigns = db.execute(select(Campaign).order_by(Campaign.id.desc())).scalars().all()
        if not campaigns:
            print("No campaigns found.")
            return

        print(f"{'ID':<4} {'Name':<25} {'Status':<12} {'Progress':<12} {'Created At'}")
        print("-" * 75)
        for c in campaigns:
            prog = f"{c.budget_completed}/{c.budget_total}"
            print(f"{c.id:<4} {c.name:<25} {c.status:<12} {prog:<12} {str(c.created_at)[:19]}")


def inspect_campaign(campaign_id: int) -> None:
    with session_scope() as db:
        c = db.get(Campaign, campaign_id)
        if not c:
            print(f"Campaign #{campaign_id} not found.")
            return

        print(
            f"Campaign #{c.id}: {c.name} (Status: {c.status}, Completed: {c.budget_completed}/{c.budget_total})"
        )
        print("\nTasks:")
        print(
            f"{'ID':<4} {'Arm':<18} {'Territory':<40} {'Status':<10} {'Sim/Total':<10} {'Passed'}"
        )
        print("-" * 90)
        for t in c.tasks:
            prog = f"{t.alphas_simulated}/{t.alphas_total}"
            print(
                f"{t.id:<4} {t.arm:<18} {t.territory_key[:38]:<40} {t.status:<10} {prog:<10} {t.alphas_passed}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--nightly", action="store_true", help="create and launch standard 200-sim nightly campaign"
    )
    ap.add_argument("--budget", type=int, default=200, help="simulation budget")
    ap.add_argument("--resume", type=int, default=None, help="campaign ID to resume")
    ap.add_argument("--inspect", type=int, default=None, help="inspect campaign tasks")
    ap.add_argument("--list", action="store_true", help="list campaigns")
    ap.add_argument(
        "--no-simulate", action="store_true", help="expand only, skip remote simulations"
    )
    ap.add_argument("--region", default="USA")
    ap.add_argument("--universe", default="TOP3000")
    ap.add_argument("--delay", type=int, default=1)
    args = ap.parse_args()

    if args.list:
        list_campaigns()
        return 0

    if args.inspect:
        inspect_campaign(args.inspect)
        return 0

    if args.resume:
        print(f"Resuming campaign #{args.resume} ...")
        res = execute_campaign(args.resume, simulate=not args.no_simulate)
        print(f"Campaign finished: {res}")
        return 0

    # Create new campaign
    print(
        f"Creating campaign (budget: {args.budget} sims, region: {args.region}/{args.universe}/d{args.delay}) ..."
    )
    with session_scope() as db:
        c = create_nightly_campaign(
            db, budget=args.budget, region=args.region, universe=args.universe, delay=args.delay
        )
        cid = c.id

    print(f"Campaign #{cid} created with 3-arm budget split.")
    inspect_campaign(cid)

    print(f"\nLaunching campaign #{cid} execution ...")
    res = execute_campaign(cid, simulate=not args.no_simulate)
    print(f"\nExecution finished: {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
