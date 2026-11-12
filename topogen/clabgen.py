"""containerlab 拓扑文件 (.clab.yml) 生成.

支持三种节点模式:
  - linux    : sat-router 镜像 (FRR/BIRD2 控制面 + eBPF IPv6/SRv6 转发面,
               或 routing=ocrm 时挂载 OCRM 协议栈源码跑语义路由守护进程)
  - sonic-vs : SONiC 虚拟交换机镜像 (仅控制面验证, frr.conf 挂载到
               SONiC 集成 FRR 的配置路径)
"""
import os

from . import addressing
from .isl import node_name, node_ifaces


def clab_topology(cfg, links):
    """生成 containerlab 拓扑 YAML 文本."""
    cons = cfg["constellation"]
    node_cfg = cfg.get("node", {})
    kind = node_cfg.get("kind", "linux")
    image = node_cfg.get("image", "sat-router:latest")
    routing = node_cfg.get("routing", "frr")
    planes = int(cons["planes"])
    sats = int(cons["sats_per_plane"])
    wrap = bool(cfg.get("isl", {}).get("wrap_planes", True))
    links_per_sat = int(cfg.get("isl", {}).get("links_per_sat", 6))
    lab_name = cons.get("name", "satlab")
    # 管理网段可配置, 多 lab 共存时需错开 (如 FRR/BIRD 对比实验)
    mgmt_subnet = node_cfg.get("mgmt_subnet", "172.100.0.0/16")

    # (节点, 接口) -> 本端互联地址 (BIRD/OCRM 模式由入口脚本赋址)
    ifaddr = {}
    # 节点 -> [(邻居, 本端接口, 对端地址)] (OCRM face 表)
    faces = {}
    for lk in links:
        if "a_addr" in lk:
            ifaddr[(lk["a"], lk["a_if"])] = lk["a_addr"]
            ifaddr[(lk["b"], lk["b_if"])] = lk["b_addr"]
            faces.setdefault(lk["a"], []).append(
                (lk["b"], lk["a_if"], lk["b_addr"]))
            faces.setdefault(lk["b"], []).append(
                (lk["a"], lk["b_if"], lk["a_addr"]))

    # OCRM 模式: 协议栈源码以只读 bind 挂进容器 (免重建镜像);
    # 镜像内 entrypoint 是构建期旧版, 一并挂载带 ocrm 分支的新版
    ocrm_src = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "ocrm"))
    entrypoint_src = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "docker", "entrypoint.sh"))

    out = []
    out.append("# 由 topogen/generate.py 自动生成, 请勿手工修改")
    out.append("name: %s" % lab_name)
    out.append("")
    out.append("mgmt:")
    out.append("  network: %s-mgmt" % lab_name)
    out.append("  ipv4-subnet: %s" % mgmt_subnet)
    out.append("")
    out.append("topology:")
    out.append("  defaults:")
    out.append("    kind: %s" % kind)
    out.append("    image: %s" % image)
    out.append("  nodes:")

    for p in range(planes):
        for s in range(sats):
            name = node_name(p, s)
            out.append("    %s:" % name)
            if kind == "sonic-vs":
                out.append("      binds:")
                out.append("        - configs/%s/frr.conf:/etc/sonic/frr/frr.conf" % name)
            else:
                ifaces = node_ifaces(p, s, planes, wrap, links_per_sat)
                if routing == "ocrm":
                    out.append("      binds:")
                    out.append("        - %s:/opt/ocrm/ocrm:ro" % ocrm_src)
                    out.append("        - %s:/entrypoint.sh:ro" % entrypoint_src)
                    out.append("      env:")
                    out.append("        SAT_NAME: %s" % name)
                    out.append("        SAT_LOOPBACK: %s" % addressing.loopback(p, s))
                    # 不设 SAT_LOCATOR: 纯内核 IPv6 转发, 不加载 eBPF/SRv6
                    out.append("        ISL_IFACES: %s" % ",".join(ifaces))
                    out.append("        ROUTING: ocrm")
                    addrs = ["%s=%s/127" % (i, ifaddr[(name, i)])
                             for i in ifaces if (name, i) in ifaddr]
                    out.append("        ISL_ADDRS: %s" % ",".join(addrs))
                    out.append("        SAT_PREFIX: %s" % addressing.locator(p, s))
                    face_specs = ["%s|%s|%s" % (nbr, itf, peer)
                                  for nbr, itf, peer in
                                  sorted(faces.get(name, []), key=lambda x: x[1])]
                    out.append("        OCRM_FACES: %s" % ",".join(face_specs))
                    continue
                out.append("      binds:")
                if routing == "bird2":
                    out.append("        - configs/%s/bird.conf:/etc/bird/bird.conf" % name)
                else:
                    out.append("        - configs/%s/frr.conf:/etc/frr/frr.conf" % name)
                    if routing == "ospf6":
                        # 镜像内 daemons 烧录为 bgpd=yes, 需覆盖为 ospf6d
                        out.append("        - daemons:/etc/frr/daemons")
                out.append("      env:")
                out.append("        SAT_NAME: %s" % name)
                out.append("        SAT_LOOPBACK: %s" % addressing.loopback(p, s))
                out.append("        SAT_LOCATOR: %s" % addressing.locator(p, s))
                out.append("        ISL_IFACES: %s" % ",".join(ifaces))
                out.append("        ROUTING: %s" % routing)
                if node_cfg.get("xdp", True) is False:
                    # 大规模拓扑(约300+节点): 不加载 eBPF/XDP 转发面,
                    # 纯内核转发 + 静态邻居表项 (见 entrypoint.sh)
                    out.append('        SAT_XDP: "0"')
                if routing == "bird2":
                    # BIRD 不管理接口地址, 由 entrypoint 按此赋值
                    addrs = ["%s=%s/127" % (i, ifaddr[(name, i)])
                             for i in ifaces if (name, i) in ifaddr]
                    out.append("        ISL_ADDRS: %s" % ",".join(addrs))

    out.append("  links:")
    for lk in links:
        out.append('    - endpoints: ["%s:%s", "%s:%s"]'
                   % (lk["a"], lk["a_if"], lk["b"], lk["b_if"]))
    return "\n".join(out) + "\n"
