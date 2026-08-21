"""Stage 6 — the daily report.

The product surface. Everything upstream exists to fill one page that answers:
what should I submit, and what should the machine try next?

Design constraint from STRATEGY.md: the operator's job is a single approve/reject
pass. So the shortlist leads, the evidence that justifies it sits directly
underneath, and the machinery's own health is a footnote. Anything that does not
change a decision is left out.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
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
    valid = db.scalar(select(func.count(Alpha.id)).where(Alpha.is_valid.is_(True))) or 0
    simulated = (
        db.scalar(
            select(func.count(func.distinct(Alpha.id))).join(
                AlphaMetric, Alpha.id == AlphaMetric.alpha_id
            )
        )
        or 0
    )
    passing = (
        db.scalar(
            select(func.count(func.distinct(Alpha.id)))
            .join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
            .where(AlphaMetric.passed_all_checks.is_(True))
        )
        or 0
    )
    submitted = (
        db.scalar(select(func.count(Alpha.id)).where(Alpha.status == AlphaStatus.SUBMITTED.value))
        or 0
    )
    submitted_ids = set(
        db.scalars(select(Alpha.id).where(Alpha.status == AlphaStatus.SUBMITTED.value)).all()
    )

    add("# Alpha research — daily report")
    add("")
    add(f"**{total} alphas · {simulated} simulated · {passing} clearing every BRAIN check**")
    add("")

    # ---- 0. Funnel Telemetry ----
    add("## Funnel Telemetry")
    add("")
    add("| Stage | Count | Conversion % |")
    add("|---|---|---|")
    add(f"| 1. Candidates Generated | {total} | 100.0% |")
    add(f"| 2. Valid AST Syntax | {valid} | {((valid / total * 100) if total else 0):.1f}% |")
    add(
        f"| 3. Simulated on BRAIN | {simulated} | {((simulated / total * 100) if total else 0):.1f}% |"
    )
    add(
        f"| 4. Passed BRAIN Checks | {passing} | {((passing / simulated * 100) if simulated else 0):.1f}% |"
    )

    all_promoted: list = []
    for family in _families(db):
        all_promoted.extend([v for v in evaluate(db, family) if v.promoted])
    promoted = [v for v in all_promoted if v.alpha_id not in submitted_ids]
    promoted.sort(key=lambda v: v.sharpe or 0, reverse=True)

    add(
        f"| 5. Promoted Shortlist | {len(promoted)} | {((len(promoted) / simulated * 100) if simulated else 0):.1f}% |"
    )
    add(f"| 6. Submitted Portfolio | {submitted} | — |")
    add("")

    # ---- 1. Per-Family Sequential Gating Telemetry ----
    add("## Per-Family Sequential Gating Breakdown")
    add("")
    add(
        "| Family | Mode | Simulated | 1. Checks | 2. Plateau | 3. Sub-Period | 4. DSR/Cold-Start | 5. Orthogonal | Promoted |"
    )
    add("|---|---|---|---|---|---|---|---|---|")
    for family in _families(db):
        f_verdicts = evaluate(db, family)
        if not f_verdicts:
            continue
        g_mode = f_verdicts[0].gate_mode
        sim_c = sum(1 for v in f_verdicts if v.sharpe is not None)
        # Calculate family and maximum slice trial counts
        surface = load_surface(db, family)
        by_slice: dict[tuple, list] = defaultdict(list)
        for p in surface:
            if p.sharpe is not None:
                by_slice[p.structure].append(p)
        max_slice = max((len(pts) for pts in by_slice.values()), default=0)
        sim_display = f"{sim_c} fam / {max_slice} slice" if sim_c != max_slice else f"{sim_c}"

        s1 = [v for v in f_verdicts if v.sharpe is not None and v.clears_bar]
        s2 = [v for v in s1 if v.is_plateau]
        s3 = [v for v in s2 if v.subperiod_passed is True]
        s4 = [v for v in s3 if v.dsr_passed is True]
        s5 = [v for v in s4 if not v.is_correlated]

        add(
            f"| `{family}` | {g_mode} | {sim_display} | {len(s1)} | {len(s2)} | {len(s3)} | {len(s4)} |"
            f" {len(s5)} | {len(s5)} |"
        )
    add("")

    # ---- 2. the shortlist: what to submit ----
    add("## Promotion shortlist")
    add("")

    if not promoted:
        add("Nothing survived the filter. That is the normal outcome for most batches —")
        add("passing BRAIN's checks is necessary but not sufficient; a result also has to sit")
        add("on a plateau and clear the multiple-testing bar. See the near-misses below.")
    else:
        add("| # | Sharpe | DSR | Fitness | neighbours | gate | expression |")
        add("|---|---|---|---|---|---|---|")
        for i, v in enumerate(promoted[:15], 1):
            nb = (
                f"{v.neighbour_median_sharpe:.2f}" if v.neighbour_median_sharpe is not None else "—"
            )
            dsr_str = f"{v.dsr:.2f}" if v.dsr is not None else "—"
            add(
                f"| {i} | {v.sharpe:.2f} | {dsr_str} | {v.fitness:.2f} | {nb} | {v.gate_mode} | `{v.expression}` |"
            )
        add("")
        add("Review, correlation-check, and **submit manually**. Nothing here has been sent.")
    add("")

    # ---- 3. near-misses: what the filter rejected and why ----
    add("## Cleared BRAIN's checks but was NOT promoted")
    add("")
    all_near: list = []
    for family in _families(db):
        all_near.extend([v for v in evaluate(db, family) if v.clears_bar and not v.promoted])
    near = [v for v in all_near if v.alpha_id not in submitted_ids]
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
    for s in dataset_stats(db, region=region, delay=delay, universe=universe):
        hr_str = f"{s.hit_rate * 100:.1f}%" if s.hit_rate is not None else "—"
        add(
            f"| `{s.dataset_code}` | {s.field_count} | {s.avg_user_count:.0f} | {s.tried} |"
            f" {s.passed} | {hr_str} |"
        )
    add("")

    # ---- 5. what to run next ----
    add("## What to try next")
    add("")
    sug_list = suggest(db, region=region, delay=delay, universe=universe)
    if not sug_list:
        add("Everything has been tried. Add a dataset or widen the search grid.")
    else:
        sug = sug_list[0]
        uc_str = f"~{sug.user_count} users" if sug.user_count is not None else ""
        add(
            f"Highest-priority unexploited field: **`{sug.field_code}`**"
            f" in dataset **`{sug.dataset_code}`**"
            f" ({uc_str}, {sug.reason})"
        )
        add("")
        add("```bash")
        add(f"python -m scripts.run --dataset {sug.dataset_code}")
        add("```")
    add("")

    # ---- 6. simulation budget & territory projections (Phase 1) ----
    add("## Simulation budget & territory projections (Phase 1)")
    add("")
    add("Standard 7x7 grid yields **49 alphas per territory** (vs 384 for wide grid).")
    add("")
    add(
        "| Daily Budget | Standard Grid (7x7=49) | Wide Grid (384) | 4-Month Territories (Standard) |"
    )
    add("|---|---|---|---|")
    for b in (50, 100, 200, 500):
        t_day_std = b / 49.0
        t_month_std = t_day_std * 30
        t_4mo_std = t_day_std * 120
        t_month_wide = (b / 384.0) * 30
        add(
            f"| {b} sims/day | {t_day_std:.1f} terr/day ({t_month_std:.0f}/mo) | {t_month_wide:.1f} terr/mo | **{t_4mo_std:.0f} territories** |"
        )
    add("")

    # ---- 7. active portfolio ----
    submitted_alphas = list(
        db.execute(
            select(Alpha, AlphaMetric)
            .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
            .where(Alpha.status == AlphaStatus.SUBMITTED.value)
            .order_by(Alpha.id.desc())
        ).all()
    )
    if submitted_alphas:
        add("## Currently submitted on BRAIN")
        add("")
        add("| # | Sharpe | Fitness | Turnover | settings | expression |")
        add("|---|---|---|---|---|---|")
        for a, m in submitted_alphas:
            sh = f"{m.sharpe:.2f}" if m and m.sharpe is not None else "—"
            ft = f"{m.fitness:.2f}" if m and m.fitness is not None else "—"
            to = f"{m.turnover * 100:.1f}%" if m and m.turnover is not None else "—"
            sett = (
                f"{a.region}/{a.universe}/d{a.delay}/{a.neutralization or 'NONE'}/n{a.decay or 0}"
            )
            add(f"| {a.id} | {sh} | {ft} | {to} | `{sett}` | `{a.expression}` |")
        add("")

    return "\n".join(out)
