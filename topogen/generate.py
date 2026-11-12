#!/usr/bin/env python3
"""拓扑与配置生成入口.

用法:
    python3 -m topogen.generate --config config/constellation.yaml --out out/

产物:
    out/<lab>.clab.yml        containerlab 拓扑
    out/configs/<node>/frr.conf   各节点 FRR 配置
    out/links.json            ISL 链路清单 (供链路动态引擎使用)
"""
import argparse
import json
import os
import sys

import yaml

from . import addressing, birdgen, clabgen, frrgen
from .isl import build_isl_links, node_name
from .orbit import make_constellation


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate(cfg, out_dir):
    cons_cfg = cfg["constellation"]
    isl_cfg = cfg.get("isl", {})
    planes = int(cons_cfg["planes"])
    sats = int(cons_cfg["sats_per_plane"])
    wrap = bool(isl_cfg.get("wrap_planes", True))
    links_per_sat = int(isl_cfg.get("links_per_sat", 6))
    lab_name = cons_cfg.get("name", "satlab")
    routing = cfg.get("node", {}).get("routing", "frr")
    if routing not in ("frr", "bird2", "ocrm", "ospf6"):
        raise ValueError(
            "node.routing 仅支持 frr / bird2 / ocrm / ospf6, 得到 %r" % routing)

    cons = make_constellation(cons_cfg)
    links = build_isl_links(planes, sats, wrap, links_per_sat)

    # 为每条链路分配 /127 互联地址, 并汇总成每节点接口表
    node_ifmap = {}   # node -> [{ifname, local, peer}]
    for i, lk in enumerate(links):
        a_addr, b_addr = addressing.isl_addr_pair(i)
        lk["a_addr"], lk["b_addr"] = a_addr, b_addr
        node_ifmap.setdefault(lk["a"], []).append(
            {"ifname": lk["a_if"], "local": a_addr, "peer": b_addr})
        node_ifmap.setdefault(lk["b"], []).append(
            {"ifname": lk["b_if"], "local": b_addr, "peer": a_addr})
    for itfs in node_ifmap.values():
        itfs.sort(key=lambda x: x["ifname"])

    os.makedirs(out_dir, exist_ok=True)

    # 1. containerlab 拓扑
    topo_path = os.path.join(out_dir, "%s.clab.yml" % lab_name)
    with open(topo_path, "w", encoding="utf-8") as f:
        f.write(clabgen.clab_topology(cfg, links))

    # 2. 各节点路由协议配置 (按 node.routing 选择协议栈;
    #    ocrm 模式无需配置文件, 全部参数由 clab env 注入)
    for p in range(planes):
        for s in range(sats):
            if routing == "ocrm":
                break
            node = node_name(p, s)
            node_dir = os.path.join(out_dir, "configs", node)
            os.makedirs(node_dir, exist_ok=True)
            if routing == "bird2":
                conf_name, conf_text = "bird.conf", birdgen.bird_conf(
                    p, s, node_ifmap.get(node, []))
            elif routing == "ospf6":
                conf_name, conf_text = "frr.conf", frrgen.ospf6_conf(
                    p, s, node_ifmap.get(node, []))
            else:
                conf_name, conf_text = "frr.conf", frrgen.frr_conf(
                    p, s, node_ifmap.get(node, []))
            with open(os.path.join(node_dir, conf_name), "w",
                      encoding="utf-8") as f:
                f.write(conf_text)

    # 非默认协议栈需覆盖镜像内烧录的 daemons 使能文件 (bgpd=yes)
    if routing == "ospf6":
        with open(os.path.join(out_dir, "daemons"), "w",
                  encoding="utf-8") as f:
            f.write(frrgen.daemons_file(enable=("ospf6d",)))

    # 3. 链路清单 (附 t=0 时刻的距离/时延, 供动态引擎与调试)
    for lk in links:
        a, b = tuple(lk["a_ps"]), tuple(lk["b_ps"])
        lk["distance_km_t0"] = round(cons.distance_km(a, b, 0.0), 1)
        lk["delay_ms_t0"] = round(cons.propagation_delay_ms(a, b, 0.0), 3)
    links_path = os.path.join(out_dir, "links.json")
    with open(links_path, "w", encoding="utf-8") as f:
        json.dump({"lab": lab_name, "links": links}, f, indent=1)

    print("[topogen] 星座: %d 轨道面 x %d 卫星 = %d 节点, 轨道周期 %.1f min"
          % (planes, sats, cons.total_sats, cons.period_s / 60.0))
    print("[topogen] 路由协议栈: %s" % routing)
    print("[topogen] ISL 链路数: %d (同轨 %d / 异轨 %d)"
          % (len(links),
             sum(1 for l in links if l["type"] == "intra"),
             sum(1 for l in links if l["type"] == "inter")))
    print("[topogen] 输出: %s" % topo_path)
    return topo_path


def main():
    ap = argparse.ArgumentParser(description="卫星网络 containerlab 拓扑生成")
    ap.add_argument("--config", default="config/constellation.yaml")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    cfg = load_config(args.config)
    generate(cfg, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
