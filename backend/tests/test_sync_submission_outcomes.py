"""Tests for sync_submission_outcomes and brain_alpha_id extraction (W3)."""

from __future__ import annotations

from app.models.alphas import Alpha
from app.models.results import SimulationImport
from app.services.result_import import extract_brain_alpha_id
from scripts.sync_submission_outcomes import _extract_brain_id


def test_extract_brain_alpha_id_from_dict():
    payload = {"id": "alpha_xyz_123", "status": "COMPLETE"}
    assert extract_brain_alpha_id(payload) == "alpha_xyz_123"

    payload_nested = {"alpha": {"id": "alpha_nested_456"}}
    assert extract_brain_alpha_id(payload_nested) == "alpha_nested_456"


def test_extract_brain_id_from_db_simulation_import(db_session):
    alpha = Alpha(
        expression="rank(close)",
        expression_hash="sync_test_hash",
        status="passed",
    )
    db_session.add(alpha)
    db_session.flush()

    sim_import = SimulationImport(
        alpha_id=alpha.id,
        raw_payload={"id": "brain_id_789"},
    )
    db_session.add(sim_import)
    db_session.flush()

    extracted = _extract_brain_id(alpha, db_session)
    assert extracted == "brain_id_789"
