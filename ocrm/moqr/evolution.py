"""NSGA-III Evolutionary Multi-Objective Optimization — §3.11.3 (R11).

Implements:
  - Das-Dennis two-layer reference vector generation
  - Task-weight-biased reference vectors (η=0.3)
  - NSGA-III selection with niche preservation
  - Tournament mating (N_pop offspring per generation, not O(N²))
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from . import MOQRPath

from .objectives import ObjectiveVector


@dataclass
class NSGAConfig:
    """NSGA-III configuration."""
    n_pop: int = 50
    g_max: int = 20
    n_objectives: int = 5
    # Das-Dennis two-layer parameters for M=5 objectives:
    # (p1=3, p2=2) -> C(7,3) + C(6,2) = 35 + 15 = 50 = N_pop
    p1: int = 3
    p2: int = 2
    eta_bias: float = 0.3  # reference vector bias toward task weights
    tournament_size: int = 3
    crossover_prob: float = 0.9
    mutation_prob: float = 0.2


def das_dennis_vectors(n_obj: int, p: int) -> list[list[float]]:
    """Generate Das-Dennis reference vectors (single layer).

    Produces C(n_obj+p-1, p) evenly spaced points on the unit simplex.
    """
    vectors = []

    def _generate(remaining: int, n_left: int, current: list[float]):
        if n_left == 0:
            current_copy = current + [remaining / p]
            vectors.append(current_copy)
            return
        for i in range(remaining + 1):
            _generate(remaining - i, n_left - 1, current + [i / p])

    _generate(p, n_obj - 1, [])
    return vectors


def two_layer_reference_vectors(n_obj: int, p1: int, p2: int) -> list[list[float]]:
    """Two-layer Das-Dennis reference vector generation (R11).

    Layer 1: boundary layer (p1 divisions)
    Layer 2: inside layer (p2 divisions, scaled by 0.5)
    For M=5, (p1=3, p2=2) gives 35+15=50 vectors matching N_pop.
    """
    # Layer 1: boundary
    layer1 = das_dennis_vectors(n_obj, p1)

    # Layer 2: inside (scaled toward center)
    layer2_raw = das_dennis_vectors(n_obj, p2)
    center = [1.0 / n_obj] * n_obj
    layer2 = []
    for v in layer2_raw:
        # Shrink toward center
        biased = [(vi + center[i]) / 2.0 for i, vi in enumerate(v)]
        # Normalize
        s = sum(biased)
        layer2.append([b / s for b in biased])

    return layer1 + layer2


def bias_reference_vectors(
    vectors: list[list[float]],
    task_weights: list[float],
    eta: float = 0.3
) -> list[list[float]]:
    """Bias reference vectors toward task preference (R11).

    v'_i = normalize((1-η)·v_i + η·W)
    """
    n_obj = len(task_weights)
    w_sum = sum(task_weights)
    w = [wi / w_sum for wi in task_weights] if w_sum > 0 else [1/n_obj] * n_obj

    biased = []
    for v in vectors:
        mixed = [(1 - eta) * v[i] + eta * w[i] for i in range(n_obj)]
        s = sum(mixed)
        if s > 0:
            biased.append([m / s for m in mixed])
        else:
            biased.append(v)
    return biased


def _dominates_vec(a: list[float], b: list[float]) -> bool:
    """Pareto dominance on raw objective lists (perf: avoids to_list churn)."""
    strict = False
    for x, y in zip(a, b):
        if x > y:
            return False
        if x < y:
            strict = True
    return strict


def non_dominated_sort(population: list["MOQRPath"]) -> list[list[int]]:
    """Fast non-dominated sorting (Deb et al. NSGA-II)."""
    n = len(population)
    if n == 0:
        return []

    # Pre-extract objective lists once (hot path)
    objs = [p.objectives.to_list() for p in population]

    dominated_by = [0] * n        # count of solutions dominating i
    dominates_set = [[] for _ in range(n)]  # set of solutions i dominates
    fronts: list[list[int]] = [[]]

    for i in range(n):
        oi = objs[i]
        for j in range(i + 1, n):
            oj = objs[j]
            if _dominates_vec(oi, oj):
                dominates_set[i].append(j)
                dominated_by[j] += 1
            elif _dominates_vec(oj, oi):
                dominates_set[j].append(i)
                dominated_by[i] += 1

    for i in range(n):
        if dominated_by[i] == 0:
            fronts[0].append(i)

    k = 0
    while fronts[k]:
        next_front = []
        for i in fronts[k]:
            for j in dominates_set[i]:
                dominated_by[j] -= 1
                if dominated_by[j] == 0:
                    next_front.append(j)
        k += 1
        fronts.append(next_front)

    # Remove empty last front
    while fronts and not fronts[-1]:
        fronts.pop()

    return fronts


def nsga3_select(
    population: list["MOQRPath"],
    n_select: int,
    ref_vectors: list[list[float]],
) -> list[int]:
    """NSGA-III environmental selection with niche preservation.

    Selects n_select individuals from population using:
    1. Non-dominated sorting
    2. Reference-point-based niche preservation for last front
    """
    if len(population) <= n_select:
        return list(range(len(population)))

    fronts = non_dominated_sort(population)
    selected: list[int] = []

    # Add complete fronts
    for front in fronts:
        if len(selected) + len(front) <= n_select:
            selected.extend(front)
        else:
            # Need to select from this front using niche preservation
            remaining = n_select - len(selected)
            niche_selected = _niche_preserve(
                population, front, remaining, ref_vectors
            )
            selected.extend(niche_selected)
            break

    return selected


def _niche_preserve(
    population: list["MOQRPath"],
    front: list[int],
    n_select: int,
    ref_vectors: list[list[float]],
) -> list[int]:
    """Reference-point niche preservation for the critical front."""
    if n_select >= len(front):
        return front

    # Associate each individual with nearest reference vector.
    # Perf: dist² = |p|² − (p·ref)²/|ref|² with precomputed ref norms
    # (avoids building projection vectors per pair).
    n_obj = 5
    associations: dict[int, list[int]] = {i: [] for i in range(len(ref_vectors))}
    ref_norm_sq = [sum(c * c for c in ref) for ref in ref_vectors]

    for idx in front:
        obj = population[idx].objectives.to_list()
        p_norm_sq = sum(o * o for o in obj)
        best_dist = float("inf")
        best_ref = 0
        for ri, ref in enumerate(ref_vectors):
            rn = ref_norm_sq[ri]
            if rn == 0:
                continue
            dot = 0.0
            for k in range(n_obj):
                dot += obj[k] * ref[k]
            dist_sq = p_norm_sq - (dot * dot) / rn
            if dist_sq < best_dist:
                best_dist = dist_sq
                best_ref = ri
        associations[best_ref].append(idx)

    # Niche count-based selection
    selected = []
    ref_order = list(range(len(ref_vectors)))
    random.shuffle(ref_order)

    while len(selected) < n_select:
        progress = False
        for ri in ref_order:
            if len(selected) >= n_select:
                break
            members = associations[ri]
            if members:
                # Pick member closest to reference vector
                chosen = members.pop(0)
                selected.append(chosen)
                progress = True
        if not progress:
            # All associations exhausted: fill remainder deterministically
            remaining = [i for i in front if i not in selected]
            selected.extend(remaining[:n_select - len(selected)])
            break

    return selected[:n_select]


def tournament_mating(
    population: list["MOQRPath"],
    n_offspring: int,
    tournament_size: int = 3,
    ranks: Optional[list[int]] = None,
) -> list[tuple[int, int]]:
    """Tournament mating selection (R11: N_pop offspring, not O(N²)).

    Winners are chosen by non-dominated rank (lower = better) with delay
    as tie-breaker; falls back to delay-only if ranks are not provided.
    """
    pairs = []
    n = len(population)
    if n < 2:
        return []

    def _key(i: int):
        rank = ranks[i] if ranks is not None and i < len(ranks) else 0
        return (rank, population[i].objectives.delay_ms)

    for _ in range(n_offspring):
        # Parent 1
        candidates1 = random.sample(range(n), min(tournament_size, n))
        p1 = min(candidates1, key=_key)
        # Parent 2
        candidates2 = random.sample(range(n), min(tournament_size, n))
        p2 = min(candidates2, key=_key)
        if p1 == p2 and n > 1:
            p2 = (p1 + 1) % n
        pairs.append((p1, p2))

    return pairs


def evolve_moo(
    initial_population: list["MOQRPath"],
    evaluate_fn: Callable[["MOQRPath"], ObjectiveVector],
    crossover_fn: Callable[["MOQRPath", "MOQRPath"], "MOQRPath"],
    mutation_fn: Callable[["MOQRPath"], "MOQRPath"],
    repair_fn: Callable[["MOQRPath"], "MOQRPath"],
    config: NSGAConfig,
    task_weights: Optional[list[float]] = None,
) -> list["MOQRPath"]:
    """Main NSGA-III evolutionary loop.

    Returns the final Pareto-approximate population.
    """
    n_obj = config.n_objectives

    # Generate biased reference vectors
    ref_vectors = two_layer_reference_vectors(n_obj, config.p1, config.p2)
    if task_weights:
        ref_vectors = bias_reference_vectors(ref_vectors, task_weights, config.eta_bias)

    # Initialize population (copy individuals to avoid aliasing)
    population = [replace(ind, nodes=list(ind.nodes))
                  for ind in initial_population[:config.n_pop]]
    while len(population) < config.n_pop:
        if population:
            src = random.choice(initial_population)
            population.append(replace(src, nodes=list(src.nodes)))
        else:
            break

    # Evaluate initial population
    for ind in population:
        if ind.objectives.delay_ms == 0.0:
            ind.objectives = evaluate_fn(ind)

    # Evolution loop
    for gen in range(config.g_max):
        # Rank population for multi-objective tournament pressure
        fronts = non_dominated_sort(population)
        ranks = [0] * len(population)
        for r, front in enumerate(fronts):
            for i in front:
                ranks[i] = r

        # Mating: produce N_pop offspring (R11: tournament, not O(N²))
        pairs = tournament_mating(
            population, config.n_pop, config.tournament_size, ranks
        )
        offspring = []

        for p1_idx, p2_idx in pairs:
            if random.random() < config.crossover_prob:
                child = crossover_fn(population[p1_idx], population[p2_idx])
            else:
                # True copy — repair/mutation must not alias the parent
                parent = population[p1_idx]
                child = replace(parent, nodes=list(parent.nodes))

            if random.random() < config.mutation_prob:
                child = mutation_fn(child)

            # Repair (R12)
            child = repair_fn(child)

            # Evaluate
            child.objectives = evaluate_fn(child)
            offspring.append(child)

        # Combine parent + offspring
        combined = population + offspring

        # Environmental selection via NSGA-III
        selected_indices = nsga3_select(combined, config.n_pop, ref_vectors)
        population = [combined[i] for i in selected_indices]

    return population
