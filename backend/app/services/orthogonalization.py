"""Orthogonalisation and Residual Expression Generator (C2).

Generates residual alpha candidates via regression_neut(y, x) to salvage signals that collide
with standard equity risk factors or already submitted alphas:
- Tier 1: Standard risk factor proxies (Size, Momentum, Volatility, Liquidity).
- Tier 2: Inlining colliding parent alpha if combined complexity_score <= 15.0.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.validator import ValidatorKB, validate

log = structlog.get_logger("orthogonalization")

# Placeholder, not a calibrated bound: BRAIN's expression-length limit is undocumented.
# Replace with the measured value once a length-related 400 tells us what it is.
DEFAULT_MAX_COMPLEXITY = 15.0

# Tier 1 standard market risk factor proxy expressions
# ``log(cap)``, not ``cap``. Market cap is extremely right-skewed, so a
# cross-sectional regression on the raw level is driven by a handful of mega-caps and
# the residual is not size-neutral in any useful sense. The operator KB's own example
# for regression_neut is ``regression_neut(signal, log(cap))``.
STANDARD_RISK_PROXIES: dict[str, str] = {
    "size": "log(cap)",
    "momentum": "divide(ts_delta(close,250),ts_delay(close,250))",
    "volatility": "ts_std_dev(returns,20)",
    "liquidity": "adv20",
}


@dataclass
class ResidualCandidate:
    expression: str
    proxy_type: str
    proxy_expression: str
    parent_alpha_id: int | None
    parent_expression: str


def build_tier1_residuals(
    expression: str,
    kb: ValidatorKB,
    *,
    parent_alpha_id: int | None = None,
) -> list[ResidualCandidate]:
    """Generate up to 4 Tier-1 residual expressions against standard risk factors."""
    out: list[ResidualCandidate] = []
    for proxy_name, proxy_expr in STANDARD_RISK_PROXIES.items():
        res_expr = f"regression_neut({expression},{proxy_expr})"
        val_res = validate(res_expr, kb)
        if val_res.valid:
            out.append(
                ResidualCandidate(
                    expression=res_expr,
                    proxy_type=proxy_name,
                    proxy_expression=proxy_expr,
                    parent_alpha_id=parent_alpha_id,
                    parent_expression=expression,
                )
            )
        else:
            log.warning("tier1_residual_invalid", expr=res_expr, errors=[e.message for e in val_res.errors])
    return out


def build_tier2_residual(
    expression: str,
    colliding_expression: str,
    kb: ValidatorKB,
    *,
    max_complexity: float = DEFAULT_MAX_COMPLEXITY,
    parent_alpha_id: int | None = None,
) -> ResidualCandidate | None:
    """Generate a Tier-2 residual against a colliding submitted alpha."""
    res_expr = f"regression_neut({expression},{colliding_expression})"
    val_res = validate(res_expr, kb)
    if not val_res.valid:
        log.warning("tier2_residual_invalid", expr=res_expr, errors=[e.message for e in val_res.errors])
        return None

    if val_res.complexity_score is not None and val_res.complexity_score > max_complexity:
        log.warning("tier2_residual_complexity_exceeded", expr=res_expr, score=val_res.complexity_score, max=max_complexity)
        return None

    return ResidualCandidate(
        expression=res_expr,
        proxy_type="colliding_parent",
        proxy_expression=colliding_expression,
        parent_alpha_id=parent_alpha_id,
        parent_expression=expression,
    )


def generate_residuals_for_alpha(
    db: Session,
    alpha_id: int,
    *,
    kb: ValidatorKB | None = None,
    colliding_alpha_id: int | None = None,
    max_complexity: float = DEFAULT_MAX_COMPLEXITY,
) -> list[Alpha]:
    """Persist residual variants of one alpha as children of it.

    Called for alphas the correlation gate rejected: each rejection cost a
    simulation to learn "too similar to something we already hold", and the residual
    is the component of the signal that the colliding factor cannot explain. That
    turns the rejection into a candidate instead of a dead end.

    Tier 1 (the four standard risk proxies) always runs. Tier 2 inlines the colliding
    alpha's own expression and is skipped when the combination gets too complex —
    BRAIN's expression-length limit is undocumented, so complexity is the proxy until
    the first length-related rejection tells us the real bound.

    Deliberately not called from ``plateau.evaluate()``: evaluation is a pure local
    computation over stored artefacts and must not write alpha rows as a side effect.
    """
    from app.services.alpha_library import AlphaSettings, create_alpha

    parent = db.get(Alpha, alpha_id)
    if parent is None:
        return []

    kb = kb or ValidatorKB.from_session(
        db, region=parent.region, delay=parent.delay, universe=parent.universe
    )
    settings = AlphaSettings(
        region=parent.region,
        universe=parent.universe,
        delay=parent.delay,
        neutralization=parent.neutralization or "SUBINDUSTRY",
        decay=parent.decay or 0,
        truncation=parent.truncation if parent.truncation is not None else 0.08,
    )

    residuals = build_tier1_residuals(parent.expression, kb, parent_alpha_id=alpha_id)

    if colliding_alpha_id is not None:
        collider = db.get(Alpha, colliding_alpha_id)
        if collider is not None:
            tier2 = build_tier2_residual(
                parent.expression,
                collider.expression,
                kb,
                max_complexity=max_complexity,
                parent_alpha_id=alpha_id,
            )
            if tier2 is not None:
                residuals.append(tier2)

    created: list[Alpha] = []
    parent_grid = (parent.feature_json or {}).get("grid") or {}
    for res in residuals:
        # Residuals keep the parent's grid coordinates so they land on their own
        # surface rather than colliding with the parent's.
        grid = dict(parent_grid, residual_of=res.proxy_type)
        result = create_alpha(
            db,
            res.expression,
            settings,
            family_key=parent.family_key,
            grid=grid,
            parent_id=alpha_id,
            # `mutation_type` is CHECK-constrained to five values (see the baseline
            # migration), and a residual is not cleanly any of them. "composition" is
            # the nearest, and no information is lost: `source` marks these as
            # orthogonalization output and `grid["residual_of"]` names the exact
            # proxy. A dedicated "residual" value would need a migration.
            mutation_type="composition",
            source="orthogonalization",
        )
        if result.alpha is not None:
            created.append(result.alpha)

    log.info(
        "residuals_generated",
        alpha_id=alpha_id,
        requested=len(residuals),
        created=len(created),
        tier2=colliding_alpha_id is not None,
    )
    return created
