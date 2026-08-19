"""Add quartile column to campaign_tasks.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-08-16 02:15:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quartile", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("campaign_tasks", schema=None) as batch_op:
        batch_op.drop_column("quartile")
