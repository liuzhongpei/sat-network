#!/bin/bash
# 3x4 星座 (BIRD2 控制面) 运行脚本: 生成拓扑 -> 镜像 -> 部署 -> 引擎/Web -> 状态摘要
# 只负责拉起环境, 不做测试断言; 测试用 test_3x4_bird.sh, 撤销用 teardown_3x4_bird.sh
#
# 用法:  bash scripts/run_3x4_bird.sh [--skip-deploy]
#   --skip-deploy  lab 已在运行时跳过部署阶段, 只重启引擎/Web
# 退出码: 0=环境就绪, 1=启动异常
set -u
cd "$(dirname "$0")/.."

CONFIG=config/walker-3x4-bird.yaml
OUT=out-3x4b
LAB=sat-3x4b
NODES=12
WEB_PORT=8083              # 与 FRR 版 (8081) 面板共存
DYN_LOG=/tmp/dynamics-3x4b.log
WEB_LOG=/tmp/web-3x4b.log
CONVERGE_WAIT=90           # BGP 收敛等待上限 (s)
DST_LO="fcbb:1:2::1"       # 对角最远节点 sat1-2 (收敛探测目标)

# ---------- 阶段 1: 部署 ----------
if [ "${1:-}" != "--skip-deploy" ]; then
    echo "==> 1/4 部署 ${LAB} (生成拓扑 -> 镜像 -> containerlab)"
    python3 -m topogen.generate --config "${CONFIG}" --out "${OUT}" || exit 1
    docker build -q -t sat-router:latest -f docker/Dockerfile . >/dev/null || exit 1
    containerlab deploy -t "${OUT}/${LAB}.clab.yml" --reconfigure >/dev/null || exit 1
else
    echo "==> 1/4 跳过部署 (--skip-deploy)"
fi

# ---------- 阶段 2: 引擎 + Web ----------
echo "==> 2/4 启动链路动态引擎 + Web 面板 (端口 ${WEB_PORT})"
pkill -f "link_dynamics.py --config ${CONFIG}" 2>/dev/null || true
pkill -f "web/server.py --config ${CONFIG}" 2>/dev/null || true
sleep 1
nohup python3 dynamics/link_dynamics.py --config "${CONFIG}" \
    --links "${OUT}/links.json" > "${DYN_LOG}" 2>&1 &
nohup python3 web/server.py --config "${CONFIG}" --links "${OUT}/links.json" \
    --port "${WEB_PORT}" --poll-interval 10 > "${WEB_LOG}" 2>&1 &

# ---------- 阶段 3: 等收敛 ----------
echo "==> 3/4 等待 BGP 收敛 (上限 ${CONVERGE_WAIT}s)"
# 先等引擎首步发布 (初始超距断链下发完成), 避免撞上重收敛窗口
for i in $(seq 30); do
    [ -f "${OUT}/link_state.json" ] \
        && [ "${OUT}/link_state.json" -nt "${OUT}/links.json" ] && break
    sleep 1
done
# 再等路由稳态: 连续 3 次 ping 通视为收敛
streak=0
for i in $(seq "${CONVERGE_WAIT}"); do
    if docker exec "clab-${LAB}-sat0-0" ping -6 -c 1 -W 1 "${DST_LO}" \
            >/dev/null 2>&1; then
        streak=$((streak+1)); [ "${streak}" -ge 3 ] && break
    else
        streak=0
    fi
    sleep 1
done

# ---------- 阶段 4: 状态摘要 ----------
echo "==> 4/4 环境状态摘要"
FAIL=0

n=$(docker ps --format '{{.Names}}' | grep -c "clab-${LAB}-" || true)
if [ "$n" -eq "${NODES}" ]; then
    echo "  容器      : ${n}/${NODES} running"
else
    echo "  容器      : ${n}/${NODES} (异常!)"; FAIL=1
fi

python3 - <<EOF
import json, re, subprocess
try:
    d = json.load(open("${OUT}/link_state.json"))
    up = sum(1 for l in d["links"] if l["up"])
    print("  链路      : up=%d down=%d" % (up, len(d["links"]) - up))
    est = 0
    for i in range(3):      # p in 0..2
        for j in range(4):  # s in 0..3
            r = subprocess.run(["docker","exec","clab-${LAB}-sat%d-%d"%(i,j),
                "birdc","-r","show","protocols"],capture_output=True,timeout=15)
            est += len(re.findall(r"^bgp_\S+\s+BGP\s.*Established",
                                  r.stdout.decode(), re.M))
    print("  BGP 会话  : Established %d/%d" % (est, 2 * up))
except Exception as e:
    print("  链路/会话 : 读取失败 (%s)" % e)
EOF

if [ "${streak:-0}" -ge 3 ]; then
    echo "  连通性    : sat0-0 -> sat1-2 (${DST_LO}) 可达"
else
    echo "  连通性    : 收敛探测未达稳态 (可稍后重试 ping)"; FAIL=1
fi

echo "=============================================="
echo "面板   : http://127.0.0.1:${WEB_PORT}"
echo "测试   : bash scripts/test_3x4_bird.sh --skip-deploy"
echo "撤销   : bash scripts/teardown_3x4_bird.sh [--purge]"
[ "${FAIL}" -eq 0 ] && echo "环境就绪" || echo "环境启动异常, 请检查 ${DYN_LOG} / ${WEB_LOG}"
exit "${FAIL}"
