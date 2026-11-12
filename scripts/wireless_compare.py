#!/usr/bin/env python3
"""无线自组网 (MANET) 场景: OCRM vs Babel 对比测试 harness.

同一 RGG 拓扑 (16 节点 / 28 无线链路, gen_wireless.py 生成) 跑两套协议栈:
  - wifi-ocrm  : OCRM 真实协议栈 (LSU->SPF->CFML->内核 IPv6)
  - wifi-babel : FRR babeld (RFC 8966 MANET/mesh 距离向量, hello 1s, 无线模式)

指标 (两栈完全同方法, churn 调度种子一致):
  1. 初始收敛时间
  2. 全对连通率 (16x15=240 loopback ping)
  3. 移动性 churn 可用性: 55s 窗口内每 8s 随机 down 一条非桥链路 12s
     (同一 seed => 两栈遭遇完全相同的链路通断序列),
     固定 10 监测对每 ~2s 采样, 可用性 = OK 采样 / 总采样
  4. 单断链黑洞时长 (0.1s 快速 ping)
  5. 控制面开销 / 内存

用法:
  python3 scripts/wireless_compare.py ocrm | babel | report
结果写入 results/wifi-<stack>.json / results/wifi-compare.md
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, "results")
LAB = {
    "ocrm":  {"topo": "out-wifi-ocrm/wifi-ocrm.clab.yml",
              "prefix": "clab-wifi-ocrm"},
    "babel": {"topo": "out-wifi-babel/wifi-babel.clab.yml",
              "prefix": "clab-wifi-babel"},
}
N = 16
NODES = [f"sat0-{i}" for i in range(N)]
CHURN_SEED = 7          # 两栈共用 => 相同链路通断序列
CHURN_PERIOD = 8.0      # 每 8s 一次链路事件
CHURN_DOWN = 12.0       # 每条链路 down 持续 12s
CHURN_WINDOW = 55.0     # 总观测窗


def loopback(name: str) -> str:
    return "fcbb:0:%x::1" % int(name.split("-")[1])


def run(cmd, timeout=60, check=False):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {proc.stderr.strip()}")
    return proc


def dexec(prefix, node, cmd, timeout=15):
    return run(["docker", "exec", f"{prefix}-{node}", "sh", "-c", cmd],
               timeout=timeout)


def ping_ok(prefix, src, dst_addr, wait=1) -> bool:
    r = dexec(prefix, src,
              f"ping -6 -c1 -W{wait} -I {loopback(src)} {dst_addr}",
              timeout=wait + 9)
    return r.returncode == 0


def load_links():
    with open(os.path.join(BASE, "out-wifi", "links.json")) as f:
        return json.load(f)["links"]


# ---------------------------------------------------------------------------
# 收敛采样对 / 监测对: 从链路清单推导远端节点对 (确定性)
# ---------------------------------------------------------------------------
CONV_PAIRS = [("sat0-0", "sat0-15"), ("sat0-1", "sat0-14"),
              ("sat0-2", "sat0-13"), ("sat0-5", "sat0-10"),
              ("sat0-0", "sat0-7"), ("sat0-3", "sat0-12")]
MONITOR_PAIRS = [("sat0-0", "sat0-15"), ("sat0-1", "sat0-14"),
                 ("sat0-2", "sat0-13"), ("sat0-3", "sat0-12"),
                 ("sat0-4", "sat0-11"), ("sat0-5", "sat0-10"),
                 ("sat0-6", "sat0-9"), ("sat0-7", "sat0-8"),
                 ("sat0-0", "sat0-8"), ("sat0-15", "sat0-5")]


def deploy_and_converge(stack):
    topo, prefix = LAB[stack]["topo"], LAB[stack]["prefix"]
    print(f"[{stack}] destroy 残留 lab (如有) ...")
    run(["containerlab", "destroy", "-t", topo, "--cleanup"], timeout=180)
    print(f"[{stack}] deploy {topo} ...")
    t0 = time.time()
    run(["containerlab", "deploy", "-t", topo, "--reconfigure"],
        timeout=600, check=True)
    t_deploy = time.time() - t0
    print(f"[{stack}] deploy 完成 ({t_deploy:.1f}s), 轮询收敛 ...")
    t1 = time.time()
    deadline = t1 + 300
    pending = dict.fromkeys(CONV_PAIRS)
    pair_times = {}
    while pending and time.time() < deadline:
        for pair in list(pending):
            src, dst = pair
            if ping_ok(prefix, src, loopback(dst)):
                pair_times[f"{src}->{dst}"] = round(time.time() - t1, 1)
                del pending[pair]
        if pending:
            time.sleep(1)
    conv = round(max(pair_times.values()), 1) if pair_times and not pending \
        else None
    print(f"[{stack}] 收敛: {conv}s, 未收敛: {list(pending)}")
    return {"deploy_s": round(t_deploy, 1), "convergence_s": conv,
            "pair_times": pair_times,
            "unconverged": [f"{a}->{b}" for a, b in pending]}


def all_pairs(stack):
    prefix = LAB[stack]["prefix"]
    tasks = [(s, d) for s in NODES for d in NODES if s != d]
    ok, fails = 0, []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for (s, d), good in ex.map(
                lambda t: (t, ping_ok(prefix, t[0], loopback(t[1]))), tasks):
            if good:
                ok += 1
            else:
                fails.append(f"{s}->{d}")
    total = len(tasks)
    print(f"[{stack}] 全对连通: {ok}/{total} = {ok/total*100:.2f}%  "
          f"失败样例: {fails[:5]}")
    return {"ok": ok, "total": total, "rate": round(ok / total, 4),
            "failures": fails[:20]}


# ---------------------------------------------------------------------------
# 移动性 churn: 种子化链路通断调度 (两栈完全一致)
# ---------------------------------------------------------------------------
def _connected_without(links, down_idx):
    """去掉 down_idx 集合后图是否仍连通 (churn 只选非桥链路, 避免把
    '物理分区'误记为协议收敛失败)."""
    adj = {n: set() for n in NODES}
    for lk in links:
        if lk["idx"] in down_idx:
            continue
        adj[lk["a"]].add(lk["b"])
        adj[lk["b"]].add(lk["a"])
    seen, stack = {NODES[0]}, [NODES[0]]
    while stack:
        for nb in adj[stack.pop()]:
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return len(seen) == len(NODES)


def churn_schedule(links):
    """预生成事件表 [(t_down, link)], 确定性 (seed 固定)."""
    rng = random.Random(CHURN_SEED)
    events = []
    down = {}                       # idx -> t_up
    t = 5.0
    while t < CHURN_WINDOW - CHURN_DOWN / 2:
        active_down = {i for i, tu in down.items() if tu > t}
        cands = [lk for lk in links
                 if lk["idx"] not in active_down
                 and _connected_without(links, active_down | {lk["idx"]})]
        if cands:
            lk = rng.choice(cands)
            events.append((t, lk))
            down[lk["idx"]] = t + CHURN_DOWN
        t += CHURN_PERIOD
    return events


def churn_availability(stack):
    prefix = LAB[stack]["prefix"]
    links = load_links()
    events = churn_schedule(links)
    print(f"[{stack}] churn 调度: " + ", ".join(
        f"t={t:.0f}s {lk['a']}:{lk['a_if']}<->{lk['b']}" for t, lk in events))

    samples = []                     # (t, ok, total)
    t0 = time.time()
    ev = list(events)
    pending_up = []                  # (t_up, node, ifname)
    while time.time() - t0 < CHURN_WINDOW:
        now = time.time() - t0
        # 触发 down 事件
        while ev and now >= ev[0][0]:
            _, lk = ev.pop(0)
            dexec(prefix, lk["a"], f"ip link set {lk['a_if']} down")
            pending_up.append((now + CHURN_DOWN, lk["a"], lk["a_if"]))
            print(f"[{stack}]  t={now:.1f}s down {lk['a']}:{lk['a_if']} "
                  f"(-> {lk['b']})")
        # 触发 up 事件
        for item in list(pending_up):
            if now >= item[0]:
                dexec(prefix, item[1], f"ip link set {item[2]} up")
                pending_up.remove(item)
                print(f"[{stack}]  t={now:.1f}s up   {item[1]}:{item[2]}")
        # 采样监测对
        with ThreadPoolExecutor(max_workers=10) as ex:
            oks = list(ex.map(
                lambda pr: ping_ok(prefix, pr[0], loopback(pr[1])),
                MONITOR_PAIRS))
        samples.append((round(now, 1), sum(oks), len(oks)))
    # 收尾: 恢复所有链路
    for _, node, ifname in pending_up:
        dexec(prefix, node, f"ip link set {ifname} up")

    ok = sum(s[1] for s in samples)
    total = sum(s[2] for s in samples)
    avail = ok / total if total else 0
    worst = min((s[1] / s[2] for s in samples), default=0)
    print(f"[{stack}] churn 可用性: {ok}/{total} = {avail*100:.2f}%  "
          f"最差采样轮: {worst*100:.0f}%")
    return {"events": [f"t={t:.0f}s {lk['a']}:{lk['a_if']}" for t, lk in events],
            "samples": samples, "ok": ok, "total": total,
            "availability": round(avail, 4),
            "worst_round": round(worst, 2)}


# ---------------------------------------------------------------------------
# 单断链黑洞 (与卫星场景同方法)
# ---------------------------------------------------------------------------
def failure_recovery(stack):
    prefix = LAB[stack]["prefix"]
    links = load_links()
    # 选一条非桥链路, 端点 a 到某远端 dst 的在用链路
    lk = next((l for l in links
               if _connected_without(links, {l["idx"]})
               and l["a"] == "sat0-0"), None)
    if lk is None:
        return {"error": "sat0-0 无非桥链路可断, 跳过"}
    src, victim_if = lk["a"], lk["a_if"]
    dst = "sat0-15"
    dst_addr = loopback(dst)
    if not ping_ok(prefix, src, dst_addr):
        return {"error": "预连通检查失败"}
    dexec(prefix, src,
          f"ping -6 -i 0.1 -W 0.2 -c 400 -I {loopback(src)} {dst_addr} "
          f"> /tmp/rapid_ping.txt 2>&1 &")
    time.sleep(5)
    print(f"[{stack}] down {src}:{victim_if} (peer {lk['b']})")
    dexec(prefix, src, f"ip link set {victim_if} down")
    time.sleep(25)
    dexec(prefix, src, f"ip link set {victim_if} up")
    for _ in range(40):
        r = dexec(prefix, src, "cat /tmp/rapid_ping.txt", timeout=10)
        if "packets transmitted" in r.stdout:
            break
        time.sleep(2)
    seqs = sorted(int(m) for m in re.findall(r"icmp_seq=(\d+)", r.stdout))
    blackout_pkts = 0
    if seqs:
        prev = seqs[0]
        for q in seqs[1:]:
            blackout_pkts = max(blackout_pkts, q - prev - 1)
            prev = q
        blackout_pkts = max(blackout_pkts, 400 - seqs[-1])
    blackout_s = round(blackout_pkts * 0.1, 1)
    m = re.search(r"(\d+) received", r.stdout)
    received = int(m.group(1)) if m else 0
    print(f"[{stack}] 断链黑洞: {blackout_s}s (收 {received}/400)")
    time.sleep(8)
    restored = ping_ok(prefix, src, dst_addr, wait=2)
    return {"victim": f"{src}:{victim_if}", "blackout_s": blackout_s,
            "received": received, "sent": 400,
            "restored_after_up": restored}


# ---------------------------------------------------------------------------
# 控制面开销 / 内存
# ---------------------------------------------------------------------------
def node_ifaces(links):
    per = {n: [] for n in NODES}
    for lk in links:
        per[lk["a"]].append(lk["a_if"])
        per[lk["b"]].append(lk["b_if"])
    return per


def control_overhead(stack, window_s=30):
    prefix = LAB[stack]["prefix"]
    per = node_ifaces(load_links())

    def snap(node):
        cmd = " ; ".join(
            f"cat /sys/class/net/{i}/statistics/tx_bytes 2>/dev/null || echo 0"
            for i in per[node])
        r = dexec(prefix, node, cmd)
        return node, sum(int(x) for x in r.stdout.split())

    print(f"[{stack}] 控制面开销采样 ({window_s}s 空闲窗口) ...")
    with ThreadPoolExecutor(max_workers=12) as ex:
        a = dict(ex.map(snap, NODES))
    t0 = time.time()
    time.sleep(window_s)
    with ThreadPoolExecutor(max_workers=12) as ex:
        b = dict(ex.map(snap, NODES))
    dt = time.time() - t0
    total = sum(b[n] - a[n] for n in NODES)
    per_node = total / len(NODES) / dt
    print(f"[{stack}] 单节点均值 {per_node:.0f} B/s")
    return {"window_s": round(dt, 1), "total_bytes": total,
            "network_Bps": round(total / dt, 1),
            "per_node_Bps": round(per_node, 1)}


def memory_usage(stack):
    prefix = LAB[stack]["prefix"]
    r = run(["docker", "stats", "--no-stream", "--format",
             "{{.Name}} {{.MemUsage}}"], timeout=60)
    mem = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if parts and parts[0].startswith(prefix + "-"):
            node = parts[0][len(prefix) + 1:]
            val, unit = re.match(r"([\d.]+)(\w+)", parts[1]).groups()
            mib = float(val) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024,
                                "B": 1 / 1048576}.get(unit, 1)
            mem[node] = round(mib, 1)
    vals = sorted(mem.values())
    stats = {"avg_MiB": round(sum(vals) / len(vals), 1) if vals else None,
             "max_MiB": vals[-1] if vals else None}
    print(f"[{stack}] 内存: 均值 {stats['avg_MiB']} MiB")
    return stats


# ---------------------------------------------------------------------------
def run_stack(stack):
    os.makedirs(RESULTS, exist_ok=True)
    res = {"stack": stack, "started": time.strftime("%F %T")}
    res["convergence"] = deploy_and_converge(stack)
    time.sleep(10)
    res["connectivity"] = all_pairs(stack)
    res["overhead"] = control_overhead(stack)
    res["memory"] = memory_usage(stack)
    res["failure"] = failure_recovery(stack)
    time.sleep(10)
    res["churn"] = churn_availability(stack)
    print(f"[{stack}] churn 后等待再收敛 20s ...")
    time.sleep(20)
    res["connectivity_after_churn"] = all_pairs(stack)
    path = os.path.join(RESULTS, f"wifi-{stack}.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(f"[{stack}] 结果 -> {path}")
    run(["containerlab", "destroy", "-t", LAB[stack]["topo"], "--cleanup"],
        timeout=180)
    return res


def report():
    data = {}
    for stack in ("ocrm", "babel"):
        p = os.path.join(RESULTS, f"wifi-{stack}.json")
        if os.path.exists(p):
            data[stack] = json.load(open(p))
    if len(data) < 2:
        print("缺少结果文件, 先分别运行 ocrm / babel")
        return
    o, b = data["ocrm"], data["babel"]

    def row(name, ov, bv):
        return f"| {name} | {ov} | {bv} |"

    lines = [
        "# 无线自组网 (MANET) 场景 — OCRM vs Babel containerlab 实测对比",
        "",
        "- 拓扑: 随机几何图 16 节点 / 28 无线链路 (覆盖半径模型, seed=42)",
        "- Babel: FRR babeld, RFC 8966, 无线模式, hello 1s — MANET/mesh 代表协议",
        "- 移动性: 种子化链路 churn (每 8s down 一条非桥链路 12s), 两栈序列完全一致",
        f"- OCRM: {o['started']}  Babel: {b['started']}",
        "",
        "| 指标 | OCRM | Babel (FRR babeld) |",
        "|---|---|---|",
        row("初始收敛时间 (s)", o["convergence"]["convergence_s"],
            b["convergence"]["convergence_s"]),
        row("全对连通率", f"{o['connectivity']['rate']*100:.2f}%",
            f"{b['connectivity']['rate']*100:.2f}%"),
        row("churn 期间可用性", f"{o['churn']['availability']*100:.2f}%",
            f"{b['churn']['availability']*100:.2f}%"),
        row("churn 最差采样轮", f"{o['churn']['worst_round']*100:.0f}%",
            f"{b['churn']['worst_round']*100:.0f}%"),
        row("churn 后连通率", f"{o['connectivity_after_churn']['rate']*100:.2f}%",
            f"{b['connectivity_after_churn']['rate']*100:.2f}%"),
        row("单断链黑洞时长 (s)", o["failure"].get("blackout_s"),
            b["failure"].get("blackout_s")),
        row("控制面开销/节点 (B/s)", o["overhead"]["per_node_Bps"],
            b["overhead"]["per_node_Bps"]),
        row("容器内存均值 (MiB)", o["memory"]["avg_MiB"], b["memory"]["avg_MiB"]),
    ]
    path = os.path.join(RESULTS, "wifi-compare.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告 -> {path}")
    print("\n".join(lines))


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg in LAB:
        run_stack(arg)
    elif arg == "report":
        report()
    else:
        print(__doc__)
        sys.exit(1)
