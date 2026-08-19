"""Answer the three open questions from the audit, using data already stored.

None of these need a human to know anything. All three were left open only because
nobody had queried for them.

Q1  What is the real LOW_SUB_UNIVERSE_SHARPE limit?
    docs/BRAIN_API.md records 0.01 from one TUTORIAL-account observation; an audit of
    stored check payloads saw 0.80. Every simulation result carries the limit BRAIN
    itself applied, so the distribution of observed limits settles it.

Q2  Does low turnover trade against sub-universe Sharpe?
    This matters because the "aim for turnover <= 0.125" idea is worthless if slow
    alphas systematically fail the sub-universe check. Both columns are stored.

Q3  Why do some fields simulate to an empty book?
    Compares fields that always produce a book against fields that never do, on the
    catalog metadata we hold, and prints what BRAIN's own checks said.

    python -m scripts.answer_open_questions

Read-only. Writes nothing, simulates nothing.
"""

from __future__ import annotations

import statistics as st
from collections import Counter

from sqlalchemy import select

from app.db.session import session_scope
from app.models.alphas import Alpha
from app.models.fields import DataField
from app.models.results import AlphaMetric

CHECK = "LOW_SUB_UNIVERSE_SHARPE"


def _checks(metric: AlphaMetric) -> list[dict]:
    return [c for c in (metric.checks or []) if isinstance(c, dict)]


def q1_subuniverse_limit(db) -> None:
    print("=" * 72)
    print("Q1  What limit does BRAIN actually apply for LOW_SUB_UNIVERSE_SHARPE?")
    print("=" * 72)

    limits: Counter = Counter()
    results: Counter = Counter()
    values: list[float] = []

    for (metric,) in db.execute(select(AlphaMetric)).all():
        for entry in _checks(metric):
            if entry.get("name") != CHECK:
                continue
            results[str(entry.get("result", "?")).upper()] += 1
            lim, val = entry.get("limit"), entry.get("value")
            if lim is not None:
                limits[float(lim)] += 1
            if isinstance(val, (int, float)):
                values.append(float(val))

    if not limits and not results:
        print(f"  NOT PRESENT — no stored check payload mentions {CHECK}.")
        print("  Nothing to conclude. The docs value stays unverified.\n")
        return

    print(f"  observed on {sum(results.values())} simulation results")
    for lim, n in sorted(limits.items()):
        print(f"    limit {lim:<6} seen {n:>5} times")
    for res, n in sorted(results.items()):
        print(f"    result {res:<6} {n:>5}")
    if values:
        print(f"  our sub-universe Sharpe: median {st.median(values):.3f}, "
              f"range {min(values):.3f}-{max(values):.3f}")

    if len(limits) == 1:
        only = next(iter(limits))
        print(f"\n  ANSWER: the limit is {only}. Update docs/BRAIN_API.md and stop treating")
        print("  it as contested.")
    elif len(limits) > 1:
        print("\n  The limit VARIES across results — it is not one constant.")
        _test_ratio_hypothesis(db)
    print()


def _test_ratio_hypothesis(db) -> None:
    """A varying limit is a clue, not a dead end: test whether it tracks our own Sharpe.

    Hypothesis: BRAIN does not apply a fixed sub-universe bar, it requires the
    sub-universe Sharpe to be some fraction of the alpha's *own* Sharpe. Two
    observations point that way — a family failing at limit 0.80 had Sharpe ~1.07
    (ratio 0.75), and the 0.01 recorded in docs/BRAIN_API.md would correspond to an
    alpha with near-zero Sharpe, which is exactly what a trivial tutorial alpha is.

    If it holds, the strategic consequence is large and counter-intuitive: raising
    Sharpe raises the bar with it, so this check cannot be beaten by a stronger signal.
    It is a robustness test, not a quality one.
    """
    pairs: list[tuple[float, float]] = []
    for (metric,) in db.execute(select(AlphaMetric)).all():
        if metric.sharpe is None:
            continue
        for entry in _checks(metric):
            if entry.get("name") != CHECK:
                continue
            lim = entry.get("limit")
            if isinstance(lim, (int, float)) and metric.sharpe not in (None, 0):
                pairs.append((float(metric.sharpe), float(lim)))

    if len(pairs) < 10:
        print(f"  CANNOT DETERMINE the rule — only {len(pairs)} (Sharpe, limit) pairs.")
        return

    ratios = [lim / sharpe for sharpe, lim in pairs if sharpe > 0.01]
    if not ratios:
        print("  CANNOT DETERMINE the rule — no positive-Sharpe rows to divide by.")
        return

    med = st.median(ratios)
    spread = st.pstdev(ratios) if len(ratios) > 1 else 0.0
    within = sum(1 for r in ratios if abs(r - med) <= 0.02) / len(ratios)

    print(f"  limit / own-Sharpe over {len(ratios)} rows: median {med:.3f}, sd {spread:.3f}")
    print(f"  {within:.0%} of rows sit within 0.02 of that median")

    if within >= 0.90:
        print(f"\n  ANSWER: the limit is NOT fixed — it is {med:.2f} x the alpha's own Sharpe.")
        print("  Consequence, and it is the important part: raising Sharpe raises this bar")
        print("  proportionally, so a stronger signal does NOT help you pass it. It is a")
        print("  robustness test — the alpha must hold up on the smaller universe too.")
        print("  Record it in docs/BRAIN_API.md as a ratio, not a constant.")
    else:
        print("\n  ANSWER: the limit varies but is NOT a clean multiple of our own Sharpe")
        print("  either. Something else drives it — do not model this check locally yet.")


def q2_turnover_vs_subuniverse(db) -> None:
    print("=" * 72)
    print("Q2  Does low turnover cost us sub-universe Sharpe?")
    print("=" * 72)

    rows = db.execute(
        select(AlphaMetric).where(
            AlphaMetric.turnover.is_not(None),
            AlphaMetric.subuniverse_sharpe.is_not(None),
        )
    ).scalars().all()

    pairs = [(m.turnover, m.subuniverse_sharpe) for m in rows]
    if len(pairs) < 10:
        print(f"  CANNOT DETERMINE — only {len(pairs)} rows have both turnover and")
        print("  subuniverse_sharpe stored. Need the sub-universe figure captured on more")
        print("  results before this question can be answered at all.\n")
        return

    slow = [s for t, s in pairs if t <= 0.125]
    fast = [s for t, s in pairs if t > 0.125]
    print(f"  {len(pairs)} rows have both figures")
    if slow:
        print(f"    turnover <= 0.125 : median sub-universe Sharpe {st.median(slow):.3f}  (n={len(slow)})")
    if fast:
        print(f"    turnover >  0.125 : median sub-universe Sharpe {st.median(fast):.3f}  (n={len(fast)})")

    if len(slow) >= 5 and len(fast) >= 5:
        diff = st.median(slow) - st.median(fast)
        if diff < -0.05:
            print("\n  ANSWER: YES — slow alphas score WORSE on the sub-universe check.")
            print("  The 'aim for turnover <= 0.125' idea is buying fitness at the cost of a")
            print("  check that has already sunk one family. Do not pursue it as stated.")
        elif diff > 0.05:
            print("\n  ANSWER: NO — slow alphas score BETTER here. The floor idea survives")
            print("  this objection and is worth a probe.")
        else:
            print("\n  ANSWER: no meaningful relationship. Turnover and the sub-universe check")
            print("  are independent, so the floor idea is not blocked by it.")
    else:
        print("\n  CANNOT DETERMINE — one side of the split has too few rows.")
    print()


def q3_dead_fields(db) -> None:
    print("=" * 72)
    print("Q3  Why do some fields simulate to an empty book?")
    print("=" * 72)

    rows = db.execute(
        select(Alpha.feature_json, AlphaMetric.long_count, AlphaMetric.short_count, AlphaMetric.checks)
        .join(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
    ).all()

    live: set[str] = set()
    dead_counts: Counter = Counter()
    sample_checks: dict[str, list] = {}

    for feat, longs, shorts, checks in rows:
        for code in ((feat or {}).get("distinct_fields") or []):
            if (longs or 0) + (shorts or 0) > 0:
                live.add(code)
            else:
                dead_counts[code] += 1
                sample_checks.setdefault(code, checks or [])

    dead = [c for c in dead_counts if c not in live]
    if not dead:
        print("  NOT PRESENT — every field that has been simulated produced a book at least once.\n")
        return

    print(f"  {len(dead)} field(s) simulated only ever produced an empty book:\n")
    for code in sorted(dead, key=lambda c: -dead_counts[c]):
        field = db.execute(select(DataField).where(DataField.field_code == code)).scalars().first()
        print(f"  {code}")
        print(f"    empty simulations: {dead_counts[code]}")
        if field is None:
            print("    catalog: NOT PRESENT in data_fields — the field does not exist for us.")
            print("    => cause is a missing field. Retire it and anything queued on it.")
        else:
            print(f"    catalog: exists — type={field.field_type}, coverage={field.coverage}, "
                  f"users={field.user_count}")
            print("    => the field exists, so an empty book means the expression evaluated to")
            print("       nothing every day. Most likely the data is absent in THIS")
            print("       region/delay/universe despite the catalog's coverage figure,")
            print("       which would make the stored coverage untrustworthy.")
        failed = [
            c.get("name") for c in sample_checks.get(code, [])
            if isinstance(c, dict) and str(c.get("result", "")).upper() == "FAIL"
        ]
        if failed:
            print(f"    BRAIN failed: {', '.join(str(f) for f in failed)}")
        print()

    print("  Compare against a live field to see whether coverage predicts this at all:")
    for code in sorted(live)[:3]:
        field = db.execute(select(DataField).where(DataField.field_code == code)).scalars().first()
        if field:
            print(f"    {code:44s} coverage={field.coverage}")
    print()


def main() -> int:
    with session_scope() as db:
        q1_subuniverse_limit(db)
        q2_turnover_vs_subuniverse(db)
        q3_dead_fields(db)
    print("All three answered from stored data. Nothing was simulated or written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
