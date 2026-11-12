#!/usr/bin/env python3
"""OCRM vs BGP/OSPFv3 containerlab 对比测试 harness.

对同一 Walker 4x6 星座 (24 节点 / 48 ISL) 的多套协议栈跑同一组实验:
  - ocrm : out-ocrm/sat-ocrm.clab.yml  (OCRM 真实协议栈, LSU->SPF->CFML->内核 IPv6)
  - bgp  : out-4x6/sat-4x6.clab.yml    (FRR eBGP, timers 3/9, redistribute)
  - ospf : out-ospf/sat-ospf.clab.yml  (FRR OSPFv3, hello/dead 3/9, 单区域)

指标 (两栈完全同方法):
  1. 初始收敛时间   : deploy 返回 -> 采样节点对全部可达 (1s 轮询)
  2. 全对连通率     : 24x23 loopback ping (-I 源 loopback, 数据面走 /48 路由)
  3. RTT            : 代表性远近节点对 ping -c 10
  4. 断链恢复       : 0.1s 快速 ping 中 down 掉在用链路, 黑洞时长 = 连续丢包x0.1s
  5. 控制面开销     : 空闲窗口 30s 内全节点 ISL 接口 tx_bytes 增量
  6. 内存占用       : docker stats RSS

用法:
  python3 scripts/clab_compare.py ocrm     # 只跑 OCRM lab
  python3 scripts/clab_compare.py bgp      # 只跑 BGP lab
  python3 scripts/clab_compare.py ospf     # 只跑 OSPFv3 lab
  python3 scripts/clab_compare.py report   # 汇总已有结果生成对比报告
结果写入 results/clab-<stack>.json / results/clab-compare.md
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

LAB = {
    "ocrm": {"topo": "out-ocrm/sat-ocrm.clab.yml", "prefix": "clab-sat-ocrm"},
    "bgp":  {"topo": "out-4x6/sat-4x6.clab.yml",   "prefix": "clab-sat-4x6"},
    "ospf": {"topo": "out-ospf/sat-ospf.clab.yml", "prefix": "clab-sat-ospf"},
}
PLANES, SATS = 4, 6
ISL_IFACES = ["eth1", "eth2", "eth3", "eth5"]
RESULTS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "results")

NODES = [f"sat{p}-{s}" for p in range(PLANES) for s in range(SATS)]


def loopback(name: str) -> str:
    p, s = name.removeprefix("sat").split("-")
    return "fcbb:%x:%x::1" % (int(p), int(s))


def run(cmd, timeout=60, check=False):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        # docker exec 偶发慢 (嵌套虚拟化): 视为失败而非崩溃
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


# ---------------------------------------------------------------------------
# 1. 部署 + 初始收敛
# ---------------------------------------------------------------------------
# 采样对: 同轨相邻 / 同轨对跖 / 跨轨 / 全网最远 (对角)
CONV_PAIRS = [
    ("sat0-0", "sat0-1"), ("sat0-0", "sat0-3"),
    ("sat0-0", "sat2-0"), ("sat0-0", "sat2-3"),
    ("sat1-2", "sat3-5"), ("sat0-5", "sat3-2"),
]


def deploy_and_converge(stack):
    topo, prefix = LAB[stack]["topo"], LAB[stack]["prefix"]
    print(f"[{stack}] destroy 残留 lab (如有) ...")
    run(["containerlab", "destroy", "-t", topo, "--cleanup"], timeout=180)
    print(f"[{stack}] deploy {topo} ...")
    t0 = time.time()
    r = run(["containerlab", "deploy", "-t", topo, "--reconfigure"],
            timeout=600, check=True)
    t_deploy = time.time() - t0
    print(f"[{stack}] deploy 完成 ({t_deploy:.1f}s), 轮询收敛 ...")

    t1 = time.time()
    deadline = t1 + 300
    pending = {p: None for p in CONV_PAIRS}
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
    print(f"[{stack}] 收敛: {conv}s, 各对: {pair_times}, 未收敛: {list(pending)}")
    return {"deploy_s": round(t_deploy, 1), "convergence_s": conv,
            "pair_times": pair_times,
            "unconverged": [f"{a}->{b}" for a, b in pending]}


# ---------------------------------------------------------------------------
# 2. 全对连通率
# ---------------------------------------------------------------------------
def all_pairs(stack):
    prefix = LAB[stack]["prefix"]
    tasks = [(s, d) for s in NODES for d in NODES if s != d]
    ok = 0
    fails = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        results = ex.map(lambda t: (t, ping_ok(prefix, t[0], loopback(t[1]))),
                         tasks)
        for (s, d), good in results:
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
# 3. RTT
# ---------------------------------------------------------------------------
RTT_PAIRS = [("sat0-0", "sat0-1"), ("sat0-0", "sat0-3"),
             ("sat0-0", "sat2-3"), ("sat1-2", "sat3-5")]


def rtt_sample(stack):
    prefix = LAB[stack]["prefix"]
    out = {}
    for src, dst in RTT_PAIRS:
        r = dexec(prefix, src,
                  f"ping -6 -c10 -i 0.2 -W1 -I {loopback(src)} {loopback(dst)}",
                  timeout=20)
        m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", r.stdout)
        out[f"{src}->{dst}"] = {
            "avg_ms": float(m.group(2)) if m else None,
            "max_ms": float(m.group(3)) if m else None,
        }
    print(f"[{stack}] RTT: {out}")
    return out


# ---------------------------------------------------------------------------
# 4. 断链恢复 (黑洞时长)
# ---------------------------------------------------------------------------
def failure_recovery(stack):
    """sat0-0 -> sat0-2 快速 ping, down 掉 sat0-0:eth1 (至 sat0-1 的在用链路).

    veth 对端 carrier 同步掉线, 两栈均按各自机制收敛到备用路径.
    黑洞时长 = 最长连续丢包段 x 0.1s.
    """
    prefix = LAB[stack]["prefix"]
    src, dst, victim_if = "sat0-0", "sat0-2", "eth1"
    dst_addr = loopback(dst)
    if not ping_ok(prefix, src, dst_addr):
        return {"error": "预连通检查失败"}

    # 后台 40s 快速 ping (0.1s 间隔, 400 包), 顺序号驱动丢包段分析
    cmd = (f"ping -6 -i 0.1 -W 0.2 -c 400 -I {loopback(src)} {dst_addr} "
           f"> /tmp/rapid_ping.txt 2>&1 &")
    dexec(prefix, src, cmd)
    time.sleep(5)                                # 稳态基线
    print(f"[{stack}] down {src}:{victim_if}")
    t_down = time.time()
    dexec(prefix, src, f"ip link set {victim_if} down")
    time.sleep(25)                               # 收敛观察窗
    dexec(prefix, src, f"ip link set {victim_if} up")
    # 等 ping 跑完写出统计行 (400 包 x 0.1s ≈ 40s, 丢包超时会拉长)
    for _ in range(40):
        r = dexec(prefix, src, "cat /tmp/rapid_ping.txt", timeout=10)
        if "packets transmitted" in r.stdout:
            break
        time.sleep(2)

    seqs = sorted(int(m) for m in
                  re.findall(r"icmp_seq=(\d+)", r.stdout))
    # 最长缺失连续段
    blackout_pkts = 0
    if seqs:
        prev = seqs[0]
        for q in seqs[1:]:
            blackout_pkts = max(blackout_pkts, q - prev - 1)
            prev = q
        # 尾部截断 (down 后一直不通到 ping 结束)
        blackout_pkts = max(blackout_pkts, 400 - seqs[-1])
    blackout_s = round(blackout_pkts * 0.1, 1)
    m = re.search(r"(\d+) received", r.stdout)
    received = int(m.group(1)) if m else 0
    print(f"[{stack}] 断链黑洞: {blackout_s}s (丢 {blackout_pkts} 包, "
          f"收 {received}/400)")

    # 恢复后确认
    time.sleep(8)
    restored = ping_ok(prefix, src, dst_addr, wait=2)
    return {"blackout_s": blackout_s, "lost_pkts": blackout_pkts,
            "received": received, "sent": 400, "restored_after_up": restored}


# ---------------------------------------------------------------------------
# 5. 控制面开销 (空闲窗口 tx_bytes 增量)
# ---------------------------------------------------------------------------
def tx_snapshot(prefix):
    def one(node):
        cmd = " ; ".join(
            f"cat /sys/class/net/{i}/statistics/tx_bytes 2>/dev/null || echo 0"
            for i in ISL_IFACES)
        r = dexec(prefix, node, cmd)
        return node, sum(int(x) for x in r.stdout.split())
    with ThreadPoolExecutor(max_workers=12) as ex:
        return dict(ex.map(one, NODES))


def control_overhead(stack, window_s=30):
    prefix = LAB[stack]["prefix"]
    print(f"[{stack}] 控制面开销采样 ({window_s}s 空闲窗口) ...")
    a = tx_snapshot(prefix)
    t0 = time.time()
    time.sleep(window_s)
    b = tx_snapshot(prefix)
    dt = time.time() - t0
    total = sum(b[n] - a[n] for n in NODES)
    per_node = total / len(NODES) / dt
    print(f"[{stack}] 总 {total} B / {dt:.1f}s = 全网 {total/dt:.0f} B/s, "
          f"单节点均值 {per_node:.0f} B/s")
    return {"window_s": round(dt, 1), "total_bytes": total,
            "network_Bps": round(total / dt, 1),
            "per_node_Bps": round(per_node, 1)}


# ---------------------------------------------------------------------------
# 6. 内存
# ---------------------------------------------------------------------------
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
    stats = {"per_node_MiB": mem,
             "avg_MiB": round(sum(vals) / len(vals), 1) if vals else None,
             "max_MiB": vals[-1] if vals else None}
    print(f"[{stack}] 内存: 均值 {stats['avg_MiB']} MiB, 峰值 {stats['max_MiB']} MiB")
    # OCRM 附加协议栈自报数据
    if stack == "ocrm":
        r = dexec(LAB[stack]["prefix"], "sat0-0",
                  "cat /tmp/ocrm_stats.json 2>/dev/null || echo {}")
        try:
            stats["sample_node_stats"] = json.loads(r.stdout)
        except json.JSONDecodeError:
            pass
    return stats


# ---------------------------------------------------------------------------
# 7. OCRM 专属: FIB 聚合观测 + 守护进程重启恢复
# ---------------------------------------------------------------------------
def fib_stats(stack, prefix=None):
    """各节点自报 FIB 条目数 (面前缀聚合压缩观测).
    无聚合基线 = N-1 条 /48 (SPF 每远端节点一条)."""
    prefix = prefix or LAB[stack]["prefix"]

    def one(node):
        r = dexec(prefix, node,
                  "cat /tmp/ocrm_stats.json 2>/dev/null || echo {}")
        try:
            d = json.loads(r.stdout)
            return node, d.get("fib_routes"), d.get("lsdb_size"), \
                d.get("aggregation")
        except json.JSONDecodeError:
            return node, None, None, None

    with ThreadPoolExecutor(max_workers=12) as ex:
        rows = list(ex.map(one, NODES))
    fibs = [f for _, f, _, _ in rows if f is not None]
    out = {"per_node": {n: f for n, f, _, _ in rows},
           "avg_fib": round(sum(fibs) / len(fibs), 1) if fibs else None,
           "max_fib": max(fibs) if fibs else None,
           "flat_baseline": len(NODES) - 1,
           "aggregation": rows[0][3] if rows else None,
           "lsdb_full": all(l == len(NODES)
                            for _, _, l, _ in rows if l is not None)}
    print(f"[{stack}] FIB: 均值 {out['avg_fib']} / 峰值 {out['max_fib']} 条 "
          f"(无聚合基线 {out['flat_baseline']}), "
          f"聚合={out['aggregation']}, LSDB全量={out['lsdb_full']}")
    return out


def daemon_restart(stack):
    """杀死一个节点的 OCRM 守护进程 (LSU seq 归零, 入口脚本 1s 拉起):
    观测数据面是否断流 (内核 FIB 保持) 与控制面重同步时长."""
    prefix = LAB[stack]["prefix"]
    victim, observer = "sat1-2", "sat0-0"
    dst = loopback(victim)
    if not ping_ok(prefix, observer, dst):
        return {"error": "预连通检查失败"}
    # 背景快速 ping: 300 包 x 0.1s = 30s 观察窗
    dexec(prefix, observer,
          f"ping -6 -i 0.1 -W 0.2 -c 300 -I {loopback(observer)} {dst} "
          f"> /tmp/restart_ping.txt 2>&1 &")
    time.sleep(3)
    print(f"[{stack}] kill OCRM daemon on {victim} (seq 归零重启)")
    t0 = time.time()
    dexec(prefix, victim,
          "pkill -f node_daemon.py 2>/dev/null || "
          "kill $(ps -eo pid,args | awk "
          "'/node_daemon.py/&&!/awk/{print $1}') 2>/dev/null")
    # 重同步: 新进程的 stats 时间戳 > t0 且 LSDB/FIB 重建完成
    resync = None
    for _ in range(120):
        r = dexec(prefix, victim,
                  "cat /tmp/ocrm_stats.json 2>/dev/null || echo {}")
        try:
            d = json.loads(r.stdout)
            if (d.get("time", 0) > t0 and d.get("lsdb_size") == len(NODES)
                    and d.get("fib_routes", 0) > 0):
                resync = round(time.time() - t0, 1)
                break
        except json.JSONDecodeError:
            pass
        time.sleep(0.5)
    # 等 ping 窗口结束, 统计丢包段
    for _ in range(30):
        r = dexec(prefix, observer, "cat /tmp/restart_ping.txt", timeout=10)
        if "packets transmitted" in r.stdout:
            break
        time.sleep(2)
    seqs = sorted(int(m) for m in re.findall(r"icmp_seq=(\d+)", r.stdout))
    lost = 0
    if seqs:
        prev = seqs[0]
        for q in seqs[1:]:
            lost = max(lost, q - prev - 1)
            prev = q
        lost = max(lost, 300 - seqs[-1])
    m = re.search(r"(\d+) received", r.stdout)
    received = int(m.group(1)) if m else 0
    # 双向后验: 观测节点<->重启节点, 重启节点->远端
    ok_after = (ping_ok(prefix, observer, dst, wait=2)
                and ping_ok(prefix, victim, loopback("sat3-5"), wait=2))
    out = {"victim": victim, "resync_s": resync,
           "blackout_s": round(lost * 0.1, 1),
           "received": received, "sent": 300, "ok_after": ok_after}
    print(f"[{stack}] 重启恢复: 控制面重同步 {resync}s, "
          f"数据面黑洞 {out['blackout_s']}s (收 {received}/300), "
          f"后验={ok_after}")
    return out


# ---------------------------------------------------------------------------
# 8. 聚合 A/B: OCRM_AGGREGATE=0 短周期部署, 采 FIB + 全对连通
# ---------------------------------------------------------------------------
NOAGG = {"topo": "out-noagg/sat-noagg.clab.yml", "prefix": "clab-sat-noagg"}


def gen_noagg_topo():
    txt = open(LAB["ocrm"]["topo"]).read()
    txt = txt.replace("name: sat-ocrm", "name: sat-noagg")
    txt = txt.replace("network: sat-ocrm-mgmt", "network: sat-noagg-mgmt")
    txt = txt.replace("ipv4-subnet: 172.101.0.0/16",
                      "ipv4-subnet: 172.105.0.0/16")
    txt = txt.replace("ROUTING: ocrm",
                      'ROUTING: ocrm\n        OCRM_AGGREGATE: "0"')
    os.makedirs("out-noagg", exist_ok=True)
    with open(NOAGG["topo"], "w") as f:
        f.write(txt)
    print(f"生成 {NOAGG['topo']} (OCRM_AGGREGATE=0)")


def agg_ab():
    """聚合关闭对照组: FIB 规模 + 全对连通 (聚合开组数据取自主实验)."""
    gen_noagg_topo()
    topo, prefix = NOAGG["topo"], NOAGG["prefix"]
    run(["containerlab", "destroy", "-t", topo, "--cleanup"], timeout=180)
    print("[noagg] deploy ...")
    run(["containerlab", "deploy", "-t", topo, "--reconfigure"],
        timeout=600, check=True)
    deadline = time.time() + 120
    pending = set(CONV_PAIRS)
    while pending and time.time() < deadline:
        pending = {p for p in pending
                   if not ping_ok(prefix, p[0], loopback(p[1]))}
        if pending:
            time.sleep(1)
    time.sleep(8)   # 稳定窗
    res = {"stack": "ocrm-noagg", "started": time.strftime("%F %T"),
           "converged": not pending}
    res["fib"] = fib_stats("ocrm", prefix=prefix)
    # 全对连通 (完备性对照)
    tasks = [(s, d) for s in NODES for d in NODES if s != d]
    ok = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for _, good in ex.map(
                lambda t: (t, ping_ok(prefix, t[0], loopback(t[1]))), tasks):
            ok += good
    res["connectivity"] = {"ok": ok, "total": len(tasks),
                           "rate": round(ok / len(tasks), 4)}
    print(f"[noagg] 全对连通 {ok}/{len(tasks)}")
    path = os.path.join(RESULTS, "clab-noagg.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(f"[noagg] 结果 -> {path}")
    run(["containerlab", "destroy", "-t", topo, "--cleanup"], timeout=180)
    return res


# ---------------------------------------------------------------------------
def run_stack(stack):
    os.makedirs(RESULTS, exist_ok=True)
    res = {"stack": stack, "started": time.strftime("%F %T")}
    res["convergence"] = deploy_and_converge(stack)
    # 收敛后留稳定窗
    time.sleep(10)
    res["connectivity"] = all_pairs(stack)
    res["rtt"] = rtt_sample(stack)
    res["overhead"] = control_overhead(stack)
    res["memory"] = memory_usage(stack)
    res["failure"] = failure_recovery(stack)
    # 恢复后复测连通
    time.sleep(10)
    res["connectivity_after_recovery"] = all_pairs(stack)
    if stack == "ocrm":
        res["fib"] = fib_stats(stack)
        res["restart"] = daemon_restart(stack)
    path = os.path.join(RESULTS, f"clab-{stack}.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(f"[{stack}] 结果 -> {path}")
    print(f"[{stack}] destroy lab ...")
    run(["containerlab", "destroy", "-t", LAB[stack]["topo"], "--cleanup"],
        timeout=180)
    return res


def report():
    data = {}
    for stack in ("ocrm", "bgp", "ospf"):
        p = os.path.join(RESULTS, f"clab-{stack}.json")
        if os.path.exists(p):
            data[stack] = json.load(open(p))
    if "ocrm" not in data or len(data) < 2:
        print("结果文件不足, 需 ocrm + 至少一个基线 (bgp/ospf)")
        return
    stacks = list(data)                      # 保持 ocrm 首列
    title = {"ocrm": "OCRM", "bgp": "BGP (FRR eBGP)",
             "ospf": "OSPFv3 (FRR ospf6d)"}

    def row(name, vals, unit=""):
        return "| " + " | ".join([name] + [f"{v}{unit}" for v in vals]) + " |"

    def col(fn):
        return [fn(data[s]) for s in stacks]

    lines = [
        "# OCRM vs 传统路由协议 — containerlab 实测对比 "
        "(Walker 4x6, 24 节点/48 ISL)",
        "",
        "- " + "  ".join(f"{title[s]} 运行时间: {data[s]['started']}"
                          for s in stacks),
        "- 同一拓扑、同一镜像、同一测量方法; 数据面均为内核 IPv6 转发。",
        "",
        "| 指标 | " + " | ".join(title[s] for s in stacks) + " |",
        "|---" * (len(stacks) + 1) + "|",
        row("初始收敛时间 (s)", col(lambda d: d["convergence"]["convergence_s"])),
        row("全对连通率", col(
            lambda d: f"{d['connectivity']['rate']*100:.2f}%")),
        row("断链黑洞时长 (s)", col(lambda d: d["failure"].get("blackout_s"))),
        row("断链后恢复", col(lambda d: d["failure"].get("restored_after_up"))),
        row("恢复后连通率", col(
            lambda d: f"{d['connectivity_after_recovery']['rate']*100:.2f}%")),
        row("控制面开销/节点 (B/s)", col(
            lambda d: d["overhead"]["per_node_Bps"])),
        row("控制面开销/全网 (B/s)", col(
            lambda d: d["overhead"]["network_Bps"])),
        row("容器内存均值 (MiB)", col(lambda d: d["memory"]["avg_MiB"])),
        row("容器内存峰值 (MiB)", col(lambda d: d["memory"]["max_MiB"])),
        "",
        "## RTT (avg ms)",
        "| 节点对 | " + " | ".join(title[s] for s in stacks) + " |",
        "|---" * (len(stacks) + 1) + "|",
    ]
    for pair in data["ocrm"]["rtt"]:
        lines.append(row(pair, col(
            lambda d, pr=pair: d["rtt"].get(pr, {}).get("avg_ms"))))
    path = os.path.join(RESULTS, "clab-compare.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告 -> {path}")
    print("\n".join(lines))


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg in LAB:
        run_stack(arg)
    elif arg == "aggab":
        agg_ab()
    elif arg == "report":
        report()
    else:
        print(__doc__)
        sys.exit(1)
