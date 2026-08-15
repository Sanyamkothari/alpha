"""add platform outcome fields to alphas

Revision ID: c8e1f2a3b4c5
Revises: b7c1d2e3f4a5
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8e1f2a3b4c5"
down_revision = "b7c1d2e3f4a5"
branch_labels = None
depends_on = None

_OUTCOMES = ("accepted", "rejected", "in_review")
_SOURCES = ("manual", "api")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("alphas") as batch:
        batch.add_column(sa.Column("platform_outcome", sa.String(16), nullable=True))
        batch.add_column(sa.Column("outcome_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("outcome_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("outcome_source", sa.String(16), nullable=True))
        batch.create_check_constraint(
            "platform_outcome_valid", _in("platform_outcome", _OUTCOMES)
        )
        batch.create_check_constraint("outcome_source_valid", _in("outcome_source", _SOURCES))


def downgrade() -> None:
    with op.batch_alter_table("alphas") as batch:
        batch.drop_constraint("platform_outcome_valid", type_="check")
        batch.drop_constraint("outcome_source_valid", type_="check")
        batch.drop_column("outcome_source")
        batch.drop_column("outcome_note")
        batch.drop_column("outcome_date")
        batch.drop_column("platform_outcome")
