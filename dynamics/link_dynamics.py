#!/usr/bin/env python3
"""链路动态引擎: 按轨道运行实时更新 ISL 时延/速率/通断.

  - 每个仿真步长根据星历计算各 ISL 两端距离 -> 传播时延
  - 通过 docker exec 在容器内用 tc netem 设置 delay/rate (链路两端同时设置)
  - 极区断链: 任一端星下点纬度超过 polar_cutoff_lat_deg 时 down 异轨链路
  - 丧失视距 (被地球遮挡) 的链路同样 down
  - 超出最大建链距离 (max_range_km) 的链路 down, 进入范围后重新建链
  - 每步原子写 <out>/link_state.json 供 Web 前端展示

用法:
    python3 dynamics/link_dynamics.py --config config/constellation.yaml \\
        --links out/links.json [--once] [--dry-run]
"""
import argparse
import json
import subprocess
import sys
import time
import os
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from topogen.orbit import make_constellation  # noqa: E402

import yaml  # noqa: E402


def sh(cmd, dry_run=False):
    if dry_run:
        print("DRY: %s" % " ".join(cmd))
        return 0
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=20)
    except subprocess.TimeoutExpired:
        # docker exec 挂死不能阻塞整个引擎步进
        print("[dynamics] WARN: cmd timeout: %s" % " ".join(cmd),
              file=sys.stderr)
        return -1
    if r.returncode != 0:
        print("[dynamics] WARN: cmd failed rc=%d: %s\n%s"
              % (r.returncode, " ".join(cmd),
                 r.stderr.decode(errors="replace").strip()),
              file=sys.stderr)
    return r.returncode


class LinkDynamics:
    def __init__(self, cfg, links_doc, dry_run=False):
        self.cons = make_constellation(cfg["constellation"])
        isl_cfg = cfg.get("isl", {})
        self.rate_mbps = float(isl_cfg.get("rate_mbps", 100))
        self.polar_cutoff = float(isl_cfg.get("polar_cutoff_lat_deg", 0))
        self.los_margin_km = float(isl_cfg.get("los_margin_km", 80))
        self.max_range_km = float(isl_cfg.get("max_range_km", 0))
        self.static_delay_ms = float(isl_cfg.get("static_delay_ms", 0))
        sim_cfg = cfg.get("simulation", {})
        self.step_s = float(sim_cfg.get("step_seconds", 10))
        self.speedup = float(sim_cfg.get("speedup", 1.0))
        self.lab = links_doc["lab"]
        self.links = links_doc["links"]
        self.dry_run = dry_run
        self.link_state = {}   # id -> bool(up)
        self.last_delay = {}   # id -> 上次下发的时延 (ms)
        # 时延变化阈值: 小于该值不重发 netem, 大幅减少 docker exec
        # 次数 (大星座下每步全量下发会拖垮步进节奏)
        self.delay_eps_ms = float(sim_cfg.get("delay_eps_ms", 0.5))
        self.state_file = None  # 状态发布文件 (供 Web 前端)
        self.pool = ThreadPoolExecutor(max_workers=24)

    def container(self, node):
        return "clab-%s-%s" % (self.lab, node)

    def flush_node_cmds(self, node_cmds):
        """每节点合并为单次 docker exec, 线程池并发下发."""
        def run_one(item):
            node, cmds = item
            sh(["docker", "exec", self.container(node),
                "sh", "-c", "; ".join(cmds)], self.dry_run)
        list(self.pool.map(run_one, node_cmds.items()))

    def step(self, t_sim):
        n_up, n_down = 0, 0
        state_links = []
        node_cmds = {}   # node -> [容器内 shell 命令]

        def add(node, cmd):
            node_cmds.setdefault(node, []).append(cmd)

        for lk in self.links:
            a, b = tuple(lk["a_ps"]), tuple(lk["b_ps"])
            lid = "%s:%s" % (lk["a"], lk["a_if"])
            dist = self.cons.distance_km(a, b, t_sim)

            # 判断链路通断
            up = True
            if lk["type"] == "inter" and self.polar_cutoff > 0:
                lat_a = abs(self.cons.latitude_deg(a[0], a[1], t_sim))
                lat_b = abs(self.cons.latitude_deg(b[0], b[1], t_sim))
                if lat_a > self.polar_cutoff or lat_b > self.polar_cutoff:
                    up = False
            # 超出最大建链距离 (终端功率/捕获能力限制)
            if up and self.max_range_km > 0 and dist > self.max_range_km:
                up = False
            if up and not self.cons.line_of_sight(a, b, t_sim,
                                                  self.los_margin_km):
                up = False

            prev = self.link_state.get(lid)
            delay = (self.cons.propagation_delay_ms(a, b, t_sim)
                     + self.static_delay_ms)
            if up:
                # 仅在通断翻转或时延变化超阈值时下发 netem
                if (prev is not True or abs(delay - self.last_delay.get(
                        lid, -1e9)) >= self.delay_eps_ms):
                    netem = ("tc qdisc replace dev %%s root netem "
                             "delay %.3fms rate %gmbit"
                             % (delay, self.rate_mbps))
                    add(lk["a"], netem % lk["a_if"])
                    add(lk["b"], netem % lk["b_if"])
                    self.last_delay[lid] = delay
                if prev is not True:  # down/未知 -> up (首步幂等拉起)
                    add(lk["a"], "ip link set %s up" % lk["a_if"])
                    add(lk["b"], "ip link set %s up" % lk["b_if"])
                n_up += 1
            else:
                if prev is not False:  # up/未知 -> down
                    add(lk["a"], "ip link set %s down" % lk["a_if"])
                    add(lk["b"], "ip link set %s down" % lk["b_if"])
                n_down += 1
            self.link_state[lid] = up
            state_links.append({
                "a": lk["a"], "b": lk["b"],
                "a_if": lk["a_if"], "b_if": lk["b_if"],
                "type": lk["type"], "up": up,
                "delay_ms": round(delay, 3),
                "distance_km": round(dist, 1),
            })
        # 先发布状态再下发 tc: 慢速下发不阻塞 Web 侧状态新鲜度
        self.publish_state(t_sim, state_links)
        t_cmd = time.monotonic()
        self.flush_node_cmds(node_cmds)
        print("[dynamics] t=%.0fs: up=%d down=%d exec_nodes=%d cost=%.1fs"
              % (t_sim, n_up, n_down, len(node_cmds),
                 time.monotonic() - t_cmd), flush=True)

    def publish_state(self, t_sim, state_links):
        """原子写状态文件: 仿真时间/星下点/链路通断-时延."""
        if not self.state_file:
            return
        sats = {}
        for p in range(self.cons.planes):
            for s in range(self.cons.sats_per_plane):
                lat, lon = self.cons.subpoint_deg(p, s, t_sim)
                sats["sat%d-%d" % (p, s)] = {
                    "plane": p, "sat": s,
                    "lat": round(lat, 3), "lon": round(lon, 3)}
        doc = {"lab": self.lab, "t_sim": round(t_sim, 1),
               "wall_time": time.time(), "speedup": self.speedup,
               "rate_mbps": self.rate_mbps,
               "polar_cutoff": self.polar_cutoff,
               "max_range_km": self.max_range_km,
               "sats": sats, "links": state_links}
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, self.state_file)

    def run(self, once=False):
        t0 = time.monotonic()
        print("[dynamics] lab=%s links=%d step=%.1fs speedup=%.1fx rate=%gMbps"
              % (self.lab, len(self.links), self.step_s, self.speedup,
                 self.rate_mbps), flush=True)
        while True:
            t_sim = (time.monotonic() - t0) * self.speedup
            self.step(t_sim)
            if once:
                break
            time.sleep(self.step_s / self.speedup)


def main():
    ap = argparse.ArgumentParser(description="ISL 链路动态引擎")
    ap.add_argument("--config", default="config/constellation.yaml")
    ap.add_argument("--links", default="out/links.json")
    ap.add_argument("--once", action="store_true",
                    help="只执行一步 (用于初始化时延/速率)")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅打印命令不执行")
    ap.add_argument("--state-file", default=None,
                    help="状态发布文件 (默认 <links目录>/link_state.json)")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open(args.links, "r", encoding="utf-8") as f:
        links_doc = json.load(f)

    eng = LinkDynamics(cfg, links_doc, args.dry_run)
    eng.state_file = args.state_file or os.path.join(
        os.path.dirname(os.path.abspath(args.links)), "link_state.json")
    eng.run(args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
