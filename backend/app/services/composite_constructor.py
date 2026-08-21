"""Phase 1 — Multi-Factor & Composite Constructor.

Synthesizes multi-dataset composite alphas with economic synergy and conditional logic:
1. Multi-factor Z-Score blends (e.g. Fundamental Value + News Sentiment + Momentum)
2. Cross-field spreads and residualizations
3. Orthogonal group-neutralized composites
4. Conditional regime-triggers (trade_when, if_else)

All candidates are:
- Validated against ValidatorKB
- Checked for structural degeneracy and identity loops
- Bounded to Depth <= 8 and Node Count <= 24
- Emitted in complete window x decay surfaces for honest plateau analysis
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from dataclasses import field as dc_field

import structlog
from sqlalchemy.orm import Session

from app.models.enums import FieldType
from app.services.alpha_library import AlphaSettings
from app.services.constructor import (
    Candidate,
    GridAxes,
)
from app.services.proxy_calibration import is_degenerate_signal
from app.validator import ValidatorKB, validate
from app.validator.ast_nodes import Field, Node, Number, OperatorCall
from app.validator.features import _depth, _operator_count
from app.validator.validator import normalize

log = structlog.get_logger("composite_constructor")


@dataclass(frozen=True)
class CompositeSpec:
    """Specification for a multi-factor composite signal family."""

    primary_field: str
    secondary_field: str
    mechanism: str
    mode: str = "blend"  # "blend" | "spread" | "orthogonal" | "conditional"
    trigger_field: str | None = None
    group_field: str | None = "sector"
    primary_denominator: str | None = None
    secondary_denominator: str | None = None
    primary_backfill: int | None = None
    secondary_backfill: int | None = None
    weight: float = 0.5
    max_tree_depth: int = 6
    max_nodes: int = 20
    axes: GridAxes = dc_field(default_factory=GridAxes)

    def family_key(self, settings: AlphaSettings | None = None) -> str:
        s = settings or AlphaSettings()
        base = f"composite:{self.mode}:{self.primary_field}+{self.secondary_field}"
        if self.trigger_field:
            base = f"{base}?{self.trigger_field}"
        return f"{base}@{s.region}/{s.universe}/d{s.delay}"


def _build_field_node(field_code: str, denominator: str | None, backfill_days: int | None) -> Node:
    node: Node = Field(field_code)
    if backfill_days:
        node = OperatorCall("ts_backfill", [node, Number(float(backfill_days), True)])
    if denominator:
        node = OperatorCall("divide", [node, Field(denominator)])
    return node


def _build_ts_node(base_node: Node, op: str, window: int) -> Node:
    return OperatorCall(op, [base_node, Number(float(window), True)])


def _build_blend_ast(
    prim_ts: Node,
    sec_ts: Node,
    cs_op: str | None,
    group: str | None,
    weight: float = 0.5,
) -> Node:
    # rank(prim) + weight * rank(sec)
    p_rank = OperatorCall("rank", [prim_ts])
    s_rank = OperatorCall("rank", [sec_ts])

    weighted_sec = OperatorCall("multiply", [s_rank, Number(weight, False)])
    blended = OperatorCall("add", [p_rank, weighted_sec])

    if cs_op:
        if group:
            blended = OperatorCall(f"group_{cs_op}", [blended, Field(group)])
        else:
            blended = OperatorCall(cs_op, [blended])
    return blended


def _build_spread_ast(
    prim_ts: Node,
    sec_ts: Node,
    group: str | None,
) -> Node:
    # rank(prim) - rank(sec)
    p_rank = OperatorCall("rank", [prim_ts])
    s_rank = OperatorCall("rank", [sec_ts])
    spread = OperatorCall("subtract", [p_rank, s_rank])
    if group:
        spread = OperatorCall("group_neutralize", [spread, Field(group)])
    return spread


def _build_orthogonal_ast(
    prim_ts: Node,
    sec_ts: Node,
    group: str | None,
) -> Node:
    # group_neutralize(rank(prim) - rank(sec), group)
    p_norm = OperatorCall("zscore", [prim_ts])
    s_norm = OperatorCall("zscore", [sec_ts])
    diff = OperatorCall("subtract", [p_norm, s_norm])
    grp = group or "sector"
    return OperatorCall("group_neutralize", [diff, Field(grp)])


def _build_conditional_ast(
    prim_ts: Node,
    trigger_node: Node,
    trigger_window: int,
    group: str | None,
) -> Node:
    # trade_when(trigger > ts_mean(trigger, w), prim_ts, -1)
    mean_trig = OperatorCall("ts_mean", [trigger_node, Number(float(trigger_window), True)])
    cond = OperatorCall("greater", [trigger_node, mean_trig])
    raw_tw = OperatorCall("trade_when", [cond, prim_ts, Number(-1.0, True)])
    if group:
        return OperatorCall("group_neutralize", [raw_tw, Field(group)])
    return OperatorCall("rank", [raw_tw])


def expand_composite(
    db: Session,
    spec: CompositeSpec,
    *,
    base_settings: AlphaSettings | None = None,
    max_candidates: int = 400,
    arm: str | None = None,
    campaign_task_id: int | None = None,
) -> list[Candidate]:
    """Expands a composite multi-factor specification across structure x settings grids."""
    base_settings = base_settings or AlphaSettings()
    kb = ValidatorKB.from_session(
        db,
        region=base_settings.region,
        delay=base_settings.delay,
        universe=base_settings.universe,
    )
    family_key = spec.family_key(base_settings)
    axes = spec.axes

    prim_base = _build_field_node(
        spec.primary_field, spec.primary_denominator, spec.primary_backfill
    )
    sec_base = _build_field_node(
        spec.secondary_field, spec.secondary_denominator, spec.secondary_backfill
    )

    trigger_base = _build_field_node(spec.trigger_field, None, None) if spec.trigger_field else None

    candidates: list[Candidate] = []
    seen: set[str] = set()

    for ts_op in axes.ts_transforms:
        for group in axes.groups:
            if group and kb.field_type(group) != FieldType.GROUP.value:
                continue

            for cs_op in axes.cross_section:
                for neut in axes.neutralizations:
                    for trunc in axes.truncations:
                        surface: list[Candidate] = []
                        valid_surface = True

                        for window, decay in itertools.product(axes.windows, axes.decays):
                            prim_ts = _build_ts_node(prim_base, ts_op, window)
                            sec_ts = _build_ts_node(sec_base, ts_op, window)

                            if spec.mode == "blend":
                                root = _build_blend_ast(prim_ts, sec_ts, cs_op, group, spec.weight)
                            elif spec.mode == "spread":
                                root = _build_spread_ast(prim_ts, sec_ts, group)
                            elif spec.mode == "orthogonal":
                                root = _build_orthogonal_ast(prim_ts, sec_ts, group)
                            elif spec.mode == "conditional":
                                trig_node = (
                                    _build_ts_node(trigger_base, "ts_mean", window)
                                    if trigger_base
                                    else sec_ts
                                )
                                root = _build_conditional_ast(prim_ts, trig_node, window, group)
                            else:
                                root = _build_blend_ast(prim_ts, sec_ts, cs_op, group, spec.weight)

                            if _depth(root) > spec.max_tree_depth:
                                valid_surface = False
                                break
                            if _operator_count(root) > spec.max_nodes:
                                valid_surface = False
                                break

                            # Check structural degeneracy
                            deg = is_degenerate_signal(root, kb)
                            if deg.is_degenerate:
                                valid_surface = False
                                break

                            expression = normalize(root)
                            val_res = validate(expression, kb)
                            if not val_res.valid:
                                valid_surface = False
                                break

                            key = f"{expression}|{neut}|{decay}|{trunc}"
                            if key in seen:
                                continue
                            seen.add(key)

                            grid = {
                                "ts": ts_op,
                                "cs": cs_op,
                                "group": group,
                                "neutralization": neut,
                                "truncation": trunc,
                                "window": window,
                                "decay": decay,
                                "mode": spec.mode,
                                "secondary_field": spec.secondary_field,
                            }

                            surface.append(
                                Candidate(
                                    expression=expression,
                                    family_key=family_key,
                                    grid=grid,
                                    settings=AlphaSettings(
                                        region=base_settings.region,
                                        universe=base_settings.universe,
                                        delay=base_settings.delay,
                                        neutralization=neut,
                                        decay=decay,
                                        truncation=trunc,
                                    ),
                                    complexity_score=val_res.complexity_score,
                                    features=val_res.features,
                                    arm=arm,
                                    campaign_task_id=campaign_task_id,
                                )
                            )

                        surface_size = len(axes.windows) * len(axes.decays)
                        if valid_surface and len(surface) == surface_size:
                            candidates.extend(surface)

                        if len(candidates) >= max_candidates:
                            log.info(
                                "composite_candidates_capped",
                                count=len(candidates),
                                family=family_key,
                            )
                            return candidates[:max_candidates]

    log.info("composite_expansion_complete", candidates=len(candidates), family=family_key)
    return candidates[:max_candidates]
