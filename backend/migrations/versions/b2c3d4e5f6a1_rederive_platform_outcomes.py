"""Rederive platform_outcome from submission_attempts with 3-state machine.

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-08-16 02:20:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

_OUTCOMES_NEW = ("accepted", "submitted", "rejected", "pending", "in_review")
_OUTCOMES_OLD = ("accepted", "rejected", "in_review")


def _in_check(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IS NULL OR {col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("alphas", recreate="always") as batch_op:
        batch_op.drop_constraint("platform_outcome_valid", type_="check")
        batch_op.create_check_constraint(
            "platform_outcome_valid",
            _in_check("platform_outcome", _OUTCOMES_NEW),
        )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE alphas
            SET platform_outcome = 'submitted', outcome_source = 'manual'
            WHERE id IN (
                SELECT DISTINCT alpha_id FROM submission_attempts WHERE result = 'submitted'
            )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE alphas
            SET platform_outcome = 'accepted'
            WHERE id IN (
                SELECT DISTINCT alpha_id FROM submission_attempts WHERE result = 'submitted'
            )
            """
        )
    )
    with op.batch_alter_table("alphas", recreate="always") as batch_op:
        batch_op.drop_constraint("platform_outcome_valid", type_="check")
        batch_op.create_check_constraint(
            "platform_outcome_valid",
            _in_check("platform_outcome", _OUTCOMES_OLD),
        )
