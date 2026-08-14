"""LLM run accounting.

``llm_runs`` is the cost/usage ledger written by the gateway on every call,
including cache hits. It backs the "cost per submittable alpha" metric in
STRATEGY.md §9.

The versioned ``prompts`` table that used to live here was removed: under the
current strategy the LLM fires once per dataset with a small number of prompts,
which belong in code under version control, not in a database table with an
eval-score column and no UI. ``llm_runs.prompt_key``/``prompt_version`` are kept
as plain descriptive columns — they never were foreign keys.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models._common import Base, IdMixin, TimestampMixin, enum_check
from app.models.enums import LLMRunStatus, ModelTier


class LLMRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "llm_runs"

    prompt_key: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    task: Mapped[str | None] = mapped_column(String(64), index=True)
    module: Mapped[str | None] = mapped_column(String(32), index=True)

    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=LLMRunStatus.OK)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        enum_check("status", LLMRunStatus),
        enum_check("tier", ModelTier),
    )
