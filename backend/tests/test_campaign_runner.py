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
    alphas = (
        db_session.execute(select(Alpha).where(Alpha.campaign_task_id.is_not(None))).scalars().all()
    )
    assert len(alphas) > 0
    for a in alphas:
        assert a.arm in ["exploit", "random_stratified", "plateau_fill"]
        assert a.campaign_task_id is not None


def test_execute_campaign_with_fake_brain_client(db_session, monkeypatch):
    """Test full multi-arm campaign execution with simulated batch via FakeBrainClient."""
    from contextlib import contextmanager

    from tests.fakes.fake_brain_client import FakeBrainClient

    # Seed test dataset and fields
    ds = Dataset(
        dataset_code="ds_camp",
        name="Campaign Dataset",
        region="USA",
        delay=1,
        universe="TOP3000",
        instrument_type="EQUITY",
    )
    db_session.add(ds)
    db_session.flush()

    for i in range(5):
        db_session.add(
            DataField(
                field_code=f"f_camp_{i}",
                dataset_id=ds.id,
                category="fundamentals",
                field_type="MATRIX",
                coverage=0.9,
                user_count=10 * i,
                region="USA",
                delay=1,
                universe="TOP3000",
            )
        )
    db_session.commit()

    camp = create_nightly_campaign(db_session, budget=100)
    cid = camp.id

    @contextmanager
    def mock_scope():
        yield db_session

    monkeypatch.setattr("app.services.campaign_runner.session_scope", mock_scope)
    monkeypatch.setattr("app.services.simulation_runner.session_scope", mock_scope)
    monkeypatch.setattr("app.services.simulation_runner.BrainClient", FakeBrainClient)
    monkeypatch.setattr("app.services.brain.BrainClient", FakeBrainClient)
    monkeypatch.setattr("app.services.brain.client.BrainClient", FakeBrainClient)

    res = execute_campaign(cid, simulate=True)
    assert res["status"] == "completed"
    assert res["total_simulated"] > 0

    c = db_session.get(Campaign, cid)
    assert c.status == "completed"
    assert c.budget_completed == res["total_simulated"]

    tasks = (
        db_session.execute(select(CampaignTask).where(CampaignTask.campaign_id == cid))
        .scalars()
        .all()
    )
    assert len(tasks) >= 2
    for t in tasks:
        assert t.status == "completed"
        assert t.alphas_simulated > 0
