"""FRR 配置生成: 编号 eBGP (每链路 /127) + Locator 通告.

设计:
  - 每颗卫星独立 AS, 每条 ISL 配 /127 互联地址跑 eBGP
    (SONiC 常见部署方式; 不依赖 RA/LLA, 规避 FRR 8.4
    zebra->bgpd 邻居 LLA 推送竞态导致的 Idle 问题)
  - Loopback/Locator 通过 redistribute connected/static 注入 BGP
  - Locator /48 以 blackhole 静态路由存在(SRv6 End SID 由 eBPF 转发面
    在进入内核前处理, 到达最终段后 DA 为 loopback /128 正常本地交付)
"""
from . import addressing


def frr_conf(plane, sat, isl_ifaces):
    """生成单节点 frr.conf 文本.

    isl_ifaces: [{"ifname": "eth1", "local": "fd00:100::..",
                  "peer": "fd00:100::.."}, ...]
    """
    my_asn = addressing.asn(plane, sat)

    lines = [
        "frr defaults datacenter",
        "hostname sat%d-%d" % (plane, sat),
        "log syslog informational",
        "service integrated-vtysh-config",
        "!",
        "interface lo",
        " ipv6 address %s" % addressing.loopback(plane, sat),
        "!",
    ]
    for itf in isl_ifaces:
        lines += [
            "interface %s" % itf["ifname"],
            " description ISL peer %s" % itf["peer"],
            " ipv6 address %s/127" % itf["local"],
            "!",
        ]
    # Locator 黑洞静态路由, 用于向全网通告 /48
    lines += [
        "ipv6 route %s blackhole" % addressing.locator(plane, sat),
        "!",
        "router bgp %d" % my_asn,
        " bgp router-id %s" % addressing.router_id(plane, sat),
        " no bgp ebgp-requires-policy",
        " bgp bestpath as-path multipath-relax",
        " neighbor ISL peer-group",
        " neighbor ISL remote-as external",
        " neighbor ISL timers 3 9",
    ]
    for itf in isl_ifaces:
        lines.append(" neighbor %s peer-group ISL" % itf["peer"])
    lines += [
        " !",
        " address-family ipv6 unicast",
        "  redistribute connected",
        "  redistribute static",
        "  neighbor ISL activate",
        "  maximum-paths 6",
        " exit-address-family",
        "!",
        "line vty",
        "!",
    ]
    return "\n".join(lines) + "\n"


def ospf6_conf(plane, sat, isl_ifaces):
    """生成单节点 OSPFv3 frr.conf 文本.

    设计: 单区域 (Area 0) 链路状态, 每 ISL 点对点;
    hello/dead 3/9s 与 eBGP 基线 timers 对齐, 保证对比公平.
    Loopback/Locator 同样经 redistribute connected/static 注入.
    """
    lines = [
        "frr defaults datacenter",
        "hostname sat%d-%d" % (plane, sat),
        "log syslog informational",
        "service integrated-vtysh-config",
        "!",
        "interface lo",
        " ipv6 address %s" % addressing.loopback(plane, sat),
        "!",
    ]
    for itf in isl_ifaces:
        lines += [
            "interface %s" % itf["ifname"],
            " description ISL peer %s" % itf["peer"],
            " ipv6 address %s/127" % itf["local"],
            " ipv6 ospf6 area 0.0.0.0",
            " ipv6 ospf6 network point-to-point",
            " ipv6 ospf6 hello-interval 3",
            " ipv6 ospf6 dead-interval 9",
            "!",
        ]
    lines += [
        "ipv6 route %s blackhole" % addressing.locator(plane, sat),
        "!",
        "router ospf6",
        " ospf6 router-id %s" % addressing.router_id(plane, sat),
        " redistribute connected",
        " redistribute static",
        " maximum-paths 6",
        "!",
        "line vty",
        "!",
    ]
    return "\n".join(lines) + "\n"


def babel_conf(hostname, loopback, locator, isl_ifaces,
               hello_ms=1000, wireless=True):
    """生成单节点 Babel (RFC 8966, MANET/mesh 路由) frr.conf 文本.

    wireless=True 时打开 babeld 无线链路模式 (不假设链路对称稳定,
    使用更保守的链路质量估计); hello_ms 默认 1s 适应高动态.
    """
    lines = [
        "frr defaults datacenter",
        "hostname %s" % hostname,
        "log syslog informational",
        "service integrated-vtysh-config",
        "!",
        "interface lo",
        " ipv6 address %s" % loopback,
        "!",
    ]
    for itf in isl_ifaces:
        lines += [
            "interface %s" % itf["ifname"],
            " description radio peer %s" % itf["peer"],
            " ipv6 address %s/127" % itf["local"],
            " babel %s" % ("wireless" if wireless else "wired"),
            " babel hello-interval %d" % hello_ms,
            "!",
        ]
    lines += [
        "ipv6 route %s blackhole" % locator,
        "!",
        "router babel",
    ]
    for itf in isl_ifaces:
        lines.append(" network %s" % itf["ifname"])
    lines += [
        " redistribute ipv6 connected",
        " redistribute ipv6 static",
        "!",
        "line vty",
        "!",
    ]
    return "\n".join(lines) + "\n"


def daemons_file(enable=("bgpd",)):
    """FRR daemons 使能文件. enable: 需额外启用的协议守护进程."""
    daemons = {
        "zebra": "yes", "bgpd": "no", "staticd": "yes",
        "ospfd": "no", "ospf6d": "no", "isisd": "no",
        "ripd": "no", "ripngd": "no", "pimd": "no",
        "ldpd": "no", "nhrpd": "no", "eigrpd": "no",
        "babeld": "no", "sharpd": "no", "pbrd": "no",
        "bfdd": "no", "fabricd": "no", "vrrpd": "no", "pathd": "no",
    }
    for d in enable:
        daemons[d] = "yes"
    lines = ["%s=%s" % (k, v) for k, v in daemons.items()]
    lines += [
        'vtysh_enable=yes',
        'zebra_options="  -A 127.0.0.1 -s 90000000"',
        'staticd_options="-A 127.0.0.1"',
    ]
    lines += ['%s_options="   -A 127.0.0.1"' % d for d in enable]
    return "\n".join(lines) + "\n"
