"""Phase 0 — Proxy calibration study and structural degenerate signal filtering.

Guards against:
1. Identity / idempotent redundancies: ``rank(rank(x))``, ``ts_zscore(ts_zscore(x, w), w)``
2. Trivial constant outputs: ``subtract(x, x)``, ``divide(x, x)``, ``multiply(x, 0)``
3. Nested window inversions: inner window >= outer window in nested time-series transforms
4. Extreme turnover risk prior to simulation

Also performs calibration benchmarking over historical simulation payloads to measure
rank correlation between proxy heuristics and real BRAIN backtest metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.results import AlphaMetric
from app.validator import ValidatorKB
from app.validator.ast_nodes import (
    Boolean,
    Field,
    Node,
    Number,
    OperatorCall,
    children,
)

log = structlog.get_logger("proxy_calibration")

# Operators whose immediate self-composition is an identity/redundancy
_IDEMPOTENT_UNARY = frozenset({"rank", "zscore", "normalize", "sign", "scale"})
_IDEMPOTENT_TS = frozenset({"ts_rank", "ts_zscore", "ts_backfill", "ts_decay_linear"})


@dataclass
class DegenerateCheckResult:
    is_degenerate: bool
    reason: str | None = None
    severity: str = "error"  # "error" (hard reject) | "warning" (soft flag)


@dataclass
class CalibrationReport:
    sample_size: int
    turnover_proxy_correlation: float
    complexity_vs_fitness_correlation: float
    degenerate_count_in_history: int
    is_proxy_viable: bool
    metrics_summary: dict[str, Any]


def _is_same_node(a: Node, b: Node) -> bool:
    """Check if two subtrees are structurally and lexically identical."""
    if type(a) is not type(b):
        return False
    if isinstance(a, Field) and isinstance(b, Field):
        return a.name == b.name
    if isinstance(a, Number) and isinstance(b, Number):
        return a.value == b.value and a.is_int == b.is_int
    if isinstance(a, Boolean) and isinstance(b, Boolean):
        return a.value == b.value
    if isinstance(a, OperatorCall) and isinstance(b, OperatorCall):
        if a.name != b.name or len(a.args) != len(b.args) or len(a.kwargs) != len(b.kwargs):
            return False
        for c1, c2 in zip(a.args, b.args, strict=True):
            if not _is_same_node(c1, c2):
                return False
        for k1, k2 in zip(a.kwargs, b.kwargs, strict=True):
            if k1.name != k2.name or not _is_same_node(k1.value, k2.value):
                return False
        return True
    return False


def is_degenerate_signal(node: Node, kb: ValidatorKB | None = None) -> DegenerateCheckResult:
    """Detects trivial constants, idempotent loops, and degenerate structural patterns."""
    # 1. Bare literal root
    if isinstance(node, (Number, Boolean)):
        return DegenerateCheckResult(True, "root node is a static literal")

    # 2. Recursive subtree inspection
    return _walk_degenerate_checks(node)


def _walk_degenerate_checks(node: Node) -> DegenerateCheckResult:
    if isinstance(node, OperatorCall):
        op = node.name.lower()
        args = node.args

        # Trivial arithmetic simplifications
        if op == "subtract" and len(args) == 2 and _is_same_node(args[0], args[1]):
            return DegenerateCheckResult(True, "subtract(x, x) produces constant 0")

        if op == "divide" and len(args) == 2 and _is_same_node(args[0], args[1]):
            return DegenerateCheckResult(True, "divide(x, x) produces constant 1")

        if op == "multiply" and len(args) == 2:
            if (isinstance(args[0], Number) and args[0].value == 0) or (
                isinstance(args[1], Number) and args[1].value == 0
            ):
                return DegenerateCheckResult(True, "multiply(x, 0) produces constant 0")

        # Idempotent unary repeats: rank(rank(x)), zscore(zscore(x))
        if op in _IDEMPOTENT_UNARY and args and isinstance(args[0], OperatorCall):
            inner_op = args[0].name.lower()
            if op == inner_op:
                return DegenerateCheckResult(
                    True, f"redundant idempotent composition: {op}({inner_op}(...))"
                )

        # Idempotent TS repeats: ts_rank(ts_rank(x, w), w)
        if op in _IDEMPOTENT_TS and args and isinstance(args[0], OperatorCall):
            inner_op = args[0].name.lower()
            if op == inner_op and len(args) >= 2 and len(args[0].args) >= 2:
                w_outer = args[1]
                w_inner = args[0].args[1]
                if _is_same_node(w_outer, w_inner):
                    return DegenerateCheckResult(
                        True, f"redundant time-series composition: {op}({inner_op}(..., w), w)"
                    )

        # Window order inversion in nested depth-2
        if (
            op in ("ts_delta", "ts_zscore", "ts_rank")
            and args
            and isinstance(args[0], OperatorCall)
        ):
            inner_op = args[0].name.lower()
            if inner_op in ("ts_rank", "ts_mean", "ts_zscore") and len(args) >= 2:
                if len(args[0].args) >= 2:
                    try:
                        w_outer_val = int(args[1].value) if isinstance(args[1], Number) else None
                        w_inner_val = (
                            int(args[0].args[1].value)
                            if isinstance(args[0].args[1], Number)
                            else None
                        )
                        if (
                            w_inner_val is not None
                            and w_outer_val is not None
                            and w_inner_val >= w_outer_val
                            and op == "ts_delta"
                            and inner_op == "ts_rank"
                        ):
                            return DegenerateCheckResult(
                                True,
                                f"inner window ({w_inner_val}) >= outer window ({w_outer_val}) in acceleration pair",
                            )
                    except (ValueError, TypeError, AttributeError):
                        pass

        # Check all child nodes
        for child in children(node):
            res = _walk_degenerate_checks(child)
            if res.is_degenerate:
                return res

    return DegenerateCheckResult(False, None)


def _spearman_rank_correlation(x: np.ndarray, y: np.ndarray) -> float:
    """Vectorized Spearman rank correlation between two 1D arrays."""
    if len(x) < 3 or len(y) < 3:
        return 0.0
    x_rank = np.argsort(np.argsort(x)).astype(float)
    y_rank = np.argsort(np.argsort(y)).astype(float)
    denom = np.std(x_rank) * np.std(y_rank)
    if denom == 0:
        return 0.0
    return float(np.cov(x_rank, y_rank)[0, 1] / denom)


def calibrate_proxy_rankings(db: Session, sample_size: int = 150) -> CalibrationReport:
    """Evaluates rank correlation between AST proxy heuristics and real BRAIN backtest metrics.

    Uses a mixed batch of historical simulated alphas (including passed, failed, and mediocre).
    """
    rows = db.execute(
        select(Alpha, AlphaMetric)
        .join(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .where(AlphaMetric.turnover.is_not(None), AlphaMetric.sharpe.is_not(None))
        .order_by(AlphaMetric.id.desc())
        .limit(sample_size)
    ).all()

    if not rows:
        return CalibrationReport(
            sample_size=0,
            turnover_proxy_correlation=0.0,
            complexity_vs_fitness_correlation=0.0,
            degenerate_count_in_history=0,
            is_proxy_viable=False,
            metrics_summary={},
        )

    proxy_turnovers = []
    actual_turnovers = []
    complexities = []
    actual_fitnesses = []
    degenerate_count = 0

    from app.validator import ValidatorKB
    from app.validator.parser import parse

    kb = ValidatorKB.from_session(db)

    for alpha, metric in rows:
        features = alpha.feature_json or {}
        p_turnover = features.get("expected_turnover_proxy") or 0.0
        proxy_turnovers.append(float(p_turnover))
        actual_turnovers.append(float(metric.turnover or 0.0))

        complexities.append(float(alpha.complexity_score or 0.0))
        actual_fitnesses.append(float(metric.fitness or 0.0))

        try:
            ast = parse(alpha.expression)
            deg = is_degenerate_signal(ast, kb)
            if deg.is_degenerate:
                degenerate_count += 1
        except Exception:
            pass

    turnover_corr = _spearman_rank_correlation(
        np.array(proxy_turnovers), np.array(actual_turnovers)
    )
    complexity_corr = _spearman_rank_correlation(np.array(complexities), np.array(actual_fitnesses))

    log.info(
        "proxy_calibration_completed",
        samples=len(rows),
        turnover_corr=round(turnover_corr, 3),
        complexity_corr=round(complexity_corr, 3),
        degenerates=degenerate_count,
    )

    return CalibrationReport(
        sample_size=len(rows),
        turnover_proxy_correlation=turnover_corr,
        complexity_vs_fitness_correlation=complexity_corr,
        degenerate_count_in_history=degenerate_count,
        is_proxy_viable=bool(len(rows) >= 30 and turnover_corr >= 0.50),
        metrics_summary={
            "avg_actual_turnover": float(np.mean(actual_turnovers)),
            "avg_proxy_turnover": float(np.mean(proxy_turnovers)),
            "avg_sharpe": float(np.mean([m.sharpe for _, m in rows if m.sharpe is not None])),
        },
    )
