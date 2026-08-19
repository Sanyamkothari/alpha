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

from collections import Counter, defaultdict
import itertools
import random
from collections.abc import Iterable
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

DEFAULT_NEUTRALIZATIONS: tuple[str, ...] = ("SUBINDUSTRY", "INDUSTRY", "SECTOR", "MARKET", "NONE")

DEFAULT_TRUNCATIONS: tuple[float, ...] = (0.01, 0.08)

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
    # 0 = every settings combination is eligible, and the diversity sampler
    # decides how many actually get emitted. STRATEGY.md Rule 2: settings are a
    # grid axis, not a wrapper -- pinning them to one combination is the mistake
    # that produced 0/51. Set >0 only to deliberately cap a confirmed-structure sweep.
    settings_per_structure: int = 0


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
        return f"{self.field_code}{denom}{sec}{op}{wrap}{hz}@{s.region}/{s.universe}/d{s.delay}"


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


def _base_node(spec: FamilySpec) -> Node:
    base: Node = Field(name=spec.field_code)
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


# Axes the sampler actively spreads across, in priority order. Operator identity
# comes first because structural diversity is what produces uncorrelated alphas;
# the settings axes follow because a family sampled at one neutralization is the
# mistake STRATEGY.md 1.3 is about -- decay and neutralization moved the
# liabilities/cap family from FAIL to PASS on an unchanged expression.
_DIVERSITY_AXES: tuple[str, ...] = (
    "neutralization",
    "cs",
    "group",
    "truncation",
    "universe",
)

# Greedy ordering is O(n^2) in the pool; past this many picks the marginal
# diversity gain is nil and the remainder is simply shuffled.
_GREEDY_ORDER_CAP = 96


def order_surface_configs(
    configs: list[SurfaceConfig],
    *,
    rng: random.Random | None = None,
) -> list[SurfaceConfig]:
    """Order configs so that consecutive picks maximise axis coverage.

    Round-robin over ``(layer, ts_sig)`` alone guarantees operator breadth and
    nothing else: whichever cross-section and neutralization the shuffle happens
    to put first in each stratum is the one that gets emitted, so the settings
    axes silently collapse to a single value. This picks, at each slot, the
    config whose stratum is least used, tie-broken by the least-used value on
    each settings axis in turn.

    Returns *every* config, ordered. Callers take from the front until their
    surface budget is filled, which is what lets a discarded surface be refilled
    rather than consuming the slot.
    """
    r = rng or random.Random(42)
    pool = list(configs)
    r.shuffle(pool)

    used_stratum: Counter = Counter()
    used_axis: dict[str, Counter] = {a: Counter() for a in _DIVERSITY_AXES}
    ordered: list[SurfaceConfig] = []

    while pool and len(ordered) < _GREEDY_ORDER_CAP:
        best_i = 0
        best_key: tuple | None = None
        for i, c in enumerate(pool):
            axis_counts = tuple(
                used_axis[a][c.grid_extra.get(a)] for a in _DIVERSITY_AXES
            )
            # Sum first, then lexicographic. Comparing lexicographically alone
            # lets the leading axis absorb every tie, which starves the ones
            # behind it -- that is how the settings axes collapsed to a single
            # neutralization while the cross-section looked healthy.
            key = (used_stratum[(c.layer, c.ts_sig)], sum(axis_counts)) + axis_counts
            if best_key is None or key < best_key:
                best_key, best_i = key, i
        chosen = pool.pop(best_i)
        ordered.append(chosen)
        used_stratum[(chosen.layer, chosen.ts_sig)] += 1
        for a in _DIVERSITY_AXES:
            used_axis[a][chosen.grid_extra.get(a)] += 1

    ordered.extend(pool)
    return ordered


def select_surface_configs(
    configs: list[SurfaceConfig],
    budget_surfaces: int,
    *,
    rng: random.Random | None = None,
) -> list[SurfaceConfig]:
    """The first ``budget_surfaces`` configs in diversity order."""
    if budget_surfaces <= 0 or not configs:
        return []
    return order_surface_configs(configs, rng=rng)[:budget_surfaces]


def expand(
    db: Session,
    spec: FamilySpec,
    *,
    base_settings: AlphaSettings | None = None,
    max_candidates: int = 400,
    policy: BudgetPolicy | None = None,
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
    policy = policy or BudgetPolicy(max_surfaces=budget_surfaces)

    settings_combinations = list(
        itertools.product(axes.neutralizations, axes.truncations, axes.universes)
    )
    num_settings = (
        len(settings_combinations)
        if policy.settings_per_structure <= 0
        else min(len(settings_combinations), policy.settings_per_structure)
    )

    all_surface_configs: list[SurfaceConfig] = []

    for settings_idx in range(num_settings):
        neutralization, truncation, universe = settings_combinations[settings_idx]
        if (family_key, neutralization, truncation) in submitted_slices:
            continue

        # ------------------------------------------------------------------
        # Layer 1: Depth-1 templates (the single-operator grid)
        # ------------------------------------------------------------------
        for ts_op, cs_op, group in itertools.product(ts_transforms, cross_sections, groups):
            def make_depth1_builder(_ts=ts_op, _cs=cs_op, _grp=group):
                def _builder(window: int) -> Node:
                    node = OperatorCall(_ts, [_base_node(spec), Number(float(window), True)])
                    return _wrap_cross_section(node, _cs, _grp)
                return _builder

            grid_extra = {
                "ts": ts_op, "cs": cs_op, "group": group, "depth": 1,
                "neutralization": neutralization, "truncation": truncation,
                "universe": universe,
            }
            all_surface_configs.append(
                SurfaceConfig(
                    layer=1,
                    ts_sig=ts_op,
                    grid_extra=grid_extra,
                    builder_fn=make_depth1_builder(),
                )
            )

        # ------------------------------------------------------------------
        # Layer 2: Depth-2 templates (nested operator pairs)
        # ------------------------------------------------------------------
        if not spec.operator_family:
            for (outer_op, inner_op), cs_op, group in itertools.product(
                axes.depth2_pairs, cross_sections, groups
            ):
                def make_depth2_builder(_outer=outer_op, _inner=inner_op, _cs=cs_op, _grp=group):
                    def _builder(window: int) -> Node:
                        inner_w = None
                        for iw in sorted(axes.inner_windows, reverse=True):
                            if iw < window:
                                inner_w = iw
                                break
                        if inner_w is None:
                            inner_w = min(axes.inner_windows) if axes.inner_windows else 5

                        inner_node = OperatorCall(
                            _inner, [_base_node(spec), Number(float(inner_w), True)]
                        )
                        outer_node = OperatorCall(
                            _outer, [inner_node, Number(float(window), True)]
                        )
                        return _wrap_cross_section(outer_node, _cs, _grp)
                    return _builder

                ts_sig = f"{outer_op}({inner_op})"
                grid_extra = {
                    "ts": ts_sig, "cs": cs_op, "group": group,
                    "depth": 2, "neutralization": neutralization, "truncation": truncation,
                    "universe": universe,
                }
                all_surface_configs.append(
                    SurfaceConfig(
                    layer=2,
                    ts_sig=ts_sig,
                    grid_extra=grid_extra,
                    builder_fn=make_depth2_builder(),
                )
            )

        # ------------------------------------------------------------------
        # Layer 3: Multi-field signal templates (ts_corr)
        # ------------------------------------------------------------------
        if spec.secondary_field and not spec.operator_family:
            for cs_op, group in itertools.product(cross_sections, groups):
                def make_corr_builder(_cs=cs_op, _grp=group, _sec=spec.secondary_field):
                    def _builder(window: int) -> Node:
                        node = OperatorCall(
                            "ts_corr",
                            [
                                _base_node(spec),
                                Field(name=_sec),
                                Number(float(window), True),
                            ],
                        )
                        return _wrap_cross_section(node, _cs, _grp)
                    return _builder

                grid_extra = {
                    "ts": "ts_corr",
                    "secondary": spec.secondary_field,
                    "cs": cs_op,
                    "group": group,
                    "depth": 1,
                    "multi_field": True,
                    "neutralization": neutralization,
                    "truncation": truncation,
                    "universe": universe,
                }
                all_surface_configs.append(
                    SurfaceConfig(
                        layer=3,
                        ts_sig="ts_corr",
                        grid_extra=grid_extra,
                        builder_fn=make_corr_builder(),
                    )
                )

    # Diversity-ordered selection. We draw until the surface budget is FILLED
    # rather than taking a fixed slice: a config whose every point fails
    # validation would otherwise consume a slot and silently shrink the family.
    ordered_configs = order_surface_configs(all_surface_configs, rng=rng)

    out: list[Candidate] = []
    rejected = 0
    emitted_surfaces = 0
    starved = 0

    for cfg in ordered_configs:
        if emitted_surfaces >= budget_surfaces:
            break
        surface, rej = _emit_surface(
            spec, kb, family_key, base_settings, axes,
            cfg.builder_fn,
            seen, cfg.grid_extra,
            arm=arm, campaign_task_id=campaign_task_id,
        )
        rejected += rej
        if surface:
            out.extend(surface)
            emitted_surfaces += 1
        else:
            starved += 1

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
            surfaces=emitted_surfaces,
            budget_surfaces=budget_surfaces,
            strata_starved=starved,
            truncated=False,
        )
    return out
