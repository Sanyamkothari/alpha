"""Tests for Submission Preflight & Quota Verification Service (W2)."""

from __future__ import annotations

from typing import Any
import pytest

from app.models.enums import SubmissionCheckName
from app.services.brain.client import BrainError
from app.services.submission_preflight import PreflightVerdict, check_submission_preflight
from tests.fakes.fake_brain_client import FakeBrainClient


def test_submission_check_name_enum_has_regular_submission():
    assert SubmissionCheckName.REGULAR_SUBMISSION.value == "REGULAR_SUBMISSION"


def test_preflight_verdict_with_available_quota():
    client = FakeBrainClient()
    verdict = check_submission_preflight(client, alpha_id="alpha_123")
    assert verdict.can_submit is True
    assert verdict.reason is None
    assert verdict.regular_submission_count == 1.0
    assert verdict.regular_submission_limit == 4.0
    assert verdict.regular_submission_headroom == 3.0


def test_preflight_verdict_blocks_when_quota_exhausted():
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

    verdict = check_submission_preflight(MockExhaustedBrain(), alpha_id="alpha_123")
    assert verdict.can_submit is False
    assert "quota exhausted" in (verdict.reason or "")
    assert verdict.regular_submission_headroom == 0.0


def test_preflight_verdict_blocks_on_failing_check():
    class MockFailingBrain(FakeBrainClient):
        def get_submission_check(self, alpha_id: str | None = None) -> dict[str, Any]:
            return {
                "checks": [
                    {
                        "name": "SELF_CORRELATION",
                        "result": "FAIL",
                        "value": 0.85,
                        "limit": 0.70,
                    }
                ]
            }

    verdict = check_submission_preflight(MockFailingBrain(), alpha_id="alpha_123")
    assert verdict.can_submit is False
    assert "failed" in (verdict.reason or "")


def test_preflight_fails_closed_on_network_error():
    class MockErrorBrain(FakeBrainClient):
        def get_submission_check(self, alpha_id: str | None = None) -> dict[str, Any]:
            raise BrainError("Connection timed out to /check")

    verdict = check_submission_preflight(MockErrorBrain(), alpha_id="alpha_123")
    assert verdict.can_submit is False
    assert "failed" in (verdict.reason or "")
