"""OCRM Routing Protocol — §3.4 (OQR/ORD + CPD + Algorithm 1'/2').

Implements the per-node routing daemon that participates in OQR/ORD exchange.
"""
from __future__ import annotations

import time
import uuid
import json
import socket
import threading
import logging
from dataclasses import dataclass, field, fields as dc_fields
from typing import Optional, Callable

from ..core.ontology import (
    Instance, super_of, is_subclass_of, DATA_PROPERTIES, make_key
)
from ..core.kv_cache import KVCache
from ..core.reasoning import ReasoningEngine, OntologyQuery, Constraint, ReasoningResult
from ..cfml import CFML, Route

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CPD: Class Potential Digest (R1)
# ---------------------------------------------------------------------------

@dataclass
class CPDEntry:
    """Per-interface, per-class digest: best reachable attribute bounds."""
    concept: str
    bounds: dict[str, float] = field(default_factory=dict)  # prop -> best value
    count: int = 0
    freshness: float = 0.0   # timestamp of newest contribution
    hops: int = 0            # propagation distance (bounded re-advertisement)

    def fold(self, prop: str, value: float, ts: float, prop_type: str = "benefit"):
        """Aggregate an incoming instance value (cost-free update from ORD)."""
        cur = self.bounds.get(prop)
        if prop_type == "benefit":
            if cur is None or value > cur:
                self.bounds[prop] = value
        else:  # cost
            if cur is None or value < cur:
                self.bounds[prop] = value
        self.count += 1
        self.freshness = max(self.freshness, ts)

    def satisfies_bounds(self, constraints: list[Constraint]) -> bool:
        """Check if digest bounds *could* satisfy the query constraints."""
        for c in constraints:
            bound = self.bounds.get(c.prop)
            if bound is None:
                continue  # unknown -> optimistic
            # Check bound direction
            prop_type = DATA_PROPERTIES.get(c.prop, ("", "benefit"))[1]
            if prop_type == "benefit" and c.op in (">=", ">"):
                if bound < float(c.threshold):
                    return False
            elif prop_type == "cost" and c.op in ("<=", "<"):
                if bound > float(c.threshold):
                    return False
        return True


# ---------------------------------------------------------------------------
# Protocol messages (JSON over UDP for prototype)
# ---------------------------------------------------------------------------

@dataclass
class OQR:
    """Ontology Query Request."""
    query_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    required_class: str = ""
    constraints: list[dict] = field(default_factory=list)  # [{prop, op, threshold}]
    task_weights: dict[str, float] = field(default_factory=dict)
    ttl: float = 5.0
    hop_limit: int = 8
    ring_radius: int = 0
    response_quota: int = 3
    min_freshness: float = 0.0   # 老化门限: 随 Interest 携带, 缓存命中同样生效
    source_id: str = ""
    path_vector: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "type": "OQR",
            "query_id": self.query_id,
            "required_class": self.required_class,
            "constraints": self.constraints,
            "task_weights": self.task_weights,
            "ttl": self.ttl,
            "hop_limit": self.hop_limit,
            "ring_radius": self.ring_radius,
            "response_quota": self.response_quota,
            "min_freshness": self.min_freshness,
            "source_id": self.source_id,
            "path_vector": self.path_vector,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, data: str) -> "OQR":
        d = json.loads(data)
        # Whitelist known fields: unknown/forged keys must not crash the daemon
        allowed = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class ORD:
    """Ontology Response Data."""
    query_id: str = ""
    satisfier_id: str = ""
    satisfier_concept: str = ""
    instance_vector: dict[str, float] = field(default_factory=dict)  # prop -> value
    path_vector: list[str] = field(default_factory=list)  # discovered route (payload)
    via: list[str] = field(default_factory=list)           # return-path visited set
    satisfier_score: float = 0.0
    causal_version: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "type": "ORD",
            "query_id": self.query_id,
            "satisfier_id": self.satisfier_id,
            "satisfier_concept": self.satisfier_concept,
            "instance_vector": self.instance_vector,
            "path_vector": self.path_vector,
            "via": self.via,
            "satisfier_score": self.satisfier_score,
            "causal_version": self.causal_version,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, data: str) -> "ORD":
        d = json.loads(data)
        allowed = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class CPA:
    """CPD Potential Advertisement — proactive digest push (Hello/LSA analog).

    Periodically announces, per concept class, the best attribute bounds
    reachable through the sender. Fixes CPD cold-start (before any ORD has
    been observed) so first queries are CPD-guided instead of ring-flooded.
    """
    sender_id: str = ""
    # [{concept, bounds, count, freshness, hops}]
    entries: list[dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "type": "CPA",
            "sender_id": self.sender_id,
            "entries": self.entries,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, data: str) -> "CPA":
        d = json.loads(data)
        allowed = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class OIN:
    """Ontology Invalidation Notice — urgent withdraw (R6 dual-class analog).

    Originated when a local axiom fires __invalidate__ (link down/dying);
    flooded hop-limited so dependent nodes purge cache + FIB immediately
    instead of waiting for TTL expiry.
    """
    oin_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    origin_id: str = ""
    individual: str = ""
    concept: str = ""
    reason: str = ""
    hop_limit: int = 4
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "type": "OIN",
            "oin_id": self.oin_id,
            "origin_id": self.origin_id,
            "individual": self.individual,
            "concept": self.concept,
            "reason": self.reason,
            "hop_limit": self.hop_limit,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, data: str) -> "OIN":
        d = json.loads(data)
        allowed = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class LSU:
    """Link State Update — ISL adjacency knowledge synchronization.

    Carries the origin's current adjacency set (hasISLWith ontology
    instances) as an R6 urgent-class write. Flooded with per-origin
    sequence dedup (same controlled-flooding discipline as OIN) so every
    node's KV-Cache/LSDB converges to the constellation link state and can
    run local SPF -> CFML installation. Control traffic only — travels
    hop-by-hop between directly-connected neighbors, never through the
    data-plane FIB.
    """
    origin_id: str = ""
    seq: int = 0
    adjacencies: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "type": "LSU",
            "origin_id": self.origin_id,
            "seq": self.seq,
            "adjacencies": self.adjacencies,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, data: str) -> "LSU":
        d = json.loads(data)
        allowed = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


# ---------------------------------------------------------------------------
# PQT: Pending Query Table (analog to NDN PIT)
# ---------------------------------------------------------------------------

@dataclass
class PQTEntry:
    query_id: str
    # NDN PIT semantics: ALL requesting faces are recorded so every
    # downstream requester receives the Data; "local" marks the originator
    in_faces: list[str]
    quota: int
    responses: list[ORD] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# OCRM Router Node
# ---------------------------------------------------------------------------

class OCRMRouter:
    """Per-node OCRM routing daemon."""

    def __init__(
        self,
        node_id: str,
        interfaces: list[str],   # neighbor node IDs
        cache: KVCache,
        port: int = 9900,
        cfml: Optional[CFML] = None,
        bind_host: str = "0.0.0.0",   # "::" for IPv6 deployments (containerlab)
    ):
        self.node_id = node_id
        self.interfaces = interfaces  # outgoing face = neighbor ID
        self.cache = cache
        self.engine = ReasoningEngine(cache)
        self.port = port
        self.bind_host = bind_host
        # CFML: FIB installation channel (§3.9); dry-run by default so the
        # daemon works without CAP_NET_ADMIN — pass a real CFML in production
        self.cfml = cfml if cfml is not None else CFML(node_id, dry_run=True)

        # CPD table: cpd[face_id][concept] = CPDEntry
        self.cpd: dict[str, dict[str, CPDEntry]] = {
            iface: {} for iface in interfaces
        }
        # PQT (guarded by _state_lock: accessed from recv thread + callers)
        self.pqt: dict[str, PQTEntry] = {}
        self.pqt_timeout_s = 5.0
        # Dead-nonce list (NDN): satisfied/expired query IDs, so looping
        # duplicates are never re-processed after PIT entry removal
        self._dead_nonces: dict[str, float] = {}
        self.dead_nonce_ttl_s = 10.0
        # CPA: proactive digest advertisement (cold-start fix)
        self.advertise_interval_s = 5.0
        self._last_cpa = 0.0
        # OIN: seen-notice dedup (flood suppression)
        self._seen_oin: dict[str, float] = {}
        # ORT: Ontology Routing Table (concept -> list of faces with cost)
        self.ort: dict[str, list[tuple[str, float]]] = {}
        # LSDB: origin -> (seq, adjacencies, received_at); own entry included
        self.lsdb: dict[str, tuple[int, list[str], float]] = {}
        self._lsu_seq = 0
        # Restart recovery: if an origin's LSDB entry has seen no refresh
        # for this long (~3x LSU_REFRESH_S), accept a lower seq — the
        # origin has likely rebooted and restarted its counter at 1
        self.lsu_stale_s = 15.0
        # Housekeeping pacing (runs on both message and timeout paths)
        self._last_housekeep = 0.0
        # Fired after any LSDB change (daemon hooks SPF + CFML install here)
        self.on_lsdb_change: Optional[Callable[[], None]] = None
        self._state_lock = threading.Lock()

        # Peer sockets (node_id -> (host, port))
        self._peers: dict[str, tuple[str, int]] = {}
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None

        # Metrics
        self.metrics = {
            "queries_sent": 0,
            "queries_received": 0,
            "queries_satisfied_local": 0,
            "responses_sent": 0,
            "responses_received": 0,
            "routes_learned": 0,
            "cpa_sent": 0,
            "cpa_received": 0,
            "oin_sent": 0,
            "oin_received": 0,
            "lsu_sent": 0,
            "lsu_received": 0,
            "bytes_sent": 0,
            "bytes_received": 0,
            "convergence_events": [],  # (timestamp, latency_ms)
        }

        # Local axiom __invalidate__ -> originate an urgent OIN (R6 class)
        cache.on_invalidate(self._on_local_invalidate)

    # ------------------------------------------------------------------
    # Network I/O
    # ------------------------------------------------------------------
    def start(self):
        family = socket.AF_INET6 if ":" in self.bind_host else socket.AF_INET
        self._sock = socket.socket(family, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.bind_host, self.port))
        self._sock.settimeout(0.5)
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        logger.info(f"[{self.node_id}] OCRM router started on port {self.port}")

    def stop(self):
        self._running = False
        if self._recv_thread:
            self._recv_thread.join(timeout=2)
        if self._sock:
            self._sock.close()

    def add_peer(self, node_id: str, host: str, port: int):
        self._peers[node_id] = (host, port)

    def _send_to(self, target_node: str, data: str):
        if target_node in self._peers and self._sock:
            host, port = self._peers[target_node]
            payload = data.encode()
            try:
                self._sock.sendto(payload, (host, port))
                self.metrics["bytes_sent"] += len(payload)
            except OSError:
                pass  # face down — datagram semantics, drop silently

    def _recv_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65536)
                self.metrics["bytes_received"] += len(data)
                msg = json.loads(data.decode())
                if msg["type"] == "OQR":
                    self._handle_oqr(OQR.from_json(data.decode()), addr)
                elif msg["type"] == "ORD":
                    self._handle_ord(ORD.from_json(data.decode()), addr)
                elif msg["type"] == "CPA":
                    self._handle_cpa(CPA.from_json(data.decode()), addr)
                elif msg["type"] == "OIN":
                    self._handle_oin(OIN.from_json(data.decode()), addr)
                elif msg["type"] == "LSU":
                    self._handle_lsu(LSU.from_json(data.decode()), addr)
            except socket.timeout:
                pass
            except Exception as e:
                logger.debug(f"[{self.node_id}] recv error: {e}")
            # Housekeeping runs on BOTH the message and timeout paths —
            # under sustained traffic recvfrom never times out, so tying
            # tick()/CPA/GC to the timeout branch alone starves them
            self._housekeep()

    def _housekeep(self):
        """Rate-paced periodic duties: batch flushes, CFML rate-limiter
        drive, CPA advertisement, and seen-OIN garbage collection."""
        now = time.time()
        if now - self._last_housekeep < 0.5:
            return
        self._last_housekeep = now
        self.cache.tick()
        self.cfml.tick()
        if now - self._last_cpa >= self.advertise_interval_s:
            self._last_cpa = now
            self.send_cpa()
        with self._state_lock:
            stale = [i for i, ts in self._seen_oin.items()
                     if now - ts > self.dead_nonce_ttl_s]
            for i in stale:
                del self._seen_oin[i]

    # ------------------------------------------------------------------
    # Algorithm 1': OQR Processing (CPD-guided)
    # ------------------------------------------------------------------
    def send_query(self, query: OntologyQuery) -> str:
        """Originate a query from this node."""
        oqr = OQR(
            required_class=query.required_class,
            constraints=[{"prop": c.prop, "op": c.op, "threshold": c.threshold}
                         for c in query.constraints],
            task_weights=query.task_weights,
            source_id=self.node_id,
            path_vector=[self.node_id],
            response_quota=query.max_results,
            min_freshness=query.min_freshness,
        )
        self.metrics["queries_sent"] += 1
        # Try local cache first
        result = self.engine.reason(query)
        if result.satisfiers:
            self.metrics["queries_satisfied_local"] += 1
            # Satisfied locally: no PQT state needed
            return oqr.query_id

        with self._state_lock:
            self._purge_expired_pqt()
            self.pqt[oqr.query_id] = PQTEntry(
                query_id=oqr.query_id, in_faces=["local"], quota=oqr.response_quota
            )
        # Forward via CPD
        self._forward_oqr(oqr)
        return oqr.query_id

    def _purge_expired_pqt(self):
        """Retire timed-out PQT entries into the dead-nonce list and drop
        stale nonces (caller holds _state_lock)."""
        now = time.time()
        expired = [qid for qid, e in self.pqt.items()
                   if now - e.timestamp > self.pqt_timeout_s]
        for qid in expired:
            del self.pqt[qid]
            self._dead_nonces[qid] = now
        stale = [qid for qid, ts in self._dead_nonces.items()
                 if now - ts > self.dead_nonce_ttl_s]
        for qid in stale:
            del self._dead_nonces[qid]

    def _handle_oqr(self, oqr: OQR, addr):
        """Algorithm 1': process incoming OQR."""
        self.metrics["queries_received"] += 1

        # Loop check (simplified: exact path vector)
        if self.node_id in oqr.path_vector:
            return  # discard loop

        # Query TTL: drop stale queries outright
        if time.time() - oqr.timestamp > oqr.ttl:
            return

        in_face = self._identify_face(addr)

        # Interest aggregation (NDN): a duplicate query is not re-forwarded,
        # but the extra requesting face is recorded so it also receives Data
        with self._state_lock:
            self._purge_expired_pqt()
            if oqr.query_id in self._dead_nonces:
                return  # already satisfied/expired — drop looping duplicate
            entry = self.pqt.get(oqr.query_id)
            if entry is not None:
                if in_face and in_face not in entry.in_faces:
                    entry.in_faces.append(in_face)
                return

        # Local cache check (aging 门限随 Interest 携带, 中途命中同样受约束)
        query = OntologyQuery(
            required_class=oqr.required_class,
            constraints=[Constraint(**c) for c in oqr.constraints],
            max_results=oqr.response_quota,
            min_freshness=oqr.min_freshness,
        )
        result = self.engine.reason(query)
        if result.satisfiers:
            self.metrics["queries_satisfied_local"] += 1
            # Schedule ORD response (quality-proportional delay: R5 simplified)
            for sat_id in result.satisfiers:
                props = self.cache.get_by_individual(sat_id)
                iv = {p: float(inst.value) for p, inst in props.items()
                      if isinstance(inst.value, (int, float))}
                score = self._compute_score(iv, query)
                ord_msg = ORD(
                    query_id=oqr.query_id,
                    satisfier_id=sat_id,
                    satisfier_concept=oqr.required_class,
                    instance_vector=iv,
                    path_vector=oqr.path_vector + [self.node_id],
                    via=[self.node_id],
                    satisfier_score=score,
                    timestamp=time.time(),
                )
                # Reverse-path forwarding: send back on the arrival face
                # (hop-by-hop PQT breadcrumbs carry it to the originator)
                if in_face:
                    self._send_to(in_face, ord_msg.to_json())
                    self.metrics["responses_sent"] += 1
            return

        # Enforce hop limit before forwarding further
        if len(oqr.path_vector) >= oqr.hop_limit:
            return

        # Record in PQT and forward
        with self._state_lock:
            self.pqt[oqr.query_id] = PQTEntry(
                query_id=oqr.query_id,
                in_faces=[in_face] if in_face else [],
                quota=oqr.response_quota,
            )
        oqr.path_vector.append(self.node_id)
        self._forward_oqr(oqr)

    def _forward_oqr(self, oqr: OQR):
        """CPD-guided forwarding with concept-hierarchy fallback."""
        concept = oqr.required_class
        constraints = [Constraint(**c) for c in oqr.constraints]

        # Try concept and its superclasses
        while concept is not None:
            feasible_faces = []
            for face in self.interfaces:
                if face in oqr.path_vector:
                    continue  # skip already-visited
                cpd_for_face = self.cpd.get(face, {})
                entry = cpd_for_face.get(concept)
                if entry is not None and entry.satisfies_bounds(constraints):
                    feasible_faces.append((face, entry.count))

            if feasible_faces:
                # Rank by count (proxy for potential), send to best
                feasible_faces.sort(key=lambda x: -x[1])
                best_face = feasible_faces[0][0]
                self._send_to(best_face, oqr.to_json())
                # Controlled bicast if top-2 close
                if len(feasible_faces) > 1:
                    if feasible_faces[1][1] > 0.8 * feasible_faces[0][1]:
                        self._send_to(feasible_faces[1][0], oqr.to_json())
                return

            # Hierarchy fallback (R1)
            concept = super_of(concept)

        # Ring search (bounded, never unbounded flood)
        if oqr.ring_radius < 3 and oqr.hop_limit > len(oqr.path_vector):
            oqr.ring_radius += 1
            # Broadcast to all unvisited
            for face in self.interfaces:
                if face not in oqr.path_vector:
                    self._send_to(face, oqr.to_json())

    # ------------------------------------------------------------------
    # Algorithm 2': ORD Processing (dual-class, CPD update, selective admission)
    # ------------------------------------------------------------------
    def _handle_ord(self, ord_msg: ORD, addr):
        """Algorithm 2': process incoming ORD."""
        self.metrics["responses_received"] += 1

        # Return-path loop check (via = nodes visited on the way back;
        # path_vector is the discovered route payload and naturally
        # contains reverse-path nodes, so it must NOT be used here)
        if self.node_id in ord_msg.via:
            return
        ord_msg.via.append(self.node_id)

        # CPD update (cost-free, from instance vector)
        in_face = self._identify_face(addr)
        if in_face:
            cpd_face = self.cpd.setdefault(in_face, {})
            entry = cpd_face.setdefault(
                ord_msg.satisfier_concept,
                CPDEntry(concept=ord_msg.satisfier_concept)
            )
            for prop, val in ord_msg.instance_vector.items():
                prop_type = DATA_PROPERTIES.get(prop, ("", "benefit"))[1]
                entry.fold(prop, val, ord_msg.timestamp, prop_type)
            # Route generation: every reverse-path node learns how to reach
            # the satisfier through the arrival face (NDN-style breadcrumb)
            self._learn_route(ord_msg, in_face)

        # Selective admission (R8): cache if query-relevant
        with self._state_lock:
            pqt_entry = self.pqt.get(ord_msg.query_id)
        for prop, val in ord_msg.instance_vector.items():
            key = make_key(self.cache.constellation, ord_msg.satisfier_concept,
                           prop, ord_msg.satisfier_id)
            inst = Instance(
                key=key, individual=ord_msg.satisfier_id,
                concept=ord_msg.satisfier_concept, prop=prop, value=val,
                timestamp=ord_msg.timestamp
            )
            # R8: admit only if we have a pending query for this class
            admit = pqt_entry is not None
            self.cache.put(inst, admit=admit)

        # Forward to pending query face
        if pqt_entry:
            pqt_entry.responses.append(ord_msg)
            # Aggregate and forward (R5: top-k)
            if (len(pqt_entry.responses) >= pqt_entry.quota or
                    time.time() - pqt_entry.timestamp > 1.0):
                # Sort by score, keep top-k
                top_k = sorted(pqt_entry.responses,
                               key=lambda r: -r.satisfier_score)[:pqt_entry.quota]
                # Forward Data to EVERY pending face (NDN PIT fan-out)
                for face in pqt_entry.in_faces:
                    if face == "local":
                        # Arrived at originator
                        t_converge = (time.time() - pqt_entry.timestamp) * 1000
                        self.metrics["convergence_events"].append(
                            (time.time(), t_converge)
                        )
                    else:
                        for resp in top_k:
                            self._send_to(face, resp.to_json())
                with self._state_lock:
                    self.pqt.pop(ord_msg.query_id, None)
                    # Dead-nonce: block late/looping duplicates of this query
                    self._dead_nonces[ord_msg.query_id] = time.time()

    # ------------------------------------------------------------------
    # Route generation: ORT + CFML FIB from converged ORDs
    # ------------------------------------------------------------------
    def _learn_route(self, ord_msg: ORD, in_face: str):
        """Derive routing state from a returning ORD (§3.9 CFML hand-off).

        path_vector = [originator, ..., satisfier-node] is the discovered
        forward path; this node lies on the reverse path, so the satisfier
        (and the node hosting it) is reachable via `in_face`. Cost = number
        of hops from this node to the satisfier-node along that path.
        """
        pv = ord_msg.path_vector
        if not pv:
            return
        satisfier_node = pv[-1]
        try:
            cost = float(len(pv) - 1 - pv.index(self.node_id))
        except ValueError:
            # Not on the recorded forward path (e.g. asymmetric return):
            # fall back to path length as a pessimistic cost
            cost = float(len(pv) - 1)
        if cost <= 0:
            return  # we host the satisfier ourselves

        # ORT: concept -> [(face, cost)], keep best cost per face
        with self._state_lock:
            entries = self.ort.setdefault(ord_msg.satisfier_concept, [])
            for i, (face, old_cost) in enumerate(entries):
                if face == in_face:
                    if cost < old_cost:
                        entries[i] = (in_face, cost)
                    break
            else:
                entries.append((in_face, cost))
            entries.sort(key=lambda e: e[1])

        # CFML: install per-destination FIB entries (satisfier individual
        # and its hosting node) via the arrival face
        for dest in {ord_msg.satisfier_id, satisfier_node}:
            if dest and dest != self.node_id:
                self.cfml.enqueue(Route(
                    destination=dest, next_hops=[in_face], cost=cost,
                    semantic_source=f"OQR:{ord_msg.query_id}",
                ))
        self.metrics["routes_learned"] += 1

    def routing_table(self) -> dict[str, list[tuple[str, float]]]:
        """Snapshot of the Ontology Routing Table (concept -> faces)."""
        with self._state_lock:
            return {c: list(faces) for c, faces in self.ort.items()}

    # ------------------------------------------------------------------
    # CPA: proactive CPD digest advertisement (Hello/LSA analog)
    # ------------------------------------------------------------------
    def send_cpa(self):
        """Advertise per-concept digests to every neighbor (split horizon)."""
        for face in self.interfaces:
            entries = self._build_cpa_entries(exclude_face=face)
            if entries:
                cpa = CPA(sender_id=self.node_id, entries=entries)
                self._send_to(face, cpa.to_json())
                self.metrics["cpa_sent"] += 1

    def _build_cpa_entries(self, exclude_face: str) -> list[dict]:
        """Digest = local cache contents (hops 0) + re-advertised remote CPD
        from other faces (split horizon, bounded propagation radius)."""
        digests: dict[str, CPDEntry] = {}
        # (a) locally cached instances: content reachable via this node
        for inst in self.cache.get_by_concept("SpaceNetworkEntity"):
            if not isinstance(inst.value, (int, float)):
                continue
            e = digests.setdefault(inst.concept, CPDEntry(concept=inst.concept))
            ptype = DATA_PROPERTIES.get(inst.prop, ("", "benefit"))[1]
            e.fold(inst.prop, float(inst.value), inst.timestamp, ptype)
        # (b) split horizon: never re-advertise a digest back onto the face
        # it was learned from; drop beyond the propagation radius
        for face, table in self.cpd.items():
            if face == exclude_face:
                continue
            for concept, remote in table.items():
                if remote.hops + 1 > 2:
                    continue
                e = digests.get(concept)
                if e is None:
                    e = digests[concept] = CPDEntry(
                        concept=concept, hops=remote.hops + 1)
                else:
                    e.hops = min(e.hops, remote.hops + 1) if e.count else remote.hops + 1
                for prop, val in remote.bounds.items():
                    ptype = DATA_PROPERTIES.get(prop, ("", "benefit"))[1]
                    e.fold(prop, val, remote.freshness, ptype)
                e.count = max(e.count, remote.count)
        return [{"concept": c, "bounds": e.bounds, "count": max(e.count, 1),
                 "freshness": e.freshness, "hops": e.hops}
                for c, e in digests.items()]

    def _handle_cpa(self, cpa: CPA, addr):
        """Refresh the per-face CPD table from a neighbor's advertisement."""
        self.metrics["cpa_received"] += 1
        in_face = self._identify_face(addr)
        if not in_face:
            return
        table = self.cpd.setdefault(in_face, {})
        for e in cpa.entries:
            concept = e.get("concept", "")
            if not concept:
                continue
            freshness = float(e.get("freshness", cpa.timestamp))
            cur = table.get(concept)
            if cur is not None and cur.freshness > freshness:
                continue  # keep the fresher digest
            table[concept] = CPDEntry(
                concept=concept,
                bounds={k: float(v) for k, v in dict(e.get("bounds", {})).items()},
                count=int(e.get("count", 1)),
                freshness=freshness,
                hops=int(e.get("hops", 0)),
            )

    # ------------------------------------------------------------------
    # OIN: urgent invalidation notice (R6-class withdraw)
    # ------------------------------------------------------------------
    def _on_local_invalidate(self, individual: str):
        """Local axiom fired __invalidate__ -> withdraw FIB + flood OIN."""
        oin = OIN(origin_id=self.node_id, individual=individual,
                  reason="axiom-invalidate")
        with self._state_lock:
            self._seen_oin[oin.oin_id] = time.time()
        self.cfml.withdraw(individual)
        for face in self.interfaces:
            self._send_to(face, oin.to_json())
            self.metrics["oin_sent"] += 1

    def _handle_oin(self, oin: OIN, addr):
        """Purge state referring to the dead individual, then re-flood
        (hop-limited, duplicate-suppressed)."""
        self.metrics["oin_received"] += 1
        with self._state_lock:
            if oin.oin_id in self._seen_oin:
                return
            self._seen_oin[oin.oin_id] = time.time()
        in_face = self._identify_face(addr)
        self._apply_invalidation(oin.individual)
        if oin.hop_limit > 1:
            oin.hop_limit -= 1
            for face in self.interfaces:
                if face != in_face and face != oin.origin_id:
                    self._send_to(face, oin.to_json())
                    self.metrics["oin_sent"] += 1

    def _apply_invalidation(self, individual: str):
        """Remove cache/FIB/ORT state that depends on a dead individual.
        Cache purge is a direct delete: it must NOT re-trigger axioms
        (that would re-originate the OIN and loop the flood)."""
        self.cache.invalidate_individual(individual)
        self.cfml.withdraw(individual)
        # If the dead individual is an adjacent node, drop its face state
        if individual in self.cpd:
            self.cpd[individual] = {}
        with self._state_lock:
            for concept in list(self.ort.keys()):
                self.ort[concept] = [(f, c) for f, c in self.ort[concept]
                                     if f != individual]

    # ------------------------------------------------------------------
    # LSU: link-state knowledge synchronization (hasISLWith instances)
    # ------------------------------------------------------------------
    def originate_lsu(self, adjacencies: list[str]):
        """Announce this node's current up-adjacency set (R6 urgent class).

        Called by the node daemon on startup and whenever a local ISL
        changes state. Floods to all faces; receivers converge their LSDB
        and re-run SPF via on_lsdb_change.
        """
        self._lsu_seq += 1
        lsu = LSU(origin_id=self.node_id, seq=self._lsu_seq,
                  adjacencies=list(adjacencies))
        with self._state_lock:
            self.lsdb[self.node_id] = (lsu.seq, list(adjacencies), time.time())
        for face in self.interfaces:
            self._send_to(face, lsu.to_json())
            self.metrics["lsu_sent"] += 1
        if self.on_lsdb_change:
            self.on_lsdb_change()

    def _handle_lsu(self, lsu: LSU, addr):
        """Merge into LSDB; re-flood only if newer (per-origin seq dedup)."""
        self.metrics["lsu_received"] += 1
        if not lsu.origin_id or lsu.origin_id == self.node_id:
            return
        with self._state_lock:
            known = self.lsdb.get(lsu.origin_id)
            if known is not None and lsu.seq <= known[0]:
                # Lower/equal seq is normally stale — but if our entry has
                # not been refreshed for > lsu_stale_s the origin likely
                # rebooted with a fresh counter; accept to re-converge
                if time.time() - known[2] <= self.lsu_stale_s:
                    return  # stale or duplicate — flood suppressed
            self.lsdb[lsu.origin_id] = (lsu.seq, list(lsu.adjacencies),
                                        time.time())
        in_face = self._identify_face(addr)
        for face in self.interfaces:
            if face != in_face and face != lsu.origin_id:
                self._send_to(face, lsu.to_json())
                self.metrics["lsu_sent"] += 1
        if self.on_lsdb_change:
            self.on_lsdb_change()

    def lsdb_snapshot(self) -> dict[str, list[str]]:
        """Consistent origin -> adjacency view for SPF."""
        with self._state_lock:
            return {origin: list(adj) for origin, (seq, adj, ts)
                    in self.lsdb.items()}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _identify_face(self, addr) -> str:
        """Map UDP source addr to node_id (face)."""
        # Exact (host, port) match first — required when multiple peers
        # share one host (e.g. containerlab/localhost deployments)
        for nid, (host, port) in self._peers.items():
            if host == addr[0] and port == addr[1]:
                return nid
        # Fallback: host-only match (distinct-host deployments where the
        # peer's source port differs from its listening port)
        for nid, (host, port) in self._peers.items():
            if host == addr[0]:
                return nid
        return ""

    def _compute_score(self, iv: dict[str, float], query: OntologyQuery) -> float:
        """Normalized quality score for ranking."""
        score = 0.0
        for c in query.constraints:
            val = iv.get(c.prop, 0.0)
            thr = float(c.threshold)
            if thr > 0:
                score += min(val / thr, 2.0)
        if query.constraints:
            score /= len(query.constraints)
        return score

    def update_cpd_from_local(self):
        """Periodically publish local instances to CPD for adjacent nodes."""
        # Self-instances contribute to all faces
        all_instances = self.cache.get_by_concept("SpaceNetworkEntity")
        for inst in all_instances:
            if inst.individual.startswith(self.node_id):
                for face in self.interfaces:
                    cpd_face = self.cpd.setdefault(face, {})
                    entry = cpd_face.setdefault(
                        inst.concept, CPDEntry(concept=inst.concept)
                    )
                    prop_type = DATA_PROPERTIES.get(inst.prop, ("", "benefit"))[1]
                    try:
                        entry.fold(inst.prop, float(inst.value),
                                   inst.timestamp, prop_type)
                    except (ValueError, TypeError):
                        pass
