"""Read-only BRAIN fetch audit (Phase 4; table exists from Phase 0).

The ``CHECK (http_method = 'GET')`` constraint is a *structural* guarantee of the
read-only contract: the database itself refuses to record any non-GET call. There
is deliberately no submission/simulation-write table anywhere in this schema.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import JSONType
from app.models._common import Base, IdMixin, TimestampMixin


class BrainFetchLog(IdMixin, TimestampMixin, Base):
    __tablename__ = "brain_fetch_log"

    http_method: Mapped[str] = mapped_column(String(8), nullable=False, default="GET")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict | None] = mapped_column(JSONType)
    status_code: Mapped[int | None] = mapped_column(Integer)
    num_records: Mapped[int | None] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("http_method = 'GET'", name="read_only_get"),)
