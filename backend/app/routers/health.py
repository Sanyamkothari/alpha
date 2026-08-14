"""Liveness/readiness + a Phase 0 self-check."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.config import settings
from app.db import get_db
from app.llm import get_gateway
from app.models.fields import DataField
from app.models.operators import Operator

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    # Building the gateway can fail on a recoverable misconfiguration (e.g.
    # LLM_PROVIDER=anthropic with no API key). The health endpoint must *report*
    # that as degraded, never 500 — liveness/readiness probes depend on it.
    try:
        llm_provider = get_gateway().provider.name
        llm_ok = True
    except Exception:
        llm_provider = settings.llm_provider
        llm_ok = False

    return {
        "status": "ok" if (db_ok and llm_ok) else "degraded",
        "version": __version__,
        "env": settings.app_env,
        "db_ok": db_ok,
        "llm_ok": llm_ok,
        "llm_provider": llm_provider,
        "counts": {
            "operators": db.query(Operator).count() if db_ok else None,
            "fields": db.query(DataField).count() if db_ok else None,
        },
        # The safety contract is always surfaced. This used to report
        # `read_only_brain: True`, which became false the moment the simulation
        # runner shipped — simulations are POSTed to the platform. Reporting the
        # two facts separately keeps it honest: backtests are automated, and
        # there is no submission code path. See docs/DECISIONS.md.
        "auto_simulate": True,
        "auto_submit": False,
    }
