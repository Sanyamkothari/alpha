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


def _op(name: str, category: str, *args: OperatorArgument, returns: str = "matrix") -> Operator:
    op = Operator(name=name, category=category, definition=name, returns=returns)
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


def _bool(name: str, position: int) -> OperatorArgument:
    return OperatorArgument(name=name, position=position, arg_type="boolean", required=True)


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
                _op("ts_std_dev", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_quantile", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("normalize", "cross_section", _matrix("x", 0)),
                _op("ts_decay_linear", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_backfill", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("ts_corr", "time_series", _matrix("x", 0), _matrix("y", 1), _window("d", 2)),
                _op("divide", "arithmetic", _matrix("x", 0), _matrix("y", 1)),
                _op("add", "arithmetic", _matrix("x", 0), _matrix("y", 1)),
                _op("subtract", "arithmetic", _matrix("x", 0), _matrix("y", 1)),
                _op("multiply", "arithmetic", _matrix("x", 0), _matrix("y", 1)),
                _op("greater", "logical", _matrix("x", 0), _matrix("y", 1), returns="boolean"),
                _op("trade_when", "transformational", _bool("trigger", 0), _matrix("alpha", 1), _matrix("exit", 2)),
                _op("group_neutralize", "group", _matrix("x", 0), _group("group", 1)),
                _op("group_rank", "group", _matrix("x", 0), _group("group", 1)),
                _op("group_zscore", "group", _matrix("x", 0), _group("group", 1)),
                _op("group_normalize", "group", _matrix("x", 0), _group("group", 1)),
                _op("hump", "transformational", _matrix("x", 0), OperatorArgument(name="hump", position=1, arg_type="float", required=False, is_window=False, min_value=0.0, max_value=1.0)),
                _op("regression_neut", "cross_section", _matrix("y", 0), _matrix("x", 1)),
                _op("days_from_last_change", "time_series", _matrix("x", 0)),
                _op("vec_avg", "vector", OperatorArgument(name="x", position=0, arg_type="vector", required=True)),
                _op("vec_sum", "vector", OperatorArgument(name="x", position=0, arg_type="vector", required=True)),
                _op("vec_count", "vector", OperatorArgument(name="x", position=0, arg_type="vector", required=True)),
                _op("vec_max", "vector", OperatorArgument(name="x", position=0, arg_type="vector", required=True)),
                _op("ts_delay", "time_series", _matrix("x", 0), _window("d", 1)),
                _op("vec_min", "vector", OperatorArgument(name="x", position=0, arg_type="vector", required=True)),
            ]
        )
        ds = Dataset(dataset_code="pv1", name="Price/Volume", region="USA", universe="TOP3000", delay=1)
        s.add(ds)
        s.flush()
        s.add_all(
            [
                DataField(
                    field_code="close", dataset_id=ds.id, category="price", field_type="MATRIX",
                    region="USA", universe="TOP3000", delay=1, coverage=1.0, user_count=50,
                ),
                DataField(
                    field_code="returns", dataset_id=ds.id, category="price", field_type="MATRIX",
                    region="USA", universe="TOP3000", delay=1, coverage=1.0, user_count=50,
                ),
                DataField(
                    field_code="volume", dataset_id=ds.id, category="price", field_type="MATRIX",
                    region="USA", universe="TOP3000", delay=1, coverage=1.0, user_count=50,
                ),
                DataField(
                    field_code="vwap", dataset_id=ds.id, category="price", field_type="MATRIX",
                    region="USA", universe="TOP3000", delay=1, coverage=1.0, user_count=50,
                ),
                DataField(
                    field_code="adv20", dataset_id=ds.id, category="price", field_type="MATRIX",
                    region="USA", universe="TOP3000", delay=1, coverage=1.0, user_count=50,
                ),
                DataField(
                    field_code="cap", dataset_id=ds.id, category="fundamentals", field_type="MATRIX",
                    region="USA", universe="TOP3000", delay=1, coverage=1.0, user_count=50,
                ),
                DataField(
                    field_code="sector",
                    dataset_id=ds.id,
                    category="fundamentals",
                    field_type="GROUP",
                    region="USA", universe="TOP3000", delay=1, coverage=1.0, user_count=50,
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


@pytest.fixture(autouse=True)
def _isolate_pnl_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate PnL storage to a temporary directory per test."""
    from app.services import pnl_storage

    store = pnl_storage.PnLStore(tmp_path / "test_pnl")
    monkeypatch.setattr(pnl_storage, "_default_store", store)
    monkeypatch.setattr(pnl_storage, "get_pnl_store", lambda: store)
