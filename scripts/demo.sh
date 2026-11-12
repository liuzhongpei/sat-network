#!/bin/bash
# Demo 一键运行脚本 (实测校准参数已固化)
#
# 星座: Walker Delta 8 轨道面 x 12 卫星 = 96 节点, 单星 4 条 ISL (+Grid)
#       550km / 倾角 53 度 / max_range_km=5500 (见 config/walker-8x12.yaml)
# 节奏: 仿真步长 60s, delay_eps_ms=1.0 (增量下发 netem), BGP 轮询 60s
#       -- 以上为 8 核/10GB 主机跑 96 容器实测可持续的参数组合
#
# 用法:
#   bash scripts/demo.sh deploy    # 部署 lab (生成拓扑+建镜像+clab deploy)
#   bash scripts/demo.sh start     # 启动链路动态引擎 + Web 面板 (后台)
#   bash scripts/demo.sh status    # 查看运行状态
#   bash scripts/demo.sh stop      # 停止引擎与 Web
#   bash scripts/demo.sh destroy   # 销毁 lab
set -e
cd "$(dirname "$0")/.."

# ---- demo 固化配置 ----
CONFIG=config/walker-8x12.yaml     # 星座配置 (轨道/ISL/仿真节奏)
OUT=out-8x12                       # 产物目录
LAB_NAME=sat-8x12                  # 与 CONFIG 中 constellation.name 一致
WEB_PORT=8080                      # Web 面板端口
POLL_INTERVAL=60                   # BGP 轮询周期 (s), 96 节点下勿低于 60
DYN_LOG=/tmp/dynamics.log
WEB_LOG=/tmp/web.log

case "${1:-}" in
deploy)
    CONFIG="${CONFIG}" OUT="${OUT}" bash scripts/deploy.sh
    ;;
start)
    pkill -f dynamics/link_dynamics.py 2>/dev/null || true
    pkill -f web/server.py 2>/dev/null || true
    sleep 1
    nohup python3 dynamics/link_dynamics.py \
        --config "${CONFIG}" --links "${OUT}/links.json" \
        > "${DYN_LOG}" 2>&1 &
    nohup python3 web/server.py \
        --config "${CONFIG}" --links "${OUT}/links.json" \
        --port "${WEB_PORT}" --poll-interval "${POLL_INTERVAL}" \
        > "${WEB_LOG}" 2>&1 &
    sleep 3
    echo "引擎: $(head -1 ${DYN_LOG})"
    echo "面板: http://127.0.0.1:${WEB_PORT}  (日志 ${WEB_LOG})"
    echo "提示: 首步为全量下发 (约 1~2 分钟), 之后进入增量节奏"
    ;;
status)
    pgrep -af "link_dynamics.py|web/server.py" || echo "引擎/Web 未运行"
    echo "---- 引擎最近步进:"
    grep -v WARN "${DYN_LOG}" 2>/dev/null | tail -3 || true
    echo "---- Web 状态:"
    curl -s "http://127.0.0.1:${WEB_PORT}/api/state" | python3 -c "
import json,sys
d=json.load(sys.stdin)
up=sum(1 for l in d['links'] if l['up'])
print('engine=%s t_sim=%.0fs up=%d down=%d max_range=%skm'
      % (d['engine'], d['t_sim'], up, len(d['links'])-up,
         d.get('max_range_km')))" 2>/dev/null || echo "Web 未响应"
    ;;
stop)
    pkill -f dynamics/link_dynamics.py 2>/dev/null || true
    pkill -f web/server.py 2>/dev/null || true
    echo "引擎与 Web 已停止 (容器保持运行, 销毁用: $0 destroy)"
    ;;
destroy)
    pkill -f dynamics/link_dynamics.py 2>/dev/null || true
    pkill -f web/server.py 2>/dev/null || true
    sudo containerlab destroy -t "${OUT}/${LAB_NAME}.clab.yml" --cleanup
    ;;
*)
    grep '^#   bash' "$0" | sed 's/^# *//'
    exit 1
    ;;
esac
