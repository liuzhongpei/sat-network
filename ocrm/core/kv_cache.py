"""Distributed KV-Cache — §3.3 of the paper.

Each node maintains a local KV-Cache storing ontology instances.
Implements: write-through with selective admission (R8), dual-class flush (R6),
ephemeris-derived virtual instances (R2), and batched reasoning fusion.
"""
from __future__ import annotations

import time
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

from .ontology import (
    Instance, Axiom, AXIOMS, D_MAX,
    is_subclass_of, DATA_PROPERTIES, make_key
)


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    admissions: int = 0
    evictions: int = 0
    aged_out: int = 0          # 老化清除 (TTL 过期扫除)
    virtual_reads: int = 0
    batch_count: int = 0
    urgent_flush_count: int = 0


class KVCache:
    """Per-node KV-Cache with reasoning fusion and dual-class flush."""

    def __init__(
        self,
        node_id: str,
        max_entries: int = 5000,
        t_batch_ms: float = 50.0,
        constellation: str = "ALPHA",
    ):
        self.node_id = node_id
        self.max_entries = max_entries
        self.t_batch_ms = t_batch_ms
        self.constellation = constellation

        self._store: OrderedDict[str, Instance] = OrderedDict()
        self._virtual: dict[str, Instance] = {}  # R2: ephemeris-derived
        self._batch_buf: list[Instance] = []
        self._batch_urgent: list[Instance] = []
        self._batch_deadline: float = 0.0
        self._lock = threading.Lock()        # guards _store
        self._buf_lock = threading.Lock()    # guards batch buffers (recv thread + callers)
        self.stats = CacheStats()

        # Callbacks
        self._on_route_update: list = []  # (key, old, new) -> None
        self._on_invalidate: list = []    # (individual) -> None

    # ------------------------------------------------------------------
    # Virtual instance layer (R2): position & link lifetime from ephemeris
    # ------------------------------------------------------------------
    def set_virtual(self, individual: str, concept: str, prop: str, value: Any):
        """Ephemeris-derived instance: always fresh, never disseminated."""
        key = make_key(self.constellation, concept, prop, individual)
        inst = Instance(
            key=key, individual=individual, concept=concept,
            prop=prop, value=value,
            timestamp=time.time(), ttl=float("inf"),
            virtual=True, instance_type="virtual",
        )
        self._virtual[key] = inst

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, key: str, now: Optional[float] = None) -> Optional[Instance]:
        """Lookup key; virtual layer first, then cache."""
        if key in self._virtual:
            self.stats.virtual_reads += 1
            return self._virtual[key]
        with self._lock:
            inst = self._store.get(key)
            if inst is None:
                self.stats.misses += 1
                return None
            if inst.expired(now):
                del self._store[key]
                self.stats.misses += 1
                return None
            self._store.move_to_end(key)
            self.stats.hits += 1
            return inst

    def get_by_individual(self, individual: str) -> dict[str, Instance]:
        """Get all instances (properties) for an individual."""
        results: dict[str, Instance] = {}
        now = time.time()
        # Virtual
        for k, v in self._virtual.items():
            if v.individual == individual:
                results[v.prop] = v
        # Cache (cache overrides if same prop exists)
        with self._lock:
            for k, v in self._store.items():
                if v.individual == individual and not v.expired(now):
                    results[v.prop] = v
        return results

    def get_by_concept(self, concept: str) -> list[Instance]:
        """Get all instances whose individual belongs to concept or subclass."""
        results = []
        now = time.time()
        for v in self._virtual.values():
            if is_subclass_of(v.concept, concept):
                results.append(v)
        with self._lock:
            for v in self._store.values():
                if is_subclass_of(v.concept, concept) and not v.expired(now):
                    results.append(v)
        return results

    def snapshot_individuals(self) -> dict[str, dict[str, Instance]]:
        """One-pass snapshot: individual -> {prop: Instance}.

        Perf helper: replaces repeated get_by_individual() full-store scans
        with a single traversal (used by MOQR hot loops).
        """
        now = time.time()
        result: dict[str, dict[str, Instance]] = {}
        for v in self._virtual.values():
            result.setdefault(v.individual, {})[v.prop] = v
        with self._lock:
            for v in self._store.values():
                if not v.expired(now):
                    result.setdefault(v.individual, {})[v.prop] = v
        return result

    # ------------------------------------------------------------------
    # Write: selective admission + dual-class buffering (R6/R8)
    # ------------------------------------------------------------------
    def put(self, instance: Instance, urgent: bool = False, admit: bool = True):
        """Write an instance. Urgent -> immediate flush; else batch."""
        if not admit:
            return
        if urgent:
            with self._buf_lock:
                self._batch_urgent.append(instance)
            self._flush_urgent()
        else:
            flush_now = False
            with self._buf_lock:
                self._batch_buf.append(instance)
                if self._batch_deadline == 0.0:
                    self._batch_deadline = time.time() + self.t_batch_ms / 1000.0
                # Check batch conditions
                if (len(self._batch_buf) >= 64 or
                        time.time() >= self._batch_deadline):
                    flush_now = True
            if flush_now:
                self._flush_regular()

    def tick(self):
        """Periodic driver: flush due batches even when no new puts arrive,
        and purge expired store entries (prevents stale-entry bloat)."""
        with self._buf_lock:
            due = (self._batch_buf and self._batch_deadline > 0.0
                   and time.time() >= self._batch_deadline)
        if due:
            self._flush_regular()
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._store.items() if v.expired(now)]
            for k in expired:
                del self._store[k]
            self.stats.aged_out += len(expired)

    def force_flush(self):
        """Force flush all pending batches (for testing)."""
        if self._batch_urgent:
            self._flush_urgent()
        if self._batch_buf:
            self._flush_regular()

    def invalidate_individual(self, individual: str) -> int:
        """Hard-remove all cached instances of an individual (OIN handling).
        Direct delete — deliberately bypasses axiom evaluation so remote
        invalidations never re-trigger __invalidate__ callbacks (flood loop)."""
        removed = 0
        with self._lock:
            doomed = [k for k, v in self._store.items()
                      if v.individual == individual]
            for k in doomed:
                del self._store[k]
                removed += 1
        return removed

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------
    def _flush_urgent(self):
        """R6: urgent flush — bypass batch window, immediate commit + infer."""
        with self._buf_lock:
            if not self._batch_urgent:
                return
            entries = self._batch_urgent[:]
            self._batch_urgent.clear()
        self.stats.urgent_flush_count += 1
        self._commit_and_infer(entries, urgent=True)

    def _flush_regular(self):
        """Regular batch: commit all, run axiom inference, update routes."""
        with self._buf_lock:
            if not self._batch_buf:
                return
            entries = self._batch_buf[:]
            self._batch_buf.clear()
            self._batch_deadline = 0.0
        self.stats.batch_count += 1
        self._commit_and_infer(entries, urgent=False)

    def _commit_and_infer(self, entries: list[Instance], urgent: bool):
        """Commit instances to store + run ontology inference."""
        with self._lock:
            for inst in entries:
                # Eviction policy: aging-first — when full, evict the lowest-
                # freshness entry (stale resource snapshots die before fresh
                # ones); pure-LRU order is the tiebreak via iteration order
                if (inst.key not in self._store and
                        len(self._store) >= self.max_entries):
                    now = time.time()
                    victim = min(self._store,
                                 key=lambda k: self._store[k].freshness(now))
                    del self._store[victim]
                    self.stats.evictions += 1
                self._store[inst.key] = inst
                self._store.move_to_end(inst.key)
                self.stats.admissions += 1

        # Ontology inference batch
        affected_individuals = {inst.individual for inst in entries}
        for indiv in affected_individuals:
            props = self.get_by_individual(indiv)
            for axiom in AXIOMS:
                if axiom.condition(props):
                    effects = axiom.effect(props)
                    for (prop_name, val) in effects:
                        if prop_name == "__invalidate__":
                            for cb in self._on_invalidate:
                                cb(indiv)
                        elif prop_name == "__classify__":
                            pass  # Reclassification noted
                        else:
                            # Inferred instance
                            inferred_key = make_key(
                                self.constellation,
                                props.get(prop_name, next(iter(props.values()))).concept,
                                prop_name, indiv
                            )
                            inferred_inst = Instance(
                                key=inferred_key, individual=indiv,
                                concept=next(iter(props.values())).concept,
                                prop=prop_name, value=val,
                                timestamp=time.time(),
                                instance_type="inferred"
                            )
                            with self._lock:
                                # Inferred writes honor the same aging-first cap
                                if (inferred_key not in self._store and
                                        len(self._store) >= self.max_entries):
                                    now2 = time.time()
                                    victim = min(
                                        self._store,
                                        key=lambda k: self._store[k].freshness(now2))
                                    del self._store[victim]
                                    self.stats.evictions += 1
                                self._store[inferred_key] = inferred_inst

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def on_invalidate(self, callback):
        self._on_invalidate.append(callback)

    def on_route_update(self, callback):
        self._on_route_update.append(callback)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self._store) + len(self._virtual)

    @property
    def hit_ratio(self) -> float:
        total = self.stats.hits + self.stats.misses + self.stats.virtual_reads
        if total == 0:
            return 0.0
        return (self.stats.hits + self.stats.virtual_reads) / total
