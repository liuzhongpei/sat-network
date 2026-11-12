# sat-router: 卫星路由器镜像
# 架构对齐 SONiC: 容器化控制面(FRR 或 BIRD2, 由 ROUTING 环境变量选择)
#                + eBPF IPv6/SRv6 转发面

# ---- 阶段1: 编译 eBPF 转发面 ----
FROM debian:bookworm-slim AS build
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
        clang llvm gcc make libbpf-dev libc6-dev linux-libc-dev \
    && rm -rf /var/lib/apt/lists/*
COPY ebpf/ /src/ebpf/
RUN make -C /src/ebpf

# ---- 阶段2: 运行镜像 ----
FROM debian:bookworm-slim
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
        frr frr-pythontools bird2 \
        iproute2 iputils-ping tcpdump procps iperf3 \
        libbpf1 bpftool \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/ebpf/sat_fwd.bpf.o /opt/satfwd/sat_fwd.bpf.o
COPY --from=build /src/ebpf/sat_fwd_loader /opt/satfwd/sat_fwd_loader
COPY docker/daemons /etc/frr/daemons
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /opt/satfwd/sat_fwd_loader

ENTRYPOINT ["/entrypoint.sh"]
