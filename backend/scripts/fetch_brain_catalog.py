"""Stage 1 — replace the mock field catalog with the real one from BRAIN.

This is the blocker for everything else. Until it runs, the constructor can only
emit expressions over 122 invented fields, none of which simulate.

What it pulls, per (region, delay, universe):
  * ``GET /data-sets``   -> ``datasets``
  * ``GET /data-fields`` -> ``data_fields`` (~4,367 for USA/TOP3000/delay-1)
  * ``GET /operators``   -> refreshes the operator knowledge base

Two details that matter downstream:

``type`` (MATRIX / VECTOR / GROUP) is load-bearing. The validator's group-operator
rule — ``group_*`` requires a GROUP field — is only correct if this is accurate,
and it cannot be inferred from the field name.

``userCount``/``alphaCount`` are the crowding signal. STRATEGY.md Rule 1 says to
mine under-used datasets; these two columns are how you find them, so they are
stored rather than discarded.

The catalog is replaced per-config, not globally: rows for the configs being
fetched are deleted first, so a USA/TOP3000 refresh never disturbs another
region. Alphas are untouched — ``data_fields`` has no inbound FK from ``alphas``.

Usage:
    python -m scripts.fetch_brain_catalog                 # USA/TOP3000/delay 1
    python -m scripts.fetch_brain_catalog --delay 0
    python -m scripts.fetch_brain_catalog --skip-operators
"""

from datetime import date

import argparse
import sys

import structlog
from sqlalchemy import delete, select

from app.db import session_scope
from app.models.enums import FieldCategory, FieldType
from app.models.fields import DataField, DataFieldSnapshot, Dataset
from app.services.brain import BrainClient

log = structlog.get_logger("fetch_brain_catalog")

# BRAIN's category ids -> our FieldCategory enum. Anything unmapped falls back to
# ALTERNATIVE rather than being dropped: an unknown category is still a usable
# field, and silently losing fields is the failure mode that matters here.
_CATEGORY_MAP = {
    "price-volume": FieldCategory.PRICE,
    "pv": FieldCategory.PRICE,
    "volume": FieldCategory.VOLUME,
    "fundamental": FieldCategory.FUNDAMENTALS,
    "cashflow": FieldCategory.CASHFLOW,
    "news": FieldCategory.NEWS,
    "social": FieldCategory.NEWS,
    "sentiment": FieldCategory.NEWS,
    "macro": FieldCategory.MACRO,
    "analyst": FieldCategory.ANALYST,
    "earnings": FieldCategory.ANALYST,
    "model": FieldCategory.RISK,
    "risk": FieldCategory.RISK,
    "option": FieldCategory.OPTIONS,
    "options": FieldCategory.OPTIONS,
    "institutions": FieldCategory.CORPORATE,
    "insiders": FieldCategory.CORPORATE,
    "shortinterest": FieldCategory.CORPORATE,
}

_VALID_TYPES = {t.value for t in FieldType}


def _category_for(payload: dict) -> str:
    cat = (payload.get("category") or {}).get("id") or ""
    mapped = _CATEGORY_MAP.get(cat.lower())
    if mapped:
        return mapped.value
    for key, val in _CATEGORY_MAP.items():
        if key in cat.lower():
            return val.value
    return FieldCategory.ALTERNATIVE.value


def _field_type_for(payload: dict) -> str:
    raw = str(payload.get("type") or "").upper()
    return raw if raw in _VALID_TYPES else FieldType.MATRIX.value


def fetch(
    *,
    region: str,
    delay: int,
    universe: str,
    skip_operators: bool = False,
    as_of: date | None = None,
) -> dict[str, int]:
    counts = {"datasets": 0, "fields": 0, "snapshots": 0, "operators": 0, "unmapped_categories": 0}
    as_of_date = as_of or date.today()

    with BrainClient() as brain:
        datasets = brain.data_sets(region=region, delay=delay, universe=universe)
        log.info("fetched_datasets", count=len(datasets))

        fields = list(brain.data_fields(region=region, delay=delay, universe=universe))
        log.info("fetched_fields", count=len(fields))

        operators = [] if skip_operators else brain.operators()

    with session_scope() as db:
        # Replace current-state data_fields for this (region, delay, universe) slice.
        db.execute(
            delete(DataField).where(
                DataField.region == region,
                DataField.delay == delay,
                DataField.universe == universe,
            )
        )
        db.execute(
            delete(Dataset).where(
                Dataset.region == region,
                Dataset.delay == delay,
                Dataset.universe == universe,
            )
        )
        # For historical snapshots, replace only today's snapshot for this config if re-run on the same day.
        # This keeps all prior historical snapshot dates completely intact.
        db.execute(
            delete(DataFieldSnapshot).where(
                DataFieldSnapshot.region == region,
                DataFieldSnapshot.delay == delay,
                DataFieldSnapshot.universe == universe,
                DataFieldSnapshot.as_of_date == as_of_date,
            )
        )
        db.flush()

        code_to_dataset: dict[str, Dataset] = {}
        for payload in datasets:
            row = Dataset(
                dataset_code=payload.get("id", ""),
                name=payload.get("name") or payload.get("id", ""),
                description=payload.get("description"),
                region=region,
                delay=delay,
                universe=universe,
                instrument_type="EQUITY",
                field_count=payload.get("fieldCount") or 0,
            )
            db.add(row)
            code_to_dataset[row.dataset_code] = row
            counts["datasets"] += 1
        db.flush()

        for payload in fields:
            code = payload.get("id")
            if not code:
                continue
            ds_code = (payload.get("dataset") or {}).get("id", "")
            category = _category_for(payload)
            if (
                category == FieldCategory.ALTERNATIVE.value
                and (payload.get("category") or {}).get("id", "").lower() not in _CATEGORY_MAP
            ):
                counts["unmapped_categories"] += 1

            ds_id = code_to_dataset.get(ds_code).id if ds_code in code_to_dataset else None
            field_type_val = _field_type_for(payload)
            coverage_val = payload.get("coverage")
            user_cnt = payload.get("userCount")
            alpha_cnt = payload.get("alphaCount")
            delay_val = payload.get("delay", delay)
            region_val = payload.get("region", region)
            universe_val = payload.get("universe", universe)

            # 1. Write current-state table
            db.add(
                DataField(
                    field_code=code,
                    description=payload.get("description"),
                    dataset_id=ds_id,
                    category=category,
                    field_type=field_type_val,
                    coverage=coverage_val,
                    user_count=user_cnt,
                    alpha_count=alpha_cnt,
                    delay=delay_val,
                    region=region_val,
                    universe=universe_val,
                    instrument_type="EQUITY",
                )
            )
            counts["fields"] += 1

            # 2. Append dated historical snapshot
            db.add(
                DataFieldSnapshot(
                    field_code=code,
                    dataset_id=ds_id,
                    category=category,
                    field_type=field_type_val,
                    coverage=coverage_val,
                    user_count=user_cnt,
                    alpha_count=alpha_cnt,
                    delay=delay_val,
                    region=region_val,
                    universe=universe_val,
                    instrument_type="EQUITY",
                    as_of_date=as_of_date,
                )
            )
            counts["snapshots"] += 1

    if operators:
        counts["operators"] = _refresh_operators(operators)

    log.info("catalog_fetch_complete", region=region, delay=delay, universe=universe, as_of_date=str(as_of_date), **counts)
    return counts


def _refresh_operators(payloads: list[dict]) -> int:
    """Add operators BRAIN knows about that the seeded YAML does not.

    Additive on purpose. The curated YAML carries hand-written argument specs and
    window bands that the API does not expose, so overwriting a seeded operator
    with the thinner API record would *lose* validator precision. Only genuinely
    new names are inserted — e.g. ``ts_backfill``, which several imported alphas
    use and the local validator currently rejects as unknown.
    """
    from app.models.enums import OperatorCategory
    from app.models.operators import Operator

    added = 0
    with session_scope() as db:
        known = {name for (name,) in db.execute(select(Operator.name))}
        for payload in payloads:
            name = payload.get("name")
            if not name or name in known:
                continue
            raw_cat = str(payload.get("category") or "").lower().replace(" ", "_")
            category = (
                raw_cat
                if raw_cat in {c.value for c in OperatorCategory}
                else OperatorCategory.SPECIAL.value
            )
            db.add(
                Operator(
                    name=name,
                    category=category,
                    # definition is NOT NULL; the API always supplies it, but fall
                    # back to the bare name rather than failing the whole refresh.
                    definition=payload.get("definition") or name,
                    typical_usage=payload.get("description"),
                    scope=payload.get("level"),
                    # API-sourced: no curated argument specs or window bands, so
                    # the validator can only check existence, not arity. Flagged
                    # so that limitation is visible rather than assumed away.
                    is_seeded=False,
                    is_verified=False,
                )
            )
            known.add(name)
            added += 1
    return added


from scripts._cli import cli_main


@cli_main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--region", default="USA")
    ap.add_argument("--delay", type=int, default=1)
    ap.add_argument("--universe", default="TOP3000")
    ap.add_argument("--skip-operators", action="store_true")
    args = ap.parse_args()
    print(
        fetch(
            region=args.region,
            delay=args.delay,
            universe=args.universe,
            skip_operators=args.skip_operators,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

