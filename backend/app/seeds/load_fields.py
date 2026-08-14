"""Load the mock field catalog from ``fields/usa_top3000_delay1_sample.json``.

Idempotent. The real read-only fetcher (Phase 4) writes the SAME tables via a thin
key-mapping shim (the live API uses camelCase ``id``/``userCount``/...).
"""

from __future__ import annotations

import json

import structlog

from app.config import FIELDS_DIR
from app.db import session_scope
from app.models.enums import FieldCategory, FieldType
from app.models.fields import DataField, Dataset

log = structlog.get_logger("seeds.fields")

FIELDS_FILE = FIELDS_DIR / "usa_top3000_delay1_sample.json"

_VALID_CATEGORIES = {e.value for e in FieldCategory}
_VALID_TYPES = {e.value for e in FieldType}


def _norm_category(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in _VALID_CATEGORIES:
        log.warning("unknown_field_category", value=value, fallback="alternative")
        return FieldCategory.ALTERNATIVE.value
    return v


def _norm_type(value: str) -> str:
    v = (value or "").strip().upper()
    if v not in _VALID_TYPES:
        log.warning("unknown_field_type", value=value, fallback="MATRIX")
        return FieldType.MATRIX.value
    return v


def run() -> dict[str, int]:
    payload = json.loads(FIELDS_FILE.read_text(encoding="utf-8"))
    results = payload.get("results", [])

    inserted = updated = 0
    with session_scope() as db:
        # Cache datasets/fields by natural key to upsert.
        ds_cache: dict[tuple, Dataset] = {}
        for ds in db.query(Dataset).all():
            ds_cache[(ds.dataset_code, ds.region, ds.delay, ds.universe)] = ds
        field_cache: dict[tuple, DataField] = {}
        for f in db.query(DataField).all():
            field_cache[(f.field_code, f.region, f.delay, f.universe)] = f

        for row in results:
            region = row.get("region", "USA")
            delay = int(row.get("delay", 1))
            universe = row.get("universe", "TOP3000")
            ds_info = row.get("dataset") or {}
            ds_code = str(ds_info.get("id", "unknown"))
            ds_key = (ds_code, region, delay, universe)
            dataset = ds_cache.get(ds_key)
            if dataset is None:
                dataset = Dataset(
                    dataset_code=ds_code,
                    name=str(ds_info.get("name", ds_code)),
                    region=region,
                    delay=delay,
                    universe=universe,
                )
                db.add(dataset)
                db.flush()  # need dataset.id
                ds_cache[ds_key] = dataset

            f_key = (row["field_code"], region, delay, universe)
            field = field_cache.get(f_key)
            if field is None:
                field = DataField(
                    field_code=row["field_code"], region=region, delay=delay, universe=universe
                )
                db.add(field)
                inserted += 1
                field_cache[f_key] = field
            else:
                updated += 1

            field.description = row.get("description")
            field.dataset_id = dataset.id
            field.category = _norm_category(row.get("category", ""))
            field.field_type = _norm_type(row.get("type", "MATRIX"))
            field.coverage = row.get("coverage")
            field.user_count = row.get("user_count")
            field.alpha_count = row.get("alpha_count")
            field.instrument_type = row.get("instrument_type", "EQUITY")
            field.unit = row.get("unit")
            field.classification_confidence = 1.0  # seeded => trusted

        # Refresh dataset field_count. Flush first: under autoflush=False the
        # last dataset's freshly-added fields are otherwise unflushed when the
        # COUNT runs, so its field_count would be stored as 0.
        db.flush()
        for dataset in ds_cache.values():
            dataset.field_count = (
                db.query(DataField).filter(DataField.dataset_id == dataset.id).count()
            )

    with session_scope() as db:
        total = db.query(DataField).count()
        groups = db.query(DataField).filter(DataField.field_type == FieldType.GROUP).count()
        datasets = db.query(Dataset).count()
    result = {
        "inserted": inserted,
        "updated": updated,
        "total_fields": total,
        "group_fields": groups,
        "datasets": datasets,
    }
    log.info("fields_seeded", **result)
    return result


if __name__ == "__main__":
    print(run())
