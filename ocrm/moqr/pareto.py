"""Interval-Robust Pareto Dominance and Selection — §3.11.1 (R10).

Implements:
  - Interval-robust dominance ≻_R (chance-constrained, confidence-weighted)
  - Robust Pareto set construction (never empty by design)
  - TOPSIS multi-criteria selection from the Pareto front
"""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import MOQRPath

from .objectives import ObjectiveVector, evaluate_path_sampled


def interval_robust_dominates(
    path_a: "MOQRPath",
    path_b: "MOQRPath",
    sampled_a: list[tuple[ObjectiveVector, float]],
    sampled_b: list[tuple[ObjectiveVector, float]],
    epsilon_r: float = 0.1,
    rho_min: float = 0.7,
) -> bool:
    """Check if path_a interval-robustly dominates path_b (≻_R).

    Definition (from paper):
      π_a ≻_R π_b iff:
        Pr[F(π_a;t_j) ≼ F(π_b;t_j)] >= 1 - ε_R  ∀j
        AND ∃j: F(π_a;t_j) ≺ F(π_b;t_j) with ρ_j >= ρ_min
    """
    n_samples = min(len(sampled_a), len(sampled_b))
    if n_samples == 0:
        return False

    # Check: at all time samples, a weakly dominates b with high probability
    dominated_count = 0
    strictly_better_with_confidence = False

    for j in range(n_samples):
        obj_a, rho_a = sampled_a[j]
        obj_b, rho_b = sampled_b[j]
        confidence = min(rho_a, rho_b)

        va = obj_a.to_list()
        vb = obj_b.to_list()

        # Check weak dominance: all a <= b
        all_leq = all(a <= b for a, b in zip(va, vb))
        any_strict = any(a < b for a, b in zip(va, vb))

        if not all_leq:
            dominated_count += 1

        # Check strict dominance with sufficient confidence
        if any_strict and all_leq and confidence >= rho_min:
            strictly_better_with_confidence = True

    # Probability of dominance at all samples
    dominance_prob = 1.0 - dominated_count / max(n_samples, 1)

    return dominance_prob >= (1.0 - epsilon_r) and strictly_better_with_confidence


def robust_pareto_set(
    paths: list["MOQRPath"],
    r_samples: int = 3,
    epsilon_r: float = 0.1,
    rho_min: float = 0.7,
) -> list["MOQRPath"]:
    """Construct the ≻_R-non-dominated set (never empty by design).

    Uses cached objective vectors for interval sampling approximation.
    """
    if not paths:
        return []
    if len(paths) == 1:
        return paths[:]

    # Pre-compute sampled evaluations using objective variations
    sampled: list[list[tuple[ObjectiveVector, float]]] = []
    confidence_decay = 0.95

    for p in paths:
        base = p.objectives
        samples = []
        for j in range(r_samples):
            confidence = confidence_decay ** j
            # Time-varying perturbation
            obj = ObjectiveVector(
                delay_ms=base.delay_ms * (1.0 + 0.02 * j + random.gauss(0, 0.01)),
                neg_bandwidth_mbps=base.neg_bandwidth_mbps * (1.0 - 0.01 * j),
                neg_compute_ratio=base.neg_compute_ratio,
                neg_lifetime_s=base.neg_lifetime_s - j * 5.0,
                energy_j=base.energy_j * (1.0 + 0.03 * j),
            )
            samples.append((obj, confidence))
        sampled.append(samples)

    # Find non-dominated set
    is_dominated = [False] * len(paths)
    for i in range(len(paths)):
        if is_dominated[i]:
            continue
        for j in range(len(paths)):
            if i == j or is_dominated[j]:
                continue
            if interval_robust_dominates(
                paths[j], paths[i], sampled[j], sampled[i],
                epsilon_r, rho_min
            ):
                is_dominated[i] = True
                break

    pareto = [p for p, dom in zip(paths, is_dominated) if not dom]

    # Never empty: if somehow all dominated (shouldn't happen with strict partial order),
    # fall back to top path by first objective
    if not pareto:
        pareto = sorted(paths, key=lambda p: p.objectives.delay_ms)[:3]

    return pareto


def topsis_select(
    pareto: list["MOQRPath"],
    weights: list[float],
) -> "MOQRPath":
    """TOPSIS (Technique for Order Preference by Similarity to Ideal Solution).

    Selects the best compromise solution from the Pareto front.
    """
    if not pareto:
        from . import MOQRPath
        return MOQRPath(nodes=[])
    if len(pareto) == 1:
        pareto[0].fitness = 1.0
        return pareto[0]

    n_obj = 5
    # Normalize weights
    w_sum = sum(weights[:n_obj]) if weights else 1.0
    w = [(weights[i] / w_sum if i < len(weights) else 0.2) for i in range(n_obj)]

    # Decision matrix
    matrix = [p.objectives.to_list() for p in pareto]

    # Normalize columns (vector normalization)
    norm_matrix = []
    for row in matrix:
        norm_matrix.append(row[:])

    for j in range(n_obj):
        col_sum_sq = sum(matrix[i][j] ** 2 for i in range(len(matrix)))
        col_norm = math.sqrt(col_sum_sq) if col_sum_sq > 0 else 1.0
        for i in range(len(matrix)):
            norm_matrix[i][j] = matrix[i][j] / col_norm

    # Weighted normalized matrix
    for i in range(len(norm_matrix)):
        for j in range(n_obj):
            norm_matrix[i][j] *= w[j]

    # Ideal (min for all objectives since all are minimize-direction)
    # Anti-ideal (max)
    ideal = [min(norm_matrix[i][j] for i in range(len(norm_matrix)))
             for j in range(n_obj)]
    anti_ideal = [max(norm_matrix[i][j] for i in range(len(norm_matrix)))
                  for j in range(n_obj)]

    # Distances and TOPSIS scores
    best_score = -1.0
    best_idx = 0
    for i in range(len(norm_matrix)):
        d_plus = math.sqrt(sum((norm_matrix[i][j] - ideal[j]) ** 2
                               for j in range(n_obj)))
        d_minus = math.sqrt(sum((norm_matrix[i][j] - anti_ideal[j]) ** 2
                                for j in range(n_obj)))
        score = d_minus / (d_plus + d_minus) if (d_plus + d_minus) > 0 else 0.5
        pareto[i].fitness = score
        if score > best_score:
            best_score = score
            best_idx = i

    return pareto[best_idx]
