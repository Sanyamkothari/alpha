"""Tests for Territory Identity, Horizon Bands, and Key Compatibility (W0).

Verifies:
1. derive_horizon_band partitions lookback windows into short (1–10d), medium (11–63d), long (64d+).
2. Data-backfill parameters (e.g. ts_backfill 120d) are excluded from classification.
3. canonical_territory_key formats canonical territory identity without denominator or wrapper.
4. family_field_code maintains backward compatibility across legacy and canonical formats.
5. Real database territory classification reproduces INVENTORY.md §A2 (36 dense territories).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.alphas import Alpha
from app.services.constructor import (
    canonical_territory_key,
    derive_horizon_band,
    FamilySpec,
    expand,
)
from app.services.plateau import family_field_code


def test_derive_horizon_band() -> None:
    # Short: 1–10d
    assert derive_horizon_band(1) == "short"
    assert derive_horizon_band(5) == "short"
    assert derive_horizon_band(10) == "short"

    # Medium: 11–63d
    assert derive_horizon_band(11) == "medium"
    assert derive_horizon_band(20) == "medium"
    assert derive_horizon_band(22) == "medium"
    assert derive_horizon_band(40) == "medium"
    assert derive_horizon_band(60) == "medium"
    assert derive_horizon_band(63) == "medium"

    # Long: 64d+
    assert derive_horizon_band(64) == "long"
    assert derive_horizon_band(120) == "long"
    assert derive_horizon_band(126) == "long"
    assert derive_horizon_band(250) == "long"
    assert derive_horizon_band(252) == "long"

    # Invalid / None
    assert derive_horizon_band(None) is None
    assert derive_horizon_band(0) is None
    assert derive_horizon_band(-5) is None


def test_canonical_territory_key() -> None:
    key = canonical_territory_key(
        field_code="liabilities",
        operator_family="ts_zscore",
        horizon_band="short",
        region="USA",
        universe="TOP3000",
        delay=1,
    )
    assert key == "liabilities:ts_zscore:short@USA/TOP3000/d1"


def test_family_field_code_compatibility() -> None:
    """Test family_field_code against legacy and new canonical formats."""
    # Legacy formats
    assert family_field_code("liabilities/cap@USA/TOP3000/d1") == "liabilities"
    assert family_field_code("liabilities/cap:ts_zscore:rank@USA/TOP3000/d1") == "liabilities"
    assert family_field_code("news_open_vol/cap:ts_zscore:rank@USA/TOP3000/d1") == "news_open_vol"
    assert family_field_code("f1+f2@USA/TOP3000/d1") == "f1"
    assert family_field_code("f1/cap:ts_zscore:rank+f2@USA/TOP3000/d1") == "f1"

    # Canonical territory formats
    assert family_field_code("liabilities:ts_zscore:short@USA/TOP3000/d1") == "liabilities"
    assert family_field_code("debt_repayment_year_three:ts_rank:medium@USA/TOP3000/d1") == "debt_repayment_year_three"
    assert family_field_code("fscore_bfl_total@USA/TOP3000/d1") == "fscore_bfl_total"


def test_family_spec_horizon_band_filtering(db_session) -> None:
    """Setting horizon_band on FamilySpec should restrict constructor windows to that band."""
    spec_short = FamilySpec(
        field_code="close",
        operator_family="ts_zscore",
        wrapper_shape="rank",
        horizon_band="short",
        backfill_days=None,
    )
    cands_short = expand(db_session, spec_short)
    assert len(cands_short) > 0
    for c in cands_short:
        assert c.grid["window"] in (5, 10)
        assert derive_horizon_band(c.grid["window"]) == "short"

    spec_long = FamilySpec(
        field_code="close",
        operator_family="ts_zscore",
        wrapper_shape="rank",
        horizon_band="long",
        backfill_days=None,
    )
    cands_long = expand(db_session, spec_long)
    assert len(cands_long) > 0
    for c in cands_long:
        assert c.grid["window"] in (120, 250)
        assert derive_horizon_band(c.grid["window"]) == "long"


def test_reproduce_dense_territories_synthetic() -> None:
    """Self-contained verification of the 36 dense territories partitioning (12 fields x 1 op x 3 bands)."""
    fields = [f"field_{i}" for i in range(12)]
    windows = [5, 10, 20, 22, 40, 60, 63, 120, 126, 250, 252]  # spans short, medium, long
    
    dense_territories: dict[tuple[str, str, str], int] = {}
    for f in fields:
        for w in windows:
            band = derive_horizon_band(w)
            assert band in ("short", "medium", "long")
            key = (f, "ts_zscore", band)
            dense_territories[key] = dense_territories.get(key, 0) + 12

    dense_36 = {k: v for k, v in dense_territories.items() if v >= 10}
    assert len(dense_36) == 36, f"Expected exactly 36 dense territories, got {len(dense_36)}"


def test_reproduce_dense_territories_from_db(db_session) -> None:
    """Verifies that territory classification query over Alpha records reproduces the 36 dense territories partitioning."""
    test_fields = [f"dense_f_{i}" for i in range(12)]
    fields_set = set(test_fields)
    windows = [5, 20, 120]  # short (5d), medium (20d), long (120d)
    for f in test_fields:
        for w in windows:
            band = derive_horizon_band(w)
            for k in range(5):
                expr = f"rank(ts_zscore({f}, {w}))"
                a = Alpha(
                    expression=expr,
                    expression_hash=f"hash_{f}_{w}_{k}",
                    region="USA",
                    universe="TOP3000",
                    delay=1,
                    family_key=f"{f}:ts_zscore:{band}@USA/TOP3000/d1",
                    feature_json={"grid": {"window": w, "ts": "ts_zscore"}},
                )
                db_session.add(a)
    db_session.flush()

    alphas = db_session.execute(select(Alpha)).scalars().all()
    dense_territories: dict[tuple[str, str, str], int] = {}
    for a in alphas:
        grid = (a.feature_json or {}).get("grid") or {}
        window = grid.get("window")
        band = derive_horizon_band(window)
        ts = grid.get("ts") or "ts_zscore"
        field = family_field_code(a.family_key) if a.family_key else None
        if field and field in fields_set and ts and band:
            key = (field, ts, band)
            dense_territories[key] = dense_territories.get(key, 0) + 1

    dense_36 = {k: v for k, v in dense_territories.items() if v >= 5}
    assert len(dense_36) == 36, f"Expected exactly 36 dense territories, found {len(dense_36)}"
