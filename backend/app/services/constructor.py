"""Stage 3 — the family constructor (STRATEGY.md Rule 2).

One economic mechanism expanded across the structure and settings grid. The LLM
never sees this code: it picks the *field* and says why the field should predict
returns; everything from there is deterministic AST assembly, so every emitted
expression is valid by construction and hallucination is impossible.

Stratified Sampling:
Guarantees every operator family (ts_transform) and depth layer receives equal
priority and coverage across the budget before repeating structural variants for
the same operator.

Two design choices worth stating, both driven by Rule 5:

**The grid is dense where plateau analysis reads it.** Judging a candidate by its
neighbours only works if the neighbours exist. ``window`` and ``decay`` are
therefore swept exhaustively, while the structural axes are sampled — a sparse
window axis would make every point look like an isolated spike.

**Settings are part of the family, not a wrapper around it.** Neutralization,
decay and truncation move Sharpe by 0.3–0.6 on an unchanged expression, which is
the single biggest reason the first 51 alphas failed: each idea was sampled at
exactly one settings point. They are grid axes here, and they ride on the alpha
row so the simulation runner reproduces them exactly.
"""

from __future__ import annotations

from collections import defaultdict
import itertools
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Callable

import structlog
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, FieldType
from app.services.alpha_library import AlphaSettings
from app.validator import ValidatorKB, validate
from app.validator.ast_nodes import Field, Node, Number, OperatorCall
from app.validator.validator import normalize

log = structlog.get_logger("constructor")

# Standard 7x7 grid (default 49 alphas per territory)
STANDARD_WINDOWS: tuple[int, ...] = (5, 10, 20, 40, 60, 120, 250)
STANDARD_DECAYS: tuple[int, ...] = (0, 1, 2, 4, 6, 8, 16)

# Wide grid (opt-in via --grid wide)
WIDE_WINDOWS: tuple[int, ...] = (5, 10, 22, 63, 126, 252)
WIDE_DECAYS: tuple[int, ...] = (0, 4, 8, 16)

# Coarse 3x3 screening grid (27 sims per 3-level sweep)
COARSE_WINDOWS: tuple[int, ...] = (5, 20, 60)
COARSE_DECAYS: tuple[int, ...] = (0, 4, 8)

DEFAULT_WINDOWS: tuple[int, ...] = STANDARD_WINDOWS
DEFAULT_DECAYS: tuple[int, ...] = STANDARD_DECAYS

# Diverse operator families from KB
DEFAULT_TS_TRANSFORMS: tuple[str, ...] = (
    "ts_zscore",
    "ts_rank",
    "ts_delta",
    "ts_mean",
    "ts_decay_linear",
    "ts_std_dev",
    "ts_quantile",
)

# Depth-2: the outer operator wraps an inner time-series result.
DEFAULT_DEPTH2_PAIRS: tuple[tuple[str, str], ...] = (
    ("ts_delta", "ts_rank"),      # acceleration of rank — regime change
    ("ts_zscore", "ts_delta"),    # z-score of momentum — mean-reversion of changes
    ("ts_rank", "ts_mean"),       # rank of smoothed signal — robust trend
    ("ts_delta", "ts_zscore"),    # change in standardised level — breakout
)

# Inner windows for depth-2 — always shorter to avoid inner >= outer redundancy.
DEFAULT_INNER_WINDOWS: tuple[int, ...] = (5, 10, 20)

# Cross-sectional normalization. None = leave the raw time-series signal.
DEFAULT_CROSS_SECTION: tuple[str | None, ...] = ("rank", "zscore", "normalize", None)

# Group-relative variants; None = ungrouped.
DEFAULT_GROUPS: tuple[str | None, ...] = (None, "sector", "industry", "subindustry")

DEFAULT_VECTOR_REDUCERS: tuple[str, ...] = ("vec_avg", "vec_sum", "vec_count", "vec_max", "vec_min")

DEFAULT_NEUTRALIZATIONS: tuple[str, ...] = ("SUBINDUSTRY", "INDUSTRY", "SECTOR", "MARKET", "NONE")

# Reference truncation FIRST: with settings_per_structure=1 the constructor
# emits settings_combinations[0], so index 0 defines the baseline every
# historical result was produced at. Probe levels follow it.
DEFAULT_TRUNCATIONS: tuple[float, ...] = (0.08, 0.04, 0.01)

DEFAULT_HUMPS: tuple[float | None, ...] = (None,)

# Universes to sweep
DEFAULT_UNIVERSES: tuple[str, ...] = ("TOP3000",)

# Map field update frequency -> backfill days.
FREQUENCY_BACKFILL: dict[str, int | None] = {
    "daily": None,       # no backfill — data arrives every day
    "weekly": 10,
    "monthly": 30,
    "quarterly": 120,    # the original hardcoded default
    "annual": 252,
    "unknown": 120,      # conservative fallback
}


@dataclass(frozen=True)
class BudgetPolicy:
    max_surfaces: int = 8
    settings_per_structure: int = 1  # >1 only for confirmed structure sweeps


@dataclass(frozen=True)
class GridAxes:
    ts_transforms: tuple[str, ...] = DEFAULT_TS_TRANSFORMS
    depth2_pairs: tuple[tuple[str, str], ...] = DEFAULT_DEPTH2_PAIRS
    windows: tuple[int, ...] = DEFAULT_WINDOWS
    inner_windows: tuple[int, ...] = DEFAULT_INNER_WINDOWS
    cross_section: tuple[str | None, ...] = DEFAULT_CROSS_SECTION
    groups: tuple[str | None, ...] = DEFAULT_GROUPS
    neutralizations: tuple[str, ...] = DEFAULT_NEUTRALIZATIONS
    decays: tuple[int, ...] = DEFAULT_DECAYS
    truncations: tuple[float, ...] = DEFAULT_TRUNCATIONS
    universes: tuple[str, ...] = DEFAULT_UNIVERSES
    humps: tuple[float | None, ...] = DEFAULT_HUMPS
    vector_reducers: tuple[str, ...] = DEFAULT_VECTOR_REDUCERS


def derive_horizon_band(window: int | None) -> str | None:
    """Map lookback window to canonical horizon band (short: 1–10d, medium: 11–63d, long: 64d+)."""
    if window is None or window <= 0:
        return None
    if window <= 10:
        return "short"
    if window <= 63:
        return "medium"
    return "long"


@dataclass(frozen=True)
class TerritorySignature:
    field_code: str
    operator_family: str
    horizon_band: str | None
    region: str
    universe: str
    delay: int


def canonical_territory_key(
    field_code: str,
    operator_family: str,
    horizon_band: str | None,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
) -> str:
    """Generate canonical territory identifier: field:op:horizon@region/universe/d<delay>."""
    h_str = horizon_band or "all"
    return f"{field_code}:{operator_family}:{h_str}@{region}/{universe}/d{delay}"


def parse_territory_signature(
    key: str,
    default_region: str = "USA",
    default_universe: str = "TOP3000",
    default_delay: int = 1,
) -> TerritorySignature:
    """Parse canonical or legacy territory key into structured signature."""
    prefix, _, settings = key.partition("@")
    reg, univ, del_str = default_region, default_universe, f"d{default_delay}"
    if settings:
        parts = settings.split("/")
        if len(parts) >= 1 and parts[0]:
            reg = parts[0]
        if len(parts) >= 2 and parts[1]:
            univ = parts[1]
        if len(parts) >= 3 and parts[2].startswith("d"):
            del_str = parts[2]
    try:
        delay = int(del_str.lstrip("d"))
    except ValueError:
        delay = default_delay

    prefix_no_sec, _, _ = prefix.partition("+")
    tokens = prefix_no_sec.split(":")
    field_part = tokens[0]
    base_field = field_part.split("/")[0]

    op = "ts_zscore"
    horizon: str | None = None
    if len(tokens) >= 2 and tokens[1]:
        op = tokens[1]
    if len(tokens) >= 3 and tokens[2]:
        h_cand = tokens[2].lower()
        if h_cand in ("short", "medium", "long"):
            horizon = h_cand

    return TerritorySignature(
        field_code=base_field,
        operator_family=op,
        horizon_band=horizon,
        region=reg,
        universe=univ,
        delay=delay,
    )


@dataclass
class FamilySpec:
    """Inputs to one family expansion."""

    field_code: str
    mechanism: str = ""
    denominator: str | None = None
    frequency: str | None = None
    backfill_days: int | None = None
    secondary_field: str | None = None
    operator_family: str | None = None
    wrapper_shape: str | None = None
    horizon_band: str | None = None
    vector_reducer: str | None = None
    is_event_field: bool = False
    grid_mode: str = "standard"
    axes: GridAxes = dc_field(default_factory=GridAxes)

    @property
    def effective_backfill(self) -> int | None:
        if self.frequency:
            return FREQUENCY_BACKFILL.get(self.frequency.lower(), self.backfill_days)
        return self.backfill_days

    def family_key(self, base_settings: AlphaSettings | None = None) -> str:
        s = base_settings or AlphaSettings()
        denom = f"/{self.denominator}" if self.denominator else ""
        sec = f"+{self.secondary_field}" if self.secondary_field else ""
        op = f":{self.operator_family}" if self.operator_family else ""
        wrap = f":{self.wrapper_shape}" if self.wrapper_shape else ""
        hz = f":{self.horizon_band}" if self.horizon_band else ""
        vec = f":{self.vector_reducer}" if self.vector_reducer else ""
        return f"{self.field_code}{denom}{sec}{op}{wrap}{hz}{vec}@{s.region}/{s.universe}/d{s.delay}"


@dataclass
class Candidate:
    """One emitted alpha expression ready for insertion."""

    expression: str
    family_key: str
    grid: dict
    settings: AlphaSettings
    complexity_score: float | None = None
    features: dict | None = None
    arm: str | None = None
    campaign_task_id: int | None = None


@dataclass
class SurfaceConfig:
    layer: int
    ts_sig: str
    grid_extra: dict
    builder_fn: Callable[[int], Node]
    # Stratum identity for budget allocation. ``base_sig`` deliberately excludes
    # turnover-control wrappers so that hump variants compete *within* a
    # structure's stratum rather than against other structures for the budget.
    # ``variant_rank`` 0 marks the control arm (no turnover control), which is
    # always drawn before any variant of the same structure.
    base_sig: str = ""
    variant_rank: int = 0

    @property
    def stratum(self) -> tuple[int, str]:
        return (self.layer, self.base_sig or self.ts_sig)


def _base_node(spec: FamilySpec, vector_reducer: str | None = None) -> Node:
    base: Node = Field(name=spec.field_code)
    reducer = vector_reducer or spec.vector_reducer
    if reducer:
        base = OperatorCall(reducer, [base])
    if spec.effective_backfill:
        base = OperatorCall(
            "ts_backfill",
            [base, Number(float(spec.effective_backfill), True)],
        )
    if spec.denominator:
        denom: Node = Field(name=spec.denominator)
        if spec.effective_backfill:
            denom = OperatorCall(
                "ts_backfill",
                [denom, Number(float(spec.effective_backfill), True)],
            )
        base = OperatorCall("divide", [base, denom])
    return base


def _wrap_hump(node: Node, hump: float | None) -> Node:
    if hump is not None and hump > 0:
        from app.validator.ast_nodes import KeywordArg
        return OperatorCall("hump", [node], [KeywordArg("hump", Number(float(hump), False))])
    return node


def _wrap_cross_section(node: Node, cs_op: str | None, group: str | None) -> Node:
    if group is not None:
        group_node = Field(name=group)
        if cs_op == "rank":
            return OperatorCall("group_rank", [node, group_node])
        if cs_op == "zscore":
            return OperatorCall("group_zscore", [node, group_node])
        if cs_op == "normalize":
            return OperatorCall("group_normalize", [node, group_node])
        return OperatorCall("group_rank", [node, group_node])
    if cs_op is not None:
        return OperatorCall(cs_op, [node])
    return node


def _group_fields(db: Session, kb: ValidatorKB, candidates: Iterable[str | None]) -> list[str | None]:
    out: list[str | None] = []
    for g in candidates:
        if g is None:
            out.append(None)
        elif kb.field_type(g) == FieldType.GROUP.value:
            out.append(g)
    return out


def _emit_surface(
    spec: FamilySpec,
    kb: ValidatorKB,
    family_key: str,
    base_settings: AlphaSettings,
    axes: GridAxes,
    node_builder: Callable[[int], Node],
    seen: set[str],
    grid_extra: dict,
    arm: str | None = None,
    campaign_task_id: int | None = None,
) -> tuple[list[Candidate], int]:
    """Build a complete (window x decay) surface for one structural config."""
    surface: list[Candidate] = []
    rejected = 0
    for window, decay in itertools.product(axes.windows, axes.decays):
        node = node_builder(window)
        expression = normalize(node)

        result = validate(expression, kb)
        if not result.valid:
            rejected += 1
            continue

        neutralization = grid_extra["neutralization"]
        truncation = grid_extra["truncation"]
        key = f"{expression}|{neutralization}|{decay}|{truncation}"
        if key in seen:
            continue
        seen.add(key)

        grid = dict(grid_extra, window=window, decay=decay)
        surface.append(
            Candidate(
                expression=expression,
                family_key=family_key,
                grid=grid,
                settings=AlphaSettings(
                    region=base_settings.region,
                    universe=grid_extra.get("universe", base_settings.universe),
                    delay=base_settings.delay,
                    neutralization=neutralization,
                    decay=decay,
                    truncation=truncation,
                ),
                complexity_score=result.complexity_score,
                features=result.features,
                arm=arm,
                campaign_task_id=campaign_task_id,
            )
        )

    surface_size = len(axes.windows) * len(axes.decays)
    if len(surface) == surface_size:
        return surface, rejected
    log.warning(
        "incomplete_surface_discarded",
        family=family_key,
        emitted=len(surface),
        expected=surface_size,
        rejected=rejected,
        grid=grid_extra,
    )
    return [], rejected


def _hump_variant_rank(hump: float | None, humps: Sequence[float | None]) -> int:
    """Stratum draw order: 0 is the control arm, 1+ are turnover-control variants.

    The control arm must be drawn first, otherwise a hump sweep can spend its
    whole budget on variants and leave nothing to measure them against.
    """
    if hump is None:
        return 0
    variants = [h for h in humps if h is not None]
    try:
        return variants.index(hump) + 1
    except ValueError:
        return len(variants) + 1


def select_surface_configs(
    configs: list[SurfaceConfig],
    budget_surfaces: int,
    *,
    rng: random.Random | None = None,
) -> list[SurfaceConfig]:
    """Round-robin across strata, control arm first within each stratum.

    Stratum key is ``(layer, base_sig)`` — the *structure* identity, excluding
    turnover-control wrappers. Two properties this buys, both of which the
    earlier ``(layer, ts_sig)`` key silently lost:

    **Widening a structure-variant axis cannot cost structural coverage.** With
    ``ts_sig`` as the key, ``humps=(None, 0.01, 0.05)`` turned 7 strata into 21
    and — because ``hump(...)`` sorts before ``ts_*`` — the budget went entirely
    to hump variants of four transforms. Variants now queue inside their own
    structure's stratum instead of competing with other structures.

    **The control arm survives.** Within a stratum, ``variant_rank`` 0 (no
    turnover control) is drawn first, so a hump sweep always retains the
    un-humped point to measure the variants against.

    Strata are visited in a seeded-shuffled order rather than sorted order:
    when strata outnumber the budget, alphabetical order biases selection
    toward whichever dimension happens to sort first.
    """
    if budget_surfaces <= 0 or not configs:
        return []

    r = rng or random.Random(42)
    by_stratum: dict[tuple[int, str], list[SurfaceConfig]] = defaultdict(list)
    for c in configs:
        by_stratum[c.stratum].append(c)

    for s_list in by_stratum.values():
        r.shuffle(s_list)
        # Control arm first, then variants; the shuffle above still randomises
        # order *within* each rank.
        s_list.sort(key=lambda c: c.variant_rank)

    strata_keys = sorted(by_stratum.keys())
    r.shuffle(strata_keys)
    selected: list[SurfaceConfig] = []
    strata_queues = {k: list(by_stratum[k]) for k in strata_keys}

    while len(selected) < budget_surfaces and any(strata_queues.values()):
        for k in strata_keys:
            if len(selected) >= budget_surfaces:
                break
            if strata_queues[k]:
                selected.append(strata_queues[k].pop(0))

    return selected


def expand(
    db: Session,
    spec: FamilySpec,
    *,
    base_settings: AlphaSettings | None = None,
    max_candidates: int = 400,
    policy: BudgetPolicy | None = None,
    rank_by_novelty: bool = True,
    arm: str | None = None,
    campaign_task_id: int | None = None,
    rng: random.Random | None = None,
) -> list[Candidate]:
    """Enumerate the family using stratified sampling across operators and depths."""
    base_settings = base_settings or AlphaSettings()
    kb = ValidatorKB.from_session(
        db,
        region=base_settings.region,
        delay=base_settings.delay,
        universe=base_settings.universe,
    )
    axes = spec.axes

    # Resolve grid ladders based on grid_mode if not explicitly customized
    if spec.grid_mode == "wide":
        windows = WIDE_WINDOWS if axes.windows == STANDARD_WINDOWS else axes.windows
        decays = WIDE_DECAYS if axes.decays == STANDARD_DECAYS else axes.decays
        axes = GridAxes(
            ts_transforms=axes.ts_transforms,
            depth2_pairs=axes.depth2_pairs,
            windows=windows,
            inner_windows=axes.inner_windows,
            cross_section=axes.cross_section,
            groups=axes.groups,
            neutralizations=axes.neutralizations,
            decays=decays,
            truncations=axes.truncations,
            universes=axes.universes,
        )

    # Filter windows by horizon band if specified
    if spec.horizon_band:
        band = spec.horizon_band.lower()
        band_windows = tuple(w for w in axes.windows if derive_horizon_band(w) == band)
        if band_windows:
            axes = GridAxes(
                ts_transforms=axes.ts_transforms,
                depth2_pairs=axes.depth2_pairs,
                windows=band_windows,
                inner_windows=axes.inner_windows,
                cross_section=axes.cross_section,
                groups=axes.groups,
                neutralizations=axes.neutralizations,
                decays=axes.decays,
                truncations=axes.truncations,
                universes=axes.universes,
            )

    # Filter transforms and cross_sections if explicitly set on FamilySpec
    ts_transforms = (
        (spec.operator_family,)
        if spec.operator_family
        else axes.ts_transforms
    )
    cross_sections = (
        (spec.wrapper_shape,)
        if spec.wrapper_shape
        else axes.cross_section
    )

    groups = _group_fields(db, kb, axes.groups)
    family_key = spec.family_key(base_settings)
    seen: set[str] = set()

    submitted_slices = set(
        (a.family_key, a.neutralization, a.truncation)
        for a in db.query(Alpha).filter_by(status=AlphaStatus.SUBMITTED.value).all()
        if a.family_key
    )

    surface_size = len(axes.windows) * len(axes.decays)
    if surface_size <= 0:
        return []

    budget_surfaces = max(1, max_candidates // surface_size)
    policy = policy or BudgetPolicy(max_surfaces=budget_surfaces, settings_per_structure=1)
    # Honour an explicitly supplied policy. This was previously constructed and then
    # ignored — select_surface_configs took the candidate-derived budget, so a caller
    # passing BudgetPolicy(max_surfaces=3) silently got something else. The candidate
    # cap still binds: a policy may narrow the budget, never widen it past what
    # max_candidates pays for.
    budget_surfaces = max(1, min(budget_surfaces, policy.max_surfaces))

    settings_combinations = list(
        itertools.product(axes.neutralizations, axes.truncations, axes.universes)
    )
    num_settings = min(len(settings_combinations), max(1, policy.settings_per_structure))

    is_vector = (kb.field_type(spec.field_code) == "VECTOR") or (spec.vector_reducer is not None)
    vec_reducers: list[str | None] = (
        [spec.vector_reducer] if spec.vector_reducer else list(axes.vector_reducers)
    ) if is_vector else [None]

    all_surface_configs: list[SurfaceConfig] = []

    # F11: when num_settings == 1 a `continue` here emitted nothing at all for the
    # whole family — every structure lost, because one settings tuple was already
    # submitted. Skip forward to the next unused settings tuple instead, and only
    # give up if every one of them is exhausted.
    usable_settings = [
        combo
        for combo in settings_combinations
        if (family_key, combo[0], combo[1]) not in submitted_slices
    ]
    if not usable_settings:
        log.warning(
            "family_settings_exhausted",
            family=family_key,
            combinations=len(settings_combinations),
        )
        return []
    num_settings = min(len(usable_settings), max(1, policy.settings_per_structure))

    for settings_idx in range(num_settings):
        neutralization, truncation, universe = usable_settings[settings_idx]

        for vec_reducer in vec_reducers:
            # ------------------------------------------------------------------
            # Layer 1: Depth-1 templates (the single-operator grid)
            # ------------------------------------------------------------------
            for ts_op, cs_op, group, hump in itertools.product(ts_transforms, cross_sections, groups, axes.humps):
                def make_depth1_builder(_ts=ts_op, _cs=cs_op, _grp=group, _hump=hump, _vec=vec_reducer):
                    def _builder(window: int) -> Node:
                        node = OperatorCall(_ts, [_base_node(spec, vector_reducer=_vec), Number(float(window), True)])
                        wrapped = _wrap_cross_section(node, _cs, _grp)
                        return _wrap_hump(wrapped, _hump)
                    return _builder

                base_ts_sig = f"{vec_reducer}:{ts_op}" if vec_reducer else ts_op
                ts_sig = f"hump({base_ts_sig},{hump})" if (hump is not None and hump > 0) else base_ts_sig
                grid_extra = {
                    "ts": ts_sig, "cs": cs_op, "group": group, "depth": 1,
                    "neutralization": neutralization, "truncation": truncation,
                    "universe": universe,
                    "hump": hump,
                }
                if vec_reducer:
                    grid_extra["vec_reducer"] = vec_reducer
                all_surface_configs.append(
                    SurfaceConfig(
                        layer=1,
                        ts_sig=ts_sig,
                        grid_extra=grid_extra,
                        builder_fn=make_depth1_builder(),
                        base_sig=base_ts_sig,
                        variant_rank=_hump_variant_rank(hump, axes.humps),
                    )
                )

                # Event-time template variant if requested
                if spec.is_event_field:
                    def make_event_builder(_ts=ts_op, _cs=cs_op, _grp=group, _hump=hump, _vec=vec_reducer):
                        def _builder(window: int) -> Node:
                            evt = OperatorCall("days_from_last_change", [_base_node(spec, vector_reducer=_vec)])
                            node = OperatorCall(_ts, [evt, Number(float(window), True)])
                            wrapped = _wrap_cross_section(node, _cs, _grp)
                            return _wrap_hump(wrapped, _hump)
                        return _builder

                    evt_ts_sig = f"days_from_last_change:{base_ts_sig}"
                    grid_extra_evt = dict(grid_extra, ts=evt_ts_sig, event_time=True)
                    all_surface_configs.append(
                        SurfaceConfig(
                            layer=1,
                            ts_sig=evt_ts_sig,
                            grid_extra=grid_extra_evt,
                            builder_fn=make_event_builder(),
                            base_sig=evt_ts_sig,
                            variant_rank=_hump_variant_rank(hump, axes.humps),
                        )
                    )

            # ------------------------------------------------------------------
            # Layer 2: Depth-2 templates (nested operator pairs)
            # ------------------------------------------------------------------
            if not spec.operator_family:
                for (outer_op, inner_op), cs_op, group, hump in itertools.product(
                    axes.depth2_pairs, cross_sections, groups, axes.humps
                ):
                    def make_depth2_builder(_outer=outer_op, _inner=inner_op, _cs=cs_op, _grp=group, _hump=hump, _vec=vec_reducer):
                        def _builder(window: int) -> Node:
                            inner_w = None
                            for iw in sorted(axes.inner_windows, reverse=True):
                                if iw < window:
                                    inner_w = iw
                                    break
                            if inner_w is None:
                                inner_w = min(axes.inner_windows) if axes.inner_windows else 5

                            inner_node = OperatorCall(
                                _inner, [_base_node(spec, vector_reducer=_vec), Number(float(inner_w), True)]
                            )
                            outer_node = OperatorCall(
                                _outer, [inner_node, Number(float(window), True)]
                            )
                            wrapped = _wrap_cross_section(outer_node, _cs, _grp)
                            return _wrap_hump(wrapped, _hump)
                        return _builder

                    base_sig = f"{outer_op}({inner_op})"
                    if vec_reducer:
                        base_sig = f"{vec_reducer}:{base_sig}"
                    ts_sig = f"hump({base_sig},{hump})" if (hump is not None and hump > 0) else base_sig
                    grid_extra = {
                        "ts": ts_sig, "cs": cs_op, "group": group,
                        "depth": 2, "neutralization": neutralization, "truncation": truncation,
                        "universe": universe,
                        "hump": hump,
                    }
                    if vec_reducer:
                        grid_extra["vec_reducer"] = vec_reducer
                    all_surface_configs.append(
                        SurfaceConfig(
                            layer=2,
                            ts_sig=ts_sig,
                            grid_extra=grid_extra,
                            builder_fn=make_depth2_builder(),
                            base_sig=base_sig,
                            variant_rank=_hump_variant_rank(hump, axes.humps),
                        )
                    )

            # ------------------------------------------------------------------
            # Layer 3: Multi-field signal templates (ts_corr)
            # ------------------------------------------------------------------
            if spec.secondary_field and not spec.operator_family:
                for cs_op, group, hump in itertools.product(cross_sections, groups, axes.humps):
                    def make_corr_builder(_cs=cs_op, _grp=group, _sec=spec.secondary_field, _hump=hump, _vec=vec_reducer):
                        def _builder(window: int) -> Node:
                            node = OperatorCall(
                                "ts_corr",
                                [
                                    _base_node(spec, vector_reducer=_vec),
                                    Field(name=_sec),
                                    Number(float(window), True),
                                ],
                            )
                            wrapped = _wrap_cross_section(node, _cs, _grp)
                            return _wrap_hump(wrapped, _hump)
                        return _builder

                    base_sig = f"{vec_reducer}:ts_corr" if vec_reducer else "ts_corr"
                    ts_sig = f"hump({base_sig},{hump})" if (hump is not None and hump > 0) else base_sig
                    grid_extra = {
                        "ts": ts_sig,
                        "secondary": spec.secondary_field,
                        "cs": cs_op,
                        "group": group,
                        "depth": 1,
                        "multi_field": True,
                        "neutralization": neutralization,
                        "truncation": truncation,
                        "universe": universe,
                        "hump": hump,
                    }
                    if vec_reducer:
                        grid_extra["vec_reducer"] = vec_reducer
                    all_surface_configs.append(
                        SurfaceConfig(
                            layer=3,
                            ts_sig=ts_sig,
                            grid_extra=grid_extra,
                            builder_fn=make_corr_builder(),
                            base_sig=base_sig,
                            variant_rank=_hump_variant_rank(hump, axes.humps),
                        )
                    )

    # Stratified selection
    chosen_configs = select_surface_configs(all_surface_configs, budget_surfaces, rng=rng)

    emitted_surfaces: list[list[Candidate]] = []
    rejected = 0

    for cfg in chosen_configs:
        surface, rej = _emit_surface(
            spec, kb, family_key, base_settings, axes,
            cfg.builder_fn,
            seen, cfg.grid_extra,
            arm=arm, campaign_task_id=campaign_task_id,
        )
        rejected += rej
        if surface:
            emitted_surfaces.append(surface)

    # Novelty prior (C1): order whole surfaces so that structurally novel mechanisms
    # reach the simulator first. Callers cap how many candidates actually run, and
    # simulation slots are the scarcest resource in the system — this spends them on
    # structures we have not already learned the answer to. A ranking key only:
    # a hard filter over a corpus this size would fit noise (STRATEGY.md §10).
    if rank_by_novelty and len(emitted_surfaces) > 1:
        from app.services.novelty import NoveltyScorer, rank_surfaces_by_novelty

        scorer = NoveltyScorer.from_session(db)
        if scorer.total_alphas > 0:
            emitted_surfaces = rank_surfaces_by_novelty(emitted_surfaces, scorer)
            for surf in emitted_surfaces:
                for cand in surf:
                    if cand.features is not None:
                        cand.features["novelty_score"] = round(scorer.score_candidate(cand), 4)
            log.info(
                "surfaces_ranked_by_novelty",
                family=family_key,
                surfaces=len(emitted_surfaces),
                corpus=scorer.corpus,
                corpus_size=scorer.total_alphas,
            )

    out: list[Candidate] = [c for surf in emitted_surfaces for c in surf]

    if max_candidates > 0 and len(out) == 0:
        log.warning(
            "family_expanded_zero_candidates",
            family=family_key,
            emitted=0,
            rejected=rejected,
            max_candidates=max_candidates,
            surface_size=surface_size,
        )
    else:
        log.info(
            "family_expanded",
            family=family_key,
            emitted=len(out),
            rejected=rejected,
            truncated=False,
        )
    return out
