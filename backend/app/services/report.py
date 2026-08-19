"""Stage 6 — the daily report.

The product surface. Everything upstream exists to fill one page that answers:
what should I submit, and what should the machine try next?

Design constraints:
- Displays declared FilterConfig operating fingerprint in the header (A6).
- Ranks shortlist by ridge_score (shrunk/neighbourhood median) rather than noisy peak Sharpe (F6).
- Enforces greedy intra-batch orthogonality on morning shortlist (F7).
- Uses cached evaluate_families to avoid redundant evaluations (D3).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.fields import DataField
from app.models.results import AlphaMetric
from app.services.allocator import dataset_stats, suggest
from app.services.clustering import select_orthogonal_batch
from app.services.filter_config import DEFAULT_FILTER_CONFIG, FilterConfig
from app.services.plateau import (
    DECAY_LADDER,
    WINDOW_LADDER,
    evaluate_families,
    load_surface,
    surface_axes,
)
from app.services.pnl_storage import get_pnl_store


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
        if k is not None
    ]


def _surface_grid(db: Session, family_key: str, structure_index: int = 0) -> list[str]:
    """Render one (window x decay) surface as a text grid."""
    points = load_surface(db, family_key, include_unsimulated=True)
    if not any(p.sharpe is not None for p in points):
        return ["  (nothing simulated yet)"]

    structures = sorted({p.structure for p in points if p.sharpe is not None}, key=str)
    if structure_index >= len(structures):
        return ["  (no such structure)"]
    target = structures[structure_index]
    sel = {(p.window, p.decay): p for p in points if p.structure == target}
    if not sel:
        return ["  (no points for this structure)"]

    ts, cs, group, neut, trunc = target[:5]
    lines = [f"  structure: ts={ts} cs={cs} group={group} neutralization={neut}"]
    windows, decays = surface_axes(points)
    header = "  decay\\win " + "".join(f"{w:>8}" for w in windows)
    lines.append(header)
    for d in decays:
        row = f"  {d:>9} "
        for w in windows:
            p = sel.get((w, d))
            row += f"{p.sharpe:>8.2f}" if p and p.sharpe is not None else f"{'·':>8}"
        lines.append(row)
    return lines


def build(
    db: Session,
    *,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
) -> str:
    out: list[str] = []
    add = out.append

    total = db.scalar(select(func.count(Alpha.id))) or 0
    valid = db.scalar(select(func.count(Alpha.id)).where(Alpha.is_valid.is_(True))) or 0
    simulated = (
        db.scalar(
            select(func.count(func.distinct(Alpha.id))).join(AlphaMetric, Alpha.id == AlphaMetric.alpha_id)
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

    store = get_pnl_store()
    families = _families(db)
    # Decision D3: evaluate all families in a single cached pass
    family_verdict_map = evaluate_families(db, families, cfg=cfg, pnl_store=store)

    add("# Alpha research — daily report")
    add("")
    add(f"**{total} alphas · {simulated} simulated · {passing} clearing every BRAIN check**")
    add(f"Filter Configuration Fingerprint: `{cfg.fingerprint()}` (Declared Operating Bar)")
    add("")

    # ---- 0. Funnel Telemetry ----
    add("## Funnel Telemetry")
    add("")
    add("| Stage | Count | Conversion % |")
    add("|---|---|---|")
    add(f"| 1. Candidates Generated | {total} | 100.0% |")
    add(f"| 2. Valid AST Syntax | {valid} | {((valid / total * 100) if total else 0):.1f}% |")
    add(f"| 3. Simulated on BRAIN | {simulated} | {((simulated / total * 100) if total else 0):.1f}% |")
    add(f"| 4. Passed BRAIN Checks | {passing} | {((passing / simulated * 100) if simulated else 0):.1f}% |")

    all_promoted: list = []
    for f_verdicts in family_verdict_map.values():
        all_promoted.extend([v for v in f_verdicts if v.promoted])

    promoted_raw = [v for v in all_promoted if v.alpha_id not in submitted_ids]
    # F7: Select orthogonal batch across families
    accepted_shortlist, deferred_batch = select_orthogonal_batch(promoted_raw, store, cfg=cfg)

    add(f"| 5. Promoted Shortlist | {len(accepted_shortlist)} | {((len(accepted_shortlist) / simulated * 100) if simulated else 0):.1f}% |")
    add(f"| 6. Submitted Portfolio | {submitted} | — |")
    add("")

    # ---- 1. Per-Family Sequential Gating Telemetry ----
    add("## Per-Family Sequential Gating Breakdown")
    add("")
    add("| Family | Mode | Simulated | 1. Checks | 2. Plateau | 3. Sub-Period | 4. DSR/Cold-Start | 5. Orthogonal | 6. Representative | Promoted |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for family in families:
        f_verdicts = family_verdict_map.get(family, [])
        if not f_verdicts:
            continue
        g_mode = f_verdicts[0].gate_mode
        sim_c = sum(1 for v in f_verdicts if v.sharpe is not None)
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
        s6 = [v for v in s5 if v.promoted]

        add(
            f"| `{family}` | {g_mode} | {sim_display} | {len(s1)} | {len(s2)} | {len(s3)} |"
            f" {len(s4)} | {len(s5)} | {len(s6)} | {sum(1 for v in f_verdicts if v.promoted)} |"
        )
    add("")

    # ---- 2. the shortlist: what to submit ----
    add("## Promotion shortlist")
    add("")

    if not accepted_shortlist:
        add("Nothing survived the filter. That is the normal outcome for most batches —")
        add("passing BRAIN's checks is necessary but not sufficient; a result also has to sit")
        add("on a plateau and clear the multiple-testing bar. See the near-misses below.")
    else:
        add("| # | Ridge Score | Sharpe | DSR | Fitness | neighbours | gate | expression |")
        add("|---|---|---|---|---|---|---|---|")
        for i, v in enumerate(accepted_shortlist[:15], 1):
            r_score = f"{v.ridge_score:.2f}" if v.ridge_score is not None else (f"{v.sharpe:.2f}" if v.sharpe else "—")
            nb = f"{v.neighbour_median_sharpe:.2f}" if v.neighbour_median_sharpe is not None else "—"
            dsr_str = f"{v.dsr:.2f}" if v.dsr is not None else "—"
            add(f"| {i} | {r_score} | {v.sharpe:.2f} | {dsr_str} | {v.fitness:.2f} | {nb} | {v.gate_mode} | `{v.expression}` |")
        add("")
        add("Review, correlation-check, and **submit manually**. Nothing here has been sent.")
    add("")

    if deferred_batch:
        add("### Held back — mutually correlated with a higher-ranked alpha in today's batch")
        add("")
        add("| Ridge Score | Sharpe | expression |")
        add("|---|---|---|")
        for v in deferred_batch[:5]:
            r_score = f"{v.ridge_score:.2f}" if v.ridge_score is not None else "—"
            add(f"| {r_score} | {v.sharpe:.2f} | `{v.expression}` |")
        add("")

    # ---- 3. near-misses: what the filter rejected and why ----
    add("## Cleared BRAIN's checks but was NOT promoted")
    add("")
    all_near: list = []
    for f_verdicts in family_verdict_map.values():
        all_near.extend([v for v in f_verdicts if v.clears_bar and not v.promoted])
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

    # ---- 4. Currently submitted on BRAIN ----
    add("## Currently submitted on BRAIN")
    add("")
    submitted_alphas = db.scalars(
        select(Alpha).where(Alpha.status == AlphaStatus.SUBMITTED.value)
    ).all()
    if not submitted_alphas:
        add("_none_")
    else:
        add("| # | Alpha ID | Expression | Settings |")
        add("|---|---|---|---|")
        for i, a in enumerate(submitted_alphas, 1):
            settings_str = f"{a.region}/{a.universe}/d{a.delay}"
            add(f"| {i} | {a.id} | `{a.expression}` | {settings_str} |")
    add("")

    # ---- 5. what to try next: allocator suggestions ----
    add("## What to try next (allocator suggestions)")
    add("")
    suggestions = suggest(db, region=region, delay=delay, universe=universe, n=5)
    if not suggestions:
        has_fields = bool(db.scalar(select(func.count(DataField.id))))
        if not has_fields:
            add(
                "No field catalog loaded. Run `python -m app.seeds.seed_all` for the "
                "offline sample catalog, or `python -m scripts.fetch_brain_catalog` "
                "for your account's live catalog."
            )
        else:
            add("Everything has been tried. Add a dataset or widen the search grid.")
    else:
        add("| Dataset | Field | Operator | Horizon | Reason |")
        add("|---|---|---|---|---|")
        for s in suggestions:
            h_str = s.horizon_band or "medium"
            add(f"| `{s.dataset_code}` | `{s.field_code}` | `{s.operator_family}` | `{h_str}` | {s.reason} |")
    add("")

    # ---- 5. dataset leaderboard ----
    add("## Dataset crowding & hit-rate leaderboard")
    add("")
    dstats = dataset_stats(db, region=region, delay=delay, universe=universe)
    if not dstats:
        add("_No dataset statistics available._")
    else:
        add("| Dataset | Fields | Avg Users | Crowding | Simulated | Passed | Hit-rate |")
        add("|---|---|---|---|---|---|---|")
        for st in dstats[:10]:
            hr_str = f"{st.hit_rate:.1%}" if st.hit_rate is not None else "—"
            add(
                f"| `{st.dataset_code}` | {st.field_count} | {st.avg_user_count:.0f} |"
                f" {st.crowding_score:.2f} | {st.tried} | {st.passed} | {hr_str} |"
            )
    add("")

    return "\n".join(out)
