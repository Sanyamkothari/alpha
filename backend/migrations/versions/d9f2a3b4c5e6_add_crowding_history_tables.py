"""add crowding history tables (data_field_snapshots, alpha_field_snapshot)

Revision ID: d9f2a3b4c5e6
Revises: c8e1f2a3b4c5
Create Date: 2026-08-15
"""

from __future__ import annotations

import json
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "d9f2a3b4c5e6"
down_revision = "c8e1f2a3b4c5"
branch_labels = None
depends_on = None

_CATEGORIES = (
    "price", "volume", "fundamentals", "cashflow", "news", "macro",
    "analyst", "corporate", "alternative", "options", "risk"
)
_FIELD_TYPES = ("MATRIX", "VECTOR", "GROUP")
_GROUP_FIELDS = {"sector", "industry", "subindustry", "market", "cap"}


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create data_field_snapshots table
    op.create_table(
        "data_field_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("field_code", sa.String(128), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False, server_default="price"),
        sa.Column("field_type", sa.String(16), nullable=False, server_default="MATRIX"),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("user_count", sa.Integer(), nullable=True),
        sa.Column("alpha_count", sa.Integer(), nullable=True),
        sa.Column("delay", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("region", sa.String(16), nullable=False, server_default="USA"),
        sa.Column("universe", sa.String(32), nullable=False, server_default="TOP3000"),
        sa.Column("instrument_type", sa.String(32), nullable=False, server_default="EQUITY"),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("field_code", "region", "delay", "universe", "as_of_date", name="field_snapshot_config_as_of"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="category_valid"),
        sa.CheckConstraint(_in("field_type", _FIELD_TYPES), name="field_type_valid"),
    )
    op.create_index("ix_data_field_snapshots_field_code", "data_field_snapshots", ["field_code"])
    op.create_index("ix_data_field_snapshots_as_of_date", "data_field_snapshots", ["as_of_date"])

    # 2. Create alpha_field_snapshot table
    op.create_table(
        "alpha_field_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alpha_id", sa.Integer(), nullable=False),
        sa.Column("field_code", sa.String(128), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=True),
        sa.Column("user_count", sa.Integer(), nullable=True),
        sa.Column("alpha_count", sa.Integer(), nullable=True),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("is_approximate", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["alpha_id"], ["alphas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["field_id"], ["data_fields.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("alpha_id", "field_code", name="alpha_field_unique"),
    )
    op.create_index("ix_alpha_field_snapshot_alpha_id", "alpha_field_snapshot", ["alpha_id"])
    op.create_index("ix_alpha_field_snapshot_field_code", "alpha_field_snapshot", ["field_code"])

    # 3. Backfill data_field_snapshots from current data_fields
    bind.exec_driver_sql("""
        INSERT OR IGNORE INTO data_field_snapshots (
            field_code, dataset_id, category, field_type,
            coverage, user_count, alpha_count,
            delay, region, universe, instrument_type,
            as_of_date, created_at, updated_at
        )
        SELECT
            field_code, dataset_id, category, field_type,
            coverage, user_count, alpha_count,
            delay, region, universe, instrument_type,
            DATE(created_at), created_at, updated_at
        FROM data_fields
    """)

    # 4. Backfill alpha_field_snapshot for existing alphas
    # Query field lookup dictionary: (field_code, region, delay, universe) -> (id, user_count, alpha_count, coverage)
    df_rows = bind.exec_driver_sql(
        "SELECT id, field_code, region, delay, universe, user_count, alpha_count, coverage FROM data_fields"
    ).fetchall()
    field_lookup = {}
    for r in df_rows:
        fid, fcode, reg, del_, univ, ucnt, acnt, cov = r
        key = (fcode.lower(), reg, int(del_), univ)
        field_lookup[key] = (fid, fcode, ucnt, acnt, cov)
        # also fallback by field_code alone
        if fcode.lower() not in field_lookup:
            field_lookup[fcode.lower()] = (fid, fcode, ucnt, acnt, cov)

    # Fetch alphas
    alpha_rows = bind.exec_driver_sql(
        "SELECT id, expression, region, delay, universe, feature_json, created_at FROM alphas"
    ).fetchall()

    snapshots_to_insert = []
    for aid, exp, reg, del_, univ, fj_str, cat in alpha_rows:
        fields = []
        if fj_str:
            try:
                fj = json.loads(fj_str) if isinstance(fj_str, str) else fj_str
                fields = fj.get("distinct_fields", [])
            except Exception:
                pass
        clean_fields = [f for f in fields if f.lower() not in _GROUP_FIELDS]
        if not clean_fields and exp:
            # Fallback extract fields from expression
            try:
                from app.validator.parser import parse
                from app.validator.ast_nodes import Field, children

                def _walk(node):
                    res = []
                    if isinstance(node, Field):
                        res.append(node.name)
                    for c in children(node):
                        res.extend(_walk(c))
                    return res

                ast = parse(exp)
                clean_fields = [f for f in sorted(set(_walk(ast))) if f.lower() not in _GROUP_FIELDS]
            except Exception:
                pass

        cat_dt = cat if isinstance(cat, datetime) else (datetime.fromisoformat(str(cat)) if cat else datetime.utcnow())

        for f in clean_fields:
            key = (f.lower(), reg, int(del_), univ)
            match = field_lookup.get(key) or field_lookup.get(f.lower())
            if match:
                fid, canonical_code, ucnt, acnt, cov = match
            else:
                fid, canonical_code, ucnt, acnt, cov = None, f, None, None, None

            snapshots_to_insert.append((
                aid, canonical_code, fid, ucnt, acnt, cov, str(cat_dt), 1
            ))

    if snapshots_to_insert:
        # Deduplicate on (alpha_id, field_code)
        seen = set()
        deduped = []
        for s in snapshots_to_insert:
            k = (s[0], s[1])
            if k not in seen:
                seen.add(k)
                deduped.append(s)

        # Batch insert
        bind.exec_driver_sql("""
            INSERT OR IGNORE INTO alpha_field_snapshot (
                alpha_id, field_code, field_id, user_count, alpha_count, coverage, captured_at, is_approximate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, deduped)


def downgrade() -> None:
    op.drop_table("alpha_field_snapshot")
    op.drop_table("data_field_snapshots")
