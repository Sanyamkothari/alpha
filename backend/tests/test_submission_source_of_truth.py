"""Single Source of Truth for Submissions (W8 & W8b)."""

from __future__ import annotations

from sqlalchemy import select

from app.models.alphas import (
    Alpha,
    PlatformOutcome,
    SubmissionAttempt,
    SubmissionResult,
    submitted_alpha_filter,
    sync_alpha_platform_outcome,
)
from app.models.enums import AlphaStatus


def test_submitted_alpha_filter_includes_verified(db_session):
    a1 = Alpha(
        expression="rank(ts_zscore(close, 20))",
        expression_hash="hash_ssot_1",
        status=AlphaStatus.SUBMITTED.value,
        platform_outcome=PlatformOutcome.SUBMITTED.value,
        is_valid=True,
    )
    a2 = Alpha(
        expression="rank(ts_zscore(open, 20))",
        expression_hash="hash_ssot_2",
        status=AlphaStatus.SUBMITTED.value,
        platform_outcome=PlatformOutcome.REJECTED.value,
        is_valid=True,
    )
    db_session.add_all([a1, a2])
    db_session.flush()

    matching_ids = {
        a.id
        for a in db_session.execute(select(Alpha).where(submitted_alpha_filter())).scalars().all()
    }
    assert a1.id in matching_ids
    assert a2.id not in matching_ids


def test_sync_alpha_platform_outcome_lifecycle(db_session):
    alpha = Alpha(
        expression="rank(ts_zscore(vwap, 20))",
        expression_hash="hash_ssot_3",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add(alpha)
    db_session.flush()

    # Initial sync
    res = sync_alpha_platform_outcome(db_session, alpha.id)
    assert res is None
    assert alpha.platform_outcome is None

    # Submission attempt
    attempt = SubmissionAttempt(
        alpha_id=alpha.id,
        result=SubmissionResult.SUBMITTED.value,
        notes="Manual upload",
    )
    db_session.add(attempt)
    db_session.flush()

    res = sync_alpha_platform_outcome(db_session, alpha.id)
    assert res == PlatformOutcome.SUBMITTED.value
    assert alpha.platform_outcome == PlatformOutcome.SUBMITTED.value
    assert alpha.status == AlphaStatus.SUBMITTED.value

    # Outcome rejection
    attempt.result = SubmissionResult.REJECTED.value
    db_session.flush()

    res = sync_alpha_platform_outcome(db_session, alpha.id)
    assert res == PlatformOutcome.REJECTED.value
    assert alpha.platform_outcome == PlatformOutcome.REJECTED.value


def test_status_submitted_alone_does_not_satisfy_filter(db_session):
    alpha = Alpha(
        expression="rank(ts_zscore(volume, 20))",
        expression_hash="hash_ssot_4",
        status=AlphaStatus.SUBMITTED.value,
        platform_outcome=PlatformOutcome.REJECTED.value,
        is_valid=True,
    )
    db_session.add(alpha)
    db_session.flush()

    matching_ids = {
        a.id
        for a in db_session.execute(select(Alpha).where(submitted_alpha_filter())).scalars().all()
    }
    assert alpha.id not in matching_ids
