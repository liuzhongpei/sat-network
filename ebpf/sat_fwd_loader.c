// SPDX-License-Identifier: GPL-2.0
/* sat_fwd_loader.c - eBPF 转发面加载器
 *
 * 用法:
 *   sat_fwd_loader load <obj.o> <locator/prefixlen> <iface> [iface...]
 *   sat_fwd_loader stats
 *   sat_fwd_loader unload <iface> [iface...]
 *
 * load:  加载 XDP 程序挂到各 ISL 接口(SKB 模式), 写入本地 Locator,
 *        并将 map 固定到 /sys/fs/bpf/satfwd/ 供 stats 查询
 */
#include <arpa/inet.h>
#include <errno.h>
#include <net/if.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <linux/if_link.h>

#define PIN_DIR    "/sys/fs/bpf/satfwd"
#define STATS_PIN  PIN_DIR "/stats"
#define LSID_PIN   PIN_DIR "/local_sid"

struct lsid_key {
    __u32 prefixlen;
    struct in6_addr addr;
};

static const char *stat_names[] = {
    "rx_ipv6", "fwd_fib", "srv6_end", "pass_kernel", "drop",
};
#define STAT_MAX 5

static int do_load(int argc, char **argv)
{
    struct bpf_object *obj;
    struct bpf_program *prog;
    struct bpf_map *lsid_map, *stats_map;
    struct lsid_key key = {};
    __u32 val = 1;
    char locator[64], *slash;
    int prog_fd, i, err;

    if (argc < 3) {
        fprintf(stderr, "usage: load <obj.o> <locator/plen> <iface>...\n");
        return 1;
    }

    /* 解析 locator 前缀 */
    snprintf(locator, sizeof(locator), "%s", argv[1]);
    slash = strchr(locator, '/');
    if (!slash) {
        fprintf(stderr, "locator 需带前缀长度, 如 fcbb:0:1::/48\n");
        return 1;
    }
    *slash = '\0';
    key.prefixlen = atoi(slash + 1);
    if (inet_pton(AF_INET6, locator, &key.addr) != 1) {
        fprintf(stderr, "无效 locator: %s\n", locator);
        return 1;
    }

    obj = bpf_object__open_file(argv[0], NULL);
    if (!obj) {
        fprintf(stderr, "打开 %s 失败: %s\n", argv[0], strerror(errno));
        return 1;
    }
    err = bpf_object__load(obj);
    if (err) {
        fprintf(stderr, "加载 BPF 对象失败: %d\n", err);
        return 1;
    }

    lsid_map = bpf_object__find_map_by_name(obj, "local_sid");
    stats_map = bpf_object__find_map_by_name(obj, "stats");
    prog = bpf_object__find_program_by_name(obj, "sat_fwd");
    if (!lsid_map || !stats_map || !prog) {
        fprintf(stderr, "BPF 对象缺少 map/prog\n");
        return 1;
    }

    err = bpf_map_update_elem(bpf_map__fd(lsid_map), &key, &val, BPF_ANY);
    if (err) {
        fprintf(stderr, "写入 local_sid 失败: %s\n", strerror(errno));
        return 1;
    }

    /* 固定 map 便于 stats 子命令与 bpftool 查看 */
    mkdir(PIN_DIR, 0755);
    bpf_map__pin(stats_map, STATS_PIN);
    bpf_map__pin(lsid_map, LSID_PIN);

    prog_fd = bpf_program__fd(prog);
    for (i = 2; i < argc; i++) {
        int ifindex = if_nametoindex(argv[i]);
        if (!ifindex) {
            fprintf(stderr, "接口不存在: %s (跳过)\n", argv[i]);
            continue;
        }
        err = bpf_xdp_attach(ifindex, prog_fd, XDP_FLAGS_SKB_MODE, NULL);
        if (err)
            fprintf(stderr, "挂载 %s 失败: %d\n", argv[i], err);
        else
            printf("[satfwd] XDP 已挂载 %s (locator %s/%u)\n",
                   argv[i], locator, key.prefixlen);
    }
    /* XDP 程序由内核持有引用, 进程退出后仍生效 */
    return 0;
}

static int do_stats(void)
{
    int fd = bpf_obj_get(STATS_PIN);
    int ncpu = libbpf_num_possible_cpus();
    __u64 *vals;
    __u32 k;

    if (fd < 0) {
        fprintf(stderr, "stats map 未找到 (转发面未加载?)\n");
        return 1;
    }
    /* percpu map 查询写入 ncpu 个条目, 按实际 CPU 数分配避免溢出 */
    vals = calloc(ncpu, sizeof(*vals));
    if (!vals) {
        fprintf(stderr, "内存不足\n");
        return 1;
    }
    for (k = 0; k < STAT_MAX; k++) {
        __u64 sum = 0;
        if (bpf_map_lookup_elem(fd, &k, vals) == 0)
            for (int c = 0; c < ncpu; c++)
                sum += vals[c];
        printf("%-12s %llu\n", stat_names[k], (unsigned long long)sum);
    }
    free(vals);
    return 0;
}

static int do_unload(int argc, char **argv)
{
    for (int i = 0; i < argc; i++) {
        int ifindex = if_nametoindex(argv[i]);
        if (ifindex)
            bpf_xdp_detach(ifindex, XDP_FLAGS_SKB_MODE, NULL);
    }
    unlink(STATS_PIN);
    unlink(LSID_PIN);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr,
                "usage: %s load <obj.o> <locator/plen> <iface>...\n"
                "       %s stats\n"
                "       %s unload <iface>...\n",
                argv[0], argv[0], argv[0]);
        return 1;
    }
    if (strcmp(argv[1], "load") == 0)
        return do_load(argc - 2, argv + 2);
    if (strcmp(argv[1], "stats") == 0)
        return do_stats();
    if (strcmp(argv[1], "unload") == 0)
        return do_unload(argc - 2, argv + 2);
    fprintf(stderr, "未知子命令: %s\n", argv[1]);
    return 1;
}
