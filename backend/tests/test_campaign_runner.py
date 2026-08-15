from sqlalchemy import select

from app.models.alphas import Alpha
from app.models.campaigns import Campaign, CampaignTask
from app.models.fields import DataField, Dataset
from app.services.campaign_runner import create_nightly_campaign, execute_campaign


def test_create_nightly_campaign_and_tasks(db_session):
    camp = create_nightly_campaign(db_session, budget=100)
    assert camp.id is not None
    assert camp.status == "queued"
    assert camp.budget_total == 100
    assert len(camp.tasks) >= 2

    # Check tasks have arms and territory keys
    arms = {t.arm for t in camp.tasks}
    assert "exploit" in arms
    for t in camp.tasks:
        assert t.status == "queued"
        assert t.field_code is not None
        assert t.alphas_total > 0


def test_execute_campaign_dry_run(db_session, monkeypatch):
    camp = create_nightly_campaign(db_session, budget=50)
    cid = camp.id

    # Mock session_scope to use in-memory db_session
    from contextlib import contextmanager
    @contextmanager
    def mock_scope():
        yield db_session

    monkeypatch.setattr("app.services.campaign_runner.session_scope", mock_scope)

    res = execute_campaign(cid, simulate=False)
    assert res["status"] == "completed"

    c = db_session.get(Campaign, cid)
    assert c.status == "completed"

    # Check that alphas were generated with arm and campaign_task_id
    alphas = db_session.execute(select(Alpha).where(Alpha.campaign_task_id.is_not(None))).scalars().all()
    assert len(alphas) > 0
    for a in alphas:
        assert a.arm in ["exploit", "random_stratified", "plateau_fill"]
        assert a.campaign_task_id is not None
