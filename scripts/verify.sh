#!/bin/bash
# 验证: BGP 邻居 / 路由收敛 / 端到端 ping / SRv6 源路由 / eBPF 统计
set -u
cd "$(dirname "$0")/.."

LINKS=${LINKS:-out/links.json}
CONFIG=${CONFIG:-config/constellation.yaml}
LAB=$(python3 -c "import json;print(json.load(open('${LINKS}'))['lab'])")
SRC="clab-${LAB}-sat0-0"

# 取星座规模, 选最远节点作为 ping 目标
read -r PLANES SATS <<< "$(CONFIG="${CONFIG}" python3 - <<'EOF'
import os, yaml
c = yaml.safe_load(open(os.environ["CONFIG"]))["constellation"]
print(c["planes"], c["sats_per_plane"])
EOF
)"
DP=$((PLANES / 2)); DS=$((SATS / 2))
DST_NAME="sat${DP}-${DS}"
DST_LO="fcbb:$(printf %x ${DP}):$(printf %x ${DS})::1"

echo "=============== 1. BGP 邻居状态 (sat0-0) ==============="
docker exec "${SRC}" vtysh -c "show bgp summary" || exit 1

echo "=============== 2. BGP IPv6 路由表规模 ==============="
docker exec "${SRC}" vtysh -c "show bgp ipv6 unicast statistics"

echo "=============== 3. 端到端连通性: sat0-0 -> ${DST_NAME} (${DST_LO}) ==============="
docker exec "${SRC}" ping -6 -c 4 "${DST_LO}"

echo "=============== 4. SRv6 源路由测试 ==============="
# 经中间段 sat1-1 的 End SID 绕行到目的节点
VIA_SID="fcbb:1:1::100"
docker exec "${SRC}" ip -6 route replace "${DST_LO}/128" \
    encap seg6 mode encap segs "${VIA_SID},${DST_LO}" dev eth1 2>/dev/null \
    && echo "已下发 SRv6 encap 路由: segs ${VIA_SID} -> ${DST_LO}" \
    || echo "SRv6 encap 路由下发失败 (需内核 CONFIG_IPV6_SEG6_LWTUNNEL)"
docker exec "${SRC}" ping -6 -c 4 "${DST_LO}"
docker exec "${SRC}" ip -6 route del "${DST_LO}/128" 2>/dev/null

echo "=============== 5. eBPF 转发面统计 ==============="
for node in "${SRC}" "clab-${LAB}-sat1-1" "clab-${LAB}-${DST_NAME}"; do
    echo "--- ${node}"
    docker exec "${node}" /opt/satfwd/sat_fwd_loader stats 2>/dev/null \
        || echo "(无统计, 转发面未加载?)"
done

echo "=============== 验证结束 ==============="
