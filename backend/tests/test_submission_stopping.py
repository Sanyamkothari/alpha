"""Tests for Submission Stopping Rule & Exhaustion Boundary Service (W4)."""

from __future__ import annotations

from typing import Any

from app.models.alphas import Alpha, SubmissionAttempt
from app.models.enums import AlphaStatus, PlatformOutcome, SubmissionCheckName, SubmissionResult
from app.services.submission_stopping import (
    MAX_CONSECUTIVE_FAMILY_REJECTIONS,
    MAX_UNPRODUCTIVE_FAMILY_REJECTIONS,
    check_alpha_stopping_bound,
    check_family_stopping_bound,
    check_submission_eligibility,
)
from tests.fakes.fake_brain_client import FakeBrainClient


def test_alpha_stopping_bound_new_alpha(db_session):
    alpha = Alpha(
        expression="rank(close)",
        expression_hash="stop_hash_1",
        status=AlphaStatus.PASSED.value,
        is_valid=True,
    )
    db_session.add(alpha)
    db_session.flush()

    verdict = check_alpha_stopping_bound(db_session, alpha.id)
    assert verdict.can_submit is True
    assert verdict.attempt_count == 0


def test_alpha_stopping_bound_blocks_already_submitted(db_session):
    alpha = Alpha(
        expression="rank(close)",
        expression_hash="stop_hash_2",
        status=AlphaStatus.SUBMITTED.value,
        platform_outcome=PlatformOutcome.SUBMITTED.value,
        is_valid=True,
    )
    db_session.add(alpha)
    db_session.flush()

    verdict = check_alpha_stopping_bound(db_session, alpha.id)
    assert verdict.can_submit is False
    assert verdict.already_submitted is True


def test_alpha_stopping_bound_blocks_terminal_failures(db_session):
    alpha = Alpha(
        expression="rank(close)",
        expression_hash="stop_hash_3",
        status=AlphaStatus.PASSED.value,
        is_valid=True,
    )
    db_session.add(alpha)
    db_session.flush()

    attempt = SubmissionAttempt(
        alpha_id=alpha.id,
        result=SubmissionResult.REJECTED.value,
        failed_check=SubmissionCheckName.SELF_CORRELATION.value,
        check_detail="value 0.82 exceeds 0.70",
    )
    db_session.add(attempt)
    db_session.flush()

    verdict = check_alpha_stopping_bound(db_session, alpha.id)
    assert verdict.can_submit is False
    assert verdict.terminal_check_failed == SubmissionCheckName.SELF_CORRELATION.value


def test_alpha_stopping_bound_allows_one_retry_for_transient_error(db_session):
    alpha = Alpha(
        expression="rank(close)",
        expression_hash="stop_hash_4",
        status=AlphaStatus.PASSED.value,
        is_valid=True,
    )
    db_session.add(alpha)
    db_session.flush()

    # 1st attempt: transient failure
    attempt1 = SubmissionAttempt(
        alpha_id=alpha.id,
        result=SubmissionResult.REJECTED.value,
        notes="Network timeout",
    )
    db_session.add(attempt1)
    db_session.flush()

    verdict = check_alpha_stopping_bound(db_session, alpha.id)
    assert verdict.can_submit is True
    assert verdict.attempt_count == 1

    # 2nd attempt: reached cap
    attempt2 = SubmissionAttempt(
        alpha_id=alpha.id,
        result=SubmissionResult.REJECTED.value,
        notes="Network timeout 2",
    )
    db_session.add(attempt2)
    db_session.flush()

    verdict = check_alpha_stopping_bound(db_session, alpha.id)
    assert verdict.can_submit is False
    assert verdict.attempt_count == 2


def test_family_stopping_bound_blocks_consecutive_rejections(db_session):
    fam = "test_fam_1"
    for i in range(MAX_CONSECUTIVE_FAMILY_REJECTIONS):
        a = Alpha(
            expression=f"rank(close + {i})",
            expression_hash=f"stop_fam_hash_{i}",
            family_key=fam,
            status=AlphaStatus.PASSED.value,
            is_valid=True,
        )
        db_session.add(a)
        db_session.flush()
        db_session.add(
            SubmissionAttempt(
                alpha_id=a.id,
                result=SubmissionResult.REJECTED.value,
            )
        )
        db_session.flush()

    verdict = check_family_stopping_bound(db_session, fam)
    assert verdict.can_submit is False
    assert verdict.exhausted is True
    assert verdict.consecutive_rejections >= MAX_CONSECUTIVE_FAMILY_REJECTIONS


def test_family_stopping_bound_blocks_unproductive_family_limit(db_session):
    fam = "test_fam_2"
    for i in range(MAX_UNPRODUCTIVE_FAMILY_REJECTIONS):
        a = Alpha(
            expression=f"rank(open + {i})",
            expression_hash=f"stop_unprod_hash_{i}",
            family_key=fam,
            status=AlphaStatus.PASSED.value,
            is_valid=True,
        )
        db_session.add(a)
        db_session.flush()
        db_session.add(
            SubmissionAttempt(
                alpha_id=a.id,
                result=SubmissionResult.REJECTED.value,
            )
        )
        db_session.flush()

    verdict = check_family_stopping_bound(db_session, fam)
    assert verdict.can_submit is False
    assert verdict.exhausted is True


def test_check_submission_eligibility_combined(db_session):
    alpha = Alpha(
        expression="rank(close)",
        expression_hash="stop_elig_hash",
        family_key="test_elig_fam",
        status=AlphaStatus.PASSED.value,
        is_valid=True,
    )
    db_session.add(alpha)
    db_session.flush()

    brain = FakeBrainClient()
    elig = check_submission_eligibility(db_session, alpha, brain=brain)
    assert elig.can_submit is True
    assert len(elig.blocking_reasons) == 0

    class MockExhaustedBrain(FakeBrainClient):
        def get_submission_check(self, alpha_id: str | None = None) -> dict[str, Any]:
            return {
                "checks": [
                    {
                        "name": "REGULAR_SUBMISSION",
                        "result": "PASS",
                        "value": 4.0,
                        "limit": 4.0,
                    }
                ]
            }

    elig_exhausted = check_submission_eligibility(db_session, alpha, brain=MockExhaustedBrain())
    assert elig_exhausted.can_submit is False
    assert any("quota exhausted" in r for r in elig_exhausted.blocking_reasons)
