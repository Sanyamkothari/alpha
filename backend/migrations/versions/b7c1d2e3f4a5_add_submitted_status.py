"""add the 'submitted' alpha status

Lets the operator record that they submitted an alpha on BRAIN by hand. Without
it the hit-rate table cannot separate "survived the filter" from "actually put
forward", which are very different numbers.

Nothing in this codebase submits. This is a human decision being written down
after the fact — see docs/DECISIONS.md and tests/test_brain_no_post.py.

The status values live in CHECK constraints on two tables, and SQLite cannot
alter a CHECK in place, so both are rebuilt via batch mode. Idempotent: the DDL
is inspected first, because SQLite DDL here is non-transactional and a failure
part-way must be safe to re-run.

Revision ID: b7c1d2e3f4a5
Revises: f0be3fc3cdad
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.base import NAMING_CONVENTION

revision = "b7c1d2e3f4a5"
down_revision = "f0be3fc3cdad"
branch_labels = None
depends_on = None

_OLD_STATUSES = ("untested", "testing", "passed", "rejected", "improved", "archived")
_STATUSES = (*_OLD_STATUSES, "submitted")
_MUTATION_TYPES = (
    "operator_swap",
    "parameter_tune",
    "field_substitution",
    "neutralization_decay",
    "composition",
)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _ddl(bind, table: str) -> str:
    return (
        bind.exec_driver_sql(
            f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'"
        ).scalar()
        or ""
    )


def _alphas_current() -> sa.Table:
    """Pre-migration ``alphas``, described explicitly so batch mode does not
    reflect (reflection is what broke the earlier prune migration)."""
    return sa.Table(
        "alphas",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("normalized_expression", sa.Text()),
        sa.Column("expression_hash", sa.String(64), nullable=False),
        sa.Column("family_key", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("universe", sa.String(32), nullable=False),
        sa.Column("delay", sa.Integer(), nullable=False),
        sa.Column("neutralization", sa.String(32)),
        sa.Column("decay", sa.Integer()),
        sa.Column("truncation", sa.Float()),
        sa.Column("settings_extra", sa.JSON()),
        sa.Column("is_valid", sa.Boolean(), nullable=False),
        sa.Column("validation_errors", sa.JSON()),
        sa.Column("validation_warnings", sa.JSON()),
        sa.Column("complexity_score", sa.Float()),
        sa.Column("feature_json", sa.JSON()),
        sa.Column("parent_id", sa.Integer()),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("mutation_type", sa.String(32)),
        sa.Column("comments", sa.Text()),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            _in("mutation_type", _MUTATION_TYPES), name="mutation_type_valid"
        ),
        sa.CheckConstraint(_in("status", _OLD_STATUSES), name="status_valid"),
        sa.ForeignKeyConstraint(["parent_id"], ["alphas.id"]),
        sa.UniqueConstraint("expression_hash", name="alpha_expression_hash"),
    )


def _history_current() -> sa.Table:
    return sa.Table(
        "alpha_status_history",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("alpha_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(16)),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            _in("from_status", _OLD_STATUSES), name="from_status_valid"
        ),
        sa.CheckConstraint(
            _in("to_status", _OLD_STATUSES), name="to_status_valid"
        ),
        sa.ForeignKeyConstraint(["alpha_id"], ["alphas.id"], ondelete="CASCADE"),
    )


def upgrade() -> None:
    bind = op.get_bind()

    # copy_from describes the table AS IT CURRENTLY IS (old CHECK); the explicit
    # drop/create is what performs the swap. An empty batch body is a silent
    # no-op -- batch mode only rebuilds when there is an operation to apply.
    if "'submitted'" not in _ddl(bind, "alphas"):
        with op.batch_alter_table("alphas", copy_from=_alphas_current()) as batch:
            batch.drop_constraint("status_valid", type_="check")
            batch.create_check_constraint("status_valid", _in("status", _STATUSES))

    if "'submitted'" not in _ddl(bind, "alpha_status_history"):
        with op.batch_alter_table(
            "alpha_status_history", copy_from=_history_current()
        ) as batch:
            batch.drop_constraint("from_status_valid", type_="check")
            batch.drop_constraint("to_status_valid", type_="check")
            batch.create_check_constraint(
                "from_status_valid", _in("from_status", _STATUSES)
            )
            batch.create_check_constraint(
                "to_status_valid", _in("to_status", _STATUSES)
            )

    # Batch mode recreates the table without indexes it was not told about.
    # Only ix_alphas_family_key is declared on the models -- do NOT invent one on
    # alpha_status_history.alpha_id, or `alembic check` reports permanent drift.
    if "ix_alphas_family_key" not in {
        r[0]
        for r in bind.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='alphas'"
        ).fetchall()
    }:
        op.create_index("ix_alphas_family_key", "alphas", ["family_key"])


def downgrade() -> None:
    raise NotImplementedError("narrowing the status CHECK could orphan existing rows")
