#!/usr/bin/env python3
"""OCRM per-satellite node daemon — containerlab production entry.

Runs the real protocol stack inside a sat-router container:

  control plane : OCRMRouter (UDP :9900 over ISL /127 addresses)
                  - LSU link-state flooding  -> LSDB -> local SPF
                  - CPA/OQR/ORD/OIN semantic messages (NDN forwarding)
                  Control messages travel hop-by-hop between direct
                  neighbors only — independent of the data-plane FIB.
  CFML          : dry_run=False -> real `ip -6 route replace/del`
  data plane    : kernel IPv6 forwarding over CFML-installed /48 routes

Environment (injected by the generated .clab.yml):
  SAT_NAME    node name, e.g. sat0-1
  SAT_PREFIX  own routable prefix, e.g. fcbb:0:1::/48
  OCRM_FACES  face table: nbr|iface|peer_addr[,...]
              e.g. sat0-1|eth1|fd00:100::1,sat1-0|eth5|fd00:100::3
  OCRM_PORT   UDP port (default 9900)
"""
from __future__ import annotations

import os
import sys
import json
import time
import random
import logging
import resource
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocrm.core.ontology import Instance, make_key
from ocrm.core.kv_cache import KVCache
from ocrm.protocol.router import OCRMRouter
from ocrm.cfml import CFML, Route

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ocrm-node")

STATS_PATH = "/tmp/ocrm_stats.json"
LINK_POLL_S = 0.3          # ISL carrier poll interval
LSU_REFRESH_S = 5.0        # periodic LSDB refresh (cold-start robustness)


def sat_prefix(name: str) -> str:
    """sat{p}-{s} -> fcbb:{p:x}:{s:x}::/48 (mirrors topogen.addressing)."""
    p, s = name.removeprefix("sat").split("-")
    return "fcbb:%x:%x::/48" % (int(p), int(s))


def plane_of(name: str) -> int:
    return int(name.removeprefix("sat").split("-")[0])


def plane_prefix(p: int) -> str:
    """轨道面聚合前缀: fcbb:{p:x}::/32 覆盖该面全部 /48."""
    return "fcbb:%x::/32" % p


class NodeDaemon:
    def __init__(self):
        self.name = os.environ["SAT_NAME"]
        self.prefix = os.environ.get("SAT_PREFIX", sat_prefix(self.name))
        self.port = int(os.environ.get("OCRM_PORT", "9900"))
        # in-network aggregation: 按轨道面聚合 FIB (默认开, 可关做 A/B)
        self.aggregate = os.environ.get("OCRM_AGGREGATE", "1") == "1"
        # faces: nbr -> (iface, peer_addr)
        self.faces: dict[str, tuple[str, str]] = {}
        for spec in os.environ["OCRM_FACES"].split(","):
            nbr, iface, peer = spec.split("|")
            self.faces[nbr] = (iface, peer)

        self.cache = KVCache(self.name, max_entries=5000,
                             constellation="ALPHA")
        # Real FIB writes; short rate-limit window for a 24-node lab
        self.cfml = CFML(self.name, t_cfml_s=0.5, dry_run=False)
        self.router = OCRMRouter(self.name, list(self.faces),
                                 self.cache, port=self.port,
                                 cfml=self.cfml, bind_host="::")
        for nbr, (iface, peer) in self.faces.items():
            self.router.add_peer(nbr, peer, self.port)
        self.router.on_lsdb_change = self._schedule_spf

        self._spf_pending = threading.Event()
        self._running = True
        self._adjacency: list[str] = []
        self._installed_dests: set[str] = set()

    # ------------------------------------------------------------------
    # Link monitoring -> LSU origination
    # ------------------------------------------------------------------
    def _iface_up(self, iface: str) -> bool:
        try:
            with open(f"/sys/class/net/{iface}/carrier") as f:
                return f.read().strip() == "1"
        except OSError:
            return False

    def _current_adjacency(self) -> list[str]:
        return sorted(nbr for nbr, (iface, peer) in self.faces.items()
                      if self._iface_up(iface))

    def _link_monitor(self):
        last_refresh = 0.0
        while self._running:
            adj = self._current_adjacency()
            now = time.time()
            if adj != self._adjacency or now - last_refresh >= LSU_REFRESH_S:
                changed = adj != self._adjacency
                self._adjacency = adj
                last_refresh = now
                self.router.originate_lsu(adj)
                if changed:
                    logger.info(f"[{self.name}] adjacency -> {adj}")
            time.sleep(LINK_POLL_S)

    # ------------------------------------------------------------------
    # SPF -> CFML installation (data-plane programming)
    # ------------------------------------------------------------------
    def _schedule_spf(self):
        self._spf_pending.set()

    def _spf_loop(self):
        while self._running:
            if not self._spf_pending.wait(timeout=1.0):
                continue
            # Debounce: coalesce LSU bursts into one SPF run
            time.sleep(0.05)
            self._spf_pending.clear()
            try:
                self._run_spf()
            except Exception as e:  # never kill the loop
                logger.warning(f"[{self.name}] SPF error: {e}")

    def _run_spf(self):
        lsdb = self.router.lsdb_snapshot()
        # Bidirectional check: edge exists only if both endpoints agree
        graph: dict[str, set[str]] = {}
        for origin, adj in lsdb.items():
            for nbr in adj:
                if origin in lsdb.get(nbr, []):
                    graph.setdefault(origin, set()).add(nbr)
                    graph.setdefault(nbr, set()).add(origin)

        # BFS shortest paths (uniform ISL cost), tracking first hop.
        # Sorted iteration => deterministic equal-cost tie-break, so the
        # chosen first hop is stable across SPF runs (no FIB flapping)
        first_hop: dict[str, str] = {}
        visited = {self.name}
        frontier: list[tuple[str, str]] = []
        for nbr in sorted(graph.get(self.name, ())):  # seed with live faces
            if nbr in self.faces:
                frontier.append((nbr, nbr))
                visited.add(nbr)
                first_hop[nbr] = nbr
        while frontier:
            nxt: list[tuple[str, str]] = []
            for node, fh in frontier:
                for nb in sorted(graph.get(node, ())):
                    if nb not in visited:
                        visited.add(nb)
                        first_hop[nb] = fh
                        nxt.append((nb, fh))
            frontier = nxt

        # Program the FIB — in-network aggregation (路由聚合):
        #   同一轨道面的 /48 若多数共享一个首跳, 则下发一条 /32 聚合路由,
        #   例外卫星保留更精确的 /48 (LPM 保证正确性);
        #   本轨道面不聚合 —— /32 会覆盖自身前缀, 把本地目的流量引出去
        by_plane: dict[int, dict[str, str]] = {}   # plane -> {dest: fh}
        for dest, fh in first_hop.items():
            by_plane.setdefault(plane_of(dest), {})[dest] = fh

        reachable: set[str] = set()
        my_plane = plane_of(self.name)
        for plane, dests in by_plane.items():
            hop_count: dict[str, int] = {}
            for fh in dests.values():
                hop_count[fh] = hop_count.get(fh, 0) + 1
            # Deterministic majority: highest count, name as tie-break
            majority_fh = max(hop_count,
                              key=lambda f: (hop_count[f], f))
            do_agg = (self.aggregate and plane != my_plane
                      and hop_count[majority_fh] >= 2)
            if do_agg:
                agg = plane_prefix(plane)
                reachable.add(agg)
                self.cfml.enqueue(Route(
                    destination=agg,
                    next_hops=[self.faces[majority_fh][1]],
                    cost=1.0, semantic_source="lsu-spf-agg",
                ), urgent=True)
            for dest, fh in dests.items():
                if do_agg and fh == majority_fh:
                    continue   # 已被聚合路由覆盖
                prefix = sat_prefix(dest)
                reachable.add(prefix)
                self.cfml.enqueue(Route(
                    destination=prefix, next_hops=[self.faces[fh][1]],
                    cost=1.0, semantic_source="lsu-spf",
                ), urgent=True)   # R6: topology-driven updates are urgent-class
        # Withdraw prefixes that became unreachable
        for prefix in self._installed_dests - reachable:
            self.cfml.withdraw(prefix)
        self._installed_dests = reachable

    # ------------------------------------------------------------------
    # Semantic layer: local resource instances (compute/energy ontology)
    # ------------------------------------------------------------------
    def _populate_instances(self):
        rng = random.Random(hash(self.name) & 0xFFFF)
        now = time.time()
        concept = rng.choice(["GPUComputeNode", "CPUComputeNode"])
        for prop, val, cpt in (
            ("hasAvailableFLOPS", rng.uniform(1.0, 50.0), concept),
            ("hasEnergyReserve", rng.uniform(500.0, 10000.0), "NetworkNode"),
        ):
            self.cache.put(Instance(
                key=make_key("ALPHA", cpt, prop, self.name),
                individual=self.name, concept=cpt, prop=prop, value=val,
                timestamp=now, ttl=3600.0, instance_type="measured",
            ), urgent=True)

    # ------------------------------------------------------------------
    # Stats export (harness reads this via docker exec)
    # ------------------------------------------------------------------
    def _stats_loop(self):
        while self._running:
            m = dict(self.router.metrics)
            m.pop("convergence_events", None)
            stats = {
                "node": self.name,
                "time": time.time(),
                "adjacency": self._adjacency,
                "lsdb_size": len(self.router.lsdb),
                "fib_routes": self.cfml.installed_count,
                "aggregation": self.aggregate,
                "cache_size": self.cache.size,
                "cache_aged_out": self.cache.stats.aged_out,
                "rss_mb": resource.getrusage(
                    resource.RUSAGE_SELF).ru_maxrss / 1024.0,
                "metrics": m,
                "cfml": {
                    "installed": self.cfml.stats.routes_installed,
                    "withdrawn": self.cfml.stats.routes_withdrawn,
                    "urgent": self.cfml.stats.urgent_installs,
                },
            }
            tmp = STATS_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(stats, f)
            os.replace(tmp, STATS_PATH)
            time.sleep(2.0)

    # ------------------------------------------------------------------
    def run(self):
        logger.info(f"[{self.name}] OCRM node daemon starting: "
                    f"prefix={self.prefix} faces={list(self.faces)}")
        self._populate_instances()
        self.router.start()
        for target in (self._link_monitor, self._spf_loop, self._stats_loop):
            threading.Thread(target=target, daemon=True).start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            self.router.stop()


if __name__ == "__main__":
    NodeDaemon().run()
