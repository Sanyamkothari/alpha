"""PnL Storage Engine — stores daily PnL vectors per alpha and computes date-aligned matrices.

Persists PnL arrays as fast individual .npy files under USER_DATA_DIR/database/pnl/
with an in-memory cache for fast sub-millisecond matrix intersections.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import structlog

from app.config import USER_DATA_DIR

log = structlog.get_logger("pnl_storage")


class PnLStore:
    """Thread-safe persistent store for daily PnL vectors."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._dir = base_dir or (USER_DATA_DIR / "database" / "pnl")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[int, tuple[list[str], np.ndarray]] = {}
        self._lock = threading.Lock()

    def save_pnl(
        self, alpha_id: int, dates: list[str], pnl_values: list[float] | np.ndarray
    ) -> None:
        """Save a daily PnL series for an alpha."""
        arr = np.asarray(pnl_values, dtype=np.float64)
        with self._lock:
            self._cache[alpha_id] = (dates, arr)
            npy_path = self._dir / f"{alpha_id}.npy"
            json_path = self._dir / f"{alpha_id}_dates.json"
            try:
                np.save(npy_path, arr)
                json_path.write_text(json.dumps(dates), encoding="utf-8")
            except Exception as exc:
                log.warning("pnl_save_failed", alpha_id=alpha_id, error=str(exc))

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
                dates = json.loads(json_path.read_text(encoding="utf-8"))
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

        # Intersect all date lists
        common_dates = set(loaded[0][1])
        for _, dates, _ in loaded[1:]:
            common_dates.intersection_update(dates)

        sorted_dates = sorted(common_dates)
        if len(sorted_dates) < min_overlap:
            log.warning("insufficient_date_overlap", common_days=len(sorted_dates), required=min_overlap)
            return [], [], np.empty((0, 0), dtype=np.float64)

        date_indices: dict[str, int] = {d: i for i, d in enumerate(sorted_dates)}
        aligned_rows: list[np.ndarray] = []
        valid_ids: list[int] = []

        for aid, dates, arr in loaded:
            # Map values to common dates
            orig_map = dict(zip(dates, arr))
            row = np.array([orig_map[d] for d in sorted_dates], dtype=np.float64)
            aligned_rows.append(row)
            valid_ids.append(aid)

        matrix = np.vstack(aligned_rows)
        return valid_ids, sorted_dates, matrix


_default_store = PnLStore()


def get_pnl_store() -> PnLStore:
    return _default_store
