"""Module 1 — Field Database.

Natural key for a field is ``(field_code, region, delay, universe)`` — ``field_code``
is NOT globally unique (the "~4367 fields" figure is per-config). Mock data (Phase 0)
and the real read-only fetcher (Phase 4) populate the *same* tables.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._common import Base, IdMixin, TimestampMixin, enum_check
from app.models.enums import FieldCategory, FieldType


class Dataset(IdMixin, TimestampMixin, Base):
    __tablename__ = "datasets"

    dataset_code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str] = mapped_column(String(16), nullable=False, default="USA")
    delay: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    universe: Mapped[str] = mapped_column(String(32), nullable=False, default="TOP3000")
    instrument_type: Mapped[str] = mapped_column(String(32), nullable=False, default="EQUITY")
    field_count: Mapped[int] = mapped_column(Integer, default=0)

    fields: Mapped[list[DataField]] = relationship(back_populates="dataset")

    __table_args__ = (
        UniqueConstraint("dataset_code", "region", "delay", "universe", name="dataset_config"),
    )


class DataField(IdMixin, TimestampMixin, Base):
    __tablename__ = "data_fields"

    field_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"))

    category: Mapped[str] = mapped_column(String(32), nullable=False, default=FieldCategory.PRICE)
    # MATRIX / VECTOR / GROUP — load-bearing for the validator (group_* needs a GROUP field).
    field_type: Mapped[str] = mapped_column(String(16), nullable=False, default=FieldType.MATRIX)

    coverage: Mapped[float | None] = mapped_column(Float)
    user_count: Mapped[int | None] = mapped_column(Integer)
    alpha_count: Mapped[int | None] = mapped_column(Integer)

    delay: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    region: Mapped[str] = mapped_column(String(16), nullable=False, default="USA")
    universe: Mapped[str] = mapped_column(String(32), nullable=False, default="TOP3000")
    instrument_type: Mapped[str] = mapped_column(String(32), nullable=False, default="EQUITY")
    unit: Mapped[str | None] = mapped_column(String(32))

    # Confidence of the auto-classification (Haiku bulk pass); 1.0 for seeded.
    classification_confidence: Mapped[float | None] = mapped_column(Float)

    dataset: Mapped[Dataset | None] = relationship(back_populates="fields")
    tags: Mapped[list[Tag]] = relationship(secondary="field_tags", back_populates="fields")

    __table_args__ = (
        UniqueConstraint("field_code", "region", "delay", "universe", name="field_config"),
        enum_check("category", FieldCategory),
        enum_check("field_type", FieldType),
    )


class Category(IdMixin, Base):
    """Lookup of field categories (seeded from :class:`FieldCategory`)."""

    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class Tag(IdMixin, Base):
    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    fields: Mapped[list[DataField]] = relationship(secondary="field_tags", back_populates="tags")


class FieldTag(Base):
    __tablename__ = "field_tags"

    field_id: Mapped[int] = mapped_column(
        ForeignKey("data_fields.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
