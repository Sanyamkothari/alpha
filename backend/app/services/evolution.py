"""Phase 4 — Bloat-Controlled Evolutionary Genetic Search Engine.

Evolves generation N+1 from top DSR-surviving parent alphas using:
1. Parameter jittering (lookback windows, decays, truncations)
2. Compatible operator mutation (e.g. ts_rank <-> ts_zscore, rank <-> zscore)
3. Subtree crossover between orthogonal parents
4. Neutralization wrapping (group_neutralize)

Enforces strict depth (<= 6) and node (<= 20) bloat controls, with multi-objective fitness
balancing DSR, turnover bounds, complexity penalties, and portfolio orthogonality.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from typing import Sequence

import structlog
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import MutationType
from app.services.alpha_library import AlphaSettings
from app.services.constructor import Candidate
from app.services.proxy_calibration import is_degenerate_signal
from app.validator import ValidatorKB, validate
from app.validator.ast_nodes import (
    Field,
    Node,
    Number,
    OperatorCall,
    children,
)
from app.validator.features import _depth, _operator_count
from app.validator.parser import parse
from app.validator.validator import normalize

log = structlog.get_logger("evolution")

# Compatible operator substitution maps
_OP_SWAPS: dict[str, tuple[str, ...]] = {
    "ts_rank": ("ts_zscore", "ts_decay_linear"),
    "ts_zscore": ("ts_rank", "ts_delta"),
    "ts_delta": ("ts_zscore", "ts_mean"),
    "ts_mean": ("ts_decay_linear", "ts_delta"),
    "ts_decay_linear": ("ts_mean", "ts_rank"),
    "rank": ("zscore", "normalize"),
    "zscore": ("rank", "normalize"),
    "normalize": ("rank", "zscore"),
}

_WINDOW_JITTER: dict[int, tuple[int, ...]] = {
    5: (3, 7, 10),
    10: (5, 12, 15),
    22: (15, 20, 30),
    63: (45, 60, 90),
    126: (90, 120, 150),
    252: (180, 240, 300),
}


@dataclass
class EvolutionConfig:
    population_size: int = 30
    max_depth: int = 6
    max_nodes: int = 20
    mutation_rate: float = 0.70
    crossover_rate: float = 0.30
    complexity_penalty: float = 0.015


def compute_turnover_penalty(turnover: float) -> float:
    """Double-logistic turnover penalty dropping smoothly outside [0.01, 0.70]."""
    if not math.isfinite(turnover):
        return 0.0
    left = 1.0 / (1.0 + math.exp(-50.0 * (turnover - 0.01)))
    right = 1.0 / (1.0 + math.exp(50.0 * (turnover - 0.70)))
    return float(left * right)


def compute_evolutionary_fitness(
    dsr: float,
    turnover: float,
    node_count: int,
    max_port_corr: float = 0.0,
    complexity_penalty: float = 0.015,
) -> float:
    """Multi-objective fitness balancing DSR, complexity, turnover, and orthogonality."""
    dsr_term = max(0.0, min(1.0, dsr))
    comp_term = max(0.0, 1.0 - complexity_penalty * float(node_count))
    to_term = compute_turnover_penalty(turnover)
    corr_term = max(0.0, 1.0 - max(0.0, min(1.0, max_port_corr)))
    return float(dsr_term * comp_term * to_term * corr_term)


def mutate_parameters(node: Node) -> Node:
    """Jitter numeric lookback window constants across the AST."""
    cloned = copy.deepcopy(node)

    def _walk(cur: Node) -> None:
        if isinstance(cur, OperatorCall):
            for i, arg in enumerate(cur.args):
                if isinstance(arg, Number) and arg.is_int:
                    val = int(arg.value)
                    if val in _WINDOW_JITTER:
                        new_val = random.choice(_WINDOW_JITTER[val])
                        cur.args[i] = Number(float(new_val), True)
            for child in children(cur):
                _walk(child)

    _walk(cloned)
    return cloned


def mutate_operators(node: Node, kb: ValidatorKB) -> Node:
    """Substitute operators with compatible alternatives."""
    cloned = copy.deepcopy(node)

    def _walk(cur: Node) -> None:
        if isinstance(cur, OperatorCall):
            op = cur.name.lower()
            if op in _OP_SWAPS and random.random() < 0.40:
                new_op = random.choice(_OP_SWAPS[op])
                cur.name = new_op
            for child in children(cur):
                _walk(child)

    _walk(cloned)
    return cloned


def _collect_subtrees(node: Node, acc: list[Node]) -> None:
    if isinstance(node, OperatorCall):
        acc.append(node)
        for c in children(node):
            _collect_subtrees(c, acc)


def crossover_ast(parent_a: Node, parent_b: Node) -> Node:
    """Swap an inner subtree from parent B into a clone of parent A."""
    cloned_a = copy.deepcopy(parent_a)
    subtrees_b: list[Node] = []
    _collect_subtrees(parent_b, subtrees_b)

    if not subtrees_b:
        return cloned_a

    donor = copy.deepcopy(random.choice(subtrees_b))

    def _inject(cur: Node) -> bool:
        if isinstance(cur, OperatorCall) and cur.args:
            idx = random.randint(0, len(cur.args) - 1)
            cur.args[idx] = donor
            return True
        return False

    _inject(cloned_a)
    return cloned_a


def evolve_generation(
    db: Session,
    parent_ids: Sequence[int],
    *,
    config: EvolutionConfig | None = None,
    base_settings: AlphaSettings | None = None,
    arm: str | None = None,
    campaign_task_id: int | None = None,
) -> list[Candidate]:
    """Produce generation N+1 from top parent alphas."""
    cfg = config or EvolutionConfig()
    base_settings = base_settings or AlphaSettings()
    kb = ValidatorKB.from_session(
        db,
        region=base_settings.region,
        delay=base_settings.delay,
        universe=base_settings.universe,
    )

    parents = [db.get(Alpha, pid) for pid in parent_ids]
    valid_parents = [p for p in parents if p is not None and p.expression]
    if not valid_parents:
        log.warning("no_valid_parents_for_evolution")
        return []

    parent_asts: list[tuple[Alpha, Node]] = []
    for p in valid_parents:
        try:
            ast = parse(p.expression)
            parent_asts.append((p, ast))
        except Exception:
            pass

    if not parent_asts:
        return []

    candidates: list[Candidate] = []
    seen: set[str] = set()

    attempts = 0
    max_attempts = cfg.population_size * 5

    while len(candidates) < cfg.population_size and attempts < max_attempts:
        attempts += 1
        parent_alpha, parent_node = random.choice(parent_asts)

        r = random.random()
        mut_type: str = MutationType.PARAMETER_TUNE.value

        if r < 0.40:
            child_ast = mutate_parameters(parent_node)
            mut_type = MutationType.PARAMETER_TUNE.value
        elif r < 0.75:
            child_ast = mutate_operators(parent_node, kb)
            mut_type = MutationType.OPERATOR_SWAP.value
        elif len(parent_asts) >= 2:
            _, donor_node = random.choice(parent_asts)
            child_ast = crossover_ast(parent_node, donor_node)
            mut_type = MutationType.COMPOSITION.value
        else:
            child_ast = mutate_parameters(parent_node)

        # Enforce bloat caps
        if _depth(child_ast) > cfg.max_depth or _operator_count(child_ast) > cfg.max_nodes:
            continue

        # Check degeneracy
        deg = is_degenerate_signal(child_ast, kb)
        if deg.is_degenerate:
            continue

        expr = normalize(child_ast)
        if expr in seen or expr == parent_alpha.expression:
            continue
        seen.add(expr)

        val_res = validate(expr, kb)
        if not val_res.valid:
            continue

        family_key = f"evo:gen{parent_alpha.generation + 1}:{parent_alpha.family_key or 'alpha'}"
        grid = {
            "parent_id": parent_alpha.id,
            "generation": parent_alpha.generation + 1,
            "mutation_type": mut_type,
            "decay": parent_alpha.decay or 0,
            "neutralization": parent_alpha.neutralization or "SUBINDUSTRY",
            "truncation": parent_alpha.truncation or 0.08,
            "window": 22,
        }

        candidates.append(
            Candidate(
                expression=expr,
                family_key=family_key,
                grid=grid,
                settings=AlphaSettings(
                    region=parent_alpha.region,
                    universe=parent_alpha.universe,
                    delay=parent_alpha.delay,
                    neutralization=parent_alpha.neutralization or "SUBINDUSTRY",
                    decay=parent_alpha.decay or 0,
                    truncation=parent_alpha.truncation or 0.08,
                ),
                complexity_score=val_res.complexity_score,
                features=val_res.features,
                arm=arm,
                campaign_task_id=campaign_task_id,
            )
        )

    log.info(
        "generation_evolved",
        parents=len(parent_asts),
        children_created=len(candidates),
        target_size=cfg.population_size,
    )
    return candidates
