"""Add campaigns, campaign_tasks tables and arm/campaign_task_id columns to alphas.

Revision ID: f1a2b3c4d5e6
Revises: e0a1b2c3d4e5
Create Date: 2026-08-15 23:15:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create campaigns table
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("budget_total", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("budget_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_campaigns_status_valid",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. Create campaign_tasks table
    op.create_table(
        "campaign_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("arm", sa.String(length=32), nullable=False),
        sa.Column("territory_key", sa.String(length=256), nullable=False),
        sa.Column("field_code", sa.String(length=128), nullable=False),
        sa.Column("operator_family", sa.String(length=64), nullable=False),
        sa.Column("wrapper_shape", sa.String(length=64), nullable=True),
        sa.Column("denominator", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("alphas_total", sa.Integer(), nullable=False, server_default="49"),
        sa.Column("alphas_simulated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alphas_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "arm IN ('exploit', 'random_stratified', 'plateau_fill')",
            name="ck_campaign_tasks_arm_valid",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'skipped')",
            name="ck_campaign_tasks_status_valid",
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_campaign_tasks_campaign_id", "campaign_tasks", ["campaign_id"])
    op.create_index("ix_campaign_tasks_status", "campaign_tasks", ["status"])

    # 3. Add arm and campaign_task_id columns to alphas
    with op.batch_alter_table("alphas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("arm", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("campaign_task_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_alphas_campaign_task_id",
            "campaign_tasks",
            ["campaign_task_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_alphas_arm_valid",
            "arm IS NULL OR arm IN ('exploit', 'random_stratified', 'plateau_fill')",
        )


def downgrade() -> None:
    with op.batch_alter_table("alphas", schema=None) as batch_op:
        batch_op.drop_constraint("ck_alphas_arm_valid", type_="check")
        batch_op.drop_constraint("fk_alphas_campaign_task_id", type_="foreignkey")
        batch_op.drop_column("campaign_task_id")
        batch_op.drop_column("arm")

    op.drop_index("ix_campaign_tasks_status", table_name="campaign_tasks")
    op.drop_index("ix_campaign_tasks_campaign_id", table_name="campaign_tasks")
    op.drop_table("campaign_tasks")
    op.drop_table("campaigns")
