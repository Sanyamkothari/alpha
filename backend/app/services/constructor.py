"""Stage 3 — the family constructor (STRATEGY.md Rule 2).

One economic mechanism expanded across the structure and settings grid. The LLM
never sees this code: it picks the *field* and says why the field should predict
returns; everything from there is deterministic AST assembly, so every emitted
expression is valid by construction and hallucination is impossible.

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

Depth-2 templates
-----------------
Depth-1 wraps a single ts-operator around the base node.  Depth-2 nests two
time-series operators — e.g. ``ts_delta(ts_rank(x, inner), outer)`` — which
captures *acceleration* and *regime-change* patterns that depth-1 cannot express.
The inner window is always shorter than the outer to avoid redundant compositions.

Multi-field families
--------------------
When ``secondary_field`` is set, the constructor also emits ``ts_corr``
variants — time-series correlation between the primary and secondary field.
This opens an entirely new signal class (cross-field relationships) that the
depth-1 template cannot reach.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dc_field

import structlog
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, FieldType
from app.services.alpha_library import AlphaSettings
from app.validator import ValidatorKB, validate
from app.validator.ast_nodes import Field, Node, Number, OperatorCall
from app.validator.validator import normalize

log = structlog.get_logger("constructor")

# Standard 7x7 grid (Task 1: default 49 alphas per territory)
STANDARD_WINDOWS: tuple[int, ...] = (5, 10, 20, 40, 60, 120, 250)
STANDARD_DECAYS: tuple[int, ...] = (0, 1, 2, 4, 6, 8, 16)

# Wide grid (opt-in via --grid wide)
WIDE_WINDOWS: tuple[int, ...] = (5, 10, 22, 63, 126, 252)
WIDE_DECAYS: tuple[int, ...] = (0, 4, 8, 16)

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

DEFAULT_TRUNCATIONS: tuple[float, ...] = (0.08,)

# Universes to sweep — each is a distinct alpha on BRAIN.
DEFAULT_UNIVERSES: tuple[str, ...] = ("TOP3000",)

# Map field update frequency → backfill days.
FREQUENCY_BACKFILL: dict[str, int | None] = {
    "daily": None,       # no backfill — data arrives every day
    "weekly": 10,
    "monthly": 30,
    "quarterly": 120,    # the original hardcoded default
    "annual": 252,
    "unknown": 120,      # conservative fallback
}


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


def derive_horizon_band(window: int | float | None) -> str | None:
    """Derive horizon band from lookback window duration in trading days.

    Bands:
    - short: 1–10d (e.g. 5, 10)
    - medium: 11–63d (e.g. 20, 22, 40, 60, 63)
    - long: 64d+ (e.g. 120, 126, 250, 252)
    """
    if window is None:
        return None
    try:
        w = int(window)
    except (ValueError, TypeError):
        return None
    if 1 <= w <= 10:
        return "short"
    if 11 <= w <= 63:
        return "medium"
    if w >= 64:
        return "long"
    return None


def canonical_territory_key(
    field_code: str,
    operator_family: str,
    horizon_band: str,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
) -> str:
    """Canonical territory identity: field x operator_family x horizon_band x simulation config.

    Denominator and wrapper shape are sub-axes within a territory, not part of the primary key.
    """
    return f"{field_code}:{operator_family}:{horizon_band}@{region}/{universe}/d{delay}"


@dataclass(frozen=True)
class FamilySpec:
    """One economic mechanism."""

    field_code: str
    mechanism: str = ""
    denominator: str | None = None
    secondary_field: str | None = None
    operator_family: str | None = None
    wrapper_shape: str | None = None
    horizon_band: str | None = None
    backfill_days: int | None = 120
    frequency: str | None = None
    grid_mode: str = "standard"  # "standard" (7x7=49) | "wide"
    axes: GridAxes = dc_field(default_factory=GridAxes)

    @property
    def effective_backfill(self) -> int | None:
        """Resolve backfill: frequency-driven when known, else the explicit value."""
        if self.frequency and self.frequency in FREQUENCY_BACKFILL:
            return FREQUENCY_BACKFILL[self.frequency]
        return self.backfill_days

    def family_key(self, settings: AlphaSettings | None = None) -> str:
        """Identity of the family/territory, INCLUDING the simulation config."""
        base = f"{self.field_code}/{self.denominator}" if self.denominator else self.field_code
        if self.operator_family:
            base = f"{base}:{self.operator_family}"
        if self.wrapper_shape:
            base = f"{base}:{self.wrapper_shape}"
        if self.horizon_band:
            base = f"{base}:{self.horizon_band}"
        if self.secondary_field:
            base = f"{base}+{self.secondary_field}"
        s = settings or AlphaSettings()
        return f"{base}@{s.region}/{s.universe}/d{s.delay}"


@dataclass
class Candidate:
    expression: str
    family_key: str
    grid: dict
    settings: AlphaSettings
    complexity_score: float | None = None
    features: dict | None = None
    arm: str | None = None
    campaign_task_id: int | None = None


def _base_node(spec: FamilySpec) -> Node:
    """The raw input: the field, optionally backfilled and ratio-normalized."""
    node: Node = Field(spec.field_code)
    backfill = spec.effective_backfill
    if backfill:
        # Fundamentals update quarterly; without a backfill the series is mostly
        # NaN between reports and the time-series operators see almost nothing.
        node = OperatorCall("ts_backfill", [node, Number(float(backfill), True)])
    if spec.denominator:
        node = OperatorCall("divide", [node, Field(spec.denominator)])
    return node


def _wrap_cross_section(node: Node, cs_op: str | None, group: str | None) -> Node:
    if cs_op is None:
        return node
    if group:
        return OperatorCall(f"group_{cs_op}", [node, Field(group)])
    return OperatorCall(cs_op, [node])


def _group_fields(
    db: Session, kb: ValidatorKB, candidates: Iterable[str | None]
) -> list[str | None]:
    """Keep only group names that really are GROUP-typed fields.

    ``group_*`` operators require a GROUP argument; the validator enforces it, so
    emitting a variant against a MATRIX field would just burn a slot producing a
    guaranteed rejection.
    """
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
    node_builder,
    seen: set[str],
    grid_extra: dict,
    arm: str | None = None,
    campaign_task_id: int | None = None,
) -> tuple[list[Candidate], int]:
    """Build a complete (window × decay) surface for one structural config.

    Returns the candidate list and count of rejections. The surface is only
    emitted if it is complete — partial surfaces are worse than useless for
    plateau analysis.
    """
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

        # Turnover pre-filter: fundamental fields at w < 5 with d == 0 systematically
        # produce >95% turnover (untradeable); retain w=5, d=0 as probe point on standard grids.
        if spec.effective_backfill and window < 5 and decay == 0:
            continue

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


def expand(
    db: Session,
    spec: FamilySpec,
    *,
    base_settings: AlphaSettings | None = None,
    max_candidates: int = 400,
    arm: str | None = None,
    campaign_task_id: int | None = None,
) -> list[Candidate]:
    """Enumerate the family, returning only validator-passing candidates.

    Generates three layers of candidates (budget permitting):
    1. **Depth-1** — the single-operator template
    2. **Depth-2** — nested operator pairs (acceleration, regime-change)
    3. **Multi-field** — ``ts_corr(primary, secondary, window)`` when a
       secondary field is specified
    """
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
        if axes.windows == STANDARD_WINDOWS:
            windows = WIDE_WINDOWS
        else:
            windows = axes.windows
        if axes.decays == STANDARD_DECAYS:
            decays = WIDE_DECAYS
        else:
            decays = axes.decays
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
    out: list[Candidate] = []
    seen: set[str] = set()
    rejected = 0

    submitted_slices = set(
        (a.family_key, a.neutralization, a.truncation)
        for a in db.query(Alpha).filter_by(status=AlphaStatus.SUBMITTED.value).all()
        if a.family_key
    )

    surface_size = len(axes.windows) * len(axes.decays)

    # ------------------------------------------------------------------
    # Layer 1: Depth-1 templates (the original grid)
    # ------------------------------------------------------------------
    configs = itertools.product(
        ts_transforms, cross_sections, groups,
        axes.neutralizations, axes.truncations, axes.universes,
    )
    for ts_op, cs_op, group, neutralization, truncation, universe in configs:
        if (family_key, neutralization, truncation) in submitted_slices:
            continue
        if len(out) + surface_size > max_candidates:
            break

        def _depth1_builder(window, _ts=ts_op, _cs=cs_op, _grp=group):
            node = OperatorCall(_ts, [_base_node(spec), Number(float(window), True)])
            return _wrap_cross_section(node, _cs, _grp)

        grid_extra = {
            "ts": ts_op, "cs": cs_op, "group": group, "depth": 1,
            "neutralization": neutralization, "truncation": truncation,
            "universe": universe,
        }
        surface, rej = _emit_surface(
            spec, kb, family_key, base_settings, axes,
            _depth1_builder,
            seen, grid_extra,
            arm=arm, campaign_task_id=campaign_task_id,
        )
        rejected += rej
        out.extend(surface)

    # ------------------------------------------------------------------
    # Layer 2: Depth-2 templates (nested operator pairs)
    # ------------------------------------------------------------------
    if not spec.operator_family:  # Only explore depth-2 if operator family is unconstrained
        for (outer_op, inner_op), cs_op, group, neutralization, truncation, universe in itertools.product(
            axes.depth2_pairs, cross_sections, groups,
            axes.neutralizations, axes.truncations, axes.universes,
        ):
            if (family_key, neutralization, truncation) in submitted_slices:
                continue
            if len(out) + surface_size > max_candidates:
                break

            def _depth2_builder(
                window, _outer=outer_op, _inner=inner_op, _cs=cs_op, _grp=group,
            ):
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

            grid_extra = {
                "ts": f"{outer_op}({inner_op})", "cs": cs_op, "group": group,
                "depth": 2, "neutralization": neutralization, "truncation": truncation,
                "universe": universe,
            }
            surface, rej = _emit_surface(
                spec, kb, family_key, base_settings, axes,
                _depth2_builder,
                seen, grid_extra,
                arm=arm, campaign_task_id=campaign_task_id,
            )
            rejected += rej
            out.extend(surface)

    # ------------------------------------------------------------------
    # Layer 3: Multi-field signal templates (ts_corr)
    # ------------------------------------------------------------------
    if spec.secondary_field and not spec.operator_family:
        for cs_op, group, neutralization, truncation, universe in itertools.product(
            cross_sections, groups, axes.neutralizations, axes.truncations, axes.universes,
        ):
            if (family_key, neutralization, truncation) in submitted_slices:
                continue
            if len(out) + surface_size > max_candidates:
                break

            def _corr_builder(
                window, _cs=cs_op, _grp=group, _sec=spec.secondary_field,
            ):
                node = OperatorCall(
                    "ts_corr",
                    [
                        _base_node(spec),
                        Field(name=_sec),
                        Number(float(window), True),
                    ],
                )
                return _wrap_cross_section(node, _cs, _grp)

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
            surface, rej = _emit_surface(
                spec, kb, family_key, base_settings, axes,
                _corr_builder,
                seen, grid_extra,
                arm=arm, campaign_task_id=campaign_task_id,
            )
            rejected += rej
            out.extend(surface)

    log.info(
        "family_expanded",
        family=family_key,
        emitted=len(out),
        rejected=rejected,
        truncated=False,
    )
    return out
