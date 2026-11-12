#!/bin/bash
# 16x20 卫星网络演示启动脚本 (320 节点: 16 轨道面 x 20 卫星, 单星 4 ISL)
#
# 用法:
#   sudo bash scripts/run_16x20.sh deploy    # 部署 lab (生成拓扑+建镜像+clab deploy+链路初始化)
#   sudo bash scripts/run_16x20.sh start     # 后台启动链路动态引擎 + Web 面板
#   sudo bash scripts/run_16x20.sh status    # 查看引擎步进/链路通断/Web 状态
#   sudo bash scripts/run_16x20.sh stop      # 停止引擎与 Web (容器保留)
#
# 撤销资源: bash scripts/teardown_16x20.sh [--purge]
set -u
cd "$(dirname "$0")/.."

# ---- 16x20 固化配置 ----
CONFIG=config/walker-16x20.yaml     # 星座配置 (轨道/ISL/仿真节奏)
OUT=out-16x20                       # 产物目录
LAB_NAME=sat-16x20                  # 与 CONFIG 中 constellation.name 一致
WEB_PORT=8086                       # Web 面板端口 (避开 8080~8085)
POLL_INTERVAL=90                    # BGP 轮询周期 (s), 320 节点下勿低于 60
DYN_LOG=/tmp/dynamics-16x20.log
WEB_LOG=/tmp/web-16x20.log

case "${1:-}" in deploy|start|status|stop) ;;
*) grep '^#   sudo bash' "$0" | sed 's/^# *//'; exit 1 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root 权限 (docker/containerlab): sudo bash $0 $1" >&2
    exit 1
fi

case "${1:-}" in
deploy)
    echo "==> 1/4 生成拓扑与配置"
    python3 -m topogen.generate --config "${CONFIG}" --out "${OUT}"

    echo "==> 2/4 构建 sat-router 镜像 (已存在则跳过)"
    if docker image inspect sat-router:latest >/dev/null 2>&1; then
        echo "  镜像已存在, 跳过构建"
    else
        # 国内网络用阿里 Debian 镜像源加速容器内 apt
        docker build --build-arg APT_MIRROR=mirrors.aliyun.com \
            -t sat-router:latest -f docker/Dockerfile .
    fi

    echo "==> 3/4 containerlab 部署 (320 节点, 需数分钟)"
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
    echo "提示: 首步为全量下发 640 链路 (约 2~4 分钟), 之后进入增量节奏"
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
    print("engine=%s t_sim=%.0fs up=%d down=%d max_range=%skm"
          % (d["engine"], d["t_sim"], up, len(d["links"]) - up,
             d.get("max_range_km")))
except Exception as e:
    print("Web 未响应:", e)
EOF
    ;;
stop)
    pkill -f "link_dynamics.py --config ${CONFIG}" 2>/dev/null && echo "引擎已停止" \
        || echo "引擎未在运行"
    pkill -f "web/server.py --config ${CONFIG}" 2>/dev/null && echo "Web 已停止" \
        || echo "Web 未在运行"
    echo "容器保持运行, 销毁用: bash scripts/teardown_16x20.sh"
    ;;
esac
