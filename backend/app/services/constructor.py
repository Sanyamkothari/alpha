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

from app.models.enums import FieldType
from app.services.alpha_library import AlphaSettings
from app.validator import ValidatorKB, validate
from app.validator.ast_nodes import Field, Node, Number, OperatorCall
from app.validator.validator import normalize

log = structlog.get_logger("constructor")

# Time-series shapes. Each takes (x, window).
DEFAULT_TS_TRANSFORMS: tuple[str, ...] = (
    "ts_zscore",
    "ts_rank",
    "ts_delta",
    "ts_mean",
    "ts_decay_linear",
)

# Depth-2: the outer operator wraps an inner time-series result.
# Kept short — the cross-product with depth-1 transforms is already large.
DEFAULT_DEPTH2_PAIRS: tuple[tuple[str, str], ...] = (
    ("ts_delta", "ts_rank"),      # acceleration of rank — regime change
    ("ts_zscore", "ts_delta"),    # z-score of momentum — mean-reversion of changes
    ("ts_rank", "ts_mean"),       # rank of smoothed signal — robust trend
    ("ts_delta", "ts_zscore"),    # change in standardised level — breakout
)

# Swept exhaustively — plateau analysis walks this axis.
DEFAULT_WINDOWS: tuple[int, ...] = (5, 10, 22, 63, 126, 252)

# Inner windows for depth-2 — always shorter to avoid inner >= outer redundancy.
DEFAULT_INNER_WINDOWS: tuple[int, ...] = (5, 10, 22)

# Cross-sectional normalization. None = leave the raw time-series signal.
DEFAULT_CROSS_SECTION: tuple[str | None, ...] = ("rank", "zscore", None)

# Group-relative variants; None = ungrouped.
DEFAULT_GROUPS: tuple[str | None, ...] = (None, "sector", "industry", "subindustry")

DEFAULT_NEUTRALIZATIONS: tuple[str, ...] = ("SUBINDUSTRY", "INDUSTRY", "SECTOR", "MARKET", "NONE")

# Also swept exhaustively — the second plateau axis.
DEFAULT_DECAYS: tuple[int, ...] = (0, 4, 8, 16)

DEFAULT_TRUNCATIONS: tuple[float, ...] = (0.08,)

# Universes to sweep — each is a distinct alpha on BRAIN.
DEFAULT_UNIVERSES: tuple[str, ...] = ("TOP3000",)

# Map field update frequency → backfill days. A quarterly fundamental without
# backfill is mostly NaN between reports; a daily news field with 120-day backfill
# blurs the signal into noise. The LLM triage reports frequency; this table
# translates it into the right construction parameter.
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


@dataclass(frozen=True)
class FamilySpec:
    """One economic mechanism.

    ``denominator`` builds a ratio — ``liabilities / cap`` rather than raw
    ``liabilities``. This matters more than it looks: a raw balance-sheet number
    is dominated by company size, so the ratio is usually the actual signal. The
    best result in the library (fitness 1.11) is exactly this shape.

    ``secondary_field`` enables multi-field families — ``ts_corr(primary,
    secondary, window)`` explores the time-series correlation between two fields,
    opening an entirely new signal class.

    ``frequency`` drives backfill duration via ``FREQUENCY_BACKFILL``. When set,
    it overrides ``backfill_days`` so that daily fields skip backfill and annual
    fields get a longer one.
    """

    field_code: str
    mechanism: str
    denominator: str | None = None
    secondary_field: str | None = None
    backfill_days: int | None = 120
    frequency: str | None = None
    axes: GridAxes = dc_field(default_factory=GridAxes)

    @property
    def effective_backfill(self) -> int | None:
        """Resolve backfill: frequency-driven when known, else the explicit value."""
        if self.frequency and self.frequency in FREQUENCY_BACKFILL:
            return FREQUENCY_BACKFILL[self.frequency]
        return self.backfill_days

    def family_key(self, settings: AlphaSettings | None = None) -> str:
        """Identity of the family, INCLUDING the simulation config.

        The config has to be part of the key. The same mechanism run at delay 0
        and delay 1 is two different backtests over two different field
        catalogues — without the suffix they share a key, and the plateau filter
        then compares a delay-0 point against a delay-1 neighbour and calls the
        result a ridge. Silently wrong, and wrong in the direction that promotes
        things.
        """
        base = f"{self.field_code}/{self.denominator}" if self.denominator else self.field_code
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
    config_key: tuple,
    seen: set[str],
    grid_extra: dict,
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
            )
        )

    surface_size = len(axes.windows) * len(axes.decays)
    if len(surface) == surface_size:
        return surface, rejected
    return [], rejected


def expand(
    db: Session,
    spec: FamilySpec,
    *,
    base_settings: AlphaSettings | None = None,
    max_candidates: int = 400,
) -> list[Candidate]:
    """Enumerate the family, returning only validator-passing candidates.

    Generates three layers of candidates (budget permitting):
    1. **Depth-1** — the original single-operator template
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
    groups = _group_fields(db, kb, axes.groups)

    family_key = spec.family_key(base_settings)
    out: list[Candidate] = []
    seen: set[str] = set()
    rejected = 0

    # The unit of emission is a COMPLETE (window x decay) surface for one
    # structural configuration — not an arbitrary slice of the cross-product.
    # Plateau analysis compares a point against its window/decay neighbours, so a
    # half-filled surface is worse than useless: missing neighbours make a real
    # plateau look like an isolated spike and it gets discarded. Truncating at
    # max_candidates therefore drops whole surfaces, never partial ones.
    surface_size = len(axes.windows) * len(axes.decays)

    # ------------------------------------------------------------------
    # Layer 1: Depth-1 templates (the original grid)
    # ------------------------------------------------------------------
    configs = itertools.product(
        axes.ts_transforms, axes.cross_section, groups,
        axes.neutralizations, axes.truncations, axes.universes,
    )
    for ts_op, cs_op, group, neutralization, truncation, universe in configs:
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
            _depth1_builder, (ts_op, cs_op, group, neutralization, truncation, universe),
            seen, grid_extra,
        )
        rejected += rej
        out.extend(surface)

    # ------------------------------------------------------------------
    # Layer 2: Depth-2 templates (nested operator pairs)
    # ------------------------------------------------------------------
    for (outer_op, inner_op), cs_op, group, neutralization, truncation in itertools.product(
        axes.depth2_pairs, axes.cross_section, groups,
        axes.neutralizations, axes.truncations,
    ):
        if len(out) + surface_size > max_candidates:
            break

        # For depth-2, inner_windows are shorter lookbacks. For each outer
        # window, we pick a single inner window (the first one that is strictly
        # shorter). This keeps the surface the same shape as depth-1 so the
        # plateau filter can compare neighbours identically.
        def _depth2_builder(
            window, _outer=outer_op, _inner=inner_op, _cs=cs_op, _grp=group,
        ):
            # Pick the longest inner window that is strictly shorter than outer.
            inner_w = None
            for iw in sorted(axes.inner_windows, reverse=True):
                if iw < window:
                    inner_w = iw
                    break
            if inner_w is None:
                # Outer window too small for any inner — use smallest inner.
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
        }
        surface, rej = _emit_surface(
            spec, kb, family_key, base_settings, axes,
            _depth2_builder,
            (outer_op, inner_op, cs_op, group, neutralization, truncation),
            seen, grid_extra,
        )
        rejected += rej
        out.extend(surface)

    # ------------------------------------------------------------------
    # Layer 3: Multi-field (ts_corr) when a secondary field is available
    # ------------------------------------------------------------------
    if spec.secondary_field and kb.field_type(spec.secondary_field) is not None:
        for cs_op, group, neutralization, truncation in itertools.product(
            axes.cross_section, groups, axes.neutralizations, axes.truncations,
        ):
            if len(out) + surface_size > max_candidates:
                break

            def _corr_builder(window, _cs=cs_op, _grp=group):
                primary = _base_node(spec)
                secondary: Node = Field(spec.secondary_field)  # type: ignore[arg-type]
                corr_node = OperatorCall(
                    "ts_corr", [primary, secondary, Number(float(window), True)]
                )
                return _wrap_cross_section(corr_node, _cs, _grp)

            grid_extra = {
                "ts": "ts_corr", "secondary": spec.secondary_field,
                "cs": cs_op, "group": group, "depth": 1,
                "neutralization": neutralization, "truncation": truncation,
            }
            surface, rej = _emit_surface(
                spec, kb, family_key, base_settings, axes,
                _corr_builder,
                ("ts_corr", spec.secondary_field, cs_op, group, neutralization, truncation),
                seen, grid_extra,
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
