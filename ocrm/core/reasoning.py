"""Three-step ontology reasoning engine — §3.4.1.

Steps:
  1. Concept Matching   — filter by concept class
  2. Axiom Evaluation   — evaluate applicable axioms (depth <= 3)
  3. Instance Constraint Satisfaction — check numerical constraints
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .ontology import (
    Instance, AXIOMS, D_MAX, is_subclass_of, DATA_PROPERTIES
)
from .kv_cache import KVCache


@dataclass
class Constraint:
    prop: str        # data property name
    op: str          # >=, <=, ==, >, <
    threshold: Any

    def satisfied(self, value: Any) -> bool:
        v = float(value)
        t = float(self.threshold)
        if self.op == ">=":
            return v >= t
        elif self.op == "<=":
            return v <= t
        elif self.op == ">":
            return v > t
        elif self.op == "<":
            return v < t
        elif self.op == "==":
            return v == t
        return False


@dataclass
class OntologyQuery:
    """A structured semantic query over the ontology."""
    required_class: str
    constraints: list[Constraint] = field(default_factory=list)
    task_weights: dict[str, float] = field(default_factory=dict)
    max_results: int = 5
    # 老化门限: 约束属性实例新鲜度 (1=刚写入, 0=TTL 耗尽) 低于此值
    # 时不作为满足者 —— 高动态资源 (算力/存储余量) 查询应设 > 0
    min_freshness: float = 0.0


@dataclass
class ReasoningResult:
    """Result of three-step reasoning."""
    satisfiers: list[str]   # individual IDs that satisfy
    step1_count: int = 0    # concept match count
    step2_count: int = 0    # post-axiom count
    step3_count: int = 0    # post-constraint count
    latency_ms: float = 0.0
    invalidated: list[str] = field(default_factory=list)


class ReasoningEngine:
    """Executes three-step ontology reasoning over local KV-Cache."""

    def __init__(self, cache: KVCache):
        self.cache = cache

    def reason(self, query: OntologyQuery) -> ReasoningResult:
        """
        Full three-step reasoning:
        Step 1: Concept Matching
        Step 2: Axiom Evaluation
        Step 3: Instance Constraint Satisfaction
        """
        t0 = time.time()

        # Step 1: Concept Matching
        instances = self.cache.get_by_concept(query.required_class)
        # Group by individual
        individuals: dict[str, dict[str, Instance]] = {}
        for inst in instances:
            individuals.setdefault(inst.individual, {})[inst.prop] = inst
        step1_individuals = set(individuals.keys())

        # Step 2: Axiom Evaluation — filter out invalidated ones
        invalidated = []
        for indiv, props in list(individuals.items()):
            for axiom in AXIOMS:
                if axiom.depth <= D_MAX and axiom.condition(props):
                    effects = axiom.effect(props)
                    for (prop_name, val) in effects:
                        if prop_name == "__invalidate__":
                            invalidated.append(indiv)
                            if indiv in individuals:
                                del individuals[indiv]
                            break
                    else:
                        continue
                    break

        step2_individuals = set(individuals.keys())

        # Step 3: Instance Constraint Satisfaction (aging-aware)
        now = time.time()
        satisfiers = []
        fresh_of: dict[str, float] = {}
        for indiv, props in individuals.items():
            all_satisfied = True
            fmin = 1.0
            for c in query.constraints:
                inst = props.get(c.prop)
                if inst is None or not c.satisfied(inst.value):
                    all_satisfied = False
                    break
                f = inst.freshness(now)
                if f < query.min_freshness:
                    all_satisfied = False   # 过老实例不可信, 不作满足者
                    break
                fmin = min(fmin, f)
            if all_satisfied:
                satisfiers.append(indiv)
                fresh_of[indiv] = fmin
        # 新鲜者优先: 同时满足时返回更新鲜的快照
        satisfiers.sort(key=lambda i: -fresh_of[i])

        latency_ms = (time.time() - t0) * 1000.0
        return ReasoningResult(
            satisfiers=satisfiers[:query.max_results],
            step1_count=len(step1_individuals),
            step2_count=len(step2_individuals),
            step3_count=len(satisfiers),
            latency_ms=latency_ms,
            invalidated=invalidated,
        )
