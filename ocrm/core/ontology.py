"""Space Network Ontology (SNO) — §3.2 of the paper.

O_SNO = <C, P_data, P_obj, A, I>
Restricted to OWL 2 RL-like forward chaining (polynomial), axiom depth <= 3.
Lightweight implementation: concept hierarchy as a tree, axioms as Python
predicates with declared category/depth, instances as typed KV records.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Concept hierarchy (C)
# ---------------------------------------------------------------------------

CONCEPT_TREE: dict[str, Optional[str]] = {
    # concept                  parent
    "SpaceNetworkEntity": None,
    "NetworkNode": "SpaceNetworkEntity",
    "CommunicationNode": "NetworkNode",
    "ComputingNode": "NetworkNode",
    "CPUComputeNode": "ComputingNode",
    "GPUComputeNode": "ComputingNode",
    "HighEndGPUComputeNode": "GPUComputeNode",
    "FPGAComputeNode": "ComputingNode",
    "StorageNode": "NetworkNode",
    "HybridNode": "NetworkNode",
    "NetworkLink": "SpaceNetworkEntity",
    "LaserISL": "NetworkLink",
    "RF_ISL": "NetworkLink",
    "GroundLink": "NetworkLink",
    "NetworkRegion": "SpaceNetworkEntity",
    "OrbitalPlaneRegion": "NetworkRegion",
    "GeoPartitionRegion": "NetworkRegion",
}


def super_of(concept: str) -> Optional[str]:
    """Direct superclass, or None at hierarchy root."""
    return CONCEPT_TREE.get(concept)


def is_subclass_of(concept: str, ancestor: str) -> bool:
    """Reflexive-transitive subsumption check over the concept tree."""
    c: Optional[str] = concept
    while c is not None:
        if c == ancestor:
            return True
        c = CONCEPT_TREE.get(c)
    return False


def concept_depth(concept: str) -> int:
    d, c = 0, CONCEPT_TREE.get(concept)
    while c is not None:
        d += 1
        c = CONCEPT_TREE.get(c)
    return d


# ---------------------------------------------------------------------------
# Data properties (P_data) — routing-relevant subset
# ---------------------------------------------------------------------------

# property -> (domain concept, benefit|cost)  benefit: larger is better
DATA_PROPERTIES: dict[str, tuple[str, str]] = {
    "hasAvailableFLOPS": ("ComputingNode", "benefit"),
    "hasAvailableBandwidth": ("NetworkLink", "benefit"),
    "hasLatency": ("NetworkLink", "cost"),
    "hasEnergyReserve": ("NetworkNode", "benefit"),
    "hasAvailableStorage": ("StorageNode", "benefit"),
    "hasLinkLifetime": ("NetworkLink", "benefit"),
    "hasConfidence": ("SpaceNetworkEntity", "benefit"),
}


# ---------------------------------------------------------------------------
# Ontology instance (I) — one typed KV record
# Key schema (§3.2.2): /space/<constellation>/<class>/<property>/<individual>
# ---------------------------------------------------------------------------

@dataclass
class Instance:
    key: str                      # namespace key
    individual: str               # e.g. SAT-0042 or LINK-0042-0043
    concept: str                  # concept class of the individual
    prop: str                     # data property name
    value: Any
    causal_version: int = 0       # Lamport clock
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    ttl: float = 30.0
    virtual: bool = False         # R2: ephemeris-derived, never disseminated
    instance_type: str = "measured"   # measured | residual | inferred | virtual

    def expired(self, now: Optional[float] = None) -> bool:
        return ((now or time.time()) - self.timestamp) > self.ttl

    def freshness(self, now: Optional[float] = None) -> float:
        """Aging: linear decay 1.0 (just written) -> 0.0 (TTL reached).
        Virtual (ephemeris-derived) instances never age."""
        if self.virtual or self.ttl == float("inf"):
            return 1.0
        age = (now or time.time()) - self.timestamp
        if self.ttl <= 0:
            return 0.0
        return max(0.0, 1.0 - age / self.ttl)


def make_key(constellation: str, concept: str, prop: str, individual: str) -> str:
    return f"/space/{constellation}/{concept}/{prop}/{individual}"


# ---------------------------------------------------------------------------
# Axioms (A) — four categories, depth-bounded
# ---------------------------------------------------------------------------

@dataclass
class Axiom:
    name: str
    category: str        # class_guarantee | direct_constraint | causal | multi_hop_causal
    depth: int           # 1..3
    # condition(instances_by_prop) -> bool  ; instances of ONE individual
    condition: Callable[[dict[str, Instance]], bool]
    # effect(instances_by_prop) -> list of (prop, new_value) derived facts,
    # or "INVALIDATE" marker for route invalidation axioms
    effect: Callable[[dict[str, Instance]], list[tuple[str, Any]]]
    urgent: bool = False  # R6: urgent axioms trigger immediate flush


def _low_energy_cond(props: dict[str, Instance]) -> bool:
    e = props.get("hasEnergyReserve")
    return e is not None and float(e.value) < 500.0


def _low_energy_effect(props: dict[str, Instance]) -> list[tuple[str, Any]]:
    # Energy -> Compute causal chain (depth 2): low energy throttles compute
    f = props.get("hasAvailableFLOPS")
    if f is None:
        return []
    # Inferred instances share the measured key: re-applying the axiom on an
    # already-throttled value would compound the halving (0.5^n decay)
    if f.instance_type == "inferred":
        return []
    return [("hasAvailableFLOPS", float(f.value) * 0.5)]


def _link_dying_cond(props: dict[str, Instance]) -> bool:
    lt = props.get("hasLinkLifetime")
    return lt is not None and float(lt.value) < 10.0


def _link_dying_effect(props: dict[str, Instance]) -> list[tuple[str, Any]]:
    # Direct constraint: link about to die -> mark unusable (INVALIDATE)
    return [("__invalidate__", True)]


def _link_down_cond(props: dict[str, Instance]) -> bool:
    bw = props.get("hasAvailableBandwidth")
    return bw is not None and float(bw.value) <= 0.0


def _link_down_effect(props: dict[str, Instance]) -> list[tuple[str, Any]]:
    return [("__invalidate__", True)]


def _gpu_guarantee_cond(props: dict[str, Instance]) -> bool:
    f = props.get("hasAvailableFLOPS")
    return f is not None and float(f.value) >= 10.0


def _gpu_guarantee_effect(props: dict[str, Instance]) -> list[tuple[str, Any]]:
    # Class guarantee: qualifies as HighEndGPUComputeNode
    return [("__classify__", "HighEndGPUComputeNode")]


AXIOMS: list[Axiom] = [
    Axiom("LinkDown->Invalidate", "direct_constraint", 1,
          _link_down_cond, _link_down_effect, urgent=True),
    Axiom("LinkDying->Invalidate", "direct_constraint", 1,
          _link_dying_cond, _link_dying_effect, urgent=True),
    Axiom("LowEnergy->ThrottleCompute", "causal", 2,
          _low_energy_cond, _low_energy_effect),
    Axiom("HighFLOPS->HighEndGPU", "class_guarantee", 1,
          _gpu_guarantee_cond, _gpu_guarantee_effect),
]

D_MAX = 3  # global axiom chain depth bound
