# 卫星网络路由协议测试系统 - 顶层构建
CONFIG ?= config/constellation.yaml
OUT    ?= out

.PHONY: all gen ebpf image deploy dynamics web verify destroy clean

all: gen image

gen:              ## 生成 containerlab 拓扑 / FRR 配置 / 链路清单
	python3 -m topogen.generate --config $(CONFIG) --out $(OUT)

ebpf:             ## 本机编译 eBPF 转发面 (调试用, 镜像内已自带)
	$(MAKE) -C ebpf

image:            ## 构建 sat-router 镜像 (含 eBPF 转发面编译)
	docker build -t sat-router:latest -f docker/Dockerfile .

deploy:           ## 一键部署
	bash scripts/deploy.sh

dynamics:         ## 启动链路动态引擎 (前台)
	python3 dynamics/link_dynamics.py --config $(CONFIG) --links $(OUT)/links.json

web:              ## 启动 Web 可视化 (星座/链路/路由 update, 默认 :8080)
	python3 web/server.py --config $(CONFIG) --links $(OUT)/links.json

verify:           ## 验证 BGP/连通性/SRv6/eBPF
	bash scripts/verify.sh

destroy:          ## 销毁 lab
	sudo containerlab destroy -t $(OUT)/*.clab.yml || true

clean: destroy
	rm -rf $(OUT)
	$(MAKE) -C ebpf clean
