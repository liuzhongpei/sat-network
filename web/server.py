#!/usr/bin/env python3
"""卫星网络 Web 可视化服务: 星座 / 链路 / BGP 路由更新.

纯 Python 标准库实现 (前端零外部依赖, 适配离线环境):
  - /                前端页面 (web/static/index.html)
  - /world.json      陆地轮廓 (地形底图, Natural Earth 数据预处理)
  - /api/meta        星座与节点静态信息
  - /api/state       卫星星下点 + 轨道地面轨迹 + 链路通断/时延
                     (优先读动态引擎发布的 link_state.json,
                      引擎离线时按轨道几何预测)
  - /api/bgp         各节点 BGP 会话/RIB 概要 + 路由更新事件流
  - /api/bgp/<node>  单节点 BGP 邻居与 IPv6 路由表详情
  - /api/path        源->目的转发路径 (逐跳 ip -6 route get 实查转发面)
  - /api/measure     路径性能实测: kind=ping 延迟/丢包, kind=bw 带宽(iperf3)

用法 (需 docker 组权限, 如 sg docker):
    python3 web/server.py --config config/test-small.yaml \\
        --links out-test/links.json [--port 8080]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from topogen import addressing              # noqa: E402
from topogen.isl import node_name           # noqa: E402
from topogen.orbit import make_constellation  # noqa: E402

import yaml                                  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "static")


def vtysh_json(container, cmd, timeout=8):
    """docker exec vtysh 并解析 JSON, 失败返回 None."""
    try:
        r = subprocess.run(
            ["docker", "exec", container, "vtysh", "-c", cmd],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout.decode())
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def birdc_text(container, cmd, timeout=8):
    """docker exec birdc (受限模式) 返回文本, 失败返回 None."""
    try:
        r = subprocess.run(
            ["docker", "exec", container, "birdc", "-r", cmd],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout.decode(errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return None


class App:
    """共享状态: 星座几何 + BGP 轮询快照与事件流."""

    def __init__(self, cfg, links_doc, poll_interval):
        self.cfg = cfg
        self.cons = make_constellation(cfg["constellation"])
        isl_cfg = cfg.get("isl", {})
        self.rate_mbps = float(isl_cfg.get("rate_mbps", 100))
        self.polar_cutoff = float(isl_cfg.get("polar_cutoff_lat_deg", 0))
        self.los_margin_km = float(isl_cfg.get("los_margin_km", 80))
        self.max_range_km = float(isl_cfg.get("max_range_km", 0))
        sim_cfg = cfg.get("simulation", {})
        self.speedup = float(sim_cfg.get("speedup", 1.0))
        self.step_s = float(sim_cfg.get("step_seconds", 10))
        self.lab = links_doc["lab"]
        self.links = links_doc["links"]
        self.state_file = os.path.join(
            os.path.dirname(os.path.abspath(links_doc["_path"])),
            "link_state.json")
        self.t0_wall = time.time()

        planes = self.cons.planes
        sats = self.cons.sats_per_plane
        self.nodes = []
        for p in range(planes):
            for s in range(sats):
                self.nodes.append({
                    "name": node_name(p, s), "plane": p, "sat": s,
                    "asn": addressing.asn(p, s),
                    "loopback": addressing.loopback(p, s),
                    "locator": addressing.locator(p, s)})
        # 互联地址 -> 节点名 (BGP 邻居地址反查); (节点, 接口) -> 对端节点
        self.routing = cfg.get("node", {}).get("routing", "frr")
        self.addr2node = {}
        self.ifpeer = {}
        self.ifpeer_addr = {}   # (节点, 接口) -> 对端互联地址 (bird 协议名反查)
        for lk in self.links:
            if "a_addr" in lk:
                self.addr2node[lk["a_addr"]] = lk["a"]
                self.addr2node[lk["b_addr"]] = lk["b"]
                self.ifpeer_addr[(lk["a"], lk["a_if"])] = lk["b_addr"]
                self.ifpeer_addr[(lk["b"], lk["b_if"])] = lk["a_addr"]
            self.ifpeer[(lk["a"], lk["a_if"])] = lk["b"]
            self.ifpeer[(lk["b"], lk["b_if"])] = lk["a"]

        self.lock = threading.Lock()
        self.meas_lock = threading.Lock()   # 测量串行化 (iperf 互斥)
        self.bgp_snap = {}          # node -> 概要
        self.events = deque(maxlen=400)
        self.event_seq = 0
        self.poll_interval = poll_interval
        threading.Thread(target=self.poll_loop, daemon=True).start()

    # ---------- 星座/链路状态 ----------

    def read_engine_state(self):
        """读动态引擎状态文件, 过期视为离线."""
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        fresh = 3.0 * self.step_s / max(self.speedup, 1e-9) + 5.0
        if time.time() - doc.get("wall_time", 0) > fresh:
            return None
        return doc

    def predicted_state(self):
        """引擎离线时按轨道几何预测 (仅展示, 不代表已下发 netem)."""
        t_sim = (time.time() - self.t0_wall) * self.speedup
        sats = {}
        for n in self.nodes:
            lat, lon = self.cons.subpoint_deg(n["plane"], n["sat"], t_sim)
            sats[n["name"]] = {"plane": n["plane"], "sat": n["sat"],
                               "lat": round(lat, 3), "lon": round(lon, 3)}
        out_links = []
        for lk in self.links:
            a, b = tuple(lk["a_ps"]), tuple(lk["b_ps"])
            dist = self.cons.distance_km(a, b, t_sim)
            up = True
            if lk["type"] == "inter" and self.polar_cutoff > 0:
                if (abs(self.cons.latitude_deg(a[0], a[1], t_sim))
                        > self.polar_cutoff
                        or abs(self.cons.latitude_deg(b[0], b[1], t_sim))
                        > self.polar_cutoff):
                    up = False
            if up and self.max_range_km > 0 and dist > self.max_range_km:
                up = False
            if up and not self.cons.line_of_sight(a, b, t_sim,
                                                  self.los_margin_km):
                up = False
            out_links.append({
                "a": lk["a"], "b": lk["b"],
                "a_if": lk["a_if"], "b_if": lk["b_if"],
                "type": lk["type"], "up": up,
                "delay_ms": round(
                    self.cons.propagation_delay_ms(a, b, t_sim), 3),
                "distance_km": round(dist, 1)})
        return {"lab": self.lab, "t_sim": round(t_sim, 1),
                "speedup": self.speedup, "rate_mbps": self.rate_mbps,
                "polar_cutoff": self.polar_cutoff,
                "max_range_km": self.max_range_km,
                "sats": sats, "links": out_links}

    def orbit_tracks(self, t_sim, samples=180):
        """各轨道面在 t_sim 时刻的轨道星下点投影 (固定时刻沿相位 0..2pi
        采样, 闭合曲线, 卫星节点严格落在轨迹上; 供前端画虚线)."""
        tracks = []
        spp = self.cons.sats_per_plane
        for p in range(self.cons.planes):
            pts = []
            for i in range(samples + 1):
                # sat 参数取小数 = 面内相位插值
                lat, lon = self.cons.subpoint_deg(p, spp * i / samples, t_sim)
                pts.append([round(lat, 2), round(lon, 2)])
            tracks.append(pts)
        return tracks

    def state(self):
        doc = self.read_engine_state()
        engine = doc is not None
        if not engine:
            doc = self.predicted_state()
        else:
            # 引擎在线: 链路 up/down/时延以引擎快照为准(权威 netem 状态),
            # 但卫星位置/轨道线按实时仿真时间推进, 避免两次步进间拓扑冻结
            # 60s(步进周期)。t_sim_live = 快照 t_sim + 墙钟流逝*speedup,
            # 与引擎下一次写入无缝衔接, 无跳变。
            t_sim_live = (doc.get("t_sim", 0.0)
                          + (time.time() - doc.get("wall_time", 0.0))
                          * self.speedup)
            sats = {}
            for n in self.nodes:
                lat, lon = self.cons.subpoint_deg(n["plane"], n["sat"],
                                                  t_sim_live)
                sats[n["name"]] = {"plane": n["plane"], "sat": n["sat"],
                                   "lat": round(lat, 3), "lon": round(lon, 3)}
            doc["t_sim"] = round(t_sim_live, 1)
            doc["sats"] = sats
        doc["engine"] = engine
        doc["tracks"] = self.orbit_tracks(doc["t_sim"])
        return doc

    # ---------- BGP 轮询与路由更新事件 ----------

    def container(self, node):
        return "clab-%s-%s" % (self.lab, node)

    # ---------- 转发路径查询 ----------

    def forwarding_path(self, src, dst, max_hops=24):
        """逐跳查转发面 (容器内 ip -6 route get), 返回实际转发路径.

        反映内核 FIB 实际选路 (含 ECMP 哈希后的单条路径).
        """
        byname = {x["name"]: x for x in self.nodes}
        dst_addr = byname[dst]["loopback"]
        hops, cur, seen = [], src, set()
        reached, reason = False, ""
        while True:
            if cur == dst:
                reached = True
                break
            if cur in seen:
                reason = "检测到环路"
                break
            if len(hops) >= max_hops:
                reason = "超过最大跳数 %d" % max_hops
                break
            seen.add(cur)
            try:
                r = subprocess.run(
                    ["docker", "exec", self.container(cur),
                     "ip", "-6", "route", "get", dst_addr],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=8)
            except (subprocess.TimeoutExpired, OSError):
                reason = "%s 查询失败" % cur
                break
            if r.returncode != 0:
                reason = "%s 无路由: %s" % (
                    cur, r.stderr.decode(errors="replace").strip())
                break
            txt = r.stdout.decode(errors="replace")
            toks = txt.split()
            via = toks[toks.index("via") + 1] if "via" in toks else ""
            dev = toks[toks.index("dev") + 1] if "dev" in toks else ""
            # 全局 /127 地址反查; fe80 下一跳按出接口反查对端
            nxt = self.addr2node.get(via) or self.ifpeer.get((cur, dev))
            hops.append({"node": cur, "via": via, "dev": dev,
                         "next": nxt or ""})
            if not nxt:
                reason = "%s 下一跳无法解析 (via=%s dev=%s)" % (
                    cur, via or "-", dev or "-")
                break
            cur = nxt
        path = [src] + [h["next"] for h in hops if h["next"]]
        return {"src": src, "dst": dst, "dst_addr": dst_addr,
                "reached": reached, "reason": reason,
                "hops": hops, "path": path}

    # ---------- 路径性能实测 (ping / iperf3) ----------

    def node_exec(self, node, args, timeout):
        return subprocess.run(
            ["docker", "exec", self.container(node)] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout)

    def has_iperf3(self, node):
        try:
            return self.node_exec(node, ["which", "iperf3"], 8).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    IPERF3_FILES = ("usr/bin/iperf3 "
                    "usr/lib/x86_64-linux-gnu/libiperf.so.0 "
                    "usr/lib/x86_64-linux-gnu/libiperf.so.0.0.0 "
                    "usr/lib/x86_64-linux-gnu/libsctp.so.1 "
                    "usr/lib/x86_64-linux-gnu/libsctp.so.1.0.19")

    def ensure_iperf3(self, node):
        """确保节点有 iperf3; 返回错误信息或 None.

        同镜像容器优先从已装节点 tar 管道拷贝 (秒级);
        无供体时退化为 apt 在线安装 (需管理口可访问 apt 源).
        """
        if self.has_iperf3(node):
            self.iperf3_donor = node
            return None
        donor = getattr(self, "iperf3_donor", None)
        if donor is None:
            donor = next((n["name"] for n in self.nodes
                          if n["name"] != node and self.has_iperf3(n["name"])),
                         None)
        if donor:
            try:
                subprocess.run(
                    "docker exec %s tar -C / -cf - %s 2>/dev/null"
                    " | docker exec -i %s tar -C / -xf -"
                    % (self.container(donor), self.IPERF3_FILES,
                       self.container(node)),
                    shell=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=30)
            except (subprocess.TimeoutExpired, OSError):
                pass
            if self.has_iperf3(node):
                self.iperf3_donor = donor   # 缓存供体, 免重复 O(N) 探测
                return None
        # 退化: 在线安装
        try:
            self.node_exec(node, ["sh", "-c",
                                  "apt-get update -qq >/dev/null 2>&1; "
                                  "apt-get install -y -qq iperf3"], 180)
            if self.has_iperf3(node):
                self.iperf3_donor = node
                return None
        except (subprocess.TimeoutExpired, OSError):
            pass
        return "%s 安装 iperf3 失败 (无可拷贝节点且 apt 安装未成功)" % node

    def measure(self, src, dst, kind, secs):
        """src->dst 实测. kind=ping: 延迟/丢包; kind=bw: iperf3 TCP 带宽."""
        if not self.meas_lock.acquire(blocking=False):
            return {"error": "已有一个测量在进行中, 请稍候再试"}
        try:
            byname = {x["name"]: x for x in self.nodes}
            # loopback 字段带 /128 前缀, 取纯地址
            src_addr = byname[src]["loopback"].split("/")[0]
            dst_addr = byname[dst]["loopback"].split("/")[0]
            if kind == "ping":
                return self.measure_ping(src, src_addr, dst_addr)
            return self.measure_bw(src, src_addr, dst, dst_addr, secs)
        finally:
            self.meas_lock.release()

    def measure_ping(self, src, src_addr, dst_addr, count=10):
        try:
            r = self.node_exec(src, ["ping", "-6", "-c", str(count),
                                     "-i", "0.2", "-W", "2",
                                     "-I", src_addr, dst_addr], 40)
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"error": "ping 执行失败: %s" % e}
        txt = r.stdout.decode(errors="replace")
        m = re.search(r"(\d+) packets transmitted, (\d+) received", txt)
        if not m:
            return {"error": "ping 输出无法解析: %s" % txt.strip()[-200:]}
        sent, recv = int(m.group(1)), int(m.group(2))
        out = {"kind": "ping", "sent": sent, "recv": recv,
               "loss_pct": round(100.0 * (sent - recv) / max(sent, 1), 1)}
        m = re.search(r"rtt min/avg/max/mdev = "
                      r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", txt)
        if m:
            out.update(rtt_min=float(m.group(1)), rtt_avg=float(m.group(2)),
                       rtt_max=float(m.group(3)), rtt_mdev=float(m.group(4)))
        return out

    def measure_bw(self, src, src_addr, dst, dst_addr, secs):
        for n in (src, dst):
            err = self.ensure_iperf3(n)
            if err:
                return {"error": err}
        try:
            # 清理残留服务端, 再拉起一次性服务端 (-1: 测完自退出)
            self.node_exec(dst, ["pkill", "-x", "iperf3"], 8)
            subprocess.run(["docker", "exec", "-d", self.container(dst),
                            "iperf3", "-s", "-1", "-B", dst_addr],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=8)
            time.sleep(0.6)
            r = self.node_exec(src, ["iperf3", "-c", dst_addr,
                                     "-B", src_addr, "-t", str(secs),
                                     "-J"], secs + 30)
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"error": "iperf3 执行失败: %s" % e}
        finally:
            try:
                self.node_exec(dst, ["pkill", "-x", "iperf3"], 8)
            except (subprocess.TimeoutExpired, OSError):
                pass
        try:
            doc = json.loads(r.stdout.decode(errors="replace"))
        except json.JSONDecodeError:
            return {"error": "iperf3 输出无法解析"}
        if doc.get("error"):
            return {"error": "iperf3: %s" % doc["error"]}
        sent = doc.get("end", {}).get("sum_sent", {})
        recv = doc.get("end", {}).get("sum_received", {})
        return {"kind": "bw", "secs": secs,
                "mbps_send": round(sent.get("bits_per_second", 0) / 1e6, 1),
                "mbps_recv": round(recv.get("bits_per_second", 0) / 1e6, 1),
                "retrans": sent.get("retransmits", 0),
                "rate_mbps": self.rate_mbps}

    def add_event(self, node, level, msg):
        self.event_seq += 1
        self.events.append({"id": self.event_seq, "t": time.time(),
                            "node": node, "level": level, "msg": msg})

    def poll_node(self, node):
        if self.routing == "bird2":
            return self.poll_node_bird(node)
        doc = vtysh_json(self.container(node), "show bgp summary json")
        if not doc or "ipv6Unicast" not in doc:
            return node, None
        u = doc["ipv6Unicast"]
        peers = {}
        for addr, p in u.get("peers", {}).items():
            peers[addr] = {"peer": self.addr2node.get(addr, addr),
                           "state": p.get("state", "?"),
                           "pfxRcd": p.get("pfxRcd", 0),
                           "uptime": p.get("peerUptime", "")}
        est = sum(1 for p in peers.values() if p["state"] == "Established")
        return node, {"est": est, "total": len(peers),
                      "rib": u.get("ribCount", 0), "peers": peers}

    def poll_node_bird(self, node):
        """BIRD2 概要: show protocols all 解析会话状态与已收前缀."""
        txt = birdc_text(self.container(node), "show protocols all")
        if txt is None:
            return node, None
        peers, cur = {}, None
        for line in txt.splitlines():
            # 协议头行: <名字> <类型> <表> <状态> <since> [Info]
            mh = re.match(r"^(\S+)\s+(\w+)\s+\S+\s+(\S+)\s+(\S+)\s*(.*)$",
                          line)
            if mh:
                name, ptype = mh.group(1), mh.group(2)
                if ptype == "BGP" and name.startswith("bgp_"):
                    ifname = name[len("bgp_"):]
                    addr = self.ifpeer_addr.get((node, ifname), ifname)
                    info = mh.group(5).strip() or mh.group(3)
                    cur = {"peer": self.addr2node.get(addr, addr),
                           "state": info, "pfxRcd": 0,
                           "uptime": mh.group(4)}
                    peers[addr] = cur
                else:
                    cur = None
                continue
            if cur is not None:
                mr = re.search(r"Routes:\s+(\d+) imported", line)
                if mr:
                    cur["pfxRcd"] = int(mr.group(1))
                    cur = None   # 一个协议块只取首个 Routes 行
        est = sum(1 for p in peers.values() if p["state"] == "Established")
        rib = 0
        cnt = birdc_text(self.container(node), "show route count")
        if cnt:
            # 逐表输出后跟 Total 行, 取末个匹配 (master4 恒为 0)
            ms = re.findall(r"(\d+) of \d+ routes", cnt)
            if ms:
                rib = int(ms[-1])
        return node, {"est": est, "total": len(peers),
                      "rib": rib, "peers": peers}

    def diff_events(self, node, prev, cur):
        """对比快照, 生成会话/路由 update 事件."""
        for addr, cp in cur["peers"].items():
            pp = prev["peers"].get(addr)
            if pp is None:
                continue
            if pp["state"] != cp["state"]:
                if cp["state"] == "Established":
                    self.add_event(node, "good", "邻居 %s (%s) 会话建立"
                                   % (cp["peer"], addr))
                elif pp["state"] == "Established":
                    self.add_event(node, "warn", "邻居 %s (%s) 会话中断 (%s)"
                                   % (cp["peer"], addr, cp["state"]))
            elif cp["pfxRcd"] != pp["pfxRcd"]:
                d = cp["pfxRcd"] - pp["pfxRcd"]
                self.add_event(node, "info", "路由更新: 来自 %s 前缀 %d -> %d (%+d)"
                               % (cp["peer"], pp["pfxRcd"], cp["pfxRcd"], d))
        if cur["rib"] != prev["rib"]:
            self.add_event(node, "info", "RIB 变化: %d -> %d 条"
                           % (prev["rib"], cur["rib"]))

    def poll_loop(self):
        pool = ThreadPoolExecutor(max_workers=8)
        while True:
            t_begin = time.time()
            results = list(pool.map(self.poll_node,
                                    [n["name"] for n in self.nodes]))
            with self.lock:
                for node, snap in results:
                    prev = self.bgp_snap.get(node)
                    if snap is None:
                        if prev is not None and prev.get("ok"):
                            self.add_event(node, "warn", "节点不可达/BGP 无响应")
                        self.bgp_snap[node] = {"ok": False, "est": 0,
                                               "total": 0, "rib": 0,
                                               "peers": {}}
                        continue
                    snap["ok"] = True
                    if prev is not None and prev.get("ok"):
                        self.diff_events(node, prev, snap)
                    self.bgp_snap[node] = snap
            time.sleep(max(1.0, self.poll_interval - (time.time() - t_begin)))

    def bgp_overview(self, since=0):
        with self.lock:
            nodes = {n: {"ok": s.get("ok", False), "est": s["est"],
                         "total": s["total"], "rib": s["rib"]}
                     for n, s in self.bgp_snap.items()}
            events = [e for e in self.events if e["id"] > since]
        return {"nodes": nodes, "events": events}

    def bgp_detail(self, node):
        """节点详情: 邻居表 + IPv6 BGP 路由 (按需实时查询)."""
        with self.lock:
            snap = self.bgp_snap.get(node)
            peers = dict(snap["peers"]) if snap and snap.get("ok") else {}
        if self.routing == "bird2":
            routes = self.bird_routes(node)
            routes.sort(key=lambda r: r["prefix"])
            return {"node": node, "peers": peers, "routes": routes}
        routes = []
        doc = vtysh_json(self.container(node),
                         "show bgp ipv6 unicast json", timeout=15)
        if doc:
            for prefix, paths in doc.get("routes", {}).items():
                nhs, aspath, best = [], "", False
                for p in paths:
                    # FRR 8.4: bestpath 为 bool; FRR 9+: dict{overall}
                    bp = p.get("bestpath")
                    if bp is True or (isinstance(bp, dict)
                                      and bp.get("overall")):
                        best = True
                        asp = p.get("aspath")
                        aspath = asp.get("string", "") \
                            if isinstance(asp, dict) \
                            else (asp or p.get("path", ""))
                    for nh in p.get("nexthops", []):
                        ip = nh.get("ip", "")
                        # 跳过 link-local, 用全局 /127 地址反查邻居节点
                        if not ip or ip.startswith("fe80"):
                            continue
                        name = self.addr2node.get(ip, ip)
                        if name not in nhs:
                            nhs.append(name)
                routes.append({"prefix": prefix, "paths": len(paths),
                               "best": best, "aspath": aspath,
                               "nexthops": nhs[:6]})
        routes.sort(key=lambda r: r["prefix"])
        return {"node": node, "peers": peers, "routes": routes}

    def bird_routes(self, node):
        """BIRD2 路由表: show route all 仅提取 BGP 来源条目.

        文本格式 (逐行状态机):
          <prefix>  unicast [bgp_eth1 <time>] * (100) [AS..i]   首条目带前缀
                    unicast [bgp_eth3 <time>] (100) [AS..i]     后续条目缩进
                via <nexthop> on <if>
                BGP.as_path: <as 列表>
        """
        txt = birdc_text(self.container(node), "show route all", timeout=15)
        if not txt:
            return []
        agg = {}                 # prefix -> 聚合条目
        cur, cur_best, last_prefix = None, False, None
        for line in txt.splitlines():
            me = re.match(
                r"^(\S+/\d+)?\s+\w+\s+\[(\S+)[^\]]*\]\s*(\*)?", line)
            if me and "[" in line:
                if me.group(1):
                    last_prefix = me.group(1)
                if not me.group(2).startswith("bgp_") or last_prefix is None:
                    cur = None       # direct/static/kernel 来源不展示
                    continue
                cur = agg.setdefault(last_prefix, {
                    "prefix": last_prefix, "paths": 0, "best": False,
                    "aspath": "", "nexthops": []})
                cur["paths"] += 1
                cur_best = bool(me.group(3))
                if cur_best:
                    cur["best"] = True
                continue
            if cur is None:
                continue
            mv = re.match(r"^\s+via\s+(\S+)\s+on\s+", line)
            if mv and not mv.group(1).startswith("fe80"):
                name = self.addr2node.get(mv.group(1), mv.group(1))
                if name not in cur["nexthops"]:
                    cur["nexthops"].append(name)
                continue
            ma = re.match(r"^\s+BGP\.as_path:\s*(.*)$", line)
            if ma and cur_best:
                cur["aspath"] = ma.group(1).strip()
        for e in agg.values():
            e["nexthops"] = e["nexthops"][:6]
        return list(agg.values())


class Handler(BaseHTTPRequestHandler):
    app = None   # 由 main 注入

    def log_message(self, fmt, *args):   # 静默访问日志
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/world.json":
                fp = os.path.join(STATIC_DIR, "world.json")
                if not os.path.isfile(fp):
                    self.send_json({"error": "no world data"}, 404)
                else:
                    with open(fp, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "max-age=86400")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            elif path == "/api/meta":
                a = self.app
                self.send_json({
                    "lab": a.lab,
                    "planes": a.cons.planes,
                    "sats_per_plane": a.cons.sats_per_plane,
                    "altitude_km": a.cons.altitude_km,
                    "period_min": round(a.cons.period_s / 60.0, 1),
                    "rate_mbps": a.rate_mbps,
                    "polar_cutoff": a.polar_cutoff,
                    "nodes": a.nodes})
            elif path == "/api/state":
                self.send_json(self.app.state())
            elif path == "/api/bgp":
                since = 0
                for kv in query.split("&"):
                    if kv.startswith("since="):
                        try:
                            since = int(kv[6:] or 0)
                        except ValueError:
                            since = 0
                self.send_json(self.app.bgp_overview(since))
            elif path.startswith("/api/bgp/"):
                node = path[len("/api/bgp/"):]
                if not any(n["name"] == node for n in self.app.nodes):
                    self.send_json({"error": "unknown node"}, 404)
                else:
                    self.send_json(self.app.bgp_detail(node))
            elif path == "/api/path":
                kv = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                src, dst = kv.get("src", ""), kv.get("dst", "")
                names = {n["name"] for n in self.app.nodes}
                if src not in names or dst not in names:
                    self.send_json({"error": "无效节点"}, 400)
                elif src == dst:
                    self.send_json({"error": "源与目的相同"}, 400)
                else:
                    self.send_json(self.app.forwarding_path(src, dst))
            elif path == "/api/measure":
                kv = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                src, dst = kv.get("src", ""), kv.get("dst", "")
                kind = kv.get("kind", "ping")
                try:
                    secs = min(max(int(kv.get("secs", "5") or 5), 2), 30)
                except ValueError:
                    secs = 5
                names = {n["name"] for n in self.app.nodes}
                if src not in names or dst not in names:
                    self.send_json({"error": "无效节点"}, 400)
                elif src == dst:
                    self.send_json({"error": "源与目的相同"}, 400)
                elif kind not in ("ping", "bw"):
                    self.send_json({"error": "kind 须为 ping 或 bw"}, 400)
                else:
                    self.send_json(self.app.measure(src, dst, kind, secs))
            else:
                self.send_json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:   # 防御: 单请求异常不能无响应断连
            try:
                self.send_json({"error": "%s: %s"
                                % (type(e).__name__, e)}, 500)
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="卫星网络 Web 可视化服务")
    ap.add_argument("--config", default="config/constellation.yaml")
    ap.add_argument("--links", default="out/links.json")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--poll-interval", type=float, default=5.0,
                    help="BGP 轮询周期 (秒)")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open(args.links, "r", encoding="utf-8") as f:
        links_doc = json.load(f)
    links_doc["_path"] = args.links

    Handler.app = App(cfg, links_doc, args.poll_interval)
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("[web] http://127.0.0.1:%d  (lab=%s, %d 节点, %d 链路)"
          % (args.port, Handler.app.lab, len(Handler.app.nodes),
             len(Handler.app.links)), flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
