"""Tests for Phase 6: The Feedback Loop (Item F1).

Validates:
- F1: Out-of-sample decay tracking against AlphaProductionSnapshot accurately computes realized decay, flags degraded alphas, and emits alerts.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.alphas import AlphaProductionSnapshot
from app.models.enums import AlphaStatus
from app.models.fields import DataField, Dataset
from app.models.results import AlphaMetric, SimulationImport
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.feedback_loop import evaluate_all_production_alphas, evaluate_production_decay


def _seed_field(db: Session, ds_code: str, f_code: str) -> str:
    ds = db.query(Dataset).filter_by(dataset_code=ds_code).first()
    if not ds:
        ds = Dataset(dataset_code=ds_code, name=ds_code, region="USA", universe="TOP3000", delay=1)
        db.add(ds)
        db.flush()
    f = db.query(DataField).filter_by(field_code=f_code).first()
    if not f:
        f = DataField(dataset_id=ds.id, field_code=f_code, field_type="MATRIX", user_count=1)
        db.add(f)
        db.flush()
    return f_code


def test_f1_production_decay_tracking(db_session) -> None:
    """F1: Decay tracking computes Sharpe decay and flags decayed alphas based on production snapshots."""
    fcode = _seed_field(db_session, "ds_f1_test", "f_f1_decay")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)

    # 1. Alpha A: Healthy in production (Backtest 2.20 -> Prod 2.00, Decay ~ 9%)
    res_a = create_alpha(db_session, f"rank(ts_zscore({fcode},20))", settings, source="test")
    aid_a = res_a.alpha.id
    res_a.alpha.status = AlphaStatus.SUBMITTED.value
    si_a = SimulationImport(alpha_id=aid_a, source="brain_api", raw_payload={}, is_latest=True)
    db_session.add(si_a)
    db_session.flush()
    db_session.add(AlphaMetric(simulation_import_id=si_a.id, alpha_id=aid_a, sharpe=2.20, fitness=1.5, turnover=0.3, passed_all_checks=True))
    db_session.add(AlphaProductionSnapshot(alpha_id=aid_a, as_of_date=date(2026, 8, 1), status="in_production", sharpe=2.00, pnl=50000.0))

    # 2. Alpha B: Decayed in production (Backtest 2.40 -> Prod 0.80, Decay ~ 66.7%)
    res_b = create_alpha(db_session, f"rank(ts_mean({fcode},40))", settings, source="test")
    aid_b = res_b.alpha.id
    res_b.alpha.status = AlphaStatus.SUBMITTED.value
    si_b = SimulationImport(alpha_id=aid_b, source="brain_api", raw_payload={}, is_latest=True)
    db_session.add(si_b)
    db_session.flush()
    db_session.add(AlphaMetric(simulation_import_id=si_b.id, alpha_id=aid_b, sharpe=2.40, fitness=1.5, turnover=0.3, passed_all_checks=True))
    db_session.add(AlphaProductionSnapshot(alpha_id=aid_b, as_of_date=date(2026, 8, 1), status="in_production", sharpe=0.80, pnl=10000.0))

    db_session.flush()

    report_a = evaluate_production_decay(db_session, aid_a)
    assert report_a is not None
    assert not report_a.is_decayed
    assert report_a.decay_pct is not None and report_a.decay_pct < 0.15
    assert report_a.alert is None

    report_b = evaluate_production_decay(db_session, aid_b)
    assert report_b is not None
    assert report_b.is_decayed
    assert report_b.decay_pct is not None and report_b.decay_pct > 0.50
    assert "decay_alert" in str(report_b.alert)

    all_reports = evaluate_all_production_alphas(db_session)
    assert len(all_reports) >= 2
    decayed_ids = {r.alpha_id for r in all_reports if r.is_decayed}
    assert aid_b in decayed_ids
    assert aid_a not in decayed_ids
