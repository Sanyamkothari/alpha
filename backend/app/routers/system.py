"""System/meta endpoints: build-stage map + the safety banner."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/system", tags=["system"])

# Kept deliberately precise. Simulation IS automated (on the user's own account,
# through BRAIN's own simulation API, rate-limited); submission is NOT and there
# is no submission code path in this repo. Saying "read-only" here would now be
# false — see docs/DECISIONS.md and tests/test_brain_no_post.py, which enforces
# the boundary that actually holds.
SAFETY_BANNER = (
    "Simulations run automatically on your own BRAIN account, rate-limited. "
    "You review and submit every alpha yourself — this tool contains no "
    "submission code path."
)

# Build stages from STRATEGY.md §6. Surfaced so progress is honest about what
# is real versus planned.
STAGES = [
    {"id": "operator-kb", "name": "Operator Knowledge Base", "stage": 0, "implemented": True},
    {"id": "validator", "name": "Expression Validator (compiler)", "stage": 0, "implemented": True},
    {"id": "alpha-library", "name": "Alpha Library", "stage": 0, "implemented": True},
    {"id": "result-import", "name": "Simulation Result Importer", "stage": 0, "implemented": True},
    {"id": "field-catalog", "name": "Real BRAIN Field Catalog", "stage": 1, "implemented": True},
    {"id": "sim-runner", "name": "Simulation Runner (batch)", "stage": 2, "implemented": True},
    {"id": "constructor", "name": "Family Constructor (grid)", "stage": 3, "implemented": True},
    {"id": "filter", "name": "Plateau + Correlation Filter", "stage": 4, "implemented": True},
    {
        "id": "allocator",
        "name": "Allocator (bandit + forced exploration)",
        "stage": 5,
        "implemented": True,
    },
    {"id": "report", "name": "Daily Shortlist Report", "stage": 6, "implemented": True},
]


@router.get("/banner")
def banner() -> dict:
    return {
        "safety_banner": SAFETY_BANNER,
        "auto_simulate": True,
        "auto_submit": False,
    }


@router.get("/modules")
def modules() -> dict:
    return {"modules": STAGES, "current_stage": 6}
