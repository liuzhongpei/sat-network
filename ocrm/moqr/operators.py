"""Ontology-Guided Genetic Operators — §3.11.4.

Implements:
  - ONTOLOGY_CROSSOVER: class-compatible crossover (unchanged from v1)
  - ONTOLOGY_MUTATION: swap with ontology-aware neighbor selection
  - AXIOM_REPAIR_CONNECTED (R12): connectivity-preserving axiom-driven repair
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import MOQRPath

from ..core.ontology import is_subclass_of, AXIOMS, D_MAX
from ..core.kv_cache import KVCache


def ontology_crossover(
    parent_a: "MOQRPath",
    parent_b: "MOQRPath",
    topology: dict[str, list[str]],
    cache: KVCache,
) -> "MOQRPath":
    """Ontology-guided crossover (§3.11.4 Algorithm).

    Steps:
      1. Identify nodes in π_a and π_b sharing ontology class
      2. Select crossover point at class-compatible node
      3. Swap sub-paths after crossover point
      4. Verify junction feasibility via axiom check
      5. If infeasible: insert shortest axiom-consistent bridge from KV-Cache
    """
    from . import MOQRPath

    pa = parent_a.nodes
    pb = parent_b.nodes

    if len(pa) < 2 or len(pb) < 2:
        return MOQRPath(nodes=pa[:], corridor=parent_a.corridor)

    # Step 1: Find class-compatible crossover points
    compatible_pairs = []
    for i, na in enumerate(pa[1:-1], 1):
        for j, nb in enumerate(pb[1:-1], 1):
            if na == nb:
                compatible_pairs.append((i, j))
            else:
                # Check ontology class compatibility
                props_a = cache.get_by_individual(na)
                props_b = cache.get_by_individual(nb)
                class_a = _get_concept(props_a)
                class_b = _get_concept(props_b)
                if class_a and class_b and (
                    is_subclass_of(class_a, class_b) or
                    is_subclass_of(class_b, class_a)
                ):
                    compatible_pairs.append((i, j))

    if not compatible_pairs:
        # No compatible crossover point; return parent_a copy
        return MOQRPath(nodes=pa[:], corridor=parent_a.corridor)

    # Step 2: Select crossover point
    i, j = random.choice(compatible_pairs)

    # Step 3: Swap sub-paths
    child_nodes = pa[:i] + pb[j:]

    # Step 4: Verify junction feasibility
    if i > 0 and j < len(pb):
        junction_src = pa[i - 1]
        junction_dst = pb[j]
        if junction_dst not in topology.get(junction_src, []):
            # Step 5: Insert bridge (intermediate nodes only)
            bridge = _find_bridge(junction_src, junction_dst, topology, cache)
            if bridge is not None:
                child_nodes = pa[:i] + bridge + pb[j:]
            else:
                return MOQRPath(nodes=pa[:], corridor=parent_a.corridor)

    # Remove duplicates while preserving order
    seen = set()
    deduped = []
    for n in child_nodes:
        if n not in seen:
            seen.add(n)
            deduped.append(n)

    return MOQRPath(nodes=deduped, corridor=parent_a.corridor)


def ontology_mutation(
    individual: "MOQRPath",
    topology: dict[str, list[str]],
    cache: KVCache,
    mutation_strength: float = 0.3,
) -> "MOQRPath":
    """Ontology-aware mutation: replace a random intermediate node.

    Selects replacement based on ontology class compatibility and
    connectivity constraints.
    """
    from . import MOQRPath

    nodes = individual.nodes[:]
    if len(nodes) <= 2:
        return MOQRPath(nodes=nodes, corridor=individual.corridor)

    # Select mutation point (not src/dst)
    mut_idx = random.randint(1, len(nodes) - 2)
    current_node = nodes[mut_idx]

    # Get current node's class
    props = cache.get_by_individual(current_node)
    current_class = _get_concept(props)

    # Find replacement candidates: neighbors of adjacent nodes
    prev_node = nodes[mut_idx - 1]
    next_node = nodes[mut_idx + 1] if mut_idx + 1 < len(nodes) else None

    candidates = set(topology.get(prev_node, []))
    if next_node:
        # Must be reachable from both prev and to next
        next_reachable = set(topology.get(next_node, []))
        # Prefer nodes connected to both
        direct_candidates = candidates & next_reachable
        if direct_candidates:
            candidates = direct_candidates

    # Filter by ontology class compatibility
    valid_candidates = []
    for cand in candidates:
        if cand in nodes:  # avoid loops
            continue
        cand_props = cache.get_by_individual(cand)
        cand_class = _get_concept(cand_props)
        if current_class and cand_class:
            if is_subclass_of(cand_class, current_class) or \
               is_subclass_of(current_class, cand_class):
                valid_candidates.append(cand)
        else:
            valid_candidates.append(cand)

    if valid_candidates:
        replacement = random.choice(valid_candidates)
        nodes[mut_idx] = replacement

    return MOQRPath(nodes=nodes, corridor=individual.corridor)


def axiom_repair_connected(
    path: list[str],
    topology: dict[str, list[str]],
    cache: KVCache,
    h_max: int = 10,
) -> list[str]:
    """Connectivity-Preserving Axiom-Driven Repair (R12, §3.11.4).

    For each disconnected pair in the path:
      1. Trace axiom chain to identify the offending node
      2. Find class-compatible alternative from cache
      3. Replace and verify connectivity; insert bridge if needed
      4. Discard if path exceeds H_max
    """
    if len(path) < 2:
        return path

    repaired = path[:]
    max_repair_attempts = 5

    for attempt in range(max_repair_attempts):
        # Find first disconnection
        disconnection_idx = -1
        for i in range(len(repaired) - 1):
            src = repaired[i]
            dst = repaired[i + 1]
            if dst not in topology.get(src, []):
                disconnection_idx = i
                break

        if disconnection_idx < 0:
            break  # Path is connected

        # Try to bridge the disconnection
        src = repaired[disconnection_idx]
        dst = repaired[disconnection_idx + 1]

        bridge = _find_bridge(src, dst, topology, cache, max_depth=3)
        if bridge is not None:
            repaired = repaired[:disconnection_idx + 1] + bridge + repaired[disconnection_idx + 1:]
        else:
            # Cannot bridge — try replacing the offending node
            # Check if src has axiom violations
            props = cache.get_by_individual(src)
            violated = False
            for axiom in AXIOMS:
                if axiom.depth <= D_MAX and axiom.condition(props):
                    effects = axiom.effect(props)
                    if any(p == "__invalidate__" for p, _ in effects):
                        violated = True
                        break

            if violated and disconnection_idx > 0:
                # Replace with alternative connected to predecessor
                prev = repaired[disconnection_idx - 1]
                alternatives = [n for n in topology.get(prev, [])
                              if n not in repaired and n != src]
                if alternatives:
                    # Pick one connected to dst or nearest
                    best = alternatives[0]
                    for alt in alternatives:
                        if dst in topology.get(alt, []):
                            best = alt
                            break
                    repaired[disconnection_idx] = best
                else:
                    # Remove the disconnected segment
                    repaired = repaired[:disconnection_idx + 1]
                    break
            else:
                # Just remove the unreachable node
                repaired.pop(disconnection_idx + 1)

        # Enforce H_max
        if len(repaired) > h_max:
            repaired = repaired[:h_max]
            break

    return repaired


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_concept(props: dict) -> str | None:
    """Extract concept class from instance properties."""
    for inst in props.values():
        if hasattr(inst, "concept"):
            return inst.concept
    return None


def _find_bridge(
    src: str, dst: str,
    topology: dict[str, list[str]],
    cache: KVCache,
    max_depth: int = 3
) -> list[str] | None:
    """BFS shortest bridge between src and dst.

    Returns the list of INTERMEDIATE nodes only (dst excluded), so callers
    can splice it between src and dst without duplicating endpoints.
    Returns None if no bridge exists within max_depth.
    """
    if dst in topology.get(src, []):
        return []  # Already connected — no intermediates needed

    visited = {src}
    queue = [(src, [])]

    for _ in range(max_depth * len(topology)):
        if not queue:
            break
        current, path = queue.pop(0)
        for nb in topology.get(current, []):
            if nb == dst:
                return path  # intermediates only
            if nb not in visited and len(path) < max_depth:
                visited.add(nb)
                queue.append((nb, path + [nb]))

    return None
