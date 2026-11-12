"""Metrics collection framework for OCRM evaluation.

Collects the key metrics from the paper (§5):
1. Global Convergence Time
2. Path Stability Index (PSI)
3. Resource Utilization (compute, task rejection, routing overhead)
4. KV-Cache Performance
5. Local reasoning ratio
"""
from __future__ import annotations

import time
import json
import statistics
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConvergenceEvent:
    """A single routing convergence measurement."""
    trigger_time: float        # when the topology change occurred
    converge_time: float       # when all affected routes stabilized
    change_type: str           # "link_down" | "link_up" | "handover"
    affected_routes: int = 0
    local_resolved: int = 0    # resolved via local cache-reasoning

    @property
    def latency_ms(self) -> float:
        return (self.converge_time - self.trigger_time) * 1000.0


@dataclass
class PathRecord:
    """Track a single path's lifetime."""
    src: str
    dst: str
    path: list[str]
    start_time: float
    end_time: Optional[float] = None
    reason_ended: str = ""     # "reroute" | "expired" | "still_active"

    @property
    def duration(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time


@dataclass
class ResourceSnapshot:
    """Periodic resource utilization snapshot."""
    timestamp: float
    node_id: str
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    routing_overhead_bps: float = 0.0
    compute_utilization: float = 0.0


@dataclass
class MOQRMetric:
    """Single MOQR optimization measurement."""
    timestamp: float
    pareto_size: int = 0
    optimization_latency_ms: float = 0.0
    warm_start_used: bool = False
    corridors_explored: int = 0
    segments_refined: int = 0
    selected_delay_ms: float = 0.0
    selected_bandwidth_mbps: float = 0.0
    selected_compute_ratio: float = 0.0
    selected_lifetime_s: float = 0.0
    selected_energy_j: float = 0.0
    topsis_score: float = 0.0
    qos_satisfied: bool = False


class MetricsCollector:
    """Centralized metrics collection and reporting."""

    def __init__(self, sim_duration_s: float = 300.0):
        self.convergence_events: list[ConvergenceEvent] = []
        self.path_records: list[PathRecord] = []
        self.resource_snapshots: list[ResourceSnapshot] = []
        self.kv_stats: dict[str, dict] = {}  # node_id -> cache stats
        self.moqr_metrics: list[MOQRMetric] = []  # MOQR optimization records
        self._start_time: float = time.time()
        self._sim_duration: float = sim_duration_s

    # ------------------------------------------------------------------
    # Convergence metrics (§5.2)
    # ------------------------------------------------------------------
    def record_convergence(self, event: ConvergenceEvent):
        self.convergence_events.append(event)

    def avg_convergence_ms(self) -> float:
        if not self.convergence_events:
            return 0.0
        return statistics.mean(e.latency_ms for e in self.convergence_events)

    def convergence_by_rate(self, changes_per_min: float) -> float:
        """Average convergence time for events at a given rate."""
        window = 60.0 / max(changes_per_min, 1)
        relevant = [e for e in self.convergence_events
                    if e.latency_ms / 1000.0 <= window * 2]
        if not relevant:
            return 0.0
        return statistics.mean(e.latency_ms for e in relevant)

    def local_reasoning_ratio(self) -> float:
        """Fraction of route updates resolved via local KV reasoning (§5.2.3)."""
        total = sum(e.affected_routes for e in self.convergence_events)
        local = sum(e.local_resolved for e in self.convergence_events)
        if total == 0:
            return 0.0
        return local / total

    # ------------------------------------------------------------------
    # Path stability (§5.3)
    # ------------------------------------------------------------------
    def record_path(self, record: PathRecord):
        self.path_records.append(record)

    def path_stability_index(self) -> float:
        """PSI: average path duration / simulation duration (capped at 1.0)."""
        if not self.path_records:
            return 0.0
        durations = [r.duration for r in self.path_records]
        avg_dur = statistics.mean(durations)
        # Normalize against simulation duration
        return min(avg_dur / max(self._sim_duration, 1.0), 1.0)

    def avg_path_duration(self) -> float:
        if not self.path_records:
            return 0.0
        return statistics.mean(r.duration for r in self.path_records)

    def rerouting_per_hour(self) -> float:
        elapsed_h = self._sim_duration / 3600.0
        reroutes = sum(1 for r in self.path_records if r.reason_ended == "reroute")
        if elapsed_h <= 0:
            return 0.0
        return reroutes / elapsed_h

    # ------------------------------------------------------------------
    # Resource utilization (§5.4)
    # ------------------------------------------------------------------
    def record_resource(self, snapshot: ResourceSnapshot):
        self.resource_snapshots.append(snapshot)

    def avg_cpu(self) -> float:
        if not self.resource_snapshots:
            return 0.0
        return statistics.mean(s.cpu_percent for s in self.resource_snapshots)

    def avg_memory_mb(self) -> float:
        if not self.resource_snapshots:
            return 0.0
        return statistics.mean(s.memory_mb for s in self.resource_snapshots)

    def avg_routing_overhead_percent(self, link_capacity_bps: float = 10e9) -> float:
        """Routing overhead as fraction of link capacity."""
        if not self.resource_snapshots or link_capacity_bps <= 0:
            return 0.0
        avg_bps = statistics.mean(s.routing_overhead_bps for s in self.resource_snapshots)
        return (avg_bps / link_capacity_bps) * 100.0

    # ------------------------------------------------------------------
    # KV-Cache metrics (§5.6)
    # ------------------------------------------------------------------
    def update_kv_stats(self, node_id: str, stats: dict):
        self.kv_stats[node_id] = stats

    def avg_kv_hit_ratio(self) -> float:
        if not self.kv_stats:
            return 0.0
        ratios = [s.get("hit_ratio", 0.0) for s in self.kv_stats.values()]
        return statistics.mean(ratios)

    # ------------------------------------------------------------------
    # MOQR metrics (§5.7 — Multi-Objective QoS Routing)
    # ------------------------------------------------------------------
    def record_moqr(self, metric: MOQRMetric):
        self.moqr_metrics.append(metric)

    def avg_moqr_latency_ms(self) -> float:
        if not self.moqr_metrics:
            return 0.0
        return statistics.mean(m.optimization_latency_ms for m in self.moqr_metrics)

    def avg_pareto_size(self) -> float:
        if not self.moqr_metrics:
            return 0.0
        return statistics.mean(m.pareto_size for m in self.moqr_metrics)

    def moqr_warm_start_ratio(self) -> float:
        if not self.moqr_metrics:
            return 0.0
        warm = sum(1 for m in self.moqr_metrics if m.warm_start_used)
        return warm / len(self.moqr_metrics)

    def qos_satisfaction_rate(self) -> float:
        if not self.moqr_metrics:
            return 0.0
        sat = sum(1 for m in self.moqr_metrics if m.qos_satisfied)
        return sat / len(self.moqr_metrics)

    def avg_topsis_score(self) -> float:
        if not self.moqr_metrics:
            return 0.0
        return statistics.mean(m.topsis_score for m in self.moqr_metrics)

    def moqr_objective_summary(self) -> dict:
        """Average selected path objectives across all MOQR optimizations."""
        if not self.moqr_metrics:
            return {}
        return {
            "avg_delay_ms": round(statistics.mean(
                m.selected_delay_ms for m in self.moqr_metrics), 2),
            "avg_bandwidth_mbps": round(statistics.mean(
                m.selected_bandwidth_mbps for m in self.moqr_metrics), 1),
            "avg_compute_ratio": round(statistics.mean(
                m.selected_compute_ratio for m in self.moqr_metrics), 3),
            "avg_lifetime_s": round(statistics.mean(
                m.selected_lifetime_s for m in self.moqr_metrics), 1),
            "avg_energy_j": round(statistics.mean(
                m.selected_energy_j for m in self.moqr_metrics), 2),
        }

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------
    def generate_report(self) -> dict:
        """Generate full metrics report (paper Table format)."""
        report = {
            "experiment_duration_s": self._sim_duration,
            "convergence": {
                "avg_ms": round(self.avg_convergence_ms(), 1),
                "events": len(self.convergence_events),
                "local_reasoning_ratio": round(self.local_reasoning_ratio(), 3),
            },
            "path_stability": {
                "PSI": round(self.path_stability_index(), 3),
                "avg_duration_s": round(self.avg_path_duration(), 1),
                "rerouting_per_hour": round(self.rerouting_per_hour(), 1),
            },
            "resource": {
                "avg_cpu_percent": round(self.avg_cpu(), 1),
                "avg_memory_mb": round(self.avg_memory_mb(), 1),
                "routing_overhead_percent": round(
                    self.avg_routing_overhead_percent(), 2),
            },
            "kv_cache": {
                "avg_hit_ratio": round(self.avg_kv_hit_ratio(), 3),
                "nodes_reporting": len(self.kv_stats),
            },
        }
        # Add MOQR section if data exists
        if self.moqr_metrics:
            report["moqr"] = {
                "optimizations": len(self.moqr_metrics),
                "avg_latency_ms": round(self.avg_moqr_latency_ms(), 2),
                "avg_pareto_size": round(self.avg_pareto_size(), 1),
                "warm_start_ratio": round(self.moqr_warm_start_ratio(), 3),
                "qos_satisfaction_rate": round(self.qos_satisfaction_rate(), 3),
                "avg_topsis_score": round(self.avg_topsis_score(), 3),
                "objectives": self.moqr_objective_summary(),
            }
        return report

    def save_report(self, filepath: str):
        """Save report as JSON."""
        report = self.generate_report()
        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)
        return report
