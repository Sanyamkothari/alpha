"""Find and remove fabricated or empty-book alphas from the research database.

Two kinds of junk turned up when the 332 real simulated points were audited:

**Fabricated rows.** Three families (``close/cap:ts_zscore:rank``, ``close_dedup/cap``,
``debug_fam/cap``) hold 49 rows each whose metrics repeat identically across every point
and whose stored ``fitness`` does not satisfy BRAIN's own formula against their stored
Sharpe, returns and turnover. They are fixtures that reached the production database.
They matter because every statistical gate in this system reads the family as a
population: DSR takes the family's Sharpe spread, ``N_eff`` takes its correlation
matrix, CSCV takes its PnL matrix, and the haircut bar takes its size. 147 invented rows
are 147 phantom trials.

**Empty-book rows.** Two families produced ``long_count == short_count == 0`` on every
point — the field is unavailable for the configured region/delay/universe, so nothing
traded. Recorded as ``rejected``, they drag down every hit rate they appear in.

Detection is by evidence, not by name. A family is only flagged if its rows actually
exhibit the defect, so this cannot quietly delete real work whose family happens to be
called something suspicious.

    python -m scripts.purge_synthetic_alphas                 # report only (default)
    python -m scripts.purge_synthetic_alphas --apply         # back up, then delete
    python -m scripts.purge_synthetic_alphas --apply --kind empty-book

Nothing is deleted without ``--apply``, and ``--apply`` writes a JSON backup of every
affected row first. This project has already lost two weeks to a state-drift incident
(see CLAUDE.md); an irreversible bulk delete is not worth the convenience.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import USER_DATA_DIR
from app.db.session import session_scope
from app.models.alphas import Alpha
from app.models.results import AlphaMetric, SimulationImport

TURNOVER_FLOOR = 0.125
# Stored fitness may drift from the formula by rounding; anything past this is not rounding.
FITNESS_TOLERANCE = 0.10
# One or two identical rows is a coincidence; a whole family of them is a fixture.
MIN_ROWS_TO_JUDGE = 5


@dataclass
class FamilyVerdict:
    family_key: str
    rows: int
    kind: str  # "fabricated" | "empty-book" | "clean"
    evidence: str
    alpha_ids: list[int] = field(default_factory=list)


def _expected_fitness(sharpe: float, returns: float, turnover: float) -> float:
    return sharpe * math.sqrt(abs(returns) / max(turnover, TURNOVER_FLOOR))


def classify(db) -> list[FamilyVerdict]:
    rows = db.execute(
        select(Alpha, AlphaMetric)
        .join(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .where(Alpha.family_key.is_not(None))
    ).all()

    by_family: dict[str, list[tuple[Alpha, AlphaMetric]]] = {}
    for alpha, metric in rows:
        by_family.setdefault(alpha.family_key, []).append((alpha, metric))

    verdicts: list[FamilyVerdict] = []
    for family, members in sorted(by_family.items()):
        ids = [a.id for a, _ in members]
        n = len(members)

        counts = [
            (m.long_count or 0) + (m.short_count or 0)
            for _, m in members
            if m.long_count is not None or m.short_count is not None
        ]
        if counts and max(counts) == 0:
            verdicts.append(
                FamilyVerdict(family, n, "empty-book", f"all {n} points held 0 positions", ids)
            )
            continue

        if n < MIN_ROWS_TO_JUDGE:
            verdicts.append(FamilyVerdict(family, n, "clean", "too few rows to judge", ids))
            continue

        tuples = {
            (m.sharpe, m.fitness, m.turnover, m.returns)
            for _, m in members
            if m.sharpe is not None
        }
        mismatches = 0
        checked = 0
        for _, m in members:
            if None in (m.sharpe, m.fitness, m.turnover, m.returns):
                continue
            checked += 1
            if abs(_expected_fitness(m.sharpe, m.returns, m.turnover) - m.fitness) > FITNESS_TOLERANCE:
                mismatches += 1

        # Fabricated = the metrics barely vary AND they do not obey BRAIN's own formula.
        # Either alone is not enough: a flat surface can be real, and a formula mismatch
        # on one row can be a rounding artefact.
        few_distinct = len(tuples) <= max(2, n // 10)
        mostly_wrong = checked > 0 and mismatches / checked > 0.5
        if few_distinct and mostly_wrong:
            verdicts.append(
                FamilyVerdict(
                    family, n, "fabricated",
                    f"{len(tuples)} distinct metric tuples across {n} rows; "
                    f"{mismatches}/{checked} violate the fitness formula",
                    ids,
                )
            )
        else:
            verdicts.append(FamilyVerdict(family, n, "clean", f"{len(tuples)} distinct tuples", ids))

    return verdicts


def backup(verdicts: list[FamilyVerdict], db) -> Path:
    out_dir = USER_DATA_DIR / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"purge_{stamp}.json"

    payload = []
    for v in verdicts:
        for alpha in db.execute(select(Alpha).where(Alpha.id.in_(v.alpha_ids))).scalars():
            metrics = db.execute(
                select(AlphaMetric).where(AlphaMetric.alpha_id == alpha.id)
            ).scalars().all()
            payload.append(
                {
                    "family_key": alpha.family_key,
                    "kind": v.kind,
                    "id": alpha.id,
                    "expression": alpha.expression,
                    "status": alpha.status,
                    "source": alpha.source,
                    "settings": {
                        "region": alpha.region, "universe": alpha.universe,
                        "delay": alpha.delay, "neutralization": alpha.neutralization,
                        "decay": alpha.decay, "truncation": alpha.truncation,
                    },
                    "feature_json": alpha.feature_json,
                    "metrics": [
                        {
                            "sharpe": m.sharpe, "fitness": m.fitness, "turnover": m.turnover,
                            "returns": m.returns, "long_count": m.long_count,
                            "short_count": m.short_count, "passed_all_checks": m.passed_all_checks,
                        }
                        for m in metrics
                    ],
                }
            )

    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually delete (default: report only)")
    ap.add_argument(
        "--kind",
        choices=["fabricated", "empty-book", "both"],
        default="both",
        help="which class of junk to act on",
    )
    args = ap.parse_args()

    wanted = {"fabricated", "empty-book"} if args.kind == "both" else {args.kind}

    with session_scope() as db:
        verdicts = classify(db)
        flagged = [v for v in verdicts if v.kind in wanted]
        clean = [v for v in verdicts if v.kind == "clean"]

        print(f"{len(verdicts)} families examined — {len(clean)} clean, {len(flagged)} flagged\n")
        if not flagged:
            print("nothing to purge.")
            return 0

        total = 0
        for v in flagged:
            total += v.rows
            print(f"  [{v.kind:11s}] {v.family_key}")
            print(f"                {v.rows} rows — {v.evidence}")
        print(f"\n{total} alpha rows flagged across {len(flagged)} families.")

        if not args.apply:
            print("\nreport only — nothing written. Re-run with --apply to back up and delete.")
            return 0

        path = backup(flagged, db)
        print(f"\nbacked up {total} rows to {path}")

        ids = [aid for v in flagged for aid in v.alpha_ids]
        for model in (AlphaMetric, SimulationImport):
            for obj in db.execute(select(model).where(model.alpha_id.in_(ids))).scalars():
                db.delete(obj)
        for alpha in db.execute(select(Alpha).where(Alpha.id.in_(ids))).scalars():
            db.delete(alpha)

        print(f"deleted {len(ids)} alphas and their metrics.")
        print("restore with the JSON above if this was wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
