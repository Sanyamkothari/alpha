from datetime import date

from sqlalchemy import select

from app.models.alphas import AlphaFieldSnapshot
from app.models.fields import DataField, DataFieldSnapshot
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.field_crowding import get_field_crowding


def test_field_snapshot_creation_and_query(db_session):
    # Setup historical snapshot and current state
    f1_snap = DataFieldSnapshot(
        field_code="test_field_1",
        category="price",
        field_type="MATRIX",
        coverage=0.95,
        user_count=100,
        alpha_count=50,
        delay=1,
        region="USA",
        universe="TOP3000",
        instrument_type="EQUITY",
        as_of_date=date(2026, 8, 1),
    )
    f1_curr = DataField(
        field_code="test_field_1",
        category="price",
        field_type="MATRIX",
        coverage=0.98,
        user_count=150,
        alpha_count=75,
        delay=1,
        region="USA",
        universe="TOP3000",
        instrument_type="EQUITY",
    )
    db_session.add_all([f1_snap, f1_curr])
    db_session.flush()

    # Query point-in-time
    pit = get_field_crowding(db_session, "test_field_1", as_of_date=date(2026, 8, 5))
    assert pit is not None
    assert pit.user_count == 100
    assert pit.alpha_count == 50
    assert pit.as_of_date == date(2026, 8, 1)

    # Query current state
    curr = get_field_crowding(db_session, "test_field_1", as_of_date=None)
    assert curr is not None
    assert curr.user_count == 150
    assert curr.alpha_count == 75


def test_alpha_creation_stamps_field_snapshots(db_session):
    # Add field to data_fields
    df = DataField(
        field_code="operating_income",
        category="fundamentals",
        field_type="MATRIX",
        coverage=0.90,
        user_count=500,
        alpha_count=200,
        delay=1,
        region="USA",
        universe="TOP3000",
        instrument_type="EQUITY",
    )
    db_session.add(df)
    db_session.flush()

    # Create alpha using operating_income and group field 'sector'
    res = create_alpha(
        db_session,
        "group_neutralize(rank(operating_income), sector)",
        AlphaSettings(),
    )
    assert res.created

    # Verify alpha_field_snapshot stamped operating_income and excluded sector
    snaps = db_session.execute(
        select(AlphaFieldSnapshot).where(AlphaFieldSnapshot.alpha_id == res.alpha.id)
    ).scalars().all()

    assert len(snaps) == 1
    assert snaps[0].field_code == "operating_income"
    assert snaps[0].field_id == df.id
    assert snaps[0].user_count == 500
    assert snaps[0].alpha_count == 200
    assert snaps[0].is_approximate is False
