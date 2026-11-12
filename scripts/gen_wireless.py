#!/usr/bin/env python3
"""无线自组网 (MANET) containerlab 拓扑生成器.

模型: 随机几何图 (RGG) — N 个节点撒在单位正方形, 距离 < radius 的
节点对之间存在无线链路 (无线电覆盖半径模型)。生成同构双栈 lab:

  out-wifi-ocrm/wifi-ocrm.clab.yml    OCRM 真实协议栈
  out-wifi-babel/wifi-babel.clab.yml  FRR babeld (RFC 8966, MANET/mesh 路由)
  out-wifi/links.json                 共享链路清单 (churn 调度用)

节点命名沿用 sat0-<i> (i<16, 单"面"), 使 OCRM 守护进程的
fcbb:0:<i:x>::/48 前缀方案直接复用; 无线场景所有节点同面,
轨道面聚合自然不触发 (符合 MANET 无层级结构的事实)。

局限 (诚实声明): veth 点对点链路模拟的是 MANET 的"邻接图"而非共享
无线信道 — 无载波竞争/干扰/隐藏终端; 移动性以链路通断 (churn) 近似。

用法: python3 scripts/gen_wireless.py [--nodes 16] [--radius 0.34] [--seed 42]
"""
import argparse
import json
import math
import os
import random
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from topogen import frrgen  # noqa: E402  (babel_conf / daemons_file)


def node_name(i):
    return "sat0-%d" % i


def loopback(i):
    return "fcbb:0:%x::1/128" % i


def prefix(i):
    return "fcbb:0:%x::/48" % i


def link_addr_pair(k):
    """第 k 条无线链路的 /127 互联地址对 (fd00:200::/64 段, 与星座网段错开)."""
    return "fd00:200::%x" % (2 * k), "fd00:200::%x" % (2 * k + 1)


def build_rgg(n, radius, seed):
    """随机几何图: 返回 (positions, edges). 半径逐步放大直到连通."""
    rng = random.Random(seed)
    pos = [(rng.random(), rng.random()) for _ in range(n)]
    r = radius
    while True:
        edges = [(i, j) for i in range(n) for j in range(i + 1, n)
                 if math.dist(pos[i], pos[j]) < r]
        # BFS 连通性检查
        adj = {i: set() for i in range(n)}
        for i, j in edges:
            adj[i].add(j)
            adj[j].add(i)
        seen, stack = {0}, [0]
        while stack:
            for nb in adj[stack.pop()]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if len(seen) == n:
            return pos, edges, r
        r += 0.02   # 半径放大 = 提高发射功率, 直到全网连通


def assign_ifaces(n, edges):
    """每节点接口编号 eth1..ethK (按对端节点号排序, 确定性)."""
    per = {i: [] for i in range(n)}
    for k, (a, b) in enumerate(edges):
        per[a].append((b, k))
        per[b].append((a, k))
    ifname = {}   # (node, link_idx) -> ethX
    for i in range(n):
        for idx, (peer, k) in enumerate(sorted(per[i]), start=1):
            ifname[(i, k)] = "eth%d" % idx
    return ifname


def gen(n, radius, seed):
    pos, edges, r_used = build_rgg(n, radius, seed)
    ifname = assign_ifaces(n, edges)

    links = []
    for k, (a, b) in enumerate(edges):
        aa, ba = link_addr_pair(k)
        links.append({
            "idx": k, "a": node_name(a), "b": node_name(b),
            "a_if": ifname[(a, k)], "b_if": ifname[(b, k)],
            "a_addr": aa, "b_addr": ba,
            "dist": round(math.dist(pos[a], pos[b]), 3),
        })

    # 每节点视图
    node_links = {i: [] for i in range(n)}
    for lk, (a, b) in zip(links, edges):
        node_links[a].append((lk["a_if"], lk["a_addr"], lk["b_addr"],
                              lk["b"]))
        node_links[b].append((lk["b_if"], lk["b_addr"], lk["a_addr"],
                              lk["a"]))
    for v in node_links.values():
        v.sort(key=lambda x: int(x[0].removeprefix("eth")))  # eth2 < eth10

    ocrm_src = os.path.join(BASE, "ocrm")
    entry_src = os.path.join(BASE, "docker", "entrypoint.sh")

    def clab_yaml(stack):
        lab = "wifi-%s" % stack
        mgmt = "172.103.0.0/16" if stack == "ocrm" else "172.104.0.0/16"
        out = ["# 由 scripts/gen_wireless.py 自动生成 (RGG n=%d r=%.2f seed=%d)"
               % (n, r_used, seed),
               "name: %s" % lab, "",
               "mgmt:", "  network: %s-mgmt" % lab,
               "  ipv4-subnet: %s" % mgmt, "",
               "topology:", "  defaults:", "    kind: linux",
               "    image: sat-router:latest", "  nodes:"]
        for i in range(n):
            name = node_name(i)
            ifaces = [x[0] for x in node_links[i]]
            addrs = ["%s=%s/127" % (x[0], x[1]) for x in node_links[i]]
            out.append("    %s:" % name)
            out.append("      binds:")
            if stack == "ocrm":
                out.append("        - %s:/opt/ocrm/ocrm:ro" % ocrm_src)
                out.append("        - %s:/entrypoint.sh:ro" % entry_src)
            else:
                out.append("        - configs/%s/frr.conf:/etc/frr/frr.conf"
                           % name)
                out.append("        - daemons:/etc/frr/daemons")
            out.append("      env:")
            out.append("        SAT_NAME: %s" % name)
            out.append("        SAT_LOOPBACK: %s" % loopback(i))
            out.append("        ISL_IFACES: %s" % ",".join(ifaces))
            if stack == "ocrm":
                out.append("        ROUTING: ocrm")
                out.append("        ISL_ADDRS: %s" % ",".join(addrs))
                out.append("        SAT_PREFIX: %s" % prefix(i))
                faces = ["%s|%s|%s" % (x[3], x[0], x[2])
                         for x in node_links[i]]
                out.append("        OCRM_FACES: %s" % ",".join(faces))
            else:
                out.append("        ROUTING: babel")
        out.append("  links:")
        for lk in links:
            out.append('    - endpoints: ["%s:%s", "%s:%s"]'
                       % (lk["a"], lk["a_if"], lk["b"], lk["b_if"]))
        return "\n".join(out) + "\n"

    # 1. OCRM lab
    d1 = os.path.join(BASE, "out-wifi-ocrm")
    os.makedirs(d1, exist_ok=True)
    with open(os.path.join(d1, "wifi-ocrm.clab.yml"), "w") as f:
        f.write(clab_yaml("ocrm"))

    # 2. Babel lab (frr.conf + daemons)
    d2 = os.path.join(BASE, "out-wifi-babel")
    os.makedirs(d2, exist_ok=True)
    with open(os.path.join(d2, "wifi-babel.clab.yml"), "w") as f:
        f.write(clab_yaml("babel"))
    with open(os.path.join(d2, "daemons"), "w") as f:
        f.write(frrgen.daemons_file(enable=("babeld",)))
    for i in range(n):
        name = node_name(i)
        nd = os.path.join(d2, "configs", name)
        os.makedirs(nd, exist_ok=True)
        itfs = [{"ifname": x[0], "local": x[1], "peer": x[2]}
                for x in node_links[i]]
        with open(os.path.join(nd, "frr.conf"), "w") as f:
            f.write(frrgen.babel_conf(name, loopback(i), prefix(i), itfs,
                                      hello_ms=1000, wireless=True))

    # 3. 共享链路清单
    d3 = os.path.join(BASE, "out-wifi")
    os.makedirs(d3, exist_ok=True)
    with open(os.path.join(d3, "links.json"), "w") as f:
        json.dump({"n": n, "radius": r_used, "seed": seed,
                   "positions": pos, "links": links}, f, indent=1)

    deg = sorted(len(v) for v in node_links.values())
    print("[wireless] RGG: %d 节点 %d 链路, 半径 %.2f, 度数 %d~%d (中位 %d)"
          % (n, len(links), r_used, deg[0], deg[-1], deg[len(deg) // 2]))
    print("[wireless] 输出: out-wifi-ocrm/ out-wifi-babel/ out-wifi/links.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, default=16)
    ap.add_argument("--radius", type=float, default=0.34)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    gen(a.nodes, a.radius, a.seed)
