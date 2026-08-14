"""Stage 6 — the daily report.

The product surface. Everything upstream exists to fill one page that answers:
what should I submit, and what should the machine try next?

Design constraint from STRATEGY.md: the operator's job is a single approve/reject
pass. So the shortlist leads, the evidence that justifies it sits directly
underneath, and the machinery's own health is a footnote. Anything that does not
change a decision is left out.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.results import AlphaMetric
from app.services.allocator import dataset_stats, suggest
from app.services.plateau import DECAY_LADDER, WINDOW_LADDER, evaluate, load_surface


@dataclass
class PortfolioLine:
    alpha_id: int
    expression: str
    settings: str
    sharpe: float | None
    fitness: float | None
    turnover: float | None


def _families(db: Session) -> list[str]:
    return [
        k
        for (k,) in db.execute(
            select(Alpha.family_key).where(Alpha.family_key.is_not(None)).group_by(Alpha.family_key)
        ).all()
    ]


def _surface_grid(db: Session, family_key: str, structure_index: int = 0) -> list[str]:
    """Render one (window x decay) surface as a text grid.

    Plateau vs spike is a *shape*, and a table of numbers shows a shape far
    faster than any single statistic does. This is the evidence the operator
    checks before promoting anything.
    """
    points = [p for p in load_surface(db, family_key) if p.sharpe is not None]
    if not points:
        return ["  (nothing simulated yet)"]

    structures = sorted({p.structure for p in points}, key=str)
    if structure_index >= len(structures):
        return ["  (no such structure)"]
    target = structures[structure_index]
    sel = {(p.window, p.decay): p for p in points if p.structure == target}
    if not sel:
        return ["  (no points for this structure)"]

    ts, cs, group, neut, trunc = target
    lines = [f"  structure: ts={ts} cs={cs} group={group} neutralization={neut}"]
    header = "  decay\\win " + "".join(f"{w:>8}" for w in WINDOW_LADDER)
    lines.append(header)
    for d in DECAY_LADDER:
        row = f"  {d:>9} "
        for w in WINDOW_LADDER:
            p = sel.get((w, d))
            row += f"{p.sharpe:>8.2f}" if p and p.sharpe is not None else f"{'·':>8}"
        lines.append(row)
    return lines


def build(db: Session, *, region: str = "USA", delay: int = 1, universe: str = "TOP3000") -> str:
    out: list[str] = []
    add = out.append

    total = db.scalar(select(func.count(Alpha.id))) or 0
    simulated = db.scalar(select(func.count(AlphaMetric.id))) or 0
    passing = (
        db.scalar(select(func.count(AlphaMetric.id)).where(AlphaMetric.passed_all_checks.is_(True)))
        or 0
    )

    add("# Alpha research — daily report")
    add("")
    add(f"**{total} alphas · {simulated} simulated · {passing} clearing every BRAIN check**")
    add("")

    # ---- 1. the shortlist: what to submit ----
    add("## Promotion shortlist")
    add("")
    promoted: list = []
    for family in _families(db):
        promoted.extend([v for v in evaluate(db, family) if v.promoted])
    promoted.sort(key=lambda v: v.sharpe or 0, reverse=True)

    if not promoted:
        add("Nothing survived the filter. That is the normal outcome for most batches —")
        add("passing BRAIN's checks is necessary but not sufficient; a result also has to sit")
        add("on a plateau and clear the multiple-testing bar. See the near-misses below.")
    else:
        add("| # | Sharpe | Fitness | neighbours | expression |")
        add("|---|---|---|---|---|")
        for i, v in enumerate(promoted[:15], 1):
            nb = (
                f"{v.neighbour_median_sharpe:.2f}" if v.neighbour_median_sharpe is not None else "—"
            )
            add(f"| {i} | {v.sharpe:.2f} | {v.fitness:.2f} | {nb} | `{v.expression}` |")
        add("")
        add("Review, correlation-check, and **submit manually**. Nothing here has been sent.")
    add("")

    # ---- 2. near-misses: what the filter rejected and why ----
    add("## Cleared BRAIN's checks but was NOT promoted")
    add("")
    near: list = []
    for family in _families(db):
        near.extend([v for v in evaluate(db, family) if v.clears_bar and not v.promoted])
    near.sort(key=lambda v: v.sharpe or 0, reverse=True)
    if not near:
        add("_none_")
    else:
        add("| Sharpe | reason held back | expression |")
        add("|---|---|---|")
        for v in near[:10]:
            add(f"| {v.sharpe:.2f} | {'; '.join(v.reasons)} | `{v.expression}` |")
    add("")

    # ---- 3. the surfaces: plateau evidence ----
    add("## Plateau surfaces (Sharpe by window x decay)")
    add("")
    add("A broad ridge is a mechanism; an isolated high cell is luck.")
    add("")
    for family in _families(db):
        pts = load_surface(db, family)
        if not any(p.sharpe is not None for p in pts):
            continue
        add(f"### `{family}`")
        add("```")
        out.extend(_surface_grid(db, family))
        add("```")
        add("")

    # ---- 4. where the edge lives ----
    add("## Dataset hit-rate and crowding")
    add("")
    add("| dataset | fields | avg users/field | tried | passed | hit-rate |")
    add("|---|---|---|---|---|---|")
    for s in sorted(
        dataset_stats(db, region=region, delay=delay, universe=universe),
        key=lambda s: (s.tried == 0, -(s.hit_rate or 0), s.avg_user_count),
    )[:12]:
        hr = f"{s.hit_rate:.1%}" if s.hit_rate is not None else "—"
        add(
            f"| `{s.dataset_code}` | {s.field_count} | {s.avg_user_count:,.0f} "
            f"| {s.tried} | {s.passed} | {hr} |"
        )
    add("")

    # ---- 5. what the machine will do next ----
    add("## Allocator — next families")
    add("")
    add("Diversity-capped: no dataset may take more than 20% of the batch, because")
    add("concentrating produces correlated alphas that BRAIN rejects.")
    add("")
    suggestions = suggest(db, region=region, delay=delay, universe=universe, n=6)
    if not suggestions:
        add("_no suggestions — is the field catalog loaded?_")
    else:
        add("| field | dataset | why |")
        add("|---|---|---|")
        for s in suggestions:
            add(f"| `{s.field_code}` | `{s.dataset_code}` | {s.reason} |")
        add("")
        first = suggestions[0]
        den = f" --denominator {first.denominator}" if first.denominator else ""
        add("```")
        add(f"python -m scripts.run_family --field {first.field_code}{den} --simulate 48")
        add("```")
    add("")
    add("---")
    add("*Simulation is automated. Submission is not — no alpha leaves this machine.*")
    return "\n".join(out)
