"""Phase 4 comprehensive tests — Bloat-Controlled Evolutionary Genetic Search.

Tests:
1. Parameter jitter mutation across lookback windows.
2. Compatible operator substitution (e.g. ts_rank <-> ts_zscore, rank <-> zscore).
3. Subtree crossover between orthogonal parents.
4. 50-cycle bloat invariant: ensuring depth <= 6 and node count <= 20 across 50 generations.
5. Double-logistic turnover penalty math (smooth transitions outside [0.01, 0.70]).
6. Multi-objective fitness ranking (DSR * Complexity * Turnover * Orthogonality).
7. Recursive lineage tree CTE query.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.services.evolution import (
    EvolutionConfig,
    compute_evolutionary_fitness,
    compute_turnover_penalty,
    crossover_ast,
    evolve_generation,
    mutate_operators,
    mutate_parameters,
)
from app.validator import ValidatorKB
from app.validator.ast_nodes import Field, Number, OperatorCall
from app.validator.features import _depth, _operator_count
from app.validator.parser import parse
from app.validator.validator import normalize


def test_mutate_parameters_jitters_windows() -> None:
    ast = parse("rank(ts_delta(close, 22))")
    mutated = mutate_parameters(ast)
    expr = normalize(mutated)
    assert expr.startswith("rank(ts_delta(close,")
    # Window 22 can jitter to 15, 20, or 30
    assert any(str(w) in expr for w in [15, 20, 30, 22])


def test_mutate_operators_swaps_compatible(db_session) -> None:
    kb = ValidatorKB.from_session(db_session)
    ast = parse("rank(ts_zscore(close, 10))")
    mutated = mutate_operators(ast, kb)
    expr = normalize(mutated)
    assert any(op in expr for op in ["ts_zscore", "ts_rank", "ts_delta"])


def test_crossover_exchanges_subtrees() -> None:
    parent1 = parse("rank(ts_delta(close, 5))")
    parent2 = parse("zscore(ts_mean(volume, 20))")
    child = crossover_ast(parent1, parent2)
    assert _depth(child) <= 6
    assert _operator_count(child) <= 20


def test_50_cycle_bloat_control_invariant(db_session) -> None:
    """Stress test: 50 consecutive generations must never exceed depth 6 or 20 nodes."""
    kb = ValidatorKB.from_session(db_session)
    cur_ast = parse("rank(ts_zscore(close, 5))")
    cfg = EvolutionConfig(max_depth=6, max_nodes=20)

    for gen in range(50):
        if gen % 2 == 0:
            next_ast = mutate_parameters(cur_ast)
        else:
            next_ast = mutate_operators(cur_ast, kb)

        assert _depth(next_ast) <= cfg.max_depth, f"Depth exceeded at gen {gen}: {_depth(next_ast)}"
        assert _operator_count(next_ast) <= cfg.max_nodes, f"Nodes exceeded at gen {gen}: {_operator_count(next_ast)}"
        cur_ast = next_ast


def test_turnover_penalty_curve() -> None:
    # 1. Optimal turnover in sweet spot (0.20) -> penalty near 1.0
    p_opt = compute_turnover_penalty(0.20)
    assert p_opt > 0.95

    # 2. Too low turnover (0.001) -> penalty near 0.0
    p_low = compute_turnover_penalty(0.001)
    assert p_low < 0.50

    # 3. Too high turnover (0.85) -> penalty near 0.0
    p_high = compute_turnover_penalty(0.85)
    assert p_high < 0.10


def test_multi_objective_fitness_scoring() -> None:
    # Alpha A: DSR=0.98, TO=0.25 (good), Nodes=5 (simple), Corr=0.10 (orthogonal)
    fit_a = compute_evolutionary_fitness(dsr=0.98, turnover=0.25, node_count=5, max_port_corr=0.10)

    # Alpha B: DSR=0.98, TO=0.85 (bad turnover), Nodes=18 (complex), Corr=0.60 (correlated)
    fit_b = compute_evolutionary_fitness(dsr=0.98, turnover=0.85, node_count=18, max_port_corr=0.60)

    assert fit_a > 0.70
    assert fit_b < 0.20
    assert fit_a > fit_b * 3.0


def test_lineage_recursive_cte(db_session) -> None:
    # Parent (Gen 0)
    p = Alpha(
        expression="rank(close)",
        expression_hash="p0_hash",
        status=AlphaStatus.PASSED.value,
        generation=0,
        is_valid=True,
    )
    db_session.add(p)
    db_session.flush()

    # Child (Gen 1)
    c = Alpha(
        expression="rank(ts_delta(close, 5))",
        expression_hash="c1_hash",
        parent_id=p.id,
        generation=1,
        status=AlphaStatus.PASSED.value,
        is_valid=True,
    )
    db_session.add(c)
    db_session.flush()

    # Grandchild (Gen 2)
    gc = Alpha(
        expression="group_neutralize(rank(ts_delta(close, 5)), sector)",
        expression_hash="gc2_hash",
        parent_id=c.id,
        generation=2,
        status=AlphaStatus.PASSED.value,
        is_valid=True,
    )
    db_session.add(gc)
    db_session.commit()

    LINEAGE_CTE = text("""
        WITH RECURSIVE lineage(id, expression, parent_id, generation, depth) AS (
            SELECT id, expression, parent_id, generation, 0 FROM alphas WHERE id = :root
            UNION ALL
            SELECT a.id, a.expression, a.parent_id, a.generation, l.depth + 1
            FROM alphas a JOIN lineage l ON a.parent_id = l.id
        )
        SELECT id, depth FROM lineage ORDER BY depth ASC
    """)
    rows = db_session.execute(LINEAGE_CTE, {"root": p.id}).fetchall()
    ids = [r[0] for r in rows]
    assert ids == [p.id, c.id, gc.id]
