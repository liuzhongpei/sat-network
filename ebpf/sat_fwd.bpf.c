// SPDX-License-Identifier: GPL-2.0
/* sat_fwd.bpf.c - 卫星路由器 eBPF 转发面 (XDP)
 *
 * 功能:
 *   1. IPv6 快速转发: bpf_fib_lookup 直接查内核 FIB
 *      (BGP 学到的路由经 zebra 下发内核后即刻生效, 控制面/转发面解耦)
 *   2. SRv6 End 行为: 目的地址命中本地 Locator (LPM) 且携带 SRH 时,
 *      segments_left--, 将下一段 SID 写入 IPv6 目的地址后继续 FIB 转发
 *   3. 最终段 / 非路由报文 XDP_PASS 交内核协议栈 (BGP、ND、ICMPv6 等)
 *
 * 挂载: 各 ISL 接口 (eth1..eth6), SKB(generic) 模式以兼容 veth
 */
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/in6.h>
#include <linux/ipv6.h>
#include <linux/seg6.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#define AF_INET6        10
#define IPPROTO_ROUTING 43
#define SRV6_MAX_SEGS   15

char LICENSE[] SEC("license") = "GPL";

/* 本地 SRv6 Locator LPM 表: 由加载器写入本星 Locator (/48) */
struct lsid_key {
    __u32 prefixlen;
    struct in6_addr addr;
};

struct {
    __uint(type, BPF_MAP_TYPE_LPM_TRIE);
    __type(key, struct lsid_key);
    __type(value, __u32);
    __uint(max_entries, 16);
    __uint(map_flags, BPF_F_NO_PREALLOC);
} local_sid SEC(".maps");

/* 统计计数器 */
enum {
    STAT_RX = 0,
    STAT_FWD,        /* 普通 IPv6 快转 */
    STAT_SRV6_END,   /* SRv6 End 处理并转发 */
    STAT_PASS,       /* 交内核协议栈 */
    STAT_DROP,
    STAT_MAX,
};

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __type(key, __u32);
    __type(value, __u64);
    __uint(max_entries, STAT_MAX);
} stats SEC(".maps");

static __always_inline void count(__u32 idx)
{
    __u64 *v = bpf_map_lookup_elem(&stats, &idx);
    if (v)
        *v += 1;
}

/* FIB 查询 + MAC 改写 + 重定向; 返回 XDP 动作 */
static __always_inline int fib_forward(struct xdp_md *ctx,
                                       struct ethhdr *eth,
                                       struct ipv6hdr *ip6,
                                       __u32 stat_idx)
{
    struct bpf_fib_lookup fib = {};
    long rc;

    if (ip6->hop_limit <= 1)
        return XDP_PASS;    /* 交内核回 ICMPv6 Time Exceeded */

    fib.family = AF_INET6;
    fib.ifindex = ctx->ingress_ifindex;
    fib.flowinfo = *(__be32 *)ip6 & bpf_htonl(0x0FFFFFFF);
    __builtin_memcpy(fib.ipv6_src, &ip6->saddr, sizeof(fib.ipv6_src));
    __builtin_memcpy(fib.ipv6_dst, &ip6->daddr, sizeof(fib.ipv6_dst));

    rc = bpf_fib_lookup(ctx, &fib, sizeof(fib), 0);
    if (rc != BPF_FIB_LKUP_RET_SUCCESS)
        return XDP_PASS;    /* 无路由/邻居未解析: 交内核慢路径处理 */

    ip6->hop_limit--;
    __builtin_memcpy(eth->h_dest, fib.dmac, ETH_ALEN);
    __builtin_memcpy(eth->h_source, fib.smac, ETH_ALEN);
    count(stat_idx);
    return bpf_redirect(fib.ifindex, 0);
}

SEC("xdp")
int sat_fwd(struct xdp_md *ctx)
{
    void *data = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;
    struct ethhdr *eth = data;
    struct ipv6hdr *ip6;
    struct lsid_key key = {};

    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;
    if (eth->h_proto != bpf_htons(ETH_P_IPV6))
        return XDP_PASS;

    ip6 = (struct ipv6hdr *)(eth + 1);
    if ((void *)(ip6 + 1) > data_end)
        return XDP_PASS;

    count(STAT_RX);

    /* link-local / 组播 (BGP, ND, RA) 一律交内核 */
    if (ip6->daddr.in6_u.u6_addr8[0] == 0xff ||
        (ip6->daddr.in6_u.u6_addr8[0] == 0xfe &&
         (ip6->daddr.in6_u.u6_addr8[1] & 0xc0) == 0x80)) {
        count(STAT_PASS);
        return XDP_PASS;
    }

    /* 目的地址是否命中本地 SRv6 Locator */
    key.prefixlen = 128;
    __builtin_memcpy(&key.addr, &ip6->daddr, sizeof(key.addr));

    if (bpf_map_lookup_elem(&local_sid, &key)) {
        /* 本地 SID: 处理 SRv6 End 行为 */
        if (ip6->nexthdr == IPPROTO_ROUTING) {
            struct ipv6_sr_hdr *srh = (struct ipv6_sr_hdr *)(ip6 + 1);
            struct in6_addr *seg;
            __u32 sl;

            if ((void *)(srh + 1) > data_end) {
                count(STAT_PASS);
                return XDP_PASS;
            }
            if (srh->type != 4 || srh->segments_left == 0) {
                /* 非 SRH 或最终段: 交内核本地交付 */
                count(STAT_PASS);
                return XDP_PASS;
            }
            sl = srh->segments_left - 1;
            /* 越界防护: SL 不得超出段列表 (first_segment 为末下标),
             * 否则畸形包会把 payload 当作 SID 写入目的地址 */
            if (sl > SRV6_MAX_SEGS || sl > srh->first_segment) {
                count(STAT_DROP);
                return XDP_DROP;
            }
            seg = (struct in6_addr *)((void *)(srh + 1) +
                                      (__u64)sl * sizeof(struct in6_addr));
            if ((void *)(seg + 1) > data_end) {
                count(STAT_DROP);
                return XDP_DROP;
            }
            /* End: SL--, DA <- Segment List[SL] */
            srh->segments_left = sl;
            __builtin_memcpy(&ip6->daddr, seg, sizeof(*seg));
            return fib_forward(ctx, eth, ip6, STAT_SRV6_END);
        }
        /* 本地 Locator 内普通地址 (loopback 等): 本地交付 */
        count(STAT_PASS);
        return XDP_PASS;
    }

    /* 非本地地址: 普通 IPv6 快速转发 */
    return fib_forward(ctx, eth, ip6, STAT_FWD);
}
