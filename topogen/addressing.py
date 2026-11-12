"""IPv6 地址、SRv6 Locator 与 BGP AS 号规划.

规划方案 (plane=p, sat=s):
  AS 号        : 4200000000 + p*1000 + s      (4 字节私有 AS)
  Router-ID    : 10.<p>.<s>.1
  Loopback     : fcbb:<p>:<s>::1/128          (SRv6 Locator 内, 可作 End SID 终点)
  SRv6 Locator : fcbb:<p>:<s>::/48            (eBPF 转发面本地 SID 匹配前缀)
  ISL 互联     : fd00:100::/64 按链路切分 /127 (编号 eBGP;
                 FRR 8.4 unnumbered 存在 zebra->bgpd LLA 推送竞态, 不可靠)
"""
import ipaddress

AS_BASE = 4200000000
ISL_NET_BASE = int(ipaddress.IPv6Address("fd00:100::"))


def asn(plane, sat):
    return AS_BASE + plane * 1000 + sat


def router_id(plane, sat):
    return "10.%d.%d.1" % (plane, sat)


def locator(plane, sat):
    """SRv6 Locator 前缀 (含掩码)."""
    return "fcbb:%x:%x::/48" % (plane, sat)


def loopback(plane, sat):
    """Loopback /128 地址 (含掩码)."""
    return "fcbb:%x:%x::1/128" % (plane, sat)


def loopback_addr(plane, sat):
    """Loopback 纯地址."""
    return "fcbb:%x:%x::1" % (plane, sat)


def end_sid(plane, sat):
    """SRv6 End SID (供构造 segment list 使用)."""
    return "fcbb:%x:%x::100" % (plane, sat)


def isl_addr_pair(link_index):
    """第 link_index 条链路的 /127 地址对 (a端, b端)."""
    base = ISL_NET_BASE + link_index * 2
    return (str(ipaddress.IPv6Address(base)),
            str(ipaddress.IPv6Address(base + 1)))
