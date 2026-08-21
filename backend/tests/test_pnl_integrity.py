"""Tests for PnLStore length invariant and torn pair handling."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.models.alphas import Alpha, SubmissionAttempt
from app.services.correlation import _date_map, check_portfolio_empirical_correlation
from app.services.pnl_storage import PnLStore


def test_save_pnl_rejects_length_mismatch(tmp_path: Path) -> None:
    store = PnLStore(tmp_path)
    with pytest.raises(ValueError, match="pnl length mismatch"):
        store.save_pnl(1, ["2023-01-01"], [1.0, 2.0])

    assert 1 not in store._cache
    assert not (tmp_path / "1.npy").exists()
    assert not (tmp_path / "1_dates.json").exists()
    assert not (tmp_path / "1.npy.tmp").exists()
    assert not (tmp_path / "1_dates.json.tmp").exists()


def test_save_pnl_roundtrips_matched_lengths(tmp_path: Path) -> None:
    store = PnLStore(tmp_path)
    dates = ["2023-01-01", "2023-01-02", "2023-01-03"]
    values = [0.1, -0.2, 0.3]
    store.save_pnl(2, dates, values)

    loaded = store.load_pnl(2)
    assert loaded is not None
    loaded_dates, loaded_values = loaded
    assert loaded_dates == dates
    np.testing.assert_allclose(loaded_values, values)

    # Check persistence and no leftover temp files
    assert (tmp_path / "2.npy").exists()
    assert (tmp_path / "2_dates.json").exists()
    assert not (tmp_path / "2.npy.tmp").exists()
    assert not (tmp_path / "2_dates.json.tmp").exists()


def test_torn_pair_on_disk_is_skipped_not_crashed(tmp_path: Path, db_session) -> None:
    store = PnLStore(tmp_path)

    # Manually create a torn pair on disk (5 dates vs 10 values)
    alpha_id = 999
    dates = [f"2023-01-{i:02d}" for i in range(1, 6)]
    values = np.ones(10, dtype=np.float64)

    (tmp_path / f"{alpha_id}_dates.json").write_text(json.dumps(dates), encoding="utf-8")
    with open(tmp_path / f"{alpha_id}.npy", "wb") as f:
        np.save(f, values)

    loaded = store.load_pnl(alpha_id)
    assert loaded is not None
    # _date_map detects length mismatch and returns None instead of crashing on zip
    assert _date_map(loaded) is None

    # Verify correlation gate handles torn candidate gracefully without crashing
    cand = Alpha(
        id=alpha_id,
        expression="rank(ts_zscore(close, 10))",
        expression_hash="torn_cand_hash",
        status="untested",
    )
    port_alpha = Alpha(
        id=1000,
        expression="rank(ts_zscore(volume, 10))",
        expression_hash="port_alpha_hash",
        status="submitted",
    )
    db_session.add(cand)
    db_session.add(port_alpha)
    db_session.flush()
    db_session.add(SubmissionAttempt(alpha_id=port_alpha.id, result="submitted"))
    db_session.flush()

    # Valid PnL for portfolio alpha
    port_dates = [f"2023-01-{i:02d}" for i in range(1, 600)]
    port_values = np.ones(len(port_dates), dtype=np.float64)
    store.save_pnl(port_alpha.id, port_dates, port_values)

    # Must complete without exception
    is_corr, reason, max_c = check_portfolio_empirical_correlation(
        db_session, cand.id, pnl_store=store
    )
    assert isinstance(is_corr, bool)
