"""PnL Storage Engine — stores daily PnL vectors per alpha and computes date-aligned matrices.

Persists PnL arrays as fast individual .npy files under USER_DATA_DIR/database/pnl/
with an in-memory cache for fast sub-millisecond matrix intersections.
Enforces boundary normalisation (differencing cumulative curves) and Sharpe reconciliation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Literal

import numpy as np
import structlog

from app.config import USER_DATA_DIR
from app.services.filter_config import DEFAULT_FILTER_CONFIG, TRADING_DAYS_PER_YEAR, FilterConfig

log = structlog.get_logger("pnl_storage")


@dataclass
class PnLSaveResult:
    alpha_id: int
    saved: bool
    series_kind: str
    reconciled: bool
    recomputed_sharpe: float | None = None
    reported_sharpe: float | None = None
    rejection_reason: str | None = None


def compute_vector_sharpe(arr: np.ndarray) -> float:
    """Compute annualized Sharpe from a 1D daily returns/PnL vector."""
    if len(arr) < 2:
        return 0.0
    std = float(np.std(arr, ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(np.mean(arr) / std * math.sqrt(TRADING_DAYS_PER_YEAR))


def compute_lag1_autocorr(arr: np.ndarray) -> float:
    """Compute lag-1 autocorrelation of a 1D series."""
    if len(arr) < 10:
        return 0.0
    std1 = float(np.std(arr[:-1]))
    std2 = float(np.std(arr[1:]))
    if std1 <= 1e-12 or std2 <= 1e-12:
        return 0.0
    corr = np.corrcoef(arr[:-1], arr[1:])[0, 1]
    return float(corr) if np.isfinite(corr) else 0.0


def is_cumulative_series(arr: np.ndarray) -> bool:
    """Detect if an array is a cumulative PnL curve rather than daily increments."""
    if len(arr) < 30:
        return False
    frac_pos = float((arr > 0).mean())
    autocorr = compute_lag1_autocorr(arr)
    return autocorr > 0.99 and (frac_pos > 0.95 or frac_pos < 0.05)


class PnLStore:
    """Thread-safe persistent store for daily PnL vectors."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._dir = base_dir or (USER_DATA_DIR / "database" / "pnl")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[int, tuple[list[str], np.ndarray]] = {}
        self._lock = threading.Lock()

    def save_pnl(
        self,
        alpha_id: int,
        dates: list[str],
        pnl_values: list[float] | np.ndarray,
        *,
        reported_sharpe: float | None = None,
        series_kind: Literal["daily", "cumulative", "auto"] = "auto",
        source: str = "brain_api",
        cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
    ) -> PnLSaveResult:
        """Save a daily PnL series for an alpha, enforcing differencing and reconciliation."""
        arr = np.asarray(pnl_values, dtype=np.float64)
        if len(arr) != len(dates):
            reason = f"dates length ({len(dates)}) != pnl values length ({len(arr)})"
            log.warning("pnl_save_rejected_dimension_mismatch", alpha_id=alpha_id, reason=reason)
            return PnLSaveResult(
                alpha_id=alpha_id, saved=False, series_kind="invalid", reconciled=False, rejection_reason=reason
            )

        detected_kind = "daily"
        if series_kind == "cumulative" or (series_kind == "auto" and is_cumulative_series(arr)):
            detected_kind = "cumulative"
            # Difference the cumulative curve to recover daily increments
            if len(arr) > 1:
                diff_arr = np.diff(arr)
                diff_dates = dates[1:]
                # Anchor first day diff as 0.0 or prepend original first point if sensible
                arr = np.concatenate([[arr[0]], diff_arr])
                log.info("cumulative_pnl_differenced", alpha_id=alpha_id, original_len=len(pnl_values))

        recomputed_sr = compute_vector_sharpe(arr)
        reconciled = True
        if reported_sharpe is not None and not math.isnan(reported_sharpe) and abs(reported_sharpe) > 0.01:
            diff = abs(recomputed_sr - reported_sharpe)
            if diff > cfg.sharpe_reconciliation_tolerance:
                reconciled = False
                reason = (
                    f"Sharpe reconciliation failed: recomputed {recomputed_sr:.3f} deviates from reported "
                    f"{reported_sharpe:.3f} (diff {diff:.3f} > {cfg.sharpe_reconciliation_tolerance:.3f})"
                )
                log.warning("pnl_save_rejected_reconciliation", alpha_id=alpha_id, reason=reason)
                return PnLSaveResult(
                    alpha_id=alpha_id,
                    saved=False,
                    series_kind=detected_kind,
                    reconciled=False,
                    recomputed_sharpe=recomputed_sr,
                    reported_sharpe=reported_sharpe,
                    rejection_reason=reason,
                )

        with self._lock:
            self._cache[alpha_id] = (dates, arr)
            npy_path = self._dir / f"{alpha_id}.npy"
            json_path = self._dir / f"{alpha_id}_dates.json"
            try:
                np.save(npy_path, arr)
                sidecar = {
                    "dates": dates,
                    "series_kind": detected_kind,
                    "source": source,
                    "reconciled": reconciled,
                    "recomputed_sharpe": recomputed_sr,
                }
                json_path.write_text(json.dumps(sidecar), encoding="utf-8")
                return PnLSaveResult(
                    alpha_id=alpha_id,
                    saved=True,
                    series_kind=detected_kind,
                    reconciled=reconciled,
                    recomputed_sharpe=recomputed_sr,
                    reported_sharpe=reported_sharpe,
                )
            except Exception as exc:
                log.warning("pnl_save_failed", alpha_id=alpha_id, error=str(exc))
                return PnLSaveResult(
                    alpha_id=alpha_id,
                    saved=False,
                    series_kind=detected_kind,
                    reconciled=False,
                    rejection_reason=str(exc),
                )

    def load_pnl(self, alpha_id: int) -> tuple[list[str], np.ndarray] | None:
        """Load the daily PnL series for an alpha."""
        with self._lock:
            if alpha_id in self._cache:
                return self._cache[alpha_id]

            npy_path = self._dir / f"{alpha_id}.npy"
            json_path = self._dir / f"{alpha_id}_dates.json"
            if not (npy_path.exists() and json_path.exists()):
                return None
            try:
                arr = np.load(npy_path)
                raw_json = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(raw_json, dict):
                    dates = raw_json.get("dates", [])
                elif isinstance(raw_json, list):
                    dates = raw_json
                else:
                    return None
                self._cache[alpha_id] = (dates, arr)
                return dates, arr
            except Exception as exc:
                log.warning("pnl_load_failed", alpha_id=alpha_id, error=str(exc))
                return None

    def get_aligned_matrix(
        self, alpha_ids: list[int], min_overlap: int = 500
    ) -> tuple[list[int], list[str], np.ndarray]:
        """Intersect date indices and return an aligned (N_alphas x T_common) matrix."""
        loaded: list[tuple[int, list[str], np.ndarray]] = []
        for aid in alpha_ids:
            res = self.load_pnl(aid)
            if res is not None:
                dates, arr = res
                if len(dates) == len(arr) and len(arr) >= min_overlap:
                    loaded.append((aid, dates, arr))

        if not loaded:
            return [], [], np.empty((0, 0), dtype=np.float64)

        common_dates = set(loaded[0][1])
        for _, dates, _ in loaded[1:]:
            common_dates.intersection_update(dates)

        sorted_dates = sorted(common_dates)
        if len(sorted_dates) < min_overlap:
            log.warning("insufficient_date_overlap", common_days=len(sorted_dates), required=min_overlap)
            return [], [], np.empty((0, 0), dtype=np.float64)

        aligned_rows: list[np.ndarray] = []
        valid_ids: list[int] = []

        for aid, dates, arr in loaded:
            orig_map = dict(zip(dates, arr))
            row = np.array([orig_map[d] for d in sorted_dates], dtype=np.float64)
            aligned_rows.append(row)
            valid_ids.append(aid)

        matrix = np.vstack(aligned_rows)
        return valid_ids, sorted_dates, matrix


_default_store: PnLStore | None = None


def get_pnl_store(base_dir: Path | None = None) -> PnLStore:
    """Singleton getter for the default PnLStore instance."""
    global _default_store
    if base_dir is not None:
        return PnLStore(base_dir)
    if _default_store is None:
        _default_store = PnLStore()
    return _default_store
