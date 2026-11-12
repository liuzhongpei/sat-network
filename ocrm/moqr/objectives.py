"""Multi-Objective QoS Routing Objectives — §3.11.1.

Five objectives aligned with the paper's formulation:
  f1: End-to-end delay (minimize)
  f2: Bottleneck bandwidth (maximize → negate to minimize)
  f3: Compute match ratio (maximize → negate)
  f4: Path lifetime (maximize → negate)
  f5: Energy consumption (minimize)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..core.kv_cache import KVCache


@dataclass
class ObjectiveVector:
    """5-dimensional objective vector F(π)."""
    delay_ms: float = 0.0          # f1: sum of edge delays (minimize)
    neg_bandwidth_mbps: float = 0.0  # f2: -min bottleneck BW (minimize = max BW)
    neg_compute_ratio: float = 0.0   # f3: -min(C_k/C_req) (minimize = max ratio)
    neg_lifetime_s: float = 0.0      # f4: -min link lifetime (minimize = max lifetime)
    energy_j: float = 0.0           # f5: sum of node energy cost (minimize)

    def to_list(self) -> list[float]:
        return [self.delay_ms, self.neg_bandwidth_mbps,
                self.neg_compute_ratio, self.neg_lifetime_s, self.energy_j]

    @staticmethod
    def from_list(vals: list[float]) -> "ObjectiveVector":
        return ObjectiveVector(
            delay_ms=vals[0], neg_bandwidth_mbps=vals[1],
            neg_compute_ratio=vals[2], neg_lifetime_s=vals[3],
            energy_j=vals[4]
        )

    def dominates(self, other: "ObjectiveVector") -> bool:
        """Standard Pareto dominance: self dominates other if all <= and at least one <."""
        s = self.to_list()
        o = other.to_list()
        at_least_one_strict = False
        for si, oi in zip(s, o):
            if si > oi:
                return False
            if si < oi:
                at_least_one_strict = True
        return at_least_one_strict


@dataclass
class QoSObjectives:
    """Configuration for objective evaluation."""
    c_req_flops: float = 5.0     # required FLOPS for compute ratio
    prediction_horizon_s: float = 60.0
    confidence_decay: float = 0.95  # per-sample confidence decay


def evaluate_path_objectives(
    path: list[str],
    topology: dict[str, list[str]],
    cache: KVCache,
    qos=None,
    props_snapshot: Optional[dict[str, dict]] = None,
) -> ObjectiveVector:
    """Evaluate the 5-objective vector for a concrete path.

    Uses current KV-Cache instances for link and node properties.
    props_snapshot (individual -> {prop: Instance}) avoids repeated
    full-store scans in hot loops.
    """
    if len(path) < 2:
        return ObjectiveVector()

    c_req = qos.min_compute_flops if qos else 5.0
    constellation = getattr(cache, "constellation", "ALPHA")

    total_delay = 0.0
    min_bw = float("inf")
    min_compute_ratio = float("inf")
    min_lifetime = float("inf")
    total_energy = 0.0

    # Edge objectives (f1, f2, f4)
    for i in range(len(path) - 1):
        src, dst = path[i], path[i + 1]
        link_id = f"LINK-{src}-{dst}"
        alt_link_id = f"LINK-{dst}-{src}"

        # Delay: check cache or use default
        delay_inst = cache.get(
            f"/space/{constellation}/LaserISL/hasLatency/{link_id}"
        ) or cache.get(
            f"/space/{constellation}/LaserISL/hasLatency/{alt_link_id}"
        )
        if delay_inst:
            total_delay += float(delay_inst.value)
        else:
            # Estimate: intra-plane 3.7ms, inter-plane 5.2ms
            total_delay += 3.7 if src[:6] == dst[:6] else 5.2

        # Bandwidth
        bw_inst = cache.get(
            f"/space/{constellation}/LaserISL/hasAvailableBandwidth/{link_id}"
        ) or cache.get(
            f"/space/{constellation}/LaserISL/hasAvailableBandwidth/{alt_link_id}"
        )
        if bw_inst:
            bw_val = float(bw_inst.value)
            min_bw = min(min_bw, bw_val)
        else:
            min_bw = min(min_bw, 5000.0)  # default 5Gbps = 5000Mbps

        # Link lifetime
        lt_inst = cache.get(
            f"/space/{constellation}/LaserISL/hasLinkLifetime/{link_id}"
        ) or cache.get(
            f"/space/{constellation}/LaserISL/hasLinkLifetime/{alt_link_id}"
        )
        if lt_inst:
            min_lifetime = min(min_lifetime, float(lt_inst.value))
        else:
            min_lifetime = min(min_lifetime, 120.0)

    # Node objectives (f3, f5)
    for nid in path:
        if props_snapshot is not None:
            props = props_snapshot.get(nid, {})
        else:
            props = cache.get_by_individual(nid)
        # Compute match ratio
        flops_inst = props.get("hasAvailableFLOPS")
        if flops_inst:
            ratio = float(flops_inst.value) / max(c_req, 0.01)
            min_compute_ratio = min(min_compute_ratio, ratio)
        else:
            min_compute_ratio = min(min_compute_ratio, 1.0)

        # Energy
        energy_inst = props.get("hasEnergyReserve")
        if energy_inst:
            # Energy cost ∝ 1/reserve (higher reserve = lower cost)
            reserve = float(energy_inst.value)
            total_energy += max(1000.0 / max(reserve, 1.0), 0.1)
        else:
            total_energy += 1.0  # default cost

    if min_bw == float("inf"):
        min_bw = 5000.0
    if min_lifetime == float("inf"):
        min_lifetime = 120.0
    if min_compute_ratio == float("inf"):
        min_compute_ratio = 1.0

    return ObjectiveVector(
        delay_ms=total_delay,
        neg_bandwidth_mbps=-min_bw,
        neg_compute_ratio=-min_compute_ratio,
        neg_lifetime_s=-min_lifetime,
        energy_j=total_energy,
    )


def evaluate_path_sampled(
    path: list[str],
    topology: dict[str, list[str]],
    cache: KVCache,
    qos=None,
    time_samples: Optional[list[float]] = None,
    r_samples: int = 3,
) -> list[tuple[ObjectiveVector, float]]:
    """Interval-sampled evaluation (R10) — evaluate at multiple time points.

    Returns list of (ObjectiveVector, confidence) pairs.
    """
    if time_samples is None:
        horizon = 60.0 if qos is None else getattr(qos, "min_lifetime_s", 60.0)
        time_samples = [i * horizon / max(r_samples - 1, 1) for i in range(r_samples)]

    results = []
    base_obj = evaluate_path_objectives(path, topology, cache, qos)
    confidence_decay = 0.95

    for j, t_j in enumerate(time_samples):
        # Simulate time-varying objectives with decay
        confidence = confidence_decay ** j
        # Delay increases slightly with time (orbital drift)
        obj = ObjectiveVector(
            delay_ms=base_obj.delay_ms * (1.0 + 0.02 * j),
            neg_bandwidth_mbps=base_obj.neg_bandwidth_mbps * (1.0 - 0.01 * j),
            neg_compute_ratio=base_obj.neg_compute_ratio,
            neg_lifetime_s=base_obj.neg_lifetime_s + t_j,  # lifetime decreases
            energy_j=base_obj.energy_j * (1.0 + 0.03 * j),
        )
        results.append((obj, confidence))

    return results
