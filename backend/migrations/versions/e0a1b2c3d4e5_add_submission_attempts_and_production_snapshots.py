"""add submission_attempts and alpha_production_snapshots

Revision ID: e0a1b2c3d4e5
Revises: d9f2a3b4c5e6
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e0a1b2c3d4e5"
down_revision = "d9f2a3b4c5e6"
branch_labels = None
depends_on = None

_RESULTS = ("submitted", "rejected")
_PROD_STATUSES = ("in_production", "decommissioned")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create submission_attempts table
    op.create_table(
        "submission_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alpha_id", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("result", sa.String(16), nullable=True),
        sa.Column("failed_check", sa.String(64), nullable=True),
        sa.Column("check_detail", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_recalled", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["alpha_id"], ["alphas.id"], ondelete="CASCADE"),
        sa.CheckConstraint(_in("result", _RESULTS), name="submission_result_valid"),
    )
    op.create_index("ix_submission_attempts_alpha_id", "submission_attempts", ["alpha_id"])

    # 2. Create alpha_production_snapshots table
    op.create_table(
        "alpha_production_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alpha_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("returns", sa.Float(), nullable=True),
        sa.Column("payment_amount", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["alpha_id"], ["alphas.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("alpha_id", "as_of_date", name="uq_alpha_production_as_of"),
        sa.CheckConstraint(_in("status", _PROD_STATUSES), name="production_status_valid"),
    )
    op.create_index("ix_alpha_production_snapshots_alpha_id", "alpha_production_snapshots", ["alpha_id"])
    op.create_index("ix_alpha_production_snapshots_as_of_date", "alpha_production_snapshots", ["as_of_date"])

    # 3. Backfill verified submissions (zqNXMEZE for #243, N1bkwYGw for #2558)
    # Check if alpha #243 and #2558 exist in the target database
    rows = bind.exec_driver_sql("SELECT id FROM alphas WHERE id IN (243, 2558)").fetchall()
    existing_ids = {r[0] for r in rows}

    if 243 in existing_ids:
        bind.exec_driver_sql("""
            INSERT OR IGNORE INTO submission_attempts (alpha_id, attempted_at, result, notes, is_recalled)
            VALUES (243, '2026-08-05 12:43:49', 'submitted', 'BRAIN alpha zqNXMEZE', 0)
        """)
    if 2558 in existing_ids:
        bind.exec_driver_sql("""
            INSERT OR IGNORE INTO submission_attempts (alpha_id, attempted_at, result, notes, is_recalled)
            VALUES (2558, '2026-08-05 12:45:03', 'submitted', 'BRAIN alpha N1bkwYGw', 0)
        """)

    # 4. Sync platform_outcome on alphas table based on submission_attempts
    bind.exec_driver_sql("""
        UPDATE alphas
        SET platform_outcome = (
            CASE
                WHEN EXISTS (SELECT 1 FROM submission_attempts WHERE alpha_id = alphas.id AND result = 'submitted') THEN 'accepted'
                WHEN EXISTS (SELECT 1 FROM submission_attempts WHERE alpha_id = alphas.id AND result = 'rejected')
                     AND NOT EXISTS (SELECT 1 FROM submission_attempts WHERE alpha_id = alphas.id AND (result = 'submitted' OR result IS NULL)) THEN 'rejected'
                WHEN EXISTS (SELECT 1 FROM submission_attempts WHERE alpha_id = alphas.id AND result IS NULL) THEN 'pending'
                ELSE NULL
            END
        )
    """)


def downgrade() -> None:
    op.drop_table("alpha_production_snapshots")
    op.drop_table("submission_attempts")
