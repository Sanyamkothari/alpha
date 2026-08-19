"""Add seed column to campaigns table for reproducible random allocation arm.

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-08-16 02:50:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a1b2"
down_revision = "b2c3d4e5f6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.add_column(sa.Column("seed", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.drop_column("seed")
