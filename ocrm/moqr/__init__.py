"""H-OG-MOQR: Hierarchical Ontology-Guided Multi-Objective QoS Routing — §3.11.

Implements Algorithm 4′ from the paper:
  Phase 0: Semantic Warm-Start (R9)
  Phase 1: Region-Level Ontology Reasoning & Corridor Search (R4)
  Phase 2: Per-Segment Refinement (R4)
  Phase 3: Interval-Robust Selection (R10)
  Phase 4: Cache & Return (R9)
"""
from __future__ import annotations

import time
import random
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from ..core.ontology import Instance, is_subclass_of, CONCEPT_TREE, make_key
from ..core.kv_cache import KVCache
from ..core.reasoning import ReasoningEngine, OntologyQuery, Constraint
from .objectives import (
    QoSObjectives, ObjectiveVector, evaluate_path_objectives,
    evaluate_path_sampled
)
from .pareto import (
    interval_robust_dominates, robust_pareto_set, topsis_select
)
from .evolution import evolve_moo, NSGAConfig
from .operators import (
    ontology_crossover, ontology_mutation, axiom_repair_connected
)

logger = logging.getLogger("moqr")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class QoSRequirement:
    """Task QoS requirements for multi-objective routing."""
    required_class: str = "ComputingNode"
    max_delay_ms: float = 100.0
    min_bandwidth_mbps: float = 1000.0
    min_compute_flops: float = 5.0  # TFLOPS
    min_lifetime_s: float = 30.0
    max_energy_j: float = 10000.0
    task_weights: dict[str, float] = field(default_factory=lambda: {
        "delay": 0.3, "bandwidth": 0.2, "compute": 0.2,
        "lifetime": 0.2, "energy": 0.1
    })


@dataclass
class MOQRPath:
    """A candidate path with objective vector."""
    nodes: list[str]
    objectives: ObjectiveVector = field(default_factory=ObjectiveVector)
    corridor: Optional[list[str]] = None  # region sequence
    segment_id: int = 0
    fitness: float = 0.0  # TOPSIS score


@dataclass
class MOQRResult:
    """Result from H-OG-MOQR."""
    selected_path: Optional[MOQRPath] = None
    pareto_set: list[MOQRPath] = field(default_factory=list)
    pareto_size: int = 0
    optimization_latency_ms: float = 0.0
    warm_start_used: bool = False
    corridors_explored: int = 0
    segments_refined: int = 0
    total_evaluations: int = 0


# ---------------------------------------------------------------------------
# Region abstraction (Level 1)
# ---------------------------------------------------------------------------

@dataclass
class RegionAggregate:
    """Aggregate properties of a network region (orbital plane)."""
    region_id: str
    nodes: list[str]
    total_flops: float = 0.0
    max_flops: float = 0.0
    min_delay_ms: float = float("inf")
    aggregate_bw_mbps: float = 0.0
    min_lifetime_s: float = float("inf")
    total_energy_j: float = 0.0


def build_region_aggregates(
    nodes_by_region: dict[str, list[str]],
    cache: KVCache
) -> dict[str, RegionAggregate]:
    """Build region aggregates from KV-Cache instances.

    The NetworkLink concept scan is performed ONCE and links are assigned
    to regions by endpoint membership (avoids O(N_nodes x cache) rescans).
    """
    regions: dict[str, RegionAggregate] = {}
    node_to_region: dict[str, str] = {}
    for region_id, node_ids in nodes_by_region.items():
        agg = RegionAggregate(region_id=region_id, nodes=node_ids)
        for nid in node_ids:
            node_to_region[nid] = region_id
            props = cache.get_by_individual(nid)
            flops_inst = props.get("hasAvailableFLOPS")
            if flops_inst:
                val = float(flops_inst.value)
                agg.total_flops += val
                agg.max_flops = max(agg.max_flops, val)
            energy_inst = props.get("hasEnergyReserve")
            if energy_inst:
                agg.total_energy_j += float(energy_inst.value)
        regions[region_id] = agg

    # Link aggregates: single scan, assign each link to its endpoint regions
    link_insts = cache.get_by_concept("NetworkLink")
    for li in link_insts:
        body = li.individual[5:] if li.individual.startswith("LINK-") else li.individual
        touched: set[str] = set()
        for nid, rid in node_to_region.items():
            if body.startswith(nid + "-") or body.endswith("-" + nid):
                touched.add(rid)
        for rid in touched:
            agg = regions[rid]
            if li.prop == "hasLatency":
                agg.min_delay_ms = min(agg.min_delay_ms, float(li.value))
            elif li.prop == "hasAvailableBandwidth":
                agg.aggregate_bw_mbps += float(li.value)
            elif li.prop == "hasLinkLifetime":
                agg.min_lifetime_s = min(agg.min_lifetime_s, float(li.value))

    for agg in regions.values():
        if agg.min_delay_ms == float("inf"):
            agg.min_delay_ms = 5.0  # default ISL delay
        if agg.min_lifetime_s == float("inf"):
            agg.min_lifetime_s = 60.0
    return regions


# ---------------------------------------------------------------------------
# Warm-start cache (R9)
# ---------------------------------------------------------------------------

class ParetoCache:
    """Cache of previous Pareto sets for semantic warm-start."""

    def __init__(self, max_entries: int = 100):
        self._store: dict[str, dict] = {}  # bucket -> {pareto_set, population, epoch}
        self.max_entries = max_entries
        self.warm_hits = 0
        self.warm_misses = 0

    def quantize_bucket(self, qos: QoSRequirement) -> str:
        """Quantize QoS requirements into a cache bucket key."""
        d_bucket = int(qos.max_delay_ms / 50) * 50
        bw_bucket = int(qos.min_bandwidth_mbps / 500) * 500
        c_bucket = int(qos.min_compute_flops / 5) * 5
        return f"{qos.required_class}/{d_bucket}/{bw_bucket}/{c_bucket}"

    def lookup(self, qos: QoSRequirement, max_dist: float = 0.3) -> Optional[dict]:
        """Nearest-neighbor lookup by ontology distance."""
        bucket = self.quantize_bucket(qos)
        if bucket in self._store:
            self.warm_hits += 1
            return self._store[bucket]
        # Fuzzy match by class hierarchy
        for stored_bucket, data in self._store.items():
            stored_class = stored_bucket.split("/")[0]
            if is_subclass_of(qos.required_class, stored_class) or \
               is_subclass_of(stored_class, qos.required_class):
                self.warm_hits += 1
                return data
        self.warm_misses += 1
        return None

    def store(self, qos: QoSRequirement, pareto_set: list[MOQRPath],
              population: list[MOQRPath], epoch: float):
        bucket = self.quantize_bucket(qos)
        if len(self._store) >= self.max_entries:
            oldest = min(self._store, key=lambda k: self._store[k]["epoch"])
            del self._store[oldest]
        self._store[bucket] = {
            "pareto_set": pareto_set, "population": population, "epoch": epoch
        }

    @property
    def hit_ratio(self) -> float:
        total = self.warm_hits + self.warm_misses
        return self.warm_hits / total if total > 0 else 0.0


# ---------------------------------------------------------------------------
# H-OG-MOQR Main Algorithm (Algorithm 4′)
# ---------------------------------------------------------------------------

class HOGMOQR:
    """Hierarchical Ontology-Guided Multi-Objective QoS Router."""

    def __init__(
        self,
        node_id: str,
        cache: KVCache,
        engine: ReasoningEngine,
        topology: dict[str, list[str]],  # node_id -> neighbor list
        regions: dict[str, list[str]],   # region_id -> node_ids
        config: Optional[dict] = None,
    ):
        self.node_id = node_id
        self.cache = cache
        self.engine = engine
        self.topology = topology
        self.regions = regions
        self.node_to_region = {}
        for rid, nids in regions.items():
            for nid in nids:
                self.node_to_region[nid] = rid

        # Configuration
        cfg = config or {}
        self.n_pop = cfg.get("n_pop", 50)
        self.g_max = cfg.get("g_max", 20)
        self.g_warm = cfg.get("g_warm", 5)
        self.k_corridors = cfg.get("k_corridors", 3)
        self.n_pop_segment = cfg.get("n_pop_segment", 20)
        self.g_segment = cfg.get("g_segment", 8)
        self.r_samples = cfg.get("r_samples", 3)
        self.epsilon_r = cfg.get("epsilon_r", 0.1)
        self.rho_min = cfg.get("rho_min", 0.7)
        self.h_max = cfg.get("h_max", 10)  # max hops
        self.eta_bias = cfg.get("eta_bias", 0.3)  # ref vector bias
        self.warm_epoch_max_age = cfg.get("warm_epoch_s", 60.0)  # R9 staleness

        # Warm-start cache
        self.pareto_cache = ParetoCache()

        # Static region graph (topology dict is fixed for the run)
        self.region_graph = self._build_region_graph()

        # Region aggregate TTL cache (perf: avoid rebuild on every optimize)
        self._region_aggs: Optional[dict[str, RegionAggregate]] = None
        self._region_aggs_ts = 0.0
        self._region_aggs_ttl = cfg.get("region_aggs_ttl_s", 1.0)

        # Node-props snapshot TTL cache (perf: one-pass instead of per-node scans)
        self._props_snap: Optional[dict[str, dict]] = None
        self._props_snap_ts = 0.0

        # Stats
        self.total_evaluations = 0
        self.total_optimizations = 0

    def optimize(
        self, destination: str, qos: QoSRequirement
    ) -> MOQRResult:
        """Execute H-OG-MOQR Algorithm 4′."""
        t0 = time.time()
        result = MOQRResult()

        # ═══ Phase 0: Semantic Warm-Start (R9) ═══
        seeds = self.pareto_cache.lookup(qos)
        # R9: reject stale seeds (epoch staleness check)
        if seeds is not None and \
                (time.time() - seeds.get("epoch", 0.0)) > self.warm_epoch_max_age:
            self.pareto_cache.warm_hits -= 1
            self.pareto_cache.warm_misses += 1
            seeds = None
        g_max = self.g_max
        if seeds is not None:
            g_max = self.g_warm
            result.warm_start_used = True
            logger.debug(f"Warm-start: reducing generations {self.g_max}->{g_max}")

        # ═══ Phase 1: Region-Level Corridor Search (R4) ═══
        src_region = self.node_to_region.get(self.node_id, "R0")
        dst_region = self.node_to_region.get(destination, "R0")

        # Region aggregates (TTL-cached) and precomputed region graph
        region_aggs = self._get_region_aggregates()
        region_graph = self.region_graph

        # Evolve region corridors
        corridors = self._evolve_corridors(
            src_region, dst_region, region_graph, region_aggs,
            qos, g_max, seeds
        )
        result.corridors_explored = len(corridors)

        # ═══ Phase 2: Per-Segment Refinement (R4) ═══
        # Hoisted per-call state (perf): BFS distances, props snapshot,
        # path-objective memo shared across corridors
        dist_to_dst = self._bfs_distances(destination)
        props_snap = self._get_props_snapshot()
        eval_memo: dict[tuple, ObjectiveVector] = {}

        # Warm-start: reuse cached valid paths as seeds, halve refinement gens
        warm_paths: list[MOQRPath] = []
        g_seg = self.g_segment
        if seeds is not None:
            g_seg = max(2, self.g_segment // 2)
            for p in seeds.get("pareto_set", []):
                if self._path_valid(p.nodes, destination):
                    warm_paths.append(
                        MOQRPath(nodes=list(p.nodes), corridor=p.corridor)
                    )

        all_paths: list[MOQRPath] = []
        for corridor in corridors[:self.k_corridors]:
            segment_paths = self._refine_corridor(
                corridor, qos, destination, region_aggs,
                dist_to_dst, eval_memo, warm_paths, g_seg, props_snap
            )
            all_paths.extend(segment_paths)
            result.segments_refined += len(corridor)

        # ═══ Phase 3: Interval-Robust Selection (R10) ═══
        # Keep only valid source→destination paths
        all_paths = [p for p in all_paths if self._path_valid(p.nodes, destination)]
        if not all_paths:
            # Fallback: BFS-guided greedy path
            gp = self._greedy_path(destination, qos, dist_to_dst, props_snap)
            all_paths = [gp]

        # Evaluate remaining unevaluated paths with current cache state
        for p in all_paths:
            if p.objectives.delay_ms == 0.0:
                key = tuple(p.nodes)
                cached = eval_memo.get(key)
                if cached is None:
                    cached = evaluate_path_objectives(
                        p.nodes, self.topology, self.cache, qos, props_snap
                    )
                    eval_memo[key] = cached
                    self.total_evaluations += 1
                p.objectives = cached

        # Robust Pareto set
        pareto = robust_pareto_set(
            all_paths, self.r_samples, self.epsilon_r, self.rho_min
        )
        result.pareto_set = pareto
        result.pareto_size = len(pareto)

        # TOPSIS selection
        weights = list(qos.task_weights.values())
        selected = topsis_select(pareto, weights)
        result.selected_path = selected

        # ═══ Phase 4: Cache & Return (R9) ═══
        self.pareto_cache.store(qos, pareto, all_paths, time.time())
        self.total_optimizations += 1
        result.total_evaluations = self.total_evaluations
        result.optimization_latency_ms = (time.time() - t0) * 1000.0

        return result

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _build_region_graph(self) -> dict[str, list[str]]:
        """Build adjacency graph between regions."""
        graph: dict[str, set[str]] = {r: set() for r in self.regions}
        for nid, neighbors in self.topology.items():
            r1 = self.node_to_region.get(nid)
            if r1 is None:
                continue
            for nb in neighbors:
                r2 = self.node_to_region.get(nb)
                if r2 and r2 != r1:
                    graph[r1].add(r2)
                    graph[r2].add(r1)
        return {r: list(nbs) for r, nbs in graph.items()}

    def _evolve_corridors(
        self, src_r: str, dst_r: str,
        region_graph: dict[str, list[str]],
        region_aggs: dict[str, RegionAggregate],
        qos: QoSRequirement,
        g_max: int,
        seeds: Optional[dict]
    ) -> list[list[str]]:
        """Evolve region-level corridors using EA."""
        # Generate initial population: BFS-based diverse paths
        population = self._generate_region_paths(src_r, dst_r, region_graph)

        # Add warm-start corridors
        if seeds:
            for p in seeds.get("pareto_set", [])[:5]:
                if p.corridor:
                    population.append(p.corridor)

        if not population:
            population = [[src_r, dst_r]]

        # Evaluate corridors by aggregate objectives
        def eval_corridor(corr: list[str]) -> float:
            total_delay = sum(region_aggs.get(r, RegionAggregate(r, [])).min_delay_ms
                            for r in corr)
            total_bw = min((region_aggs.get(r, RegionAggregate(r, [])).aggregate_bw_mbps
                          for r in corr), default=0.0)
            total_flops = min((region_aggs.get(r, RegionAggregate(r, [])).max_flops
                             for r in corr), default=0.0)
            # Multi-objective score (weighted sum for corridor-level)
            w = qos.task_weights
            score = (
                -w.get("delay", 0.3) * total_delay / max(qos.max_delay_ms, 1) +
                w.get("bandwidth", 0.2) * total_bw / max(qos.min_bandwidth_mbps, 1) +
                w.get("compute", 0.2) * total_flops / max(qos.min_compute_flops, 1) +
                w.get("lifetime", 0.2) * min(
                    (region_aggs.get(r, RegionAggregate(r, [])).min_lifetime_s
                     for r in corr), default=60) / max(qos.min_lifetime_s, 1)
            )
            return score

        # Evolutionary loop
        for gen in range(g_max):
            scored = [(eval_corridor(c), c) for c in population]
            scored.sort(reverse=True)
            # Selection: keep top half
            survivors = [c for _, c in scored[:max(len(scored)//2, 2)]]
            # Crossover
            offspring = []
            for _ in range(len(survivors)):
                if len(survivors) >= 2:
                    p1, p2 = random.sample(survivors, 2)
                    child = self._corridor_crossover(p1, p2, region_graph)
                    offspring.append(child)
            # Mutation
            for c in offspring:
                if random.random() < 0.2:
                    self._corridor_mutate(c, region_graph)
            population = survivors + offspring

        # Return top-k corridors
        scored_final = [(eval_corridor(c), c) for c in population]
        scored_final.sort(reverse=True)
        return [c for _, c in scored_final[:self.k_corridors]]

    def _generate_region_paths(
        self, src: str, dst: str, graph: dict[str, list[str]], max_paths: int = 10
    ) -> list[list[str]]:
        """BFS-based diverse region path generation."""
        if src == dst:
            return [[src]]
        paths = []
        queue = [[src]]
        visited_paths = set()
        max_depth = min(len(self.regions), 6)

        while queue and len(paths) < max_paths:
            path = queue.pop(0)
            if len(path) > max_depth:
                continue
            current = path[-1]
            if current == dst:
                key = tuple(path)
                if key not in visited_paths:
                    visited_paths.add(key)
                    paths.append(path)
                continue
            for nb in graph.get(current, []):
                if nb not in path:
                    queue.append(path + [nb])
        return paths if paths else [[src, dst]]

    def _corridor_crossover(
        self, p1: list[str], p2: list[str], graph: dict[str, list[str]]
    ) -> list[str]:
        """Crossover two corridors at a common region."""
        common = set(p1) & set(p2)
        if len(common) <= 2:  # only src/dst shared
            return p1[:]
        common_middle = [r for r in common if r != p1[0] and r != p1[-1]]
        if not common_middle:
            return p1[:]
        xpoint = random.choice(common_middle)
        i1 = p1.index(xpoint)
        i2 = p2.index(xpoint)
        child = p1[:i1] + p2[i2:]
        return child

    def _corridor_mutate(self, corridor: list[str], graph: dict[str, list[str]]):
        """Mutate a corridor by replacing a random intermediate region."""
        if len(corridor) <= 2:
            return
        idx = random.randint(1, len(corridor) - 2)
        prev_r = corridor[idx - 1]
        next_r = corridor[idx + 1] if idx + 1 < len(corridor) else None
        candidates = [r for r in graph.get(prev_r, [])
                     if r not in corridor or r == next_r]
        if candidates:
            corridor[idx] = random.choice(candidates)

    def _get_region_aggregates(self) -> dict[str, RegionAggregate]:
        """TTL-cached region aggregates (rebuilt at most once per TTL)."""
        now = time.time()
        if self._region_aggs is None or \
                (now - self._region_aggs_ts) > self._region_aggs_ttl:
            self._region_aggs = build_region_aggregates(self.regions, self.cache)
            self._region_aggs_ts = now
        return self._region_aggs

    def _get_props_snapshot(self) -> dict[str, dict]:
        """TTL-cached one-pass node-props snapshot (perf hot-path helper)."""
        now = time.time()
        if self._props_snap is None or \
                (now - self._props_snap_ts) > self._region_aggs_ttl:
            self._props_snap = self.cache.snapshot_individuals()
            self._props_snap_ts = now
        return self._props_snap

    def _bfs_distances(self, target: str) -> dict[str, int]:
        """Hop distances from every node to target (BFS from target)."""
        dist = {target: 0}
        queue = deque([target])
        while queue:
            cur = queue.popleft()
            for nb in self.topology.get(cur, []):
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    queue.append(nb)
        return dist

    def _path_valid(self, nodes: list[str], destination: str) -> bool:
        """A path is valid iff anchored at src/dst, loop-free, hop-connected,
        and within H_max hops."""
        if not nodes or nodes[0] != self.node_id or nodes[-1] != destination:
            return False
        if len(nodes) > self.h_max + 1:
            return False
        if len(set(nodes)) != len(nodes):
            return False
        for i in range(len(nodes) - 1):
            if nodes[i + 1] not in self.topology.get(nodes[i], []):
                return False
        return True

    def _random_corridor_path(
        self, destination: str, corridor_set: set[str],
        dist_to_dst: dict[str, int], qos: QoSRequirement,
        props_snap: dict[str, dict]
    ) -> Optional[list[str]]:
        """Randomized destination-guided walk biased toward corridor regions
        and ontology compute capability. Always anchored src→dst or None."""
        nodes = [self.node_id]
        current = self.node_id
        visited = {current}
        for _ in range(self.h_max):
            if current == destination:
                return nodes
            nbs = [n for n in self.topology.get(current, [])
                   if n not in visited and n in dist_to_dst]
            if not nbs:
                return None
            cur_d = dist_to_dst.get(current, len(self.topology))
            closer = [n for n in nbs if dist_to_dst[n] < cur_d]
            # 80% prefer progress toward destination, 20% explore
            pool = closer if closer and random.random() < 0.8 else nbs
            weights = []
            for n in pool:
                w = 1.0
                if self.node_to_region.get(n) in corridor_set:
                    w *= 2.0  # corridor bias (R4)
                f = props_snap.get(n, {}).get("hasAvailableFLOPS")
                if f:
                    w *= 1.0 + min(float(f.value), 50.0) / 50.0  # ontology bias
                weights.append(w)
            nxt = random.choices(pool, weights=weights, k=1)[0]
            nodes.append(nxt)
            visited.add(nxt)
            current = nxt
        return nodes if current == destination else None

    def _refine_corridor(
        self, corridor: list[str], qos: QoSRequirement,
        destination: str, region_aggs: dict[str, RegionAggregate],
        dist_to_dst: dict[str, int],
        eval_memo: dict[tuple, ObjectiveVector],
        warm_paths: list[MOQRPath],
        g_seg: int,
        props_snap: dict[str, dict],
    ) -> list[MOQRPath]:
        """Level 2: refine a corridor into concrete src→dst paths.

        Seeds anchored destination-guided walks within the corridor, then
        runs NSGA-III with ontology operators (R11/R12) per Algorithm 4′.
        """
        if not corridor:
            return []
        corridor_set = set(corridor)
        if self.node_id not in dist_to_dst:
            return []  # destination unreachable

        # Seed population: warm-start paths first, then anchored random walks
        seeds: list[MOQRPath] = []
        seen: set[tuple] = set()
        for wp in warm_paths:
            key = tuple(wp.nodes)
            if key not in seen:
                seen.add(key)
                seeds.append(MOQRPath(nodes=list(wp.nodes), corridor=corridor))
        for _ in range(self.n_pop_segment * 3):
            if len(seeds) >= self.n_pop_segment:
                break
            nodes = self._random_corridor_path(
                destination, corridor_set, dist_to_dst, qos, props_snap
            )
            if nodes and self._path_valid(nodes, destination):
                key = tuple(nodes)
                if key not in seen:
                    seen.add(key)
                    seeds.append(MOQRPath(nodes=nodes, corridor=corridor))
        if not seeds:
            gp = self._greedy_path(destination, qos, dist_to_dst, props_snap)
            if not self._path_valid(gp.nodes, destination):
                return []
            gp.corridor = corridor
            seeds = [gp]

        # NSGA-III refinement with ontology operators (R11/R12)
        nsga_cfg = NSGAConfig(
            n_pop=self.n_pop_segment, g_max=g_seg,
            eta_bias=self.eta_bias
        )
        weights = list(qos.task_weights.values())

        def _eval(ind: MOQRPath) -> ObjectiveVector:
            key = tuple(ind.nodes)
            cached = eval_memo.get(key)
            if cached is None:
                self.total_evaluations += 1
                cached = evaluate_path_objectives(
                    ind.nodes, self.topology, self.cache, qos, props_snap
                )
                eval_memo[key] = cached
            return cached

        def _cross(a: MOQRPath, b: MOQRPath) -> MOQRPath:
            child = ontology_crossover(a, b, self.topology, self.cache)
            child.corridor = corridor
            return child

        def _mut(ind: MOQRPath) -> MOQRPath:
            mutated = ontology_mutation(ind, self.topology, self.cache)
            mutated.corridor = corridor
            return mutated

        def _repair(ind: MOQRPath) -> MOQRPath:
            ind.nodes = axiom_repair_connected(
                ind.nodes, self.topology, self.cache, self.h_max
            )
            return ind

        final_pop = evolve_moo(
            seeds, _eval, _cross, _mut, _repair, nsga_cfg, weights
        )

        # Keep only valid anchored paths; fall back to the seeds otherwise
        valid = [p for p in final_pop if self._path_valid(p.nodes, destination)]
        return valid if valid else seeds

    def _greedy_path(
        self, destination: str, qos: QoSRequirement,
        dist_to_dst: Optional[dict[str, int]] = None,
        props_snap: Optional[dict[str, dict]] = None
    ) -> MOQRPath:
        """Fallback: BFS-distance-guided greedy path with ontology bias."""
        if dist_to_dst is None:
            dist_to_dst = self._bfs_distances(destination)
        if props_snap is None:
            props_snap = self._get_props_snapshot()
        path = [self.node_id]
        current = self.node_id
        visited = {current}
        for _ in range(self.h_max):
            if current == destination:
                break
            nbs = [n for n in self.topology.get(current, [])
                   if n not in visited and n in dist_to_dst]
            if not nbs:
                break

            def _rank(n: str):
                f = props_snap.get(n, {}).get("hasAvailableFLOPS")
                flops = float(f.value) if f else 0.0
                # Primary: closer to destination; secondary: better compute
                return (dist_to_dst[n], -flops)

            best_nb = min(nbs, key=_rank)
            path.append(best_nb)
            visited.add(best_nb)
            current = best_nb
        return MOQRPath(nodes=path)
