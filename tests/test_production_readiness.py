"""Production-readiness regression tests.

Covers the bugs fixed in the production-path review:
  1. ORD reverse-path delivery over real UDP sockets (multi-hop closure)
  2. LowEnergy axiom must not compound (0.5^n decay) across flushes
  3. LRU must not evict when updating an existing key at capacity
  4. CFML must reject unsafe route tokens / keep FIB-map sync on failure
  5. KV-Cache tick() flushes overdue batches without new puts
  6. Routing-table generation: converged ORDs populate ORT + CFML FIB
  7. NDN semantics: Interest aggregation fan-out + dead-nonce dedup
  8. CPA proactive advertisement fills cold CPD (guided vs ring search)
  9. OIN invalidation purges cache/FIB and floods hop-limited exactly once
 10. CFML IPv6 LPM: longest prefix wins, host route beats /64, withdraw
 11. Simulator IPv6 data plane: point-to-point delivery over prefix FIBs,
     control-plane messaging independent of the data-plane FIB
 12. Cache aging: freshness decay, aging-first eviction, query freshness
     threshold (min_freshness) enforced locally and via OQR
 13. In-network route aggregation: plane /32 + exception /48, LPM correct
 14. Review fixes + routing completeness: housekeeping under sustained
     traffic, LSU seq restart re-acceptance, deterministic SPF/aggregation,
     full-coverage FIB (every reachable node LPM-resolves), unidirectional
     link exclusion, partition withdraw
"""
import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ocrm.core.kv_cache import KVCache
from ocrm.core.ontology import Instance, make_key
from ocrm.core.reasoning import OntologyQuery, Constraint
from ocrm.protocol.router import OCRMRouter, OQR, ORD, CPA, OIN
from ocrm.cfml import CFML, Route

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# 1. Multi-hop query closure over real UDP (A -> B -> C, ORD back to A)
# ---------------------------------------------------------------------------
def test_udp_query_closure():
    print("[1] UDP multi-hop query closure (OQR out, ORD back)")
    base = 19900
    ids = ["NODE-A", "NODE-B", "NODE-C"]
    caches = {nid: KVCache(nid, t_batch_ms=1.0) for nid in ids}

    # Only NODE-C holds a satisfying GPU instance
    key = make_key("ALPHA", "GPUComputeNode", "hasAvailableFLOPS", "GPU-C1")
    caches["NODE-C"].put(Instance(
        key=key, individual="GPU-C1", concept="GPUComputeNode",
        prop="hasAvailableFLOPS", value=20.0, ttl=60.0))
    caches["NODE-C"].force_flush()

    # Line topology A - B - C
    routers = {
        "NODE-A": OCRMRouter("NODE-A", ["NODE-B"], caches["NODE-A"], port=base),
        "NODE-B": OCRMRouter("NODE-B", ["NODE-A", "NODE-C"], caches["NODE-B"], port=base + 1),
        "NODE-C": OCRMRouter("NODE-C", ["NODE-B"], caches["NODE-C"], port=base + 2),
    }
    ports = {"NODE-A": base, "NODE-B": base + 1, "NODE-C": base + 2}
    for rid, r in routers.items():
        for peer in r.interfaces:
            r.add_peer(peer, "127.0.0.1", ports[peer])
        r.start()

    try:
        q = OntologyQuery(
            required_class="GPUComputeNode",
            constraints=[Constraint("hasAvailableFLOPS", ">=", 10.0)],
            max_results=1,
        )
        routers["NODE-A"].send_query(q)
        # ORD aggregation waits up to 1s at each PQT hop; allow the full
        # reverse path (C->B->A) to complete
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if routers["NODE-A"].metrics["convergence_events"]:
                break
            time.sleep(0.05)

        check("OQR reached NODE-C and was satisfied there",
              routers["NODE-C"].metrics["queries_satisfied_local"] >= 1)
        check("ORD traversed reverse path back to originator NODE-A",
              len(routers["NODE-A"].metrics["convergence_events"]) >= 1,
              f"events={routers['NODE-A'].metrics['convergence_events']}")
        # Admitted write sits in the batch window until the recv thread's
        # idle tick (0.5s socket timeout) drives the flush — wait one cycle
        time.sleep(0.7)
        check("R8 admission cached the satisfier at NODE-A",
              len(caches["NODE-A"].get_by_concept("GPUComputeNode")) >= 1)
        check("PQT cleaned up at originator",
              "local" not in [f for e in routers["NODE-A"].pqt.values()
                              for f in e.in_faces])

        # 6. Routing-table generation from the converged query
        routers["NODE-A"].cfml.force_flush()
        routers["NODE-B"].cfml.force_flush()
        ort_a = routers["NODE-A"].routing_table()
        check("ORT generated at originator (concept -> face)",
              ort_a.get("GPUComputeNode") == [("NODE-B", 2.0)],
              f"ort={ort_a}")
        fib_a = routers["NODE-A"].cfml.lookup("GPU-C1")
        check("FIB entry for satisfier installed at NODE-A via NODE-B",
              fib_a is not None and fib_a.next_hops == ["NODE-B"],
              f"fib={fib_a}")
        fib_b = routers["NODE-B"].cfml.lookup("NODE-C")
        check("transit NODE-B learned satisfier-node route via NODE-C",
              fib_b is not None and fib_b.next_hops == ["NODE-C"],
              f"fib={fib_b}")
        check("routes_learned metric counted on reverse path",
              routers["NODE-A"].metrics["routes_learned"] >= 1 and
              routers["NODE-B"].metrics["routes_learned"] >= 1)
    finally:
        for r in routers.values():
            r.stop()


# ---------------------------------------------------------------------------
# 2. Axiom must not compound across flushes
# ---------------------------------------------------------------------------
def test_axiom_no_compounding():
    print("[2] LowEnergy->ThrottleCompute does not compound")
    cache = KVCache("NODE-X", t_batch_ms=1.0)
    for prop, val in [("hasEnergyReserve", 100.0), ("hasAvailableFLOPS", 8.0)]:
        cache.put(Instance(
            key=make_key("ALPHA", "GPUComputeNode", prop, "GPU-X"),
            individual="GPU-X", concept="GPUComputeNode",
            prop=prop, value=val, ttl=300.0))
    cache.force_flush()
    v1 = cache.get(make_key("ALPHA", "GPUComputeNode", "hasAvailableFLOPS", "GPU-X")).value
    # Trigger three more flush cycles on the same individual
    for _ in range(3):
        cache.put(Instance(
            key=make_key("ALPHA", "GPUComputeNode", "hasEnergyReserve", "GPU-X"),
            individual="GPU-X", concept="GPUComputeNode",
            prop="hasEnergyReserve", value=100.0, ttl=300.0))
        cache.force_flush()
    v2 = cache.get(make_key("ALPHA", "GPUComputeNode", "hasAvailableFLOPS", "GPU-X")).value
    check("first throttle applied once (8.0 -> 4.0)", v1 == 4.0, f"v1={v1}")
    check("value stable across repeated flushes", v2 == 4.0, f"v2={v2}")


# ---------------------------------------------------------------------------
# 3. LRU update-in-place must not evict at capacity
# ---------------------------------------------------------------------------
def test_lru_update_no_evict():
    print("[3] LRU: updating an existing key at capacity evicts nothing")
    cache = KVCache("NODE-Y", max_entries=4, t_batch_ms=1.0)
    for i in range(4):
        cache.put(Instance(
            key=f"/space/ALPHA/NetworkNode/p/{i}", individual=f"N{i}",
            concept="NetworkNode", prop="p", value=i, ttl=300.0))
    cache.force_flush()
    ev0 = cache.stats.evictions
    # Update existing key 0 while full
    cache.put(Instance(
        key="/space/ALPHA/NetworkNode/p/0", individual="N0",
        concept="NetworkNode", prop="p", value=99, ttl=300.0))
    cache.force_flush()
    check("no eviction on in-place update", cache.stats.evictions == ev0,
          f"evictions {ev0} -> {cache.stats.evictions}")
    check("all 4 entries still present",
          all(cache.get(f"/space/ALPHA/NetworkNode/p/{i}") for i in range(4)))


# ---------------------------------------------------------------------------
# 4. CFML safety + FIB map consistency
# ---------------------------------------------------------------------------
def test_cfml_safety():
    print("[4] CFML: unsafe tokens rejected, dry-run ECMP merged atomically")
    cfml = CFML("NODE-Z", dry_run=True)
    cfml.enqueue(Route(destination="2001:db8::1/128", next_hops=["fe80::1"]), urgent=True)
    check("legit route installed", cfml.lookup("2001:db8::1/128") is not None)
    # ECMP merge through the pending path
    cfml.enqueue(Route(destination="D1", next_hops=["fe80::a"], cost=1.0))
    cfml.enqueue(Route(destination="D1", next_hops=["fe80::b"], cost=1.5))
    cfml.force_flush()
    r = cfml.lookup("D1")
    check("conditional ECMP merged both next-hops",
          r is not None and set(r.next_hops) == {"fe80::a", "fe80::b"},
          f"r={r}")
    # Unsafe token must never reach the kernel (validated in non-dry-run path)
    real = CFML("NODE-Z2", dry_run=False)
    ok = real._write_fib(Route(destination="D2; rm -rf /", next_hops=["fe80::1"]))
    check("unsafe destination rejected before subprocess", ok is False)
    # Explicit withdraw removes from map
    cfml.withdraw("D1")
    check("withdraw removes installed entry", cfml.lookup("D1") is None)


# ---------------------------------------------------------------------------
# 5. tick() flushes overdue batches without new puts
# ---------------------------------------------------------------------------
def test_cache_tick():
    print("[5] KV-Cache tick() drains overdue batch")
    cache = KVCache("NODE-T", t_batch_ms=10.0)
    cache.put(Instance(
        key="/space/ALPHA/NetworkNode/p/t", individual="NT",
        concept="NetworkNode", prop="p", value=1, ttl=300.0))
    time.sleep(0.05)  # past the 10ms deadline, no further put arrives
    cache.tick()
    check("batch committed by tick alone",
          cache.get("/space/ALPHA/NetworkNode/p/t") is not None)


# ---------------------------------------------------------------------------
# 6. NDN semantics: Interest aggregation fan-out + dead-nonce list
# ---------------------------------------------------------------------------
def test_ndn_semantics():
    print("[6] NDN PIT: multi-face aggregation, Data fan-out, dead-nonce")
    sent = []  # capture outbound messages without sockets

    cache = KVCache("NODE-R", t_batch_ms=1.0)
    r = OCRMRouter("NODE-R", ["F-X", "F-Y", "F-Z"], cache, port=0)
    for i, face in enumerate(r.interfaces):
        r.add_peer(face, f"10.0.0.{i + 1}", 9000)
    r._send_to = lambda target, data: sent.append(target)  # stub transport

    oqr = OQR(query_id="q-ndn-1", required_class="GPUComputeNode",
              constraints=[], source_id="F-X", path_vector=["F-X"],
              response_quota=1)
    # Interest from face X, then a duplicate of the SAME Interest from face Y
    r._handle_oqr(oqr, ("10.0.0.1", 9000))
    dup = OQR(query_id="q-ndn-1", required_class="GPUComputeNode",
              constraints=[], source_id="F-Y", path_vector=["F-Y"],
              response_quota=1)
    r._handle_oqr(dup, ("10.0.0.2", 9000))
    entry = r.pqt.get("q-ndn-1")
    check("PIT aggregated both requesting faces",
          entry is not None and entry.in_faces == ["F-X", "F-Y"],
          f"entry={entry}")

    # Data arrives from face Z -> must fan out to BOTH X and Y
    sent.clear()
    ord_msg = ORD(query_id="q-ndn-1", satisfier_id="GPU-9",
                  satisfier_concept="GPUComputeNode",
                  instance_vector={"hasAvailableFLOPS": 20.0},
                  path_vector=["F-X", "NODE-R", "F-Z"], via=["F-Z"])
    r._handle_ord(ord_msg, ("10.0.0.3", 9000))
    check("Data fanned out to every pending face",
          sent.count("F-X") == 1 and sent.count("F-Y") == 1, f"sent={sent}")
    check("PIT entry consumed after satisfaction", "q-ndn-1" not in r.pqt)

    # A late looping duplicate of the satisfied Interest must be dropped
    late = OQR(query_id="q-ndn-1", required_class="GPUComputeNode",
               constraints=[], source_id="F-X", path_vector=["F-X"],
               response_quota=1)
    r._handle_oqr(late, ("10.0.0.1", 9000))
    check("dead-nonce blocks re-processing of satisfied Interest",
          "q-ndn-1" not in r.pqt)


# ---------------------------------------------------------------------------
# 7. CPA: proactive digest advertisement (cold-start fix)
# ---------------------------------------------------------------------------
def test_cpa_advertisement():
    print("[7] CPA: proactive digest fills cold CPD, enables guided forwarding")
    cache_p = KVCache("NODE-P", t_batch_ms=1.0)
    cache_p.put(Instance(
        key=make_key("ALPHA", "GPUComputeNode", "hasAvailableFLOPS", "GPU-P1"),
        individual="GPU-P1", concept="GPUComputeNode",
        prop="hasAvailableFLOPS", value=32.0, ttl=300.0))
    cache_p.force_flush()
    rp = OCRMRouter("NODE-P", ["NODE-Q"], cache_p, port=0)
    out = []
    rp._send_to = lambda t, d: out.append((t, d))
    rp.send_cpa()
    check("CPA emitted to neighbor", len(out) == 1 and out[0][0] == "NODE-Q",
          f"out={[t for t, _ in out]}")

    rq = OCRMRouter("NODE-Q", ["NODE-P", "NODE-S"], KVCache("NODE-Q"), port=0)
    rq.add_peer("NODE-P", "10.1.0.1", 9000)
    rq.add_peer("NODE-S", "10.1.0.2", 9000)
    rq._handle_cpa(CPA.from_json(out[0][1]), ("10.1.0.1", 9000))
    entry = rq.cpd.get("NODE-P", {}).get("GPUComputeNode")
    check("cold CPD filled from CPA",
          entry is not None and entry.bounds.get("hasAvailableFLOPS") == 32.0,
          f"entry={entry}")

    # With a warm CPD the first query is guided unicast, not ring flooding
    fwd = []
    rq._send_to = lambda t, d: fwd.append(t)
    oqr = OQR(required_class="GPUComputeNode",
              constraints=[{"prop": "hasAvailableFLOPS", "op": ">=",
                            "threshold": 10.0}],
              source_id="NODE-Q", path_vector=["NODE-Q"])
    rq._forward_oqr(oqr)
    check("first query CPD-guided to advertising face only",
          fwd == ["NODE-P"], f"fwd={fwd}")


# ---------------------------------------------------------------------------
# 8. OIN: urgent invalidation notice (withdraw analog)
# ---------------------------------------------------------------------------
def test_oin_invalidation():
    print("[8] OIN: purges cache/FIB, hop-limited flood, duplicate-suppressed")
    cache = KVCache("NODE-V", t_batch_ms=1.0)
    key = make_key("ALPHA", "LaserISL", "hasAvailableBandwidth", "LINK-7-8")
    cache.put(Instance(key=key, individual="LINK-7-8", concept="LaserISL",
                       prop="hasAvailableBandwidth", value=10.0, ttl=300.0))
    cache.force_flush()
    r = OCRMRouter("NODE-V", ["F-1", "F-2", "F-3"], cache, port=0)
    for i, f in enumerate(r.interfaces):
        r.add_peer(f, f"10.2.0.{i + 1}", 9000)
    r.cfml.enqueue(Route(destination="LINK-7-8", next_hops=["F-1"]), urgent=True)
    sent = []
    r._send_to = lambda t, d: sent.append(t)

    oin = OIN(origin_id="F-1", individual="LINK-7-8", reason="link-down")
    r._handle_oin(oin, ("10.2.0.1", 9000))
    check("cached instances of dead individual purged", cache.get(key) is None)
    check("FIB entry withdrawn", r.cfml.lookup("LINK-7-8") is None)
    check("re-flooded to other faces only", set(sent) == {"F-2", "F-3"},
          f"sent={sent}")

    sent.clear()
    r._handle_oin(OIN(oin_id=oin.oin_id, origin_id="F-1",
                      individual="LINK-7-8"), ("10.2.0.2", 9000))
    check("duplicate OIN suppressed", sent == [], f"sent={sent}")

    # Local origin: urgent bandwidth<=0 write -> axiom __invalidate__ -> OIN
    sent.clear()
    cache.put(Instance(key=key, individual="LINK-7-8", concept="LaserISL",
                       prop="hasAvailableBandwidth", value=0.0, ttl=300.0),
              urgent=True)
    check("local __invalidate__ originates OIN on all faces",
          set(sent) == {"F-1", "F-2", "F-3"}, f"sent={sent}")


# ---------------------------------------------------------------------------
# 10. CFML IPv6 longest-prefix match
# ---------------------------------------------------------------------------
def test_cfml_ipv6_lpm():
    print("[10] CFML IPv6 LPM: longest prefix wins, host route beats /64")
    cfml = CFML("NODE-L", dry_run=True)
    cfml.enqueue(Route(destination="fd00::/16", next_hops=["fd00:0:0:1::1"]),
                 urgent=True)
    cfml.enqueue(Route(destination="fd00:0:2:3::/64", next_hops=["fd00:0:0:2::1"]),
                 urgent=True)

    r = cfml.lookup_lpm("fd00:0:2:3::1")
    check("/64 preferred over /16",
          r is not None and r.next_hops == ["fd00:0:0:2::1"],
          f"got={r.next_hops if r else None}")
    r = cfml.lookup_lpm("fd00:0:9:9::1")
    check("falls back to covering /16",
          r is not None and r.next_hops == ["fd00:0:0:1::1"])

    # Host route outranks the /64 (exact-match fast path)
    cfml.enqueue(Route(destination="fd00:0:2:3::1", next_hops=["fd00:0:0:3::1"]),
                 urgent=True)
    r = cfml.lookup_lpm("fd00:0:2:3::1")
    check("host route beats /64",
          r is not None and r.next_hops == ["fd00:0:0:3::1"])

    cfml.withdraw("fd00:0:2:3::/64")
    cfml.withdraw("fd00:0:2:3::1")
    r = cfml.lookup_lpm("fd00:0:2:3::1")
    check("withdraw removes prefix from LPM table",
          r is not None and r.next_hops == ["fd00:0:0:1::1"])
    check("unroutable address returns None", cfml.lookup_lpm("2001:db8::1") is None)
    check("individual-ID routes never satisfy address lookups",
          cfml.lookup_lpm("GPU-C1") is None)


# ---------------------------------------------------------------------------
# 11. Simulator IPv6 data plane: point-to-point delivery
# ---------------------------------------------------------------------------
def test_simulator_ipv6_dataplane():
    print("[11] Simulator IPv6 data plane: p2p delivery over prefix FIBs")
    import run_experiment as rx

    nodes, links = rx.build_walker_topology(2, 3)   # 6 sats, plus-grid
    node_map = {n.node_id: n for n in nodes}
    rx.LinkDynamicsEngine(links, seed=7)            # init lifetimes, all up
    dp = rx.DataPlane(node_map, links)

    src, dst = "SAT-0000", "SAT-0102"
    path = dp.compute_path(src, dst, sim_time=0.0)
    check("path computed on control plane", path is not None, f"path={path}")
    dp.install_path(path, urgent=True)

    fib = node_map[src].cfml.lookup_lpm(node_map[dst].ipv6_addr)
    check("FIB entry is an IPv6 prefix route",
          fib is not None and fib.destination == node_map[dst].ipv6_prefix
          and all(":" in nh for nh in fib.next_hops),
          f"fib={fib}")

    ok = dp.send_packet(src, dst)
    check("point-to-point IPv6 packet delivered", ok,
          f"stats={dp.stats}")
    check("delay/hops accounted", dp.stats["hops_sum"] >= 1
          and dp.stats["delay_sum_ms"] > 0.0)

    # No return route installed -> reverse direction must drop (no_route),
    # proving forwarding really consults the FIB rather than topology
    ok_rev = dp.send_packet(dst, src)
    check("reverse without FIB entry drops (no_route)",
          not ok_rev and dp.stats["drop_no_route"] == 1)

    # Break a link on the path: data plane drops (link_down) until the
    # control plane repairs -- the bounded inconsistency window
    link = dp.link_map[(path[0], path[1])]
    link.up = False
    ok_broken = dp.send_packet(src, dst)
    check("down link on path drops packet (inconsistency window)",
          not ok_broken and dp.stats["drop_link_down"] == 1)

    # Control-plane repair goes around the dead link and reinstalls
    new_path = dp.compute_path(src, dst, sim_time=0.0)
    check("control plane reroutes around dead link",
          new_path is not None and not rx._path_uses_link(
              new_path, path[0], path[1]))
    dp.install_path(new_path, urgent=True)
    check("delivery restored after repair", dp.send_packet(src, dst))

    # Independence: routing-protocol messaging (NDN faces / neighbor UDP)
    # never consults this FIB -- wiping every data-plane FIB entry must not
    # affect a control-plane query exchanged between routers
    for n in nodes:
        for rt in list(n.cfml.get_routes()):
            n.cfml.withdraw(rt.destination)
    check("data-plane FIBs wiped", all(
        n.cfml.installed_count == 0 for n in nodes))
    node_map[src].cache.put(Instance(
        key=make_key("ALPHA", "NetworkNode", "hasEnergyReserve", src),
        individual=src, concept="NetworkNode", prop="hasEnergyReserve",
        value=4200.0, ttl=60.0, instance_type="measured"), urgent=True)
    qr = node_map[src].engine.reason(rx.OntologyQuery(
        required_class="NetworkNode",
        constraints=[rx.Constraint("hasEnergyReserve", ">=", 0.0)]))
    check("control-plane query still works with empty data-plane FIB",
          qr is not None and len(qr.satisfiers) >= 1,
          f"satisfiers={qr.satisfiers if qr else None}")



# ---------------------------------------------------------------------------
# 12. Cache aging: freshness decay + aging-first eviction + query threshold
# ---------------------------------------------------------------------------
def test_cache_aging():
    print("\n[12] cache aging (freshness decay / eviction / query threshold)")
    now = time.time()

    # freshness 线性衰减
    inst = Instance(key="k", individual="i", concept="NetworkNode",
                    prop="hasEnergyReserve", value=1.0,
                    timestamp=now - 5.0, ttl=10.0)
    check("freshness decays linearly with age",
          abs(inst.freshness(now) - 0.5) < 0.01, f"f={inst.freshness(now)}")
    check("expired instance has zero freshness",
          Instance(key="k2", individual="i", concept="NetworkNode",
                   prop="p", value=1.0, timestamp=now - 20.0,
                   ttl=10.0).freshness(now) == 0.0)
    check("virtual instance never ages",
          Instance(key="k3", individual="i", concept="NetworkNode",
                   prop="p", value=1.0, timestamp=now - 999.0,
                   ttl=10.0, virtual=True).freshness(now) == 1.0)

    # 老化优先淘汰: 容量 2, 先写入一新一旧, 第三条应淘汰旧的而非 LRU 头
    cache = KVCache("aging", max_entries=2, constellation="T")
    stale = Instance(key=make_key("T", "NetworkNode", "p1", "old"),
                     individual="old", concept="NetworkNode", prop="p1",
                     value=1.0, timestamp=now - 50.0, ttl=60.0)
    fresh = Instance(key=make_key("T", "NetworkNode", "p1", "new"),
                     individual="new", concept="NetworkNode", prop="p1",
                     value=1.0, timestamp=now, ttl=60.0)
    cache.put(stale, urgent=True)
    cache.put(fresh, urgent=True)
    cache.get(stale.key)   # 访问旧条目, 若是纯 LRU 则它会被保护
    cache.put(Instance(key=make_key("T", "NetworkNode", "p1", "third"),
                       individual="third", concept="NetworkNode", prop="p1",
                       value=1.0, timestamp=now, ttl=60.0), urgent=True)
    check("aging-first eviction removes stalest entry (not LRU order)",
          cache.get(stale.key) is None and cache.get(fresh.key) is not None)

    # tick() 老化扫除计数
    c2 = KVCache("sweeper", constellation="T")
    c2.put(Instance(key=make_key("T", "NetworkNode", "p", "dying"),
                    individual="dying", concept="NetworkNode", prop="p",
                    value=1.0, timestamp=now - 5.0, ttl=0.1), urgent=True)
    c2.tick()
    check("tick() sweeps aged-out entries", c2.stats.aged_out == 1,
          f"aged_out={c2.stats.aged_out}")

    # 查询新鲜度门限: 同一实例, 门限以下拒绝 / 门限以上接受
    from ocrm.core.reasoning import ReasoningEngine
    c3 = KVCache("aged-q", constellation="T")
    c3.put(Instance(key=make_key("T", "NetworkNode", "hasEnergyReserve", "n1"),
                    individual="n1", concept="NetworkNode",
                    prop="hasEnergyReserve", value=5000.0,
                    timestamp=now - 8.0, ttl=10.0), urgent=True)  # f=0.2
    eng = ReasoningEngine(c3)
    q_strict = OntologyQuery(required_class="NetworkNode",
                             constraints=[Constraint("hasEnergyReserve",
                                                     ">=", 0.0)],
                             min_freshness=0.5)
    q_loose = OntologyQuery(required_class="NetworkNode",
                            constraints=[Constraint("hasEnergyReserve",
                                                    ">=", 0.0)],
                            min_freshness=0.1)
    check("stale instance rejected above freshness threshold",
          len(eng.reason(q_strict).satisfiers) == 0)
    check("same instance accepted below threshold",
          len(eng.reason(q_loose).satisfiers) == 1)

    # OQR 携带 min_freshness 跨节点生效 (序列化往返)
    oqr = OQR(required_class="NetworkNode", min_freshness=0.7)
    check("OQR carries min_freshness across the wire",
          OQR.from_json(oqr.to_json()).min_freshness == 0.7)


# ---------------------------------------------------------------------------
# 13. In-network route aggregation: plane /32 + exception /48
# ---------------------------------------------------------------------------
def test_route_aggregation():
    print("\n[13] in-network route aggregation (plane /32 + exception /48)")
    import os
    os.environ.setdefault("SAT_NAME", "sat0-0")   # 仅供模块级常量, 不建 daemon
    from ocrm.node_daemon import sat_prefix, plane_of, plane_prefix

    check("plane prefix covers member /48s",
          plane_prefix(2) == "fcbb:2::/32" and
          sat_prefix("sat2-5") == "fcbb:2:5::/48" and
          plane_of("sat2-5") == 2)

    # CFML LPM: /48 例外路由优于 /32 聚合路由
    cfml = CFML("agg-node", dry_run=True)
    cfml.enqueue(Route(destination="fcbb:2::/32",
                       next_hops=["fd00:100::10"], cost=1.0,
                       semantic_source="lsu-spf-agg"), urgent=True)
    cfml.enqueue(Route(destination="fcbb:2:5::/48",
                       next_hops=["fd00:100::20"], cost=1.0,
                       semantic_source="lsu-spf"), urgent=True)
    via_agg = cfml.lookup_lpm("fcbb:2:1::1")
    via_exc = cfml.lookup_lpm("fcbb:2:5::1")
    check("aggregate /32 serves plane members",
          via_agg is not None and via_agg.next_hops == ["fd00:100::10"])
    check("exception /48 wins over aggregate by LPM",
          via_exc is not None and via_exc.next_hops == ["fd00:100::20"])
    check("FIB compressed: 2 entries stand in for 6 destinations",
          cfml.installed_count == 2)

    # 聚合决策逻辑 (与 node_daemon._run_spf 同构): 多数首跳聚合 + 例外 /48
    first_hop = {"sat2-0": "nbrL", "sat2-1": "nbrL", "sat2-2": "nbrL",
                 "sat2-3": "nbrL", "sat2-4": "nbrL", "sat2-5": "nbrR"}
    hop_count = {}
    for fh in first_hop.values():
        hop_count[fh] = hop_count.get(fh, 0) + 1
    majority = max(hop_count, key=hop_count.get)
    exceptions = [d for d, fh in first_hop.items() if fh != majority]
    check("majority/exception split is correct",
          majority == "nbrL" and exceptions == ["sat2-5"])


# ---------------------------------------------------------------------------
# 14. Review fixes + routing completeness
# ---------------------------------------------------------------------------
def _make_daemon(name, nbrs):
    """NodeDaemon with dry-run CFML: SPF/aggregation logic only, no root."""
    import os
    from ocrm import node_daemon as nd_mod
    os.environ["SAT_NAME"] = name
    os.environ["SAT_PREFIX"] = nd_mod.sat_prefix(name)
    os.environ["OCRM_FACES"] = ",".join(
        "%s|eth%d|fd00:14::%d" % (nbr, i, i)
        for i, nbr in enumerate(nbrs, 1))
    nd = nd_mod.NodeDaemon()
    dry = CFML(name, dry_run=True)
    nd.cfml = dry
    nd.router.cfml = dry
    return nd


def test_review_fixes_completeness():
    print("\n[14] review fixes + routing completeness")
    from ocrm.node_daemon import sat_prefix as sp

    # --- 14a. housekeeping is NOT starved by sustained traffic ----------
    cache = KVCache("HK", t_batch_ms=10.0)
    r = OCRMRouter("HK", [], cache, port=19977)
    r.start()
    cache.put(Instance(
        key="/space/ALPHA/NetworkNode/hk/t", individual="HK-I",
        concept="NetworkNode", prop="hk", value=1, ttl=300.0))
    import socket as _s
    spam = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
    t0 = time.time()
    while time.time() - t0 < 1.2:      # datagram every 20ms << 500ms timeout
        spam.sendto(b"not-json", ("127.0.0.1", 19977))
        time.sleep(0.02)               # recvfrom never times out
    spam.close()
    got = cache.get("/space/ALPHA/NetworkNode/hk/t")
    r.stop()
    check("batch flushed by housekeeping despite sustained traffic",
          got is not None)

    # --- 14b. LSU restart: lower seq re-accepted after staleness --------
    from ocrm.protocol.router import LSU
    r2 = OCRMRouter("RX", [], KVCache("RX"))
    r2._handle_lsu(LSU(origin_id="Y", seq=5, adjacencies=["RX"]),
                   ("127.0.0.1", 1))
    r2._handle_lsu(LSU(origin_id="Y", seq=1, adjacencies=["RX", "Z"]),
                   ("127.0.0.1", 1))
    check("fresh entry still rejects lower seq", r2.lsdb["Y"][0] == 5)
    with r2._state_lock:               # age the entry past lsu_stale_s
        r2.lsdb["Y"] = (5, ["RX"], time.time() - 20.0)
    r2._handle_lsu(LSU(origin_id="Y", seq=1, adjacencies=["RX", "Z"]),
                   ("127.0.0.1", 1))
    check("stale entry accepts restarted origin (seq reset)",
          r2.lsdb["Y"][0] == 1 and r2.lsdb["Y"][1] == ["RX", "Z"])

    # --- 14c/d/e/f. SPF completeness on a 2-plane x 4-sat grid ----------
    adj = {
        "sat0-0": ["sat0-1", "sat0-3", "sat1-0"],
        "sat0-1": ["sat0-0", "sat0-2", "sat1-1"],
        "sat0-2": ["sat0-1", "sat0-3", "sat1-2"],
        "sat0-3": ["sat0-0", "sat0-2", "sat1-3"],
        "sat1-0": ["sat0-0", "sat1-1", "sat1-3"],
        "sat1-1": ["sat0-1", "sat1-0", "sat1-2"],
        "sat1-2": ["sat0-2", "sat1-1", "sat1-3"],
        "sat1-3": ["sat0-3", "sat1-0", "sat1-2"],
    }

    def build(order):
        nd = _make_daemon("sat0-0", adj["sat0-0"])
        now = time.time()
        for origin in order:
            nd.router.lsdb[origin] = (1, list(adj[origin]), now)
        # unidirectional claim: sat9-9 lists sat0-0, but not vice versa
        nd.router.lsdb["sat9-9"] = (1, ["sat0-0"], now)
        nd._run_spf()
        return nd

    nd = build(sorted(adj))
    host = lambda dest: sp(dest).split("/")[0] + "1"   # fcbb:p:s::1

    # 14c completeness: every reachable node LPM-resolves via a live face
    peer_addrs = {peer for _, peer in nd.faces.values()}
    unresolved = [d for d in adj if d != "sat0-0"
                  and (nd.cfml.lookup_lpm(host(d)) is None
                       or nd.cfml.lookup_lpm(host(d)).next_hops[0]
                       not in peer_addrs)]
    check("all 7 reachable nodes LPM-resolve to a live face",
          not unresolved, f"unresolved={unresolved}")

    # 14d aggregation coverage: /32 + exceptions, no redundant /48
    installed = {rt.destination for rt in nd.cfml.get_routes()}
    check("remote plane compressed to /32 + exception /48s",
          "fcbb:1::/32" in installed
          and "fcbb:1:1::/48" not in installed
          and "fcbb:1:2::/48" not in installed
          and "fcbb:1:0::/48" in installed
          and "fcbb:1:3::/48" in installed, f"installed={installed}")
    check("own plane never aggregated (no fcbb:0::/32)",
          "fcbb:0::/32" not in installed)

    # 14e unidirectional adjacency excluded from FIB
    check("unidirectional link (sat9-9) gets no route",
          nd.cfml.lookup_lpm("fcbb:9:9::1") is None)

    # 14f determinism: LSDB insertion order must not change the FIB
    nd2 = build(sorted(adj, reverse=True))
    fib1 = {rt.destination: tuple(rt.next_hops) for rt in nd.cfml.get_routes()}
    fib2 = {rt.destination: tuple(rt.next_hops) for rt in nd2.cfml.get_routes()}
    check("SPF + aggregation deterministic across LSDB orders",
          fib1 == fib2, f"{fib1} != {fib2}")

    # 14g partition: dead plane withdrawn, surviving side keeps routes
    with nd.router._state_lock:
        for origin in list(nd.router.lsdb):
            if origin.startswith("sat1-"):
                del nd.router.lsdb[origin]
    nd._run_spf()
    check("partitioned plane fully withdrawn",
          all(nd.cfml.lookup_lpm(host(d)) is None
              for d in adj if d.startswith("sat1-")))
    check("surviving plane unaffected by withdraw",
          all(nd.cfml.lookup_lpm(host(d)) is not None
              for d in adj if d.startswith("sat0-") and d != "sat0-0"))


if __name__ == "__main__":
    test_udp_query_closure()
    test_axiom_no_compounding()
    test_lru_update_no_evict()
    test_cfml_safety()
    test_cache_tick()
    test_ndn_semantics()
    test_cpa_advertisement()
    test_oin_invalidation()
    test_cfml_ipv6_lpm()
    test_simulator_ipv6_dataplane()
    test_cache_aging()
    test_route_aggregation()
    test_review_fixes_completeness()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
