"""BIRD2 配置生成: 编号 eBGP (每链路 /127) + Locator 通告.

与 frrgen 等价的 BIRD2 实现 (node.routing: bird2 时启用):
  - 每颗卫星独立 AS, 每条 ISL 一个 bgp 协议实例 (协议名 bgp_<ifname>)
  - direct 协议注入接口直连路由 (等效 redistribute connected)
  - static 协议通告 Locator /48 blackhole (等效 staticd + redistribute static)
  - kernel 协议把最优路由写内核 FIB (eBPF bpf_fib_lookup 依赖),
    merge paths 开启 ECMP 合并
  - 接口地址由容器入口脚本按 ISL_ADDRS 环境变量赋值
    (FRR 模式由 zebra 按 frr.conf 配置, BIRD 不管理接口地址)
"""
from . import addressing


def bird_conf(plane, sat, isl_ifaces):
    """生成单节点 bird.conf 文本.

    isl_ifaces: [{"ifname": "eth1", "local": "fd00:100::..",
                  "peer": "fd00:100::.."}, ...]
    """
    my_asn = addressing.asn(plane, sat)

    lines = [
        "# 由 topogen/birdgen.py 自动生成, 请勿手工修改",
        "log syslog all;",
        "router id %s;" % addressing.router_id(plane, sat),
        "",
        "protocol device { scan time 5; }",
        "",
        "# 接口直连路由注入 (loopback /128 + ISL /127, 排除管理口)",
        "protocol direct {",
        "    ipv6;",
        '    interface "-eth0", "eth*", "lo";',
        "}",
        "",
        "# SRv6 Locator 黑洞路由 (End SID 由 eBPF 转发面处理)",
        "protocol static {",
        "    ipv6;",
        "    route %s blackhole;" % addressing.locator(plane, sat),
        "}",
        "",
        "# 最优路由下发内核 FIB, ECMP 多路径合并",
        "protocol kernel {",
        "    ipv6 { export all; import none; };",
        "    merge paths on limit 6;",
        "}",
    ]
    for itf in isl_ifaces:
        lines += [
            "",
            "# ISL %s 对端 %s" % (itf["ifname"], itf["peer"]),
            "protocol bgp bgp_%s {" % itf["ifname"],
            "    local %s as %d;" % (itf["local"], my_asn),
            "    neighbor %s external;" % itf["peer"],
            "    hold time 9;",
            "    keepalive time 3;",
            "    ipv6 { import all; export all; };",
            "}",
        ]
    return "\n".join(lines) + "\n"
