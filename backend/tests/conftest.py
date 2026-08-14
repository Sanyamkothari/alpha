"""Shared test fixtures.

The endpoint tests must not depend on the developer's on-disk dev DB having been
migrated and seeded out of band (that made ``pytest -q`` pass or fail depending on
hidden environment state). Here we build a throwaway SQLite DB, create the full
schema on it, seed a small known corpus, and override the FastAPI ``get_db``
dependency so the app reads exclusively from the isolated DB.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_db
from app.main import app
from app.models import Base
from app.models.fields import DataField, Dataset
from app.models.operators import Operator, OperatorArgument


def _op(name: str, category: str, *args: OperatorArgument) -> Operator:
    op = Operator(name=name, category=category, definition=name, returns="matrix")
    op.arguments = list(args)
    return op


def _matrix(name: str, position: int) -> OperatorArgument:
    return OperatorArgument(name=name, position=position, arg_type="matrix", required=True)


def _window(name: str, position: int) -> OperatorArgument:
    return OperatorArgument(
        name=name,
        position=position,
        arg_type="int",
        required=True,
        is_window=True,
        min_value=2,
        max_value=512,
    )


def _group(name: str, position: int) -> OperatorArgument:
    return OperatorArgument(name=name, position=position, arg_type="group", required=True)


@pytest.fixture(scope="session")
def app_db_engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    eng = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(eng, "connect")
    def _fk_pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def test_session_factory(app_db_engine: Engine) -> sessionmaker[Session]:
    # Mirror production SessionLocal EXACTLY (autoflush=False): a test factory that
    # defaults to autoflush=True hides flush-ordering bugs that only bite in prod.
    return sessionmaker(
        bind=app_db_engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _seed(test_session_factory: sessionmaker[Session]) -> None:
    """A known corpus (operators WITH arg specs + a few fields) so the validator,
    Alpha Library, and Result Importer endpoints are exercised against real signatures."""
    with test_session_factory() as s:
        s.add_all(
            [
                _op("rank", "cross_section", _matrix("x", 0)),
                _op("zscore", "cross_section", _matrix("x", 0)),
                _op("ts_rank", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_mean", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_delta", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_zscore", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_decay_linear", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_backfill", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_corr", "time_series", _matrix("x", 0), _matrix("y", 1), _window("d", 2)),
                _op("divide", "arithmetic", _matrix("x", 0), _matrix("y", 1)),
                _op("group_neutralize", "group", _matrix("x", 0), _group("group", 1)),
                # The constructor emits group_<cs_op>; without these the grid's
                # group axis would silently collapse to ungrouped variants only.
                _op("group_rank", "group", _matrix("x", 0), _group("group", 1)),
                _op("group_zscore", "group", _matrix("x", 0), _group("group", 1)),
            ]
        )
        ds = Dataset(dataset_code="pv1", name="Price/Volume", region="USA")
        s.add(ds)
        s.flush()
        s.add_all(
            [
                DataField(
                    field_code="close", dataset_id=ds.id, category="price", field_type="MATRIX"
                ),
                DataField(
                    field_code="returns", dataset_id=ds.id, category="price", field_type="MATRIX"
                ),
                DataField(
                    field_code="volume", dataset_id=ds.id, category="price", field_type="MATRIX"
                ),
                DataField(
                    field_code="sector",
                    dataset_id=ds.id,
                    category="fundamentals",
                    field_type="GROUP",
                ),
            ]
        )
        s.commit()


@pytest.fixture()
def client(test_session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def _override() -> Iterator[Session]:
        # Mirror the real get_db unit-of-work: commit on success, rollback on error.
        s = test_session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def db_session(test_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """A plain session for service-level tests that do not go through HTTP.

    Rolls back at the end so a test that writes alphas cannot leak rows into the
    session-scoped seed corpus the other tests assert against.
    """
    s = test_session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
