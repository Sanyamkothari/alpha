"""Orthogonalisation and Residual Expression Generator (C2).

Generates residual alpha candidates via regression_neut(y, x) to salvage signals that collide
with standard equity risk factors or already submitted alphas:
- Tier 1: Standard risk factor proxies (Size, Momentum, Volatility, Liquidity).
- Tier 2: Inlining colliding parent alpha if combined complexity_score <= 15.0.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.validator import ValidatorKB, validate

log = structlog.get_logger("orthogonalization")

# Tier 1 standard market risk factor proxy expressions
STANDARD_RISK_PROXIES: dict[str, str] = {
    "size": "cap",
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
    max_complexity: float = 15.0,
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
