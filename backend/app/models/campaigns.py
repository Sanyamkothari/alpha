"""Database models for long-running batch campaigns and tasks (Phase 1)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._common import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    budget_total: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    budget_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    tasks: Mapped[list[CampaignTask]] = relationship(
        "CampaignTask", back_populates="campaign", cascade="all, delete-orphan", order_by="CampaignTask.id"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_campaigns_status_valid",
        ),
    )


class CampaignTask(Base):
    __tablename__ = "campaign_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    arm: Mapped[str] = mapped_column(String(32), nullable=False)
    territory_key: Mapped[str] = mapped_column(String(256), nullable=False)
    field_code: Mapped[str] = mapped_column(String(128), nullable=False)
    operator_family: Mapped[str] = mapped_column(String(64), nullable=False)
    wrapper_shape: Mapped[str | None] = mapped_column(String(64), nullable=True)
    denominator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    alphas_total: Mapped[int] = mapped_column(Integer, nullable=False, default=49)
    alphas_simulated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alphas_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    campaign: Mapped[Campaign] = relationship("Campaign", back_populates="tasks")
    alphas: Mapped[list[Alpha]] = relationship("Alpha", back_populates="campaign_task")

    __table_args__ = (
        CheckConstraint(
            "arm IN ('exploit', 'random_stratified', 'plateau_fill')",
            name="ck_campaign_tasks_arm_valid",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'skipped')",
            name="ck_campaign_tasks_status_valid",
        ),
    )
