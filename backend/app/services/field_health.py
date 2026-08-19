"""Which data fields are worth spending simulations on, derived from what they did.

Two fields in this database were simulated 44 times between them and produced a
portfolio holding **zero positions** every single time. 686 further alphas are queued
against those same fields. Nothing stopped the campaign drawing more of them.

**Derived, never stored.** There is deliberately no ``retired`` column on
``data_fields``. This project already lost two weeks to a state-drift incident caused by
hand-written local flags (see CLAUDE.md), and the response baked into the design was
that facts are derived from evidence and have exactly one source of truth. "This field
is dead" is a fact about simulation results, so it is computed from simulation results
and cannot drift away from them.

**Stored coverage does not predict this.** Measured on our own catalog:

    coverage 0.7264  anl4_..._sh_equity_mean   -> empty book, always
    coverage 0.5374  anl4_..._cff_mean         -> empty book, always
    coverage 0.5000  fnd6_newqv1300_spcep12    -> empty book, always
    coverage 0.4350  anl4_..._totgw_median     -> works, 42 simulations, real books

The field with the highest coverage is dead and the one with the lowest works. Coverage
cannot be used to pre-screen; only observed behaviour can.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.results import AlphaMetric

log = structlog.get_logger("field_health")

# One empty book can be an expression that legitimately produced nothing.
MIN_EMPTY_SIMS_TO_RETIRE = 3

# The harder question: all 23 empty results for `fnd6_newqv1300_spcep12` come from ONE
# family, so they share one template — `divide(ts_backfill(FIELD,120), cap)`. Evidence
# from a single template cannot distinguish "the field is empty" from "this template
# fails on this field", and retiring a field on a template's failure would discard real
# territory. So a field is only judged dead on one of two grounds:
#
#   * empties across MIN_FAMILIES_FOR_CONFIDENCE or more distinct templates, or
#   * a run of empties long enough within one template that a data problem is the only
#     plausible explanation — varying window and decay across a whole grid cannot turn a
#     field with data into an empty book, but it also cannot rule out a broken template
#     on fewer than a handful of points.
MIN_FAMILIES_FOR_CONFIDENCE = 2
MIN_EMPTY_SIMS_SINGLE_FAMILY = 10


@dataclass(frozen=True)
class FieldHealth:
    field_code: str
    simulated: int
    empty: int
    referenced: int  # alphas mentioning it, simulated or not
    families: int = 0  # distinct templates the empty results came from

    @property
    def is_dead(self) -> bool:
        if self.simulated < MIN_EMPTY_SIMS_TO_RETIRE or self.empty != self.simulated:
            return False
        # Corroborated across templates, or too long a run for a template bug to explain.
        return (
            self.families >= MIN_FAMILIES_FOR_CONFIDENCE
            or self.empty >= MIN_EMPTY_SIMS_SINGLE_FAMILY
        )

    @property
    def evidence(self) -> str:
        if not self.is_dead:
            if self.simulated and self.empty == self.simulated:
                return (
                    f"{self.empty} empty across {self.families} template(s) — not enough "
                    "to separate a dead field from a failing template"
                )
            return "produces a book"
        basis = (
            f"{self.families} distinct templates"
            if self.families >= MIN_FAMILIES_FOR_CONFIDENCE
            else f"{self.empty} empties within one template"
        )
        return f"never held a position; {basis}"

    @property
    def queued_waste(self) -> int:
        """Alphas queued against a dead field — the cost still ahead of us."""
        return max(0, self.referenced - self.simulated) if self.is_dead else 0


def field_health(db: Session) -> dict[str, FieldHealth]:
    """Per-field simulation outcomes, keyed by field code."""
    rows = db.execute(
        select(Alpha.feature_json, Alpha.family_key, AlphaMetric.long_count, AlphaMetric.short_count)
        .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
    ).all()

    referenced: dict[str, int] = {}
    simulated: dict[str, int] = {}
    empty: dict[str, int] = {}
    empty_families: dict[str, set] = {}

    for feat, family_key, longs, shorts in rows:
        for code in ((feat or {}).get("distinct_fields") or []):
            referenced[code] = referenced.get(code, 0) + 1
            if longs is None and shorts is None:
                continue
            simulated[code] = simulated.get(code, 0) + 1
            if (longs or 0) + (shorts or 0) == 0:
                empty[code] = empty.get(code, 0) + 1
                empty_families.setdefault(code, set()).add(family_key)

    return {
        code: FieldHealth(
            field_code=code,
            simulated=simulated.get(code, 0),
            empty=empty.get(code, 0),
            referenced=n_ref,
            families=len(empty_families.get(code, ())),
        )
        for code, n_ref in referenced.items()
    }


def dead_field_codes(db: Session) -> set[str]:
    """Fields that have been simulated enough times to judge and never held a position.

    Callers should exclude these before spending a simulation slot. Cheap enough to call
    per campaign batch; it is a single scan over alphas joined to their metrics.
    """
    dead = {code for code, h in field_health(db).items() if h.is_dead}
    if dead:
        log.info("dead_fields_excluded", count=len(dead), fields=sorted(dead))
    return dead
