"""PnL Storage Engine — stores daily PnL vectors per alpha and computes date-aligned matrices.

Persists PnL arrays as fast individual .npy files under USER_DATA_DIR/database/pnl/
with an in-memory cache for fast sub-millisecond matrix intersections.
"""

from __future__ import annotations

import json
import os
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
        self,
        alpha_id: int,
        dates: list[str],
        pnl_values: list[float] | np.ndarray,
        *,
        convention: str = "daily",
        reported_sharpe: float | None = None,
    ) -> None:
        """Save a daily PnL series for an alpha, with provenance metadata."""
        arr = np.asarray(pnl_values, dtype=np.float64)
        if len(dates) != len(arr):
            raise ValueError(
                f"pnl length mismatch for alpha {alpha_id}: {len(dates)} dates vs {len(arr)} values"
            )
        with self._lock:
            self._cache[alpha_id] = (dates, arr)
            npy_path = self._dir / f"{alpha_id}.npy"
            json_path = self._dir / f"{alpha_id}_dates.json"
            meta_path = self._dir / f"{alpha_id}_meta.json"
            tmp_npy = self._dir / f"{alpha_id}.npy.tmp"
            tmp_json = self._dir / f"{alpha_id}_dates.json.tmp"
            tmp_meta = self._dir / f"{alpha_id}_meta.json.tmp"
            try:
                with open(tmp_npy, "wb") as fh:
                    np.save(fh, arr)
                tmp_json.write_text(json.dumps(dates), encoding="utf-8")
                meta = {
                    "convention_at_source": convention,
                    "reported_sharpe": reported_sharpe,
                }
                tmp_meta.write_text(json.dumps(meta), encoding="utf-8")

                os.replace(tmp_npy, npy_path)
                os.replace(tmp_json, json_path)
                os.replace(tmp_meta, meta_path)
            except Exception as exc:
                self._cache.pop(alpha_id, None)
                for p in (tmp_npy, tmp_json, tmp_meta):
                    if p.exists():
                        try:
                            p.unlink()
                        except OSError:
                            pass
                log.warning("pnl_save_failed", alpha_id=alpha_id, error=str(exc))
                raise

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
                raw_dates = json.loads(json_path.read_text(encoding="utf-8"))
                dates = raw_dates.get("dates", []) if isinstance(raw_dates, dict) else raw_dates
                self._cache[alpha_id] = (dates, arr)
                return dates, arr
            except Exception as exc:
                log.warning("pnl_load_failed", alpha_id=alpha_id, error=str(exc))
                return None

    def load_meta(self, alpha_id: int) -> dict | None:
        """Load provenance metadata for an alpha PnL series."""
        meta_path = self._dir / f"{alpha_id}_meta.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("pnl_meta_load_failed", alpha_id=alpha_id, error=str(exc))
            return None

    def iter_alpha_ids(self) -> list[int]:
        """List all alpha IDs with stored .npy PnL files."""
        ids = []
        for npy_file in self._dir.glob("*.npy"):
            stem = npy_file.stem
            if stem.isdigit():
                ids.append(int(stem))
        return sorted(ids)

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
            log.warning(
                "insufficient_date_overlap", common_days=len(sorted_dates), required=min_overlap
            )
            return [], [], np.empty((0, 0), dtype=np.float64)

        aligned_rows: list[np.ndarray] = []
        valid_ids: list[int] = []

        for aid, dates, arr in loaded:
            orig_map = dict(zip(dates, arr, strict=True))
            row = np.array([orig_map[d] for d in sorted_dates], dtype=np.float64)
            aligned_rows.append(row)
            valid_ids.append(aid)

        matrix = np.vstack(aligned_rows)
        return valid_ids, sorted_dates, matrix


_default_store = PnLStore()


def get_pnl_store() -> PnLStore:
    return _default_store
