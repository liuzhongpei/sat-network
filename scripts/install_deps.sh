#!/bin/bash
# 安装测试系统依赖: Docker + containerlab (+ 可选 SONiC-VS 镜像)
# 用法: sudo bash scripts/install_deps.sh
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 sudo 运行: sudo bash $0" >&2
    exit 1
fi
REAL_USER=${SUDO_USER:-$USER}

echo "==> 1/3 安装 Docker (docker.io)"
if ! command -v docker >/dev/null; then
    apt-get update
    apt-get install -y docker.io
    systemctl enable --now docker
fi
usermod -aG docker "${REAL_USER}" || true
docker version --format 'Docker Server {{.Server.Version}} OK'

echo "==> 2/3 安装 containerlab"
if ! command -v containerlab >/dev/null; then
    bash -c "$(curl -sL https://get.containerlab.dev)"
fi
containerlab version | head -3

echo "==> 3/3 可选: 拉取 SONiC-VS 镜像 (约 1GB, 失败不影响默认 sat-router 模式)"
docker pull sonicdev-microsoft.azurecr.io/docker-sonic-vs:latest \
    && docker tag sonicdev-microsoft.azurecr.io/docker-sonic-vs:latest docker-sonic-vs:latest \
    || echo "SONiC-VS 镜像拉取失败(网络/仓库原因), 跳过; 默认 sat-router 模式不受影响"

echo
echo "安装完成. ${REAL_USER} 已加入 docker 组 (新开终端生效)."
