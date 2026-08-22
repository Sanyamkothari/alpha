"""Submission Stopping Rule & Exhaustion Boundary Service (W4).

Enforces stopping boundaries across three levels:
1. Per-Alpha Stopping: Blocks resubmission of already-submitted alphas, terminal
   check failures (SELF_CORRELATION, PROD_CORRELATION, MATCHES_COMPETITION), and caps
   lifetime attempts to 2.
2. Per-Family / Territory Stopping: Blocks families with >= 3 consecutive rejections
   or >= 5 total rejections with 0 acceptances (diminishing returns).
3. Platform Quota Preflight: Respects daily platform submission headroom (<= 4/day).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha, SubmissionAttempt
from app.models.enums import PlatformOutcome, SubmissionCheckName, SubmissionResult
from app.services.brain.client import BrainClient
from app.services.submission_preflight import PreflightVerdict, check_submission_preflight

log = structlog.get_logger("submission_stopping")

TERMINAL_CHECKS: frozenset[str] = frozenset(
    {
        SubmissionCheckName.SELF_CORRELATION.value,
        SubmissionCheckName.PROD_CORRELATION.value,
        SubmissionCheckName.MATCHES_COMPETITION.value,
        SubmissionCheckName.UNITS.value,
    }
)

MAX_ATTEMPTS_PER_ALPHA = 2
MAX_CONSECUTIVE_FAMILY_REJECTIONS = 3
MAX_UNPRODUCTIVE_FAMILY_REJECTIONS = 5


@dataclass(frozen=True)
class AlphaStoppingVerdict:
    can_submit: bool
    alpha_id: int
    attempt_count: int
    terminal_check_failed: str | None = None
    already_submitted: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class FamilyStoppingVerdict:
    can_submit: bool
    family_key: str | None
    total_attempts: int
    rejected_count: int
    consecutive_rejections: int
    accepted_count: int
    exhausted: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class SubmissionEligibility:
    can_submit: bool
    alpha_id: int
    family_key: str | None
    alpha_verdict: AlphaStoppingVerdict
    family_verdict: FamilyStoppingVerdict
    preflight_verdict: PreflightVerdict | None = None
    blocking_reasons: list[str] = field(default_factory=list)


def check_alpha_stopping_bound(db: Session, alpha_id: int) -> AlphaStoppingVerdict:
    """Evaluate per-alpha stopping criteria based on lifetime submission attempts."""
    alpha = db.get(Alpha, alpha_id)
    if not alpha:
        return AlphaStoppingVerdict(
            can_submit=False,
            alpha_id=alpha_id,
            attempt_count=0,
            reason=f"alpha #{alpha_id} not found",
        )

    attempts = list(
        db.execute(
            select(SubmissionAttempt)
            .where(SubmissionAttempt.alpha_id == alpha_id)
            .order_by(SubmissionAttempt.id.asc())
        )
        .scalars()
        .all()
    )
    attempt_count = len(attempts)

    # 1. Check if already successfully submitted
    if alpha.platform_outcome == PlatformOutcome.SUBMITTED.value or any(
        a.result == SubmissionResult.SUBMITTED.value for a in attempts
    ):
        return AlphaStoppingVerdict(
            can_submit=False,
            alpha_id=alpha_id,
            attempt_count=attempt_count,
            already_submitted=True,
            reason=f"alpha #{alpha_id} already submitted",
        )

    # 2. Check for terminal check failures in prior attempts
    for att in attempts:
        if att.failed_check:
            chk_name = str(att.failed_check).upper()
            if chk_name in TERMINAL_CHECKS:
                return AlphaStoppingVerdict(
                    can_submit=False,
                    alpha_id=alpha_id,
                    attempt_count=attempt_count,
                    terminal_check_failed=chk_name,
                    reason=f"alpha #{alpha_id} previously failed terminal check {chk_name}",
                )

    # 3. Check attempt limit
    if attempt_count >= MAX_ATTEMPTS_PER_ALPHA:
        return AlphaStoppingVerdict(
            can_submit=False,
            alpha_id=alpha_id,
            attempt_count=attempt_count,
            reason=f"alpha #{alpha_id} reached max submission attempts ({attempt_count}/{MAX_ATTEMPTS_PER_ALPHA})",
        )

    return AlphaStoppingVerdict(
        can_submit=True,
        alpha_id=alpha_id,
        attempt_count=attempt_count,
    )


def check_family_stopping_bound(db: Session, family_key: str | None) -> FamilyStoppingVerdict:
    """Evaluate family-level diminishing returns stopping boundary."""
    if not family_key:
        return FamilyStoppingVerdict(
            can_submit=True,
            family_key=None,
            total_attempts=0,
            rejected_count=0,
            consecutive_rejections=0,
            accepted_count=0,
        )

    attempts = list(
        db.execute(
            select(SubmissionAttempt, Alpha)
            .join(Alpha, SubmissionAttempt.alpha_id == Alpha.id)
            .where(Alpha.family_key == family_key)
            .order_by(SubmissionAttempt.id.asc())
        ).all()
    )

    total_attempts = len(attempts)
    if total_attempts == 0:
        return FamilyStoppingVerdict(
            can_submit=True,
            family_key=family_key,
            total_attempts=0,
            rejected_count=0,
            consecutive_rejections=0,
            accepted_count=0,
        )

    rejected_count = sum(1 for sa, _ in attempts if sa.result == SubmissionResult.REJECTED.value)
    accepted_count = sum(1 for sa, _ in attempts if sa.result == SubmissionResult.SUBMITTED.value)

    # Calculate consecutive trailing rejections
    consecutive_rejections = 0
    for sa, _ in reversed(attempts):
        if sa.result == SubmissionResult.REJECTED.value:
            consecutive_rejections += 1
        elif sa.result == SubmissionResult.SUBMITTED.value:
            break

    if consecutive_rejections >= MAX_CONSECUTIVE_FAMILY_REJECTIONS:
        return FamilyStoppingVerdict(
            can_submit=False,
            family_key=family_key,
            total_attempts=total_attempts,
            rejected_count=rejected_count,
            consecutive_rejections=consecutive_rejections,
            accepted_count=accepted_count,
            exhausted=True,
            reason=f"family '{family_key}' exhausted by {consecutive_rejections} consecutive rejections",
        )

    if rejected_count >= MAX_UNPRODUCTIVE_FAMILY_REJECTIONS and accepted_count == 0:
        return FamilyStoppingVerdict(
            can_submit=False,
            family_key=family_key,
            total_attempts=total_attempts,
            rejected_count=rejected_count,
            consecutive_rejections=consecutive_rejections,
            accepted_count=accepted_count,
            exhausted=True,
            reason=f"family '{family_key}' exhausted by {rejected_count} rejections with 0 acceptances",
        )

    return FamilyStoppingVerdict(
        can_submit=True,
        family_key=family_key,
        total_attempts=total_attempts,
        rejected_count=rejected_count,
        consecutive_rejections=consecutive_rejections,
        accepted_count=accepted_count,
    )


def check_submission_eligibility(
    db: Session,
    alpha: Alpha,
    brain: BrainClient | None = None,
    brain_alpha_id: str | None = None,
) -> SubmissionEligibility:
    """Evaluate holistic submission eligibility across alpha, family, and platform preflight."""
    blocking_reasons: list[str] = []

    alpha_verdict = check_alpha_stopping_bound(db, alpha.id)
    if not alpha_verdict.can_submit and alpha_verdict.reason:
        blocking_reasons.append(alpha_verdict.reason)

    family_verdict = check_family_stopping_bound(db, alpha.family_key)
    if not family_verdict.can_submit and family_verdict.reason:
        blocking_reasons.append(family_verdict.reason)

    preflight_verdict = None
    if brain is not None:
        preflight_verdict = check_submission_preflight(brain, alpha_id=brain_alpha_id)
        if not preflight_verdict.can_submit and preflight_verdict.reason:
            blocking_reasons.append(preflight_verdict.reason)

    can_submit = (
        alpha_verdict.can_submit
        and family_verdict.can_submit
        and (preflight_verdict is None or preflight_verdict.can_submit)
    )

    return SubmissionEligibility(
        can_submit=can_submit,
        alpha_id=alpha.id,
        family_key=alpha.family_key,
        alpha_verdict=alpha_verdict,
        family_verdict=family_verdict,
        preflight_verdict=preflight_verdict,
        blocking_reasons=blocking_reasons,
    )
