"""CFML — Control-Forwarding Mapping Layer (§3.9).

Translates semantic routing decisions to FIB entries (ip route commands).
Three components: Constraint Compiler, Conditional ECMP, Update Rate Limiter.
"""
from __future__ import annotations

import time
import re
import ipaddress
import subprocess
import threading
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Tokens fed into `ip route` must be plain addresses/prefixes/IDs — reject
# anything that could smuggle extra arguments into the command line.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/\-]+$")


@dataclass
class Route:
    """A semantic route compiled into forwarding entries."""
    destination: str       # /prefix or individual ID
    next_hops: list[str]   # list of interface/nexthop IPs
    cost: float = 1.0
    installed_at: float = 0.0
    semantic_source: str = ""  # which concept/query generated this


@dataclass
class CFMLStats:
    routes_installed: int = 0
    routes_withdrawn: int = 0
    rate_limited_updates: int = 0
    urgent_installs: int = 0
    ecmp_installs: int = 0


class CFML:
    """Per-node CFML: semantic route -> FIB translation with rate limiting."""

    def __init__(
        self,
        node_id: str,
        t_cfml_s: float = 2.0,    # rate limiter window
        ecmp_cost_ratio: float = 2.0,  # max ratio for ECMP
        dry_run: bool = False,    # if True, don't actually install routes
    ):
        self.node_id = node_id
        self.t_cfml_s = t_cfml_s
        self.ecmp_cost_ratio = ecmp_cost_ratio
        self.dry_run = dry_run

        self._pending: list[Route] = []
        self._installed: dict[str, Route] = {}  # dest -> Route
        # Parsed prefix cache for LPM lookups (None = non-address dest,
        # e.g. an individual ID installed by the semantic protocol stack)
        self._nets: dict[str, ipaddress.IPv6Network | ipaddress.IPv4Network | None] = {}
        self._last_flush: float = time.time()
        self._lock = threading.Lock()
        self.stats = CFMLStats()

    # ------------------------------------------------------------------
    # Interface: enqueue semantic route for installation
    # ------------------------------------------------------------------
    def enqueue(self, route: Route, urgent: bool = False):
        """Enqueue a route for FIB installation."""
        with self._lock:
            if urgent:
                self._install_route(route)
                self.stats.urgent_installs += 1
            else:
                self._pending.append(route)

    def tick(self):
        """Called periodically to flush rate-limited updates."""
        now = time.time()
        with self._lock:
            if now - self._last_flush >= self.t_cfml_s and self._pending:
                self._flush_pending()
                self._last_flush = now

    def force_flush(self):
        """Flush pending routes immediately (simulation clock helper)."""
        with self._lock:
            if self._pending:
                self._flush_pending()
                self._last_flush = time.time()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _flush_pending(self):
        """Install all pending routes, applying conditional ECMP."""
        # Group by destination
        by_dest: dict[str, list[Route]] = {}
        for r in self._pending:
            by_dest.setdefault(r.destination, []).append(r)
        self._pending.clear()

        for dest, routes in by_dest.items():
            routes.sort(key=lambda r: r.cost)
            best = routes[0]

            # Conditional ECMP: install multi-path only if cost ratio < threshold
            ecmp_routes = [best]
            for r in routes[1:]:
                if best.cost > 0 and r.cost / best.cost <= self.ecmp_cost_ratio:
                    ecmp_routes.append(r)

            if len(ecmp_routes) > 1:
                self.stats.ecmp_installs += 1

            # Merge next_hops
            merged = Route(
                destination=dest,
                next_hops=[nh for r in ecmp_routes for nh in r.next_hops],
                cost=best.cost,
                semantic_source=best.semantic_source,
            )
            self._install_route(merged)

    def _install_route(self, route: Route):
        """Install route in the kernel FIB (make-before-break)."""
        existing = self._installed.get(route.destination)
        if existing and existing.next_hops == route.next_hops:
            self.stats.rate_limited_updates += 1
            return  # no change

        # `ip route replace` atomically swaps the entry for the same
        # destination — never withdraw first (that opens a blackhole window)
        if not self._write_fib(route):
            logger.warning(f"[{self.node_id}] FIB write failed, keeping "
                           f"previous route for {route.destination}")
            return
        if existing:
            self.stats.routes_withdrawn += 1  # replaced, not deleted
        route.installed_at = time.time()
        self._installed[route.destination] = route
        if route.destination not in self._nets:
            try:
                self._nets[route.destination] = ipaddress.ip_network(
                    route.destination, strict=False)
            except ValueError:
                self._nets[route.destination] = None
        self.stats.routes_installed += 1

    def _write_fib(self, route: Route) -> bool:
        """Write route to kernel FIB via a single atomic ip-route replace.
        Returns True on success (FIB and self._installed stay in sync)."""
        if self.dry_run:
            logger.debug(f"[{self.node_id}] DRY-RUN install: {route.destination} "
                         f"via {route.next_hops}")
            return True
        tokens = [route.destination] + list(route.next_hops)
        if not all(_SAFE_TOKEN.match(t) for t in tokens):
            logger.error(f"[{self.node_id}] rejected unsafe route tokens: {tokens}")
            return False
        # One atomic replace carrying all next-hops (real ECMP); sequential
        # per-nexthop replaces would each overwrite the previous one
        cmd = ["ip", "-6", "route", "replace", route.destination]
        for nh in route.next_hops:
            cmd += ["nexthop", "via", nh]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=2)
            if proc.returncode != 0:
                logger.warning(f"[{self.node_id}] FIB install failed: "
                               f"{proc.stderr.decode(errors='replace').strip()}")
                return False
            return True
        except Exception as e:
            logger.warning(f"[{self.node_id}] FIB install failed: {e}")
            return False

    def _withdraw_fib(self, route: Route):
        """Remove route from kernel FIB (explicit withdrawal only)."""
        self.stats.routes_withdrawn += 1
        if self.dry_run:
            return
        if not _SAFE_TOKEN.match(route.destination):
            return
        cmd = ["ip", "-6", "route", "del", route.destination]
        try:
            subprocess.run(cmd, capture_output=True, timeout=2)
        except Exception:
            pass

    def withdraw(self, destination: str):
        """Explicitly withdraw an installed destination (e.g. flow teardown)."""
        with self._lock:
            route = self._installed.pop(destination, None)
            self._nets.pop(destination, None)
            if route:
                self._withdraw_fib(route)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def lookup(self, destination: str) -> Route | None:
        """Exact-match FIB lookup (individual-ID routes from the protocol stack)."""
        with self._lock:
            return self._installed.get(destination)

    def lookup_lpm(self, address: str) -> Route | None:
        """IPv6 forwarding-plane lookup: longest-prefix match.

        Exact destination match wins first (host route / individual ID),
        then the longest installed prefix containing `address`.
        """
        with self._lock:
            route = self._installed.get(address)
            if route is not None:
                return route
            try:
                addr = ipaddress.ip_address(address)
            except ValueError:
                return None
            best: Route | None = None
            best_len = -1
            for dest, net in self._nets.items():
                if net is not None and addr in net and net.prefixlen > best_len:
                    best_len = net.prefixlen
                    best = self._installed.get(dest)
            return best

    @property
    def installed_count(self) -> int:
        return len(self._installed)

    def get_routes(self) -> list[Route]:
        return list(self._installed.values())
