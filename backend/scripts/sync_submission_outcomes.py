"""Sync WorldQuant BRAIN platform submission outcomes.

Fetches the latest platform review status for submitted alphas via authenticated
GET /alphas/{brain_id} queries without performing any automated submissions.

Usage:
    python -m scripts.sync_submission_outcomes [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime

import structlog
from sqlalchemy import select

from app.db import session_scope
from app.models.alphas import Alpha, AlphaStatusHistory
from app.models.enums import AlphaStatus, OutcomeSource, PlatformOutcome
from app.models.results import SimulationImport
from app.services.brain import BrainClient

log = structlog.get_logger("sync_submission_outcomes")


def _extract_brain_id(alpha: Alpha, db) -> str | None:
    # 1. From comments
    if alpha.comments:
        m = re.search(r"alpha\s+([A-Za-z0-9]+)", alpha.comments)
        if m:
            return m.group(1)

    # 2. From status history
    hist = db.execute(
        select(AlphaStatusHistory)
        .where(AlphaStatusHistory.alpha_id == alpha.id)
        .order_by(AlphaStatusHistory.id.desc())
    ).scalars().all()
    for h in hist:
        if h.note and "brain sim" in h.note:
            parts = h.note.split()
            if len(parts) >= 3:
                return parts[-1]

    # 3. From simulation_imports
    sim = db.execute(
        select(SimulationImport)
        .where(SimulationImport.alpha_id == alpha.id)
        .order_by(SimulationImport.id.desc())
    ).scalars().first()
    if sim and sim.raw_payload:
        pid = sim.raw_payload.get("id")
        if pid:
            return str(pid)

    return None


def run(dry_run: bool = False) -> dict[str, int]:
    counts = {"submitted_found": 0, "checked": 0, "updated": 0, "unresolved": 0}

    with session_scope() as db:
        submitted = list(
            db.execute(
                select(Alpha).where(Alpha.status == AlphaStatus.SUBMITTED.value)
            ).scalars().all()
        )
        counts["submitted_found"] = len(submitted)
        if not submitted:
            log.info("no_submitted_alphas")
            return counts

        with BrainClient() as brain:
            for alpha in submitted:
                brain_id = _extract_brain_id(alpha, db)
                if not brain_id:
                    counts["unresolved"] += 1
                    log.warning("cannot_resolve_brain_id", alpha_id=alpha.id)
                    continue

                try:
                    payload = brain.alpha(brain_id)
                except Exception as e:
                    log.error("fetch_alpha_failed", alpha_id=alpha.id, brain_id=brain_id, error=str(e))
                    counts["unresolved"] += 1
                    continue

                counts["checked"] += 1
                status = str(payload.get("status") or "").upper()
                stage = str(payload.get("stage") or "").upper()
                date_sub = payload.get("dateSubmitted")

                # Map platform response to PlatformOutcome
                outcome = None
                if status in ("ACCEPTED", "APPROVED") or stage in ("PROD", "OS"):
                    outcome = PlatformOutcome.ACCEPTED.value
                elif status in ("REJECTED", "DECLINED"):
                    outcome = PlatformOutcome.REJECTED.value
                elif status in ("IN_REVIEW", "SUBMITTED", "PENDING"):
                    outcome = PlatformOutcome.IN_REVIEW.value

                if outcome and outcome != alpha.platform_outcome:
                    if not dry_run:
                        alpha.platform_outcome = outcome
                        alpha.outcome_source = OutcomeSource.API.value
                        if date_sub:
                            try:
                                alpha.outcome_date = datetime.fromisoformat(date_sub.replace("Z", "+00:00")).date()
                            except Exception:
                                alpha.outcome_date = date.today()
                        else:
                            alpha.outcome_date = date.today()

                        db.add(
                            AlphaStatusHistory(
                                alpha_id=alpha.id,
                                from_status=alpha.status,
                                to_status=alpha.status,
                                note=f"outcome:{outcome} (api sync from BRAIN {brain_id})",
                            )
                        )
                    counts["updated"] += 1
                    log.info("updated_outcome", alpha_id=alpha.id, brain_id=brain_id, outcome=outcome)
                else:
                    log.info("outcome_unchanged", alpha_id=alpha.id, brain_id=brain_id, status=status, stage=stage)

    log.info("sync_complete", **counts)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(run(dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
