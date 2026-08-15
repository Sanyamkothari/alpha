"""Schema coherence: the full model set builds on a fresh SQLite engine and the
read-only CHECK is actually enforced by the database."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.models import Base


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def test_all_tables_created(engine) -> None:
    with engine.connect() as conn:
        names = [
            r[0]
            for r in conn.execute(
                text(
                    "select name from sqlite_master where type='table' "
                    "and name not like 'sqlite_%'"
                )
            )
        ]
    # The schema was pruned to the alpha-generation core (STRATEGY.md §7).
    # Asserted exactly, not as a floor: a table reappearing here means a deleted
    # model got re-imported into app.models without a migration.
    assert sorted(names) == [
        "alpha_field_snapshot",
        "alpha_metrics",
        "alpha_production_snapshots",
        "alpha_status_history",
        "alphas",
        "brain_fetch_log",
        "campaign_tasks",
        "campaigns",
        "categories",
        "data_field_snapshots",
        "data_fields",
        "datasets",
        "field_tags",
        "llm_runs",
        "operator_arguments",
        "operator_compatibility",
        "operator_examples",
        "operators",
        "simulation_imports",
        "submission_attempts",
        "tags",
    ]


def test_pruned_tables_stay_gone(engine) -> None:
    """The measurement rig and unused module skeletons must not resurrect."""
    with engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                text(
                    "select name from sqlite_master where type='table' "
                    "and name not like 'sqlite_%'"
                )
            )
        }
    for gone in (
        "experiments",
        "trials",
        "trial_events",
        "generation_attempts",
        "experiment_generation_scaffolds",
        "hypotheses",
        "kg_nodes",
        "kg_edges",
        "critic_notes",
        "embeddings",
        "notebook_entries",
        "research_templates",
        "alpha_lineage",
    ):
        assert gone not in names, f"{gone} came back — add a migration or drop the model"


def test_brain_fetch_log_rejects_non_get(engine) -> None:
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "insert into brain_fetch_log (http_method, url, ok, created_at, updated_at) "
                    "values ('POST', 'x', 1, '2026-01-01', '2026-01-01')"
                )
            )


def test_brain_fetch_log_allows_get(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into brain_fetch_log (http_method, url, ok, created_at, updated_at) "
                "values ('GET', 'https://api.worldquantbrain.com/data-fields', 1, "
                "'2026-01-01', '2026-01-01')"
            )
        )


def test_alembic_migrations_roundtrip(tmp_path) -> None:
    """Ensure alembic migrations can upgrade to head, downgrade -1, and re-upgrade on throwaway DB."""
    from alembic.config import Config
    from alembic import command
    from pathlib import Path

    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(alembic_ini))
    db_file = tmp_path / "alembic_test.db"
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")

    # Pass 1
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    # Pass 2: verify re-run idempotency on another isolated file
    db_file_2 = tmp_path / "alembic_test_2.db"
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file_2}")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")


def test_suite_never_touches_production_database(tmp_path) -> None:
    """Regression test: assert that tests never mutate or write to the production database."""
    from pathlib import Path
    from app.config import REPO_ROOT

    prod_db = REPO_ROOT / "database" / "wq.db"
    if prod_db.exists():
        initial_mtime = prod_db.stat().st_mtime
        # Re-run a schema inspection
        from alembic.config import Config
        from alembic import command

        alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
        cfg = Config(str(alembic_ini))
        isolated_db = tmp_path / "isolation_check.db"
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{isolated_db}")
        command.upgrade(cfg, "head")

        # Production database must remain completely untouched
        assert prod_db.stat().st_mtime == initial_mtime, "Production wq.db was touched by migration!"
