#!/bin/bash
# sat-router 容器入口: 等待 ISL 接口就绪 -> 启动路由控制面 -> 加载 eBPF 转发面
# 环境变量 (由 containerlab 拓扑注入):
#   SAT_NAME      节点名, 如 sat0-1
#   SAT_LOOPBACK  loopback 地址, 如 fcbb:0:1::1/128
#   SAT_LOCATOR   SRv6 locator, 如 fcbb:0:1::/48
#   ISL_IFACES    逗号分隔接口列表, 如 eth1,eth2,eth3,eth4,eth5,eth6
#   ROUTING       路由协议栈: frr(默认) / bird2
#   ISL_ADDRS     bird2 模式接口赋址表, 如 eth1=fd00:100::/127,...
#                 (FRR 模式由 zebra 按 frr.conf 配置, 无需此变量)
set -u

echo "[entrypoint] ${SAT_NAME:-sat}: 初始化"

# 1. 内核转发参数
sysctl -qw net.ipv6.conf.all.forwarding=1
sysctl -qw net.ipv6.conf.default.forwarding=1
sysctl -qw net.ipv4.ip_forward=1
# 链路 down/up (极区断链模拟) 时保留 IPv6 地址, 否则 /127 互联地址丢失
sysctl -qw net.ipv6.conf.all.keep_addr_on_down=1
sysctl -qw net.ipv6.conf.default.keep_addr_on_down=1
# 内核 SRv6 兜底 (eBPF PASS 的最终段报文交内核 seg6 处理)
sysctl -qw net.ipv6.conf.all.seg6_enabled=1 2>/dev/null || true

# 2. bpffs (map pin 需要)
mountpoint -q /sys/fs/bpf || mount -t bpf bpf /sys/fs/bpf

# 3. loopback 地址
if [ -n "${SAT_LOOPBACK:-}" ]; then
    ip -6 addr replace "${SAT_LOOPBACK}" dev lo
fi

# 4. 等待 containerlab 注入全部 ISL 接口 (容器先启动, veth 后注入)
IFS=',' read -ra IFACES <<< "${ISL_IFACES:-eth1,eth2}"
deadline=$((SECONDS + 120))
for ifname in "${IFACES[@]}"; do
    while [ ! -d "/sys/class/net/${ifname}" ]; do
        if [ ${SECONDS} -ge ${deadline} ]; then
            echo "[entrypoint] 等待接口 ${ifname} 超时, 跳过该接口" >&2
            continue 2
        fi
        sleep 0.5
    done
    sysctl -qw "net.ipv6.conf.${ifname}.forwarding=1"
    sysctl -qw "net.ipv6.conf.${ifname}.seg6_enabled=1" 2>/dev/null || true
    ip link set "${ifname}" up
done
echo "[entrypoint] ISL 接口就绪: ${ISL_IFACES:-}"

# 5. 启动路由控制面 (配置由 containerlab bind 挂载)
ROUTING="${ROUTING:-frr}"
if [ "${ROUTING}" = "bird2" ] || [ "${ROUTING}" = "ocrm" ]; then
    # BIRD/OCRM 不管理接口地址, 入口脚本按 ISL_ADDRS 赋值
    IFS=',' read -ra ADDRS <<< "${ISL_ADDRS:-}"
    for kv in "${ADDRS[@]}"; do
        ifname="${kv%%=*}"; addr="${kv#*=}"
        [ -d "/sys/class/net/${ifname}" ] \
            && ip -6 addr replace "${addr}" dev "${ifname}"
    done
    if [ "${ROUTING}" = "ocrm" ]; then
        echo "[entrypoint] OCRM 控制面启动 (真实协议栈 + 内核 IPv6 数据面)"
        # 守护循环: 进程崩溃/被杀后 1s 自动拉起 (重启恢复实验依赖此路径,
        # 重启后 LSU seq 归零, 由邻居侧 lsu_stale_s 机制重新接纳)
        ( while true; do
              python3 /opt/ocrm/ocrm/node_daemon.py
              echo "[entrypoint] OCRM daemon 退出, 1s 后重启" >&2
              sleep 1
          done ) &
        echo "[entrypoint] ${SAT_NAME:-sat} 就绪"
        exec sleep infinity
    fi
    mkdir -p /run/bird
    bird -c /etc/bird/bird.conf
    echo "[entrypoint] BIRD2 控制面已启动"
else
    chown -R frr:frr /etc/frr 2>/dev/null || true
    /usr/lib/frr/frrinit.sh start
    echo "[entrypoint] FRR 控制面已启动"
fi

# 6. 转发面
if [ "${SAT_XDP:-1}" = "0" ]; then
    # 大规模拓扑(约300+节点)模式: 不加载 XDP (bpf_redirect 会绕过 netem
    # 时延), 纯内核转发; 同时该规模下内核对链路本地下一跳的 ND 解析会
    # 失效, 后台从全局邻居表项按 EUI-64 推导静态链路本地邻居表项兜底
    echo "[entrypoint] SAT_XDP=0: 内核转发模式"
    (
        need=${#IFACES[@]}
        for attempt in $(seq 120); do
            have=$(python3 - <<'PYEOF' 2>/dev/null || echo 0
import subprocess, re, os
ifaces = set(os.environ.get("ISL_IFACES", "").split(","))
out = subprocess.run(["ip","-6","neigh","show"], capture_output=True, text=True).stdout
have = 0
for line in out.splitlines():
    m = re.match(r"(\S+) dev (\S+) lladdr (\S+)", line)
    if not m:
        continue
    addr, dev, mac = m.groups()
    if dev not in ifaces or addr.startswith("fe80"):
        continue
    b = bytes.fromhex(mac.replace(":",""))
    b = bytes([b[0]^2]) + b[1:3] + b"\xff\xfe" + b[3:]
    ll = "fe80::" + ":".join(b[i:i+2].hex() for i in (0,2,4,6))
    r = subprocess.run(["ip","-6","neigh","show", ll, "dev", dev],
                       capture_output=True, text=True)
    if "PERMANENT" in r.stdout:
        have += 1
        continue
    subprocess.run(["ip","-6","neigh","replace", ll, "lladdr", mac,
                    "dev", dev, "nud", "permanent"])
print(have)
PYEOF
)
            if [ "${have:-0}" -ge "${need}" ] 2>/dev/null; then
                break
            fi
            sleep 2
        done
    ) &
elif [ -n "${SAT_LOCATOR:-}" ]; then
    /opt/satfwd/sat_fwd_loader load /opt/satfwd/sat_fwd.bpf.o \
        "${SAT_LOCATOR}" "${IFACES[@]}" \
        || echo "[entrypoint] 警告: eBPF 转发面加载失败, 回退内核转发" >&2
fi

echo "[entrypoint] ${SAT_NAME:-sat} 就绪"
exec sleep infinity
