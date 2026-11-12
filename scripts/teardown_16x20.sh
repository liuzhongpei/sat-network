#!/bin/bash
# 16x20 卫星网络演示资源撤销: 停引擎/Web -> 销毁 lab 容器与连线 -> (可选) 清产物
#
# 用法:  sudo bash scripts/teardown_16x20.sh [--purge]
#   --purge  额外删除生成产物 out-16x20/ 与日志
set -u
cd "$(dirname "$0")/.."

CONFIG=config/walker-16x20.yaml
OUT=out-16x20
LAB=sat-16x20
TOPO="${OUT}/${LAB}.clab.yml"

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root 权限 (docker/containerlab): sudo bash $0 ${1:-}" >&2
    exit 1
fi

echo "==> 1/3 停止链路动态引擎与 Web 面板"
pkill -f "link_dynamics.py --config ${CONFIG}" 2>/dev/null && echo "  引擎已停止" \
    || echo "  引擎未在运行"
pkill -f "web/server.py --config ${CONFIG}" 2>/dev/null && echo "  Web 已停止" \
    || echo "  Web 未在运行"

echo "==> 2/3 销毁 containerlab 部署 (${LAB}, 320 节点需 1~2 分钟)"
if [ -f "${TOPO}" ]; then
    containerlab destroy -t "${TOPO}" --cleanup 2>&1 | tail -1
else
    echo "  拓扑文件不存在: ${TOPO} (跳过)"
fi
# 兜底: 清掉可能残留的同 lab 容器
left=$(docker ps -aq --filter "name=clab-${LAB}-" | wc -l)
if [ "${left}" -gt 0 ]; then
    echo "  兜底清理残留容器 ${left} 个"
    docker rm -f $(docker ps -aq --filter "name=clab-${LAB}-") >/dev/null
fi

echo "==> 3/3 校验"
n=$(docker ps -a --format '{{.Names}}' | grep -c "clab-${LAB}-" || true)
[ "${n}" -eq 0 ] && echo "  容器已全部清除" || { echo "  仍有 ${n} 个容器!"; exit 1; }

if [ "${1:-}" = "--purge" ]; then
    rm -rf "${OUT}" /tmp/dynamics-16x20.log /tmp/web-16x20.log
    echo "  产物目录 ${OUT}/ 与日志已删除"
fi
echo "资源撤销完成"
