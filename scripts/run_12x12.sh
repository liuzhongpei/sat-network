#!/bin/bash
# 12x12 卫星网络演示启动脚本 (144 节点: 12 轨道面 x 12 卫星, 单星 4 ISL)
#
# 固化参数 (config/walker-12x12.yaml):
#   - Walker Delta 12 面 x 12 星 = 144 节点, 550km / 75° 高倾角
#     星下点覆盖 ±75° (加拿大全域含北极群岛, 不覆盖两极)
#   - speedup=2.0 (2 倍速): 引擎每 30s 墙钟步进 60s 仿真时间
#   - max_range_km=3700: 同轨 3583km 恒通(骨干), 异轨 1221~4060km 约 97% 在线
#   - Web 面板端口 8084, BGP 轮询 90s (144 节点下勿低于 60)
#
# 权限: 需 docker/containerlab 访问 (加入 docker/clab_admins 组即可, 无需 root)
#
# 用法:
#   bash scripts/run_12x12.sh deploy    # 部署 lab (生成拓扑+建镜像+clab deploy+链路初始化)
#   bash scripts/run_12x12.sh start     # 后台启动链路动态引擎 + Web 面板
#   bash scripts/run_12x12.sh status    # 查看引擎步进/链路通断/Web 状态
#   bash scripts/run_12x12.sh verify    # 星座/路由/转发/SRv6/eBPF 全功能验证
#   bash scripts/run_12x12.sh stop      # 停止引擎与 Web (容器保留)
#
# 撤销资源: bash scripts/teardown_12x12.sh [--purge]
set -u
cd "$(dirname "$0")/.."

# ---- 12x12 固化配置 ----
CONFIG=config/walker-12x12.yaml     # 星座配置 (轨道/ISL/仿真节奏)
OUT=out-12x12                       # 产物目录
LAB_NAME=sat-12x12                  # 与 CONFIG 中 constellation.name 一致
WEB_PORT=8084                       # Web 面板端口 (避开 8080~8083)
POLL_INTERVAL=90                    # BGP 轮询周期 (s), 144 节点下勿低于 60
DYN_LOG=/tmp/dynamics-12x12.log
WEB_LOG=/tmp/web-12x12.log

case "${1:-}" in deploy|start|status|verify|stop) ;;
*) grep '^#   bash scripts/run_12x12.sh' "$0" | sed 's/^# *//'; exit 1 ;;
esac

# ---- 权限检查: docker/containerlab 可用即可 (docker 组 / 免密 sudo 均可) ----
if ! docker ps >/dev/null 2>&1; then
    if sudo -n docker ps >/dev/null 2>&1; then
        # 无 sudo 前缀的历史写法不再要求; 此处提示用 sudo 重跑
        exec sudo -n bash "$0" "$@"
    fi
    echo "无法访问 docker: 请将用户加入 docker 组 (或配置免密 sudo) 后重试" >&2
    exit 1
fi

case "${1:-}" in
deploy)
    # 注意: 容器运行后生成的 frr.conf 属主会变为容器内用户 (非当前用户),
    # 直接覆盖会 PermissionError, 故先清理历史生成产物再重新生成
    echo "==> 1/4 清理历史产物并生成拓扑与配置"
    rm -rf "${OUT}/configs" "${OUT}/link_state.json"
    python3 -m topogen.generate --config "${CONFIG}" --out "${OUT}"

    echo "==> 2/4 构建 sat-router 镜像 (已存在则跳过)"
    if docker image inspect sat-router:latest >/dev/null 2>&1; then
        echo "  镜像已存在, 跳过构建"
    else
        # 国内网络用阿里 Debian 镜像源加速容器内 apt
        docker build --build-arg APT_MIRROR=mirrors.aliyun.com \
            -t sat-router:latest -f docker/Dockerfile .
    fi

    echo "==> 3/4 containerlab 部署 (144 节点, 需数分钟)"
    containerlab deploy -t "${OUT}/${LAB_NAME}.clab.yml" --reconfigure

    echo "==> 4/4 初始化 ISL 时延/速率"
    python3 dynamics/link_dynamics.py --config "${CONFIG}" \
        --links "${OUT}/links.json" --once
    echo "部署完成. 启动引擎与面板: bash $0 start"
    ;;
start)
    pkill -f "link_dynamics.py --config ${CONFIG}" 2>/dev/null || true
    pkill -f "web/server.py --config ${CONFIG}" 2>/dev/null || true
    sleep 1
    nohup python3 dynamics/link_dynamics.py \
        --config "${CONFIG}" --links "${OUT}/links.json" \
        > "${DYN_LOG}" 2>&1 &
    nohup python3 web/server.py \
        --config "${CONFIG}" --links "${OUT}/links.json" \
        --port "${WEB_PORT}" --poll-interval "${POLL_INTERVAL}" \
        > "${WEB_LOG}" 2>&1 &
    sleep 3
    echo "引擎: $(head -1 ${DYN_LOG} 2>/dev/null || echo '日志待生成')"
    echo "面板: http://127.0.0.1:${WEB_PORT}  (日志 ${WEB_LOG})"
    echo "提示: 首步为全量下发 288 链路 (约 1~2 分钟), 之后每 30s 增量下发 (2 倍速)"
    ;;
status)
    pgrep -af "link_dynamics.py --config ${CONFIG}|web/server.py --config ${CONFIG}" \
        || echo "引擎/Web 未运行"
    echo "---- 引擎最近步进:"
    grep -v WARN "${DYN_LOG}" 2>/dev/null | tail -3 || echo "  无日志"
    echo "---- Web 状态:"
    python3 - <<EOF
import json, urllib.request
try:
    d = json.load(urllib.request.urlopen(
        "http://127.0.0.1:${WEB_PORT}/api/state", timeout=10))
    up = sum(1 for l in d["links"] if l["up"])
    lats = [s["lat"] for s in d["sats"].values()]
    print("engine=%s t_sim=%.0fs up=%d down=%d 纬度%.0f°S~%.0f°N max_range=%skm"
          % (d["engine"], d["t_sim"], up, len(d["links"]) - up,
             min(lats), max(lats), d.get("max_range_km")))
except Exception as e:
    print("Web 未响应:", e)
EOF
    ;;
verify)
    echo "==== 12x12 全功能验证 ===="
    SRC=clab-${LAB_NAME}-sat0-0

    echo "== [1/6] Web 引擎状态 =="
    python3 - <<EOF
import json, urllib.request
try:
    d = json.load(urllib.request.urlopen(
        "http://127.0.0.1:${WEB_PORT}/api/state", timeout=10))
    up = sum(1 for l in d["links"] if l["up"])
    lats = [s["lat"] for s in d["sats"].values()]
    print("engine=%s t_sim=%.0fs speedup=%sx 卫星=%d 纬度%.0f°S~%.0f°N "
          "链路up=%d/down=%d"
          % (d["engine"], d["t_sim"], d["speedup"], len(d["sats"]),
             min(lats), max(lats), up, len(d["links"]) - up))
except Exception as e:
    print("Web 未响应:", e); exit(1)
EOF

    echo "== [2/6] 星座规模 =="
    curl -s "http://127.0.0.1:${WEB_PORT}/api/meta" | python3 -c "
import json,sys
m=json.load(sys.stdin)
print('%dx%d=%d 节点, %dkm, 周期 %.1fmin, 速率 %.0fMbps' % (
    m['planes'], m['sats_per_plane'], m['planes']*m['sats_per_plane'],
    m['altitude_km'], m['period_min'], m['rate_mbps']))"

    echo "== [3/6] BGP 收敛 (抽查 5 节点) =="
    for n in sat0-0 sat6-6 sat11-11 sat1-0 sat3-3; do
        docker exec "clab-${LAB_NAME}-${n}" vtysh -c 'show bgp summary json' \
            2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)['ipv6Unicast']
    peers=d.get('peers',{})
    est=sum(1 for p in peers.values() if p.get('state')=='Established')
    print('%-9s Established=%d/%d RIB=%s' % ('$n', est, len(peers), d.get('ribCount')))
except Exception:
    print('%-9s 查询失败' % '$n')"
    done

    echo "== [4/6] 端到端转发 (sat0-0 到 3 目标) =="
    for entry in "sat6-6|fcbb:6:6::1" "sat11-11|fcbb:b:b::1" "sat0-11|fcbb:0:b::1"; do
        name="${entry%%|*}"; ip="${entry##*|}"
        out=$(docker exec "${SRC}" ping6 -c 3 -W 2 "$ip" 2>&1 | grep "packets transmitted")
        echo "  → ${name}: ${out}"
    done

    echo "== [5/6] SRv6 源路由 (经 sat1-1 End SID 绕行) =="
    docker exec "${SRC}" ip -6 route replace fcbb:6:6::1/128 \
        encap seg6 mode encap segs "fcbb:1:1::100,fcbb:6:6::1" dev eth1 2>/dev/null \
        && echo "  SRv6 路由下发成功" || echo "  SRv6 下发失败 (缺 CONFIG_IPV6_SEG6_LWTUNNEL)"
    docker exec "${SRC}" ping6 -c 3 -W 2 fcbb:6:6::1 2>&1 | grep "packets transmitted"
    docker exec "${SRC}" ip -6 route del fcbb:6:6::1/128 2>/dev/null

    echo "== [6/6] eBPF 转发面统计 =="
    for n in sat0-0 sat1-1 sat6-6; do
        echo "  ${n}: $(docker exec "clab-${LAB_NAME}-${n}" \
            /opt/satfwd/sat_fwd_loader stats 2>/dev/null | head -1 || echo 无统计)"
    done
    echo "==== 验证结束 ===="
    ;;
stop)
    pkill -f "link_dynamics.py --config ${CONFIG}" 2>/dev/null && echo "引擎已停止" \
        || echo "引擎未在运行"
    pkill -f "web/server.py --config ${CONFIG}" 2>/dev/null && echo "Web 已停止" \
        || echo "Web 未在运行"
    echo "容器保持运行, 销毁用: bash scripts/teardown_12x12.sh"
    ;;
esac
