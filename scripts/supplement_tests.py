#!/usr/bin/env python3
"""补充测试 harness: 路径最优性 / 吞吐 / CPU / 卫星 churn 可用性.

在已有 clab_compare.py / wireless_compare.py 指标之外补齐:
  1. 路径拉伸 (stretch): 全对 ping 回包 TTL 反推转发跳数, 对比拓扑 BFS 最优
     跳数 => 平均/最大拉伸, 最优路径占比, 无持久环路验证 (功能指标)
  2. 数据面吞吐: iperf3 -6 TCP 5s, 邻接对 + 远端对 (性能指标)
  3. 控制面 CPU: docker stats CPUPerc 稳态采样 5x2s (性能指标)
  4. 卫星持续扰动 (LEO 切换仿真): 55s 窗口每 8s down 一条链路 12s
     (确定性种子, 三栈同序列, 与无线 churn 方法对齐), 10 监测对采样
     可用性 + 扰动后全对连通复测 (功能+性能指标)

用法:
  python3 scripts/supplement_tests.py sat-ocrm|sat-bgp|sat-ospf
  python3 scripts/supplement_tests.py wifi-ocrm|wifi-babel   (无 churn, 复用已有)
  python3 scripts/supplement_tests.py report
结果写入 results/supp-<env>-<stack>.json / results/supplement.md
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, "results")

ENVS = {
    "sat-ocrm":  {"topo": "out-ocrm/sat-ocrm.clab.yml",
                  "prefix": "clab-sat-ocrm", "kind": "sat"},
    "sat-bgp":   {"topo": "out-4x6/sat-4x6.clab.yml",
                  "prefix": "clab-sat-4x6", "kind": "sat"},
    "sat-ospf":  {"topo": "out-ospf/sat-ospf.clab.yml",
                  "prefix": "clab-sat-ospf", "kind": "sat"},
    "wifi-ocrm": {"topo": "out-wifi-ocrm/wifi-ocrm.clab.yml",
                  "prefix": "clab-wifi-ocrm", "kind": "wifi"},
    "wifi-babel": {"topo": "out-wifi-babel/wifi-babel.clab.yml",
                   "prefix": "clab-wifi-babel", "kind": "wifi"},
}
SAT_NODES = [f"sat{p}-{s}" for p in range(4) for s in range(6)]
WIFI_NODES = [f"sat0-{i}" for i in range(16)]

# 卫星 churn 参数 (与 wireless_compare.py 对齐)
CHURN_SEED, CHURN_PERIOD, CHURN_DOWN, CHURN_WINDOW = 7, 8.0, 12.0, 55.0
SAT_MONITOR_PAIRS = [
    ("sat0-0", "sat0-3"), ("sat0-0", "sat2-3"), ("sat1-2", "sat3-5"),
    ("sat0-5", "sat3-2"), ("sat1-0", "sat2-4"), ("sat0-1", "sat3-4"),
    ("sat2-0", "sat0-4"), ("sat3-1", "sat1-5"), ("sat0-2", "sat2-5"),
    ("sat1-4", "sat3-0"),
]
# 收敛判定采样对 (与 clab_compare.py CONV_PAIRS 一致)
SAT_CONV = [("sat0-0", "sat0-1"), ("sat0-0", "sat0-3"), ("sat0-0", "sat2-0"),
            ("sat0-0", "sat2-3"), ("sat1-2", "sat3-5"), ("sat0-5", "sat3-2")]
WIFI_CONV = [("sat0-0", "sat0-15"), ("sat0-0", "sat0-8"),
             ("sat0-3", "sat0-12"), ("sat0-5", "sat0-10")]
# 吞吐测量对: (邻接, 远端)
TPUT_PAIRS = {"sat": [("sat0-0", "sat0-1"), ("sat0-0", "sat2-3")],
              "wifi": [("sat0-0", "sat0-1"), ("sat0-0", "sat0-15")]}


def nodes_of(env):
    return SAT_NODES if ENVS[env]["kind"] == "sat" else WIFI_NODES


def loopback(env, name):
    p, s = name.removeprefix("sat").split("-")
    return "fcbb:%x:%x::1" % (int(p), int(s))


def run(cmd, timeout=60, check=False):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {proc.stderr.strip()}")
    return proc


def dexec(env, node, cmd, timeout=15):
    return run(["docker", "exec", f"{ENVS[env]['prefix']}-{node}",
                "sh", "-c", cmd], timeout=timeout)


def ping_out(env, src, dst_addr, wait=1):
    return dexec(env, src,
                 f"ping -6 -c1 -W{wait} -I {loopback(env, src)} {dst_addr}",
                 timeout=wait + 9)


def ping_ok(env, src, dst_addr, wait=1) -> bool:
    return ping_out(env, src, dst_addr, wait).returncode == 0


# ---------------------------------------------------------------------------
# 拓扑解析 + BFS 最优跳数
# ---------------------------------------------------------------------------
def parse_links(env):
    """从 clab.yml 提取 [(nodeA, ifA, nodeB, ifB)] 物理链路表."""
    links = []
    with open(os.path.join(BASE, ENVS[env]["topo"])) as f:
        for line in f:
            m = re.search(r'endpoints:\s*\["([\w-]+):(\w+)",\s*'
                          r'"([\w-]+):(\w+)"\]', line)
            if m:
                links.append(m.groups())
    return links


def bfs_dists(links, nodes):
    adj = {n: set() for n in nodes}
    for a, _, b, _ in links:
        adj[a].add(b)
        adj[b].add(a)
    dist = {}
    for src in nodes:
        d = {src: 0}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in d:
                    d[v] = d[u] + 1
                    q.append(v)
        dist[src] = d
    return dist


def connected_without(links, nodes, down_idxs):
    adj = {n: set() for n in nodes}
    for i, (a, _, b, _) in enumerate(links):
        if i not in down_idxs:
            adj[a].add(b)
            adj[b].add(a)
    seen = {nodes[0]}
    q = deque([nodes[0]])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                q.append(v)
    return len(seen) == len(nodes)


# ---------------------------------------------------------------------------
# 部署 + 收敛等待
# ---------------------------------------------------------------------------
def deploy(env):
    topo = ENVS[env]["topo"]
    print(f"[{env}] destroy 残留 ...")
    run(["containerlab", "destroy", "-t", topo, "--cleanup"], timeout=180)
    print(f"[{env}] deploy ...")
    run(["containerlab", "deploy", "-t", topo, "--reconfigure"],
        timeout=600, check=True)
    pairs = SAT_CONV if ENVS[env]["kind"] == "sat" else WIFI_CONV
    deadline = time.time() + 300
    pending = set(pairs)
    while pending and time.time() < deadline:
        pending = {p for p in pending
                   if not ping_ok(env, p[0], loopback(env, p[1]))}
        if pending:
            time.sleep(1)
    if pending:
        raise RuntimeError(f"未收敛: {pending}")
    print(f"[{env}] 收敛完成, 稳定窗 10s")
    time.sleep(10)


def destroy(env):
    run(["containerlab", "destroy", "-t", ENVS[env]["topo"], "--cleanup"],
        timeout=180)


# ---------------------------------------------------------------------------
# 1. 路径拉伸 (全对 TTL 反推跳数 vs BFS 最优)
# ---------------------------------------------------------------------------
def path_stretch(env):
    nodes = nodes_of(env)
    dist = bfs_dists(parse_links(env), nodes)
    tasks = [(s, d) for s in nodes for d in nodes if s != d]

    def one(t):
        s, d = t
        r = ping_out(env, s, loopback(env, d))
        m = re.search(r"ttl=(\d+)", r.stdout)
        if r.returncode != 0 or not m:
            return t, None
        return t, 64 - int(m.group(1)) + 1     # 回程转发跳数+终点=边数

    hops = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for t, h in ex.map(one, tasks):
            hops[t] = h
    ratios, sub, over, miss = [], 0, [], 0
    for t, h in hops.items():
        opt = dist[t[0]][t[1]]
        if h is None:
            miss += 1
            continue
        ratios.append(h / opt)
        if h < opt:
            sub += 1                            # 理论上不可能 => TTL 异常告警
        elif h > opt:
            over.append((f"{t[0]}->{t[1]}", h, opt))
    out = {"pairs": len(tasks), "measured": len(ratios), "unreachable": miss,
           "avg_stretch": round(sum(ratios) / len(ratios), 4) if ratios else None,
           "max_stretch": round(max(ratios), 4) if ratios else None,
           "optimal_pairs_pct": round(
               100 * sum(1 for r in ratios if r == 1.0) / len(ratios), 2)
           if ratios else None,
           "sub_optimal_anomaly": sub, "worst_pairs": over[:5],
           "loop_free": all(h is not None and h <= 2 * max(
               dist[s].values()) for (s, _), h in hops.items())}
    print(f"[{env}] 拉伸: avg={out['avg_stretch']} max={out['max_stretch']} "
          f"最优占比={out['optimal_pairs_pct']}% 环路自由={out['loop_free']}")
    return out


# ---------------------------------------------------------------------------
# 2. 吞吐 (iperf3 TCP 5s)
# ---------------------------------------------------------------------------
def throughput(env):
    out = {}
    for src, dst in TPUT_PAIRS[ENVS[env]["kind"]]:
        dexec(env, dst, "pkill iperf3 2>/dev/null; iperf3 -s -6 -D")
        time.sleep(1)
        r = dexec(env, src,
                  f"iperf3 -6 -c {loopback(env, dst)} "
                  f"-B {loopback(env, src)} -t 5 -J", timeout=30)
        mbps = None
        try:
            j = json.loads(r.stdout)
            mbps = round(j["end"]["sum_received"]["bits_per_second"] / 1e6, 1)
        except (json.JSONDecodeError, KeyError):
            pass
        dexec(env, dst, "pkill iperf3 2>/dev/null")
        out[f"{src}->{dst}"] = {"mbps": mbps}
        print(f"[{env}] 吞吐 {src}->{dst}: {mbps} Mbps")
    return out


# ---------------------------------------------------------------------------
# 3. CPU (docker stats 5x2s)
# ---------------------------------------------------------------------------
def cpu_usage(env, samples=5, interval=2):
    prefix = ENVS[env]["prefix"]
    acc = {}
    for _ in range(samples):
        r = run(["docker", "stats", "--no-stream", "--format",
                 "{{.Name}} {{.CPUPerc}}"], timeout=60)
        for line in r.stdout.splitlines():
            parts = line.split()
            if parts and parts[0].startswith(prefix + "-"):
                node = parts[0][len(prefix) + 1:]
                acc.setdefault(node, []).append(
                    float(parts[1].rstrip("%")))
        time.sleep(interval)
    per_node = {n: round(sum(v) / len(v), 2) for n, v in acc.items()}
    vals = sorted(per_node.values())
    out = {"avg_pct": round(sum(vals) / len(vals), 2) if vals else None,
           "max_pct": vals[-1] if vals else None, "per_node": per_node}
    print(f"[{env}] CPU: 均值 {out['avg_pct']}% 峰值 {out['max_pct']}%")
    return out


# ---------------------------------------------------------------------------
# 4. 卫星 churn (确定性种子, 三栈同序列)
# ---------------------------------------------------------------------------
def churn_schedule(links, nodes):
    rng = random.Random(CHURN_SEED)
    order = list(range(len(links)))
    rng.shuffle(order)
    events, cursor = [], 0
    t = 0.0
    while t < CHURN_WINDOW:
        # 找一条 down 后 (含仍处于 down 期的前一事件) 图仍连通的链路
        active = {e[0] for e in events if e[1] + CHURN_DOWN > t}
        for k in range(cursor, cursor + len(order)):
            idx = order[k % len(order)]
            if idx not in active and connected_without(
                    links, nodes, active | {idx}):
                events.append((idx, t))
                cursor = k + 1
                break
        t += CHURN_PERIOD
    return events


def churn_availability(env):
    nodes = nodes_of(env)
    links = parse_links(env)
    events = churn_schedule(links, nodes)
    print(f"[{env}] churn 序列: "
          f"{[(links[i][0], links[i][1], round(t,1)) for i, t in events]}")
    samples = {"ok": 0, "total": 0, "rounds": []}
    t0 = time.time()
    ei = 0
    restore = []                                # (t_restore, node, iface)
    while time.time() - t0 < CHURN_WINDOW:
        now = time.time() - t0
        while ei < len(events) and events[ei][1] <= now:
            idx, te = events[ei]
            a, ifa, b, ifb = links[idx]
            print(f"[{env}] t={now:.1f}s down {a}:{ifa} <-> {b}:{ifb}")
            dexec(env, a, f"ip link set {ifa} down")
            restore.append((te + CHURN_DOWN, a, ifa))
            ei += 1
        for tr, n, i in list(restore):
            if tr <= now:
                dexec(env, n, f"ip link set {i} up")
                restore.remove((tr, n, i))
        with ThreadPoolExecutor(max_workers=10) as ex:
            oks = list(ex.map(
                lambda p: ping_ok(env, p[0], loopback(env, p[1])),
                SAT_MONITOR_PAIRS))
        ok = sum(oks)
        samples["ok"] += ok
        samples["total"] += len(oks)
        samples["rounds"].append(
            {"t": round(now, 1), "ok": ok, "of": len(oks)})
        time.sleep(max(0, 2 - (time.time() - t0 - now)))
    for _, n, i in restore:                     # 恢复残余 down 链路
        dexec(env, n, f"ip link set {i} up")
    avail = round(samples["ok"] / samples["total"], 4)
    worst = min(r["ok"] / r["of"] for r in samples["rounds"])
    print(f"[{env}] churn 可用性: {samples['ok']}/{samples['total']} = "
          f"{avail*100:.2f}%, 最差轮 {worst*100:.0f}%")
    # 扰动后完备性复测
    time.sleep(15)
    tasks = [(s, d) for s in nodes for d in nodes if s != d]
    okc = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for good in ex.map(
                lambda p: ping_ok(env, p[0], loopback(env, p[1])), tasks):
            okc += good
    print(f"[{env}] churn 后全对连通: {okc}/{len(tasks)}")
    return {"events": len(events), "availability": avail,
            "worst_round": round(worst, 2), "samples": samples["total"],
            "ok": samples["ok"],
            "post_churn_connectivity": {"ok": okc, "total": len(tasks),
                                        "rate": round(okc / len(tasks), 4)}}


# ---------------------------------------------------------------------------
def run_env(env):
    os.makedirs(RESULTS, exist_ok=True)
    res = {"env": env, "started": time.strftime("%F %T")}
    deploy(env)
    res["stretch"] = path_stretch(env)
    res["throughput"] = throughput(env)
    res["cpu"] = cpu_usage(env)
    if ENVS[env]["kind"] == "sat":
        res["churn"] = churn_availability(env)
    path = os.path.join(RESULTS, f"supp-{env}.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(f"[{env}] 结果 -> {path}")
    destroy(env)
    return res


def report():
    data = {}
    for env in ENVS:
        p = os.path.join(RESULTS, f"supp-{env}.json")
        if os.path.exists(p):
            data[env] = json.load(open(p))
    if not data:
        print("无补充结果")
        return
    lines = ["# 补充测试报告 (路径最优性 / 吞吐 / CPU / 卫星 churn)", ""]
    for kind, envs in (("sat", ["sat-ocrm", "sat-bgp", "sat-ospf"]),
                       ("wifi", ["wifi-ocrm", "wifi-babel"])):
        have = [e for e in envs if e in data]
        if not have:
            continue
        lines += [f"## {'卫星 Walker 4x6' if kind=='sat' else '无线 RGG 16'} 环境",
                  "", "| 指标 | " + " | ".join(have) + " |",
                  "|---" * (len(have) + 1) + "|"]

        def row(name, fn, unit=""):
            vals = []
            for e in have:
                try:
                    v = fn(data[e])
                except (KeyError, TypeError):
                    v = "-"
                vals.append(f"{v}{unit}")
            lines.append("| " + " | ".join([name] + vals) + " |")

        row("平均路径拉伸", lambda d: d["stretch"]["avg_stretch"])
        row("最大路径拉伸", lambda d: d["stretch"]["max_stretch"])
        row("最优路径占比", lambda d: d["stretch"]["optimal_pairs_pct"], "%")
        row("持久环路", lambda d: "无" if d["stretch"]["loop_free"] else "有")
        for pair in data[have[0]]["throughput"]:
            row(f"吞吐 {pair} (Mbps)",
                lambda d, p=pair: d["throughput"][p]["mbps"])
        row("CPU 均值", lambda d: d["cpu"]["avg_pct"], "%")
        row("CPU 峰值", lambda d: d["cpu"]["max_pct"], "%")
        if kind == "sat":
            row("churn 可用性", lambda d:
                f"{d['churn']['availability']*100:.2f}")
            row("churn 最差轮", lambda d:
                f"{d['churn']['worst_round']*100:.0f}")
            row("churn 后连通率", lambda d:
                f"{d['churn']['post_churn_connectivity']['rate']*100:.2f}")
        lines.append("")
    path = os.path.join(RESULTS, "supplement.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"报告 -> {path}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg in ENVS:
        run_env(arg)
    elif arg.startswith("tput:"):            # 单项补测吞吐量, 合并入已有 JSON
        env = arg.split(":", 1)[1]
        deploy(env)
        t = throughput(env)
        destroy(env)
        p = os.path.join(RESULTS, f"supp-{env}.json")
        d = json.load(open(p)) if os.path.exists(p) else {"env": env}
        d["throughput"] = t
        with open(p, "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        print(f"[{env}] 吞吐量已合并 -> {p}")
    elif arg == "report":
        report()
    else:
        print(__doc__)
        sys.exit(1)
