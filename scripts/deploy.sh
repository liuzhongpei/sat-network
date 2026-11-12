#!/bin/bash
# 一键部署: 生成拓扑 -> 构建镜像 -> containerlab deploy -> 初始化链路参数
set -e
cd "$(dirname "$0")/.."

CONFIG=${CONFIG:-config/constellation.yaml}
OUT=${OUT:-out}

echo "==> 1/4 生成拓扑与配置"
python3 -m topogen.generate --config "${CONFIG}" --out "${OUT}"

echo "==> 2/4 构建 sat-router 镜像"
docker build -t sat-router:latest -f docker/Dockerfile .

echo "==> 3/4 containerlab 部署"
TOPO=$(ls "${OUT}"/*.clab.yml | head -1)
sudo containerlab deploy -t "${TOPO}" --reconfigure

echo "==> 4/4 初始化 ISL 时延/速率"
python3 dynamics/link_dynamics.py --config "${CONFIG}" \
    --links "${OUT}/links.json" --once

cat <<'EOF'

部署完成. 后续操作:
  持续链路动态:  python3 dynamics/link_dynamics.py &
  验证:          scripts/verify.sh
  销毁:          sudo containerlab destroy -t out/*.clab.yml
EOF
