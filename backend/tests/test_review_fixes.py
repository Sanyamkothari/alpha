"""Regression tests locking in the Phase 0 adversarial-review fixes.

Each test maps to a finding from the Phase 0 review so the fix cannot silently
regress: GW-1 (cache double-counting), WIRING-1 (health 500 vs degraded), and
SCHEMA-1 (audit-trail status CHECK).
"""

from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.llm import Capability, LLMGateway
from app.llm.cache import OutputCache
from app.llm.providers.fake_provider import FakeProvider
from app.models import Base
from app.models.alphas import Alpha, AlphaStatusHistory
from app.models.enums import AlphaStatus
from app.models.prompts import LLMRun


def test_cache_hit_logs_zero_cost_and_tokens(
    test_session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """GW-1: an output-cache hit must contribute 0 cost / 0 tokens to llm_runs."""
    import app.llm.gateway as gw_mod

    @contextlib.contextmanager
    def _scope():  # type: ignore[no-untyped-def]
        s = test_session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(gw_mod, "session_scope", _scope)
    gw = LLMGateway(FakeProvider(), output_cache=OutputCache(enabled=True), log_runs=True)

    gw.complete(Capability.CHEAP_CLASSIFICATION, "ledger probe", task="gw1_probe")
    gw.complete(Capability.CHEAP_CLASSIFICATION, "ledger probe", task="gw1_probe")  # cache hit

    with test_session_factory() as s:
        rows = (
            s.execute(select(LLMRun).where(LLMRun.task == "gw1_probe").order_by(LLMRun.id))
            .scalars()
            .all()
        )

    assert len(rows) == 2
    paid, cached = rows
    assert paid.status == "ok"
    assert paid.input_tokens > 0  # the real call billed tokens
    assert cached.status == "cached"
    assert cached.cached is True
    # The hit was free: nothing must be added to spend/token aggregates.
    assert cached.cost_usd == 0.0
    assert cached.input_tokens == 0
    assert cached.output_tokens == 0


def test_health_degraded_not_500_when_gateway_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WIRING-1: a gateway that fails to build (e.g. missing key) => degraded, not 500."""
    import app.routers.health as health_mod

    def _boom() -> None:
        raise ValueError("ANTHROPIC_API_KEY is required for the anthropic provider")

    monkeypatch.setattr(health_mod, "get_gateway", _boom)
    r = client.get("/health")
    assert r.status_code == 200  # must NOT crash
    body = r.json()
    assert body["status"] == "degraded"
    assert body["llm_ok"] is False


def test_alpha_status_history_rejects_invalid_status(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """SCHEMA-1: the append-only audit trail must reject out-of-enum statuses."""
    eng = create_engine(f"sqlite:///{tmp_path / 'h.db'}", future=True)

    @event.listens_for(eng, "connect")
    def _fk(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    with Session(eng) as s:
        alpha = Alpha(
            expression="rank(close)",
            expression_hash="schema1",
            status=AlphaStatus.UNTESTED.value,
            is_valid=True,
        )
        s.add(alpha)
        s.flush()

        # Valid transition commits fine.
        s.add(AlphaStatusHistory(alpha_id=alpha.id, to_status=AlphaStatus.TESTING.value))
        s.commit()

        # Garbage status is rejected by the CHECK constraint.
        s.add(AlphaStatusHistory(alpha_id=alpha.id, to_status="frobnicate"))
        with pytest.raises(IntegrityError):
            s.commit()
    eng.dispose()
