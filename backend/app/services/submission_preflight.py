"""Submission Preflight & Quota Verification Service (W2).

Reads platform submission headroom and check verdicts via GET /check or
GET /alphas/{brain_id}/check before any submission is initiated, ensuring
daily submission quota (REGULAR_SUBMISSION) and checks are respected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from app.models.enums import SubmissionCheckName
from app.services.brain.client import BrainClient, BrainError

log = structlog.get_logger("submission_preflight")

_PASS_RESULTS = frozenset({"PASS", "PASSED", "OK", "TRUE"})
_FAIL_RESULTS = frozenset({"FAIL", "FAILED", "ERROR", "FALSE"})


@dataclass(frozen=True)
class PreflightVerdict:
    can_submit: bool
    reason: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    regular_submission_count: float | None = None
    regular_submission_limit: float | None = None
    regular_submission_headroom: float | None = None


def check_submission_preflight(
    brain: BrainClient,
    alpha_id: str | None = None,
) -> PreflightVerdict:
    """Query /check (or /alphas/{alpha_id}/check) and evaluate submission headroom."""
    try:
        data = brain.get_submission_check(alpha_id=alpha_id)
    except BrainError as exc:
        log.warning("submission_check_request_failed", error=str(exc))
        return PreflightVerdict(
            can_submit=False,
            reason=f"platform preflight check request failed: {exc}",
        )
    except Exception as exc:
        log.warning("submission_check_unexpected_error", error=str(exc))
        return PreflightVerdict(
            can_submit=False,
            reason=f"platform preflight check unexpected error: {exc}",
        )

    checks = data.get("checks", []) if isinstance(data, dict) else []
    reg_val = None
    reg_lim = None
    headroom = None

    for chk in checks:
        name = str(chk.get("name", "")).upper()
        res = str(chk.get("result", "")).upper()
        val = chk.get("value")
        lim = chk.get("limit")

        if name == SubmissionCheckName.REGULAR_SUBMISSION.value:
            reg_val = float(val) if val is not None else None
            reg_lim = float(lim) if lim is not None else None
            if reg_val is not None and reg_lim is not None:
                headroom = max(0.0, reg_lim - reg_val)
                if headroom <= 0:
                    return PreflightVerdict(
                        can_submit=False,
                        reason=f"regular submission quota exhausted ({reg_val}/{reg_lim})",
                        checks=checks,
                        regular_submission_count=reg_val,
                        regular_submission_limit=reg_lim,
                        regular_submission_headroom=headroom,
                    )
        elif res in _FAIL_RESULTS:
            return PreflightVerdict(
                can_submit=False,
                reason=f"preflight check {name} failed with result {res}",
                checks=checks,
                regular_submission_count=reg_val,
                regular_submission_limit=reg_lim,
                regular_submission_headroom=headroom,
            )

    return PreflightVerdict(
        can_submit=True,
        reason=None,
        checks=checks,
        regular_submission_count=reg_val,
        regular_submission_limit=reg_lim,
        regular_submission_headroom=headroom,
    )
