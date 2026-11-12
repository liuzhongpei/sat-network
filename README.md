# 卫星网络路由协议测试系统 (sat-network-lab)

基于 **containerlab** 的 LEO 卫星星座路由协议测试平台：

- **路由器架构对齐 SONiC**：控制面为容器化 **FRR**（SONiC 的 BGP 容器即 FRR，
  亦可通过 `node.routing: bird2` 切换为 **BIRD2** 控制面做协议栈对比），
  运行 **BGP**（每链路 /127 编号 eBGP）路由协议；同时支持 `sonic-vs` 节点模式做纯控制面验证
- **转发面**：基于 **eBPF/XDP** 的 **IPv6 + SRv6** 转发
  （`bpf_fib_lookup` 查内核 FIB + SRv6 End 行为）
- **星座建模**：Walker 星座圆轨道递推，按轨道运行计算星间链路
  （ISL）距离/时延/通断，每颗卫星 **前向1 + 后向1 + 左向2 + 右向2 = 6 条 ISL**
- **可配置**：轨道参数（轨道面数、每面卫星数、高度、倾角、相位因子等）、
  ISL 速率与最大建链距离均可在 `config/constellation.yaml` 设置
- **Web 可视化**：内置零依赖 Web 面板（`make web`，默认 :8080），
  实时展示星座星下点/轨道轨迹/链路通断时延/BGP 会话与**路由 update 事件流**，
  并支持源-目的**转发路径实查**

---

## 目录

1. [系统架构](#1-系统架构)
2. [模块功能](#2-模块功能)
3. [模块接口](#3-模块接口)
4. [数据流转流程](#4-数据流转流程)
5. [安装方法](#5-安装方法)
6. [运行命令与参数](#6-运行命令与参数)
7. [配置说明](#7-配置说明-configconstellationyaml)
8. [Web 可视化面板](#8-web-可视化面板)
9. [常用调试命令](#9-常用调试命令)
10. [测试场景示例](#10-测试场景示例)
11. [目录结构](#11-目录结构)
12. [关于 SONiC 模式的说明](#12-关于-sonic-模式的说明)
13. [大规模部署注意事项](#13-大规模部署注意事项)
14. [OCRM 本体中心路由机制](#14-ocrm-本体中心路由机制)

---

## 1. 系统架构

系统分五层：**配置层 → 生成层 → 部署层 → 运行层 → 观测层**。

```
┌─ 配置层 ──────────────────────────────────────────────────────────┐
│  config/constellation.yaml  (轨道参数 / ISL 速率与建链距离 / 节点类型 / 仿真参数)  │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌─ 生成层 topogen/ ─────────────────────────────────────────────────┐
│  orbit.py 轨道递推 → isl.py +Grid 6链路 → addressing.py 地址/AS 规划   │
│  → frrgen.py FRR 配置 / clabgen.py clab 拓扑 / generate.py CLI 入口  │
│  产物: out/<lab>.clab.yml   out/configs/<node>/frr.conf   out/links.json │
└──────────────┬───────────────────────────────┬───────────────────┘
               ▼                               │
┌─ 部署层 ─────────────────────────┐            │
│  docker/  sat-router 镜像        │            │
│  (FRR + eBPF 转发面 多阶段构建)    │            │
│  containerlab deploy             │            │
└──────────────┬──────────────────┘            │
               ▼                               ▼
┌─ 运行层 (每颗卫星一个容器) ────────┐  ┌─ 链路动态引擎 dynamics/ ─────────┐
│ ┌─────────────────────┐          │  │  link_dynamics.py                │
│ │ FRR 控制面 (bgpd)    │◄─ SONiC  │  │  按轨道递推逐步计算每条 ISL:       │
│ │ 编号 eBGP (/127)    │   式容器  │  │  距离→传播时延, 极区断链,          │
│ └──────────┬──────────┘   化 FRR │  │  视距判断, 最大建链距离            │
│        内核 FIB                  │  │  ↓ docker exec                   │
│ ┌──────────┴──────────┐          │◄─┤  tc netem delay/rate,            │
│ │ eBPF/XDP 转发面      │          │  │  ip link up/down                 │
│ │ IPv6 快转 + SRv6 End│          │  │  ↓ 原子发布                       │
│ │ (eth1..eth6 ISL)    │          │  │  out/link_state.json             │
│ └─────────────────────┘          │  └──────────────┬──────────────────┘
└──────────────┬──────────────────┘                 │
               ▼                                    ▼
┌─ 观测层 web/ ─────────────────────────────────────────────────────┐
│  server.py: HTTP API + BGP 轮询线程 (vtysh json) + 路径实查 (route get) │
│  static/index.html: Canvas 星座地图 / 链路表 / BGP 事件流 / 路径查询+延迟带宽实测 │
└──────────────────────────────────────────────────────────────────┘
```

### ISL 接口约定 (+Grid, 每星 4 或 6 链路)

| 接口 | 方向  | 对端卫星       | 对端接口 | 构型 |
|------|-------|----------------|----------|------|
| eth1 | 前向  | (p, s+1)       | eth2     | 4/6  |
| eth2 | 后向  | (p, s-1)       | eth1     | 4/6  |
| eth3 | 左向1 | (p-1, s)       | eth5     | 4/6  |
| eth4 | 左向2 | (p-1, s+1)     | eth6     | 仅 6 |
| eth5 | 右向1 | (p+1, s)       | eth3     | 4/6  |
| eth6 | 右向2 | (p+1, s-1)     | eth4     | 仅 6 |

`isl.links_per_sat: 4` 时为经典 +Grid（前/后/左/右各 1，无斜向链路），
`6` 时另加斜向左 2/右 2。

### 地址与 AS 规划 (plane=p, sat=s，十六进制记 p/s)

| 项目         | 规划                          |
|--------------|-------------------------------|
| AS 号        | 4200000000 + p*1000 + s       |
| Loopback     | `fcbb:<p>:<s>::1/128`         |
| SRv6 Locator | `fcbb:<p>:<s>::/48`           |
| End SID      | `fcbb:<p>:<s>::100`           |
| ISL 互联     | 每链路 /127（`fd00:100::/64` 顺序分配，编号 eBGP） |

### 链路通断三重判据

动态引擎每步对每条 ISL 依次判断（任一不满足即 down）：

1. **极区断链**：异轨链路任一端 |纬度| > `polar_cutoff_lat_deg`
2. **视距遮挡**：两星连线距地心最近点低于 `地球半径 + los_margin_km`
3. **最大建链距离**：星间距离 > `max_range_km`（模拟终端功率/捕获能力限制）

---

## 2. 模块功能

| 模块 | 文件 | 功能 |
|------|------|------|
| **轨道递推** | `topogen/orbit.py` | Walker 星座圆轨道模型：由轨道参数计算任意时刻卫星 ECEF 位置、星下点（含地球自转修正）、星间距离/传播时延/视距 |
| **ISL 生成** | `topogen/isl.py` | 按 +Grid 规则生成全部星间链路（同轨前后向 + 异轨左右向），输出链路清单与每节点接口表 |
| **地址规划** | `topogen/addressing.py` | AS 号、Router-ID、Loopback、SRv6 Locator/End SID、/127 互联地址的统一编址 |
| **FRR 配置生成** | `topogen/frrgen.py` | 生成各节点 frr.conf：编号 eBGP 邻居、Loopback/Locator 宣告、SRv6 locator 配置 |
| **BIRD2 配置生成** | `topogen/birdgen.py` | `node.routing: bird2` 时生成各节点 bird.conf：与 frrgen 等价的编号 eBGP + direct/static/kernel 协议（ECMP merge paths） |
| **clab 拓扑生成** | `topogen/clabgen.py` | 生成 containerlab 拓扑 YAML：节点（镜像/绑定/环境变量）+ 全部 ISL veth 连线 |
| **生成入口** | `topogen/generate.py` | CLI 入口，串联以上模块，产出 `out/` 三类产物 |
| **eBPF 转发面** | `ebpf/sat_fwd.bpf.c` | XDP 程序：IPv6 快转（`bpf_fib_lookup` + `bpf_redirect`）、SRv6 End 行为（LPM 命中本地 Locator 时 SL--/换 DA）、统计计数 |
| **eBPF 加载器** | `ebpf/sat_fwd_loader.c` | load/stats/unload 三个子命令，挂载 XDP 到 ISL 接口、写 Locator map、pin 到 bpffs 供统计查询 |
| **路由器镜像** | `docker/` | sat-router 镜像多阶段构建（FRR + BIRD2 + eBPF 编译产物 + iperf3）；`entrypoint.sh` 编排接口等待→sysctl→控制面启动（ROUTING 环境变量选 FRR/BIRD2/OCRM）→eBPF 加载 |
| **OCRM 路由协议栈** | `ocrm/` | 本体中心路由：双平面控制面（LSU/LSDB/SPF 拓扑面 + OQR/ORD/CPA/OIN 语义面）、三步本体推理、CFML 语义→FIB 映射、轨道面聚合（详见第 14 节） |
| **链路动态引擎** | `dynamics/link_dynamics.py` | 按仿真时间步进：重算每条 ISL 距离/时延/通断，`docker exec` 下发 tc netem 与 ip link，原子发布 `link_state.json` |
| **Web 后端** | `web/server.py` | 纯标准库 HTTP 服务：状态 API、BGP 轮询线程（事件流）、逐跳转发路径实查、路径延迟/带宽实测（ping/iperf3）、轨道轨迹计算 |
| **Web 前端** | `web/static/index.html` | 单文件 Canvas 前端（无 CDN 依赖）：地形底图、星座/轨迹/链路绘制、BGP 事件流、路径查询高亮 |
| **脚本** | `scripts/` | `install_deps.sh` 安装依赖、`deploy.sh` 一键部署、`verify.sh` 五步验证 |

---

## 3. 模块接口

### 3.1 topogen Python API

`topogen/orbit.py`：

```python
cons = make_constellation(cfg["constellation"])   # 从配置构造 Constellation
cons.position(plane, sat, t_s)          # ECEF 坐标 (km)；sat 支持小数(面内相位插值)
cons.subpoint_deg(plane, sat, t_s)      # 星下点 (lat, lon)，含地球自转修正
cons.latitude_deg(plane, sat, t_s)      # 纬度（极区断链判断用）
cons.distance_km(a, b, t_s)             # 星间直线距离，a/b 为 (plane, sat)
cons.propagation_delay_ms(a, b, t_s)    # 光速传播时延
cons.line_of_sight(a, b, t_s, margin_km)# 视距判断（地球遮挡）
cons.period_s / cons.planes / cons.sats_per_plane
```

`topogen/isl.py`：

```python
node_name(plane, sat)                        # -> "sat<p>-<s>"
build_isl_links(planes, sats_per_plane, wrap_planes, links_per_sat=6)
# -> [{a, a_if, a_ps, b, b_if, b_ps, type("intra"/"inter")}, ...]
node_ifaces(plane, sat, planes, wrap_planes, links_per_sat=6) # 单节点接口表
```

`topogen/addressing.py`：

```python
asn(p, s)  router_id(p, s)  loopback(p, s)  loopback_addr(p, s)
locator(p, s)  end_sid(p, s)  isl_addr_pair(link_index)  # -> (a_addr, b_addr) /127
```

`topogen/frrgen.py` / `clabgen.py`：

```python
frr_conf(plane, sat, isl_ifaces)   # -> frr.conf 文本; isl_ifaces=[{ifname,local,peer}]
daemons_file()                     # -> FRR daemons 文件文本
clab_topology(cfg, links)          # -> containerlab 拓扑 YAML 文本
```

### 3.2 CLI 接口

见 [第 6 节 运行命令与参数](#6-运行命令与参数)。

### 3.3 容器接口（entrypoint 环境变量）

由 clab 拓扑注入 sat-router 容器：

| 环境变量 | 示例 | 说明 |
|----------|------|------|
| `SAT_NAME` | `sat0-1` | 节点名 |
| `SAT_LOOPBACK` | `fcbb:0:1::1/128` | Loopback 地址 |
| `SAT_LOCATOR` | `fcbb:0:1::/48` | SRv6 Locator（写入 eBPF LPM map） |
| `ISL_IFACES` | `eth1,...,eth6` | ISL 接口列表（等待就绪 + XDP 挂载） |
| `SAT_XDP` | `0` | 可选。`0`=跳过 XDP、纯内核转发并建静态邻居表项（大规模，见第 13 节） |
| `ROUTING` | `frr`/`bird2`/`ocrm` | 控制面协议栈选择 |
| `SAT_PREFIX` | `fcbb:0:1::/48` | OCRM：本节点可路由前缀 |
| `OCRM_FACES` | `sat0-1\|eth1\|fd00:100::1,...` | OCRM：face 表（邻居\|接口\|对端地址） |
| `OCRM_PORT` | `9900` | OCRM：UDP 端口（默认 9900） |
| `OCRM_AGGREGATE` | `1` | OCRM：轨道面 /32 聚合开关（`0` 关，做 A/B） |

eBPF 加载器（容器内 `/opt/satfwd/`）：

```
sat_fwd_loader load <obj.o> <locator/plen> <iface> [iface...]   # 挂载 XDP + 写 Locator
sat_fwd_loader stats                                            # 读 pin 的统计 map
sat_fwd_loader unload <iface> [iface...]                        # 卸载
```

### 3.4 HTTP API（web/server.py）

| 端点 | 方法 | 参数 | 返回 |
|------|------|------|------|
| `/` | GET | - | 前端页面 |
| `/world.json` | GET | - | 陆地轮廓矢量数据（24h 缓存） |
| `/api/meta` | GET | - | lab 名、星座参数、节点表（loopback/asn/locator）、链路表 |
| `/api/state` | GET | - | 实时状态：引擎发布的链路状态（或引擎离线时几何预测）+ 星下点 + 各面轨道轨迹 + `max_range_km` |
| `/api/bgp` | GET | `since=<事件id>` | 全网 BGP 概览 + 增量 update 事件流 |
| `/api/bgp/<node>` | GET | - | 单节点 BGP 邻居表 + IPv6 路由表（下一跳反查节点名） |
| `/api/path` | GET | `src=<node>&dst=<node>` | 逐跳转发路径实查结果（见 4.5） |
| `/api/measure` | GET | `src&dst&kind=ping\|bw&secs=` | 路径性能实测：ping 延迟/丢包（RTT min/avg/max/mdev）或 iperf3 TCP 带宽（发送/接收 Mbps、重传） |

### 3.5 文件/数据接口

**`out/links.json`**（生成层 → 动态引擎/Web，静态链路清单）：

```json
{"lab": "leo-demo",
 "links": [{"a": "sat0-0", "a_if": "eth1", "a_ps": [0,0],
            "b": "sat0-1", "b_if": "eth2", "b_ps": [0,1],
            "type": "intra", "a_addr": "fd00:100::", "b_addr": "fd00:100::1",
            "distance_km_t0": 7571.0, "delay_ms_t0": 25.254}]}
```

**`out/link_state.json`**（动态引擎 → Web，每步原子发布 tmp+rename）：

```json
{"lab": "...", "t_sim": 123.0, "wall_time": 1753..., "speedup": 1,
 "rate_mbps": 50, "polar_cutoff": 66, "max_range_km": 7600,
 "sats": {"sat0-0": {"plane": 0, "sat": 0, "lat": 62.87, "lon": 2.25}},
 "links": [{"a": "sat0-0", "b": "sat0-1", "a_if": "eth1", "b_if": "eth2",
            "type": "intra", "up": true, "delay_ms": 25.254,
            "distance_km": 7571.0}]}
```

其余产物：`out/<lab>.clab.yml`（containerlab 拓扑）、
`out/configs/<node>/frr.conf`（挂载进各容器的 FRR 配置）。

---

## 4. 数据流转流程

### 4.1 生成流程（make gen）

```
constellation.yaml → make_constellation() 轨道模型
                   → build_isl_links() 链路清单 → isl_addr_pair() 逐链路分配 /127
                   → 每节点: frr_conf() 写 out/configs/<node>/frr.conf
                   → clab_topology() 写 out/<lab>.clab.yml
                   → 链路清单 + t0 距离/时延 写 out/links.json
```

### 4.2 部署流程（make deploy = scripts/deploy.sh）

```
1/4 topogen.generate 生成产物
2/4 docker build sat-router:latest (FRR + eBPF 多阶段)
3/4 containerlab deploy: 创建容器 + veth 连线, 挂载 frr.conf, 注入 SAT_* 环境变量
    容器内 entrypoint.sh: 等 ISL 接口 → sysctl(forwarding/keep_addr_on_down/seg6)
                          → 配 Loopback/互联地址 → 启 FRR → sat_fwd_loader load
4/4 link_dynamics.py --once 初始化各链路 netem 时延/速率
```

### 4.3 链路动态更新流程（make dynamics，每步）

```
t_sim += step_seconds×speedup
对每条链路: distance_km() → 传播时延; 三重判据(极区/视距/距离)判通断
  状态变化 → docker exec ip link set up/down (两端)
  up 链路  → docker exec tc qdisc replace ... netem delay <ms> rate <mbps>
全部下发后 → 原子写 link_state.json (tmp + os.replace)
链路 down → 接口 down → BGP 会话断 → 撤销路由 → 全网重收敛 (BGP 自愈)
```

### 4.4 报文转发数据通路（容器内）

1. XDP 收包 → 非 IPv6 / link-local / 组播（BGP、ND）→ `XDP_PASS` 交内核
2. 目的地址命中本地 Locator（LPM）且带 SRH、SL>0 → **End 行为**：
   `SL--`，`DA ← Segment List[SL]`，继续 FIB 转发
3. 其余 IPv6 → `bpf_fib_lookup`（BGP 路由经 zebra 已在内核 FIB）
   → 改写 MAC、`hop_limit--`、`bpf_redirect` 到出接口
4. 查表失败/邻居未解析/最终段 → 交内核慢路径兜底

控制面到转发面：`bgpd 选路 → zebra 下发内核 FIB → bpf_fib_lookup 直接命中`，
无需自维护路由 map。

### 4.5 BGP 事件流与路径查询（Web）

- **事件流**：后台线程按 `--poll-interval` 轮询各节点
  `vtysh -c "show bgp summary json"`，对比上次快照生成事件
  （会话建立/中断、收到前缀数变化、RIB 增减），前端以 `since` 游标增量拉取
- **路径实查**：`/api/path` 从 src 起逐跳 `docker exec <c> ip -6 route get
  <dst_loopback>`（内核 FIB 真实选路，含 ECMP 哈希结果）；下一跳为全局 /127
  地址时直接反查节点，为 fe80 链路本地地址时按 (节点, 出接口) 反查对端；
  带环路检测与最大跳数保护

---

## 5. 安装方法

### 5.1 一键安装（Ubuntu 22.04+）

```bash
sudo bash scripts/install_deps.sh
```

依次完成：① 安装 Docker（docker.io）并将当前用户加入 docker 组（新开终端生效）；
② 安装 containerlab（官方脚本）；③ 可选拉取 SONiC-VS 镜像（失败不影响默认模式）。

### 5.2 手动安装

```bash
# Docker + containerlab (>= 0.40)
sudo apt-get install -y docker.io
bash -c "$(curl -sL https://get.containerlab.dev)"
sudo usermod -aG docker $USER   # 重新登录生效

# Python 依赖 (仅 PyYAML, Web/引擎均为纯标准库)
pip install pyyaml
```

### 5.3 环境要求

- 内核支持 eBPF/XDP（Ubuntu 22.04+ 默认满足）；SRv6 encap 测试需
  `CONFIG_IPV6_SEG6_LWTUNNEL`
- 磁盘：sat-router 镜像构建约需 1GB；SONiC-VS 镜像额外约 1GB
- 规模参考：24 节点（4×6）常规笔记本即可；66 节点（6×11）建议 8GB+ 内存；
  22 核/30GB 主机实测 144（12×12）与 256（16×16，`node.xdp: false`）可完全
  收敛并稳定运行；320（16×20）会触发内核 7.0（Ubuntu 26.04）大规模 netns
  初始化缺陷（约 10% 节点收发路径损坏，确定性不可用）

## 快速开始

```bash
vim config/constellation.yaml   # 1. 按需修改轨道参数与 ISL 速率/建链距离
make deploy                     # 2. 生成 -> 建镜像 -> clab deploy -> 初始化链路
make dynamics                   # 3. 链路动态引擎 (前台, Ctrl-C 停止)
make web                        # 4. Web 面板 http://127.0.0.1:8080
make verify                     # 5. BGP / ping / SRv6 / eBPF 验证
make destroy                    # 6. 销毁
```

### Demo 一键运行 (scripts/demo.sh, 实测参数已固化)

96 星 Walker Delta 演示构型 (`config/walker-8x12.yaml`: 8面×12星、单星
4 ISL、550km/53°、max_range_km=5500；仿真步长 60s、delay_eps_ms=1.0
增量下发、BGP 轮询 60s —— 8 核/10GB 主机实测可持续)：

```bash
bash scripts/demo.sh deploy    # 部署 lab (生成拓扑+建镜像+clab deploy)
bash scripts/demo.sh start     # 后台启动链路动态引擎 + Web 面板
bash scripts/demo.sh status    # 查看引擎步进/链路通断/Web 状态
bash scripts/demo.sh stop      # 停止引擎与 Web (容器保留)
bash scripts/demo.sh destroy   # 销毁 lab
```

### Demo: 12x12 (144 节点, 高倾角覆盖加拿大, scripts/run_12x12.sh)

12 面 × 12 星 = 144 节点 Walker Delta 高倾角演示构型
(`config/walker-12x12.yaml`：550km / **75° 倾角** / 单星 4 ISL /
`max_range_km=3700` / 仿真步长 60s / **speedup=2.0 两倍速** / Web 端口 8084)：

- **南北覆盖 ±75°**：星下点覆盖加拿大全域（含北极群岛），不覆盖两极。
  倾角 53°→75° 不改变异轨链路配对（无需重部署容器），仅改几何距离——
  同轨 3583km 恒通（骨干保留），异轨 1221~4060km 约 97% 在线（少量周期性
  超距断链演示路由自适应）。
- **2 倍速运行**：引擎每 30s 墙钟步进 60s 仿真时间；Web 卫星位置/轨道线按
  `t_sim + 墙钟流逝×speedup` 实时推进（无 60s 冻结），链路 up/down 仍以引擎
  netem 快照为准（权威）。
- **无需 root**（docker/clab_admins 组即可）；`deploy` 自动清理历史生成产物
  （修复容器运行后 frr.conf 属主变化导致覆盖失败的问题）。

```bash
bash scripts/run_12x12.sh deploy    # 部署 (生成拓扑+建镜像+clab deploy+链路初始化)
bash scripts/run_12x12.sh start     # 后台启动引擎 + Web 面板 :8084
bash scripts/run_12x12.sh status    # 引擎步进/链路通断/Web 状态
bash scripts/run_12x12.sh verify    # 六步全功能验证 (星座/BGP/转发/SRv6/eBPF)
bash scripts/run_12x12.sh stop      # 停止引擎与 Web (容器保留)
bash scripts/teardown_12x12.sh [--purge]  # 销毁 lab (+清产物)
```

`run_12x12.sh verify` 六步实测结果（144 节点 / 2 倍速 / 22 核 30GB）：

| 检查 | 结果 |
|------|------|
| Web 引擎 | engine=True, speedup=2.0x, 144 星, 链路 284 up / 4 down |
| 星座 | 12×12=144 节点, 550km, 周期 95.5min, 覆盖 ±75° |
| BGP 收敛 | 抽查节点 Established 4/4, RIB 999 前缀/节点 |
| 端到端转发 | sat0-0→sat6-6/11-11/0-11 全部 0% 丢包 (RTT 24~290ms) |
| SRv6 源路由 | 经 sat1-1 End SID (`fcbb:1:1::100`) 绕行成功, 0% 丢包 |
| eBPF 转发面 | `sat_fwd_loader` rx_ipv6 计数活跃 |

---

## 6. 运行命令与参数

### 6.1 Make 目标

| 目标 | 作用 | 可覆盖变量 |
|------|------|-----------|
| `make gen` | 生成拓扑/FRR 配置/链路清单 | `CONFIG=<yaml>` `OUT=<dir>` |
| `make image` | 构建 sat-router 镜像 | - |
| `make ebpf` | 本机编译 eBPF（调试用，镜像内已自带） | - |
| `make deploy` | 一键部署（gen→image→clab deploy→链路初始化） | `CONFIG` `OUT`（环境变量） |
| `make dynamics` | 前台运行链路动态引擎 | `CONFIG` `OUT` |
| `make web` | 启动 Web 面板（:8080） | `CONFIG` `OUT` |
| `make verify` | 五步验证 | `CONFIG` `LINKS`（环境变量） |
| `make destroy` / `make clean` | 销毁 lab / 另加删除产物 | - |

### 6.2 拓扑生成器

```bash
python3 -m topogen.generate [--config config/constellation.yaml] [--out out]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--config` | `config/constellation.yaml` | 星座配置文件 |
| `--out` | `out` | 产物输出目录 |

### 6.3 链路动态引擎

```bash
python3 dynamics/link_dynamics.py [--config <yaml>] [--links out/links.json]
                                  [--once] [--dry-run] [--state-file <path>]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--config` | `config/constellation.yaml` | 星座配置（步长/加速比也在此） |
| `--links` | `out/links.json` | 链路清单 |
| `--once` | - | 只执行一步（部署后初始化链路参数） |
| `--dry-run` | - | 只打印命令不下发（离线演算） |
| `--state-file` | `<links目录>/link_state.json` | 状态发布文件路径 |

### 6.4 Web 可视化服务

```bash
python3 web/server.py [--config <yaml>] [--links out/links.json]
                      [--port 8080] [--poll-interval 5.0]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--config` | `config/constellation.yaml` | 星座配置（轨迹/预测计算） |
| `--links` | `out/links.json` | 链路清单（同目录找 link_state.json） |
| `--port` | `8080` | 监听端口（0.0.0.0） |
| `--poll-interval` | `5.0` | BGP 轮询周期（秒），节点多可调大 |

### 6.5 脚本

```bash
sudo bash scripts/install_deps.sh          # 安装依赖 (需 root)
CONFIG=<yaml> OUT=<dir> bash scripts/deploy.sh    # 一键部署
CONFIG=<yaml> LINKS=<links.json> bash scripts/verify.sh  # 验证
bash scripts/demo.sh {deploy|start|status|stop|destroy}  # 96 星 demo 一键运行
bash scripts/run_12x12.sh {deploy|start|status|verify|stop}  # 12x12 构型 (144 星, 75° 覆盖加拿大, 2 倍速): 启动+六步验证 (面板端口 8084)
bash scripts/teardown_12x12.sh [--purge]   # 12x12 构型: 资源撤销
bash scripts/test_3x4.sh [--skip-deploy]   # 3x4 构型 (FRR): 部署+10 项断言自动化测试
bash scripts/teardown_3x4.sh [--purge]     # 3x4 构型 (FRR): 资源撤销 (--purge 连产物)
bash scripts/run_3x4_bird.sh [--skip-deploy]   # 3x4 构型 (BIRD2): 仅拉起环境+状态摘要
bash scripts/test_3x4_bird.sh [--skip-deploy]  # 3x4 构型 (BIRD2): 同上 (面板端口 8083)
bash scripts/teardown_3x4_bird.sh [--purge]    # 3x4 构型 (BIRD2): 资源撤销
bash scripts/test_4x6.sh [--skip-deploy]   # 4x6 构型: 同上 (面板端口 8082)
bash scripts/teardown_4x6.sh [--purge]     # 4x6 构型: 资源撤销
bash scripts/run_16x16.sh {deploy|start|status|stop}   # 16x16 构型 (256 星): 启动 (面板端口 8087)
bash scripts/teardown_16x16.sh [--purge]   # 16x16 构型: 资源撤销
bash scripts/run_16x20.sh {deploy|start|status|stop}   # 16x20 构型 (320 星): 启动 (面板端口 8086)
bash scripts/teardown_16x20.sh [--purge]   # 16x20 构型: 资源撤销
```

`verify.sh` 五步：① BGP 邻居状态 ② 路由表规模 ③ 端到端 ping（自动选最远节点）
④ SRv6 源路由下发与 ping ⑤ eBPF 转发统计。

---

## 7. 配置说明 (`config/constellation.yaml`)

| 参数 | 说明 |
|------|------|
| `constellation.name` | lab 名（容器前缀 `clab-<name>-`） |
| `constellation.planes` / `sats_per_plane` | 轨道面数 P / 每面卫星数 N |
| `constellation.altitude_km` | 轨道高度（决定轨道周期与 ISL 距离） |
| `constellation.inclination_deg` | 轨道倾角 |
| `constellation.raan_spread_deg` | 360=Walker Delta；180=Walker Star |
| `constellation.phase_factor` | Walker 相位因子 F |
| `isl.rate_mbps` | **星间链路速率**（tc netem rate） |
| `isl.links_per_sat` | **单星 ISL 数**：4=前/后/左/右各 1；6=另加斜向 2 条（默认） |
| `isl.wrap_planes` | 轨道面环是否闭合（Star 构型建议 false） |
| `isl.polar_cutoff_lat_deg` | 极区断链纬度阈值（异轨链路） |
| `isl.los_margin_km` | 视距判断的大气余量（连线最低点须高于地表该高度） |
| `isl.max_range_km` | **最大建链距离**：星间距离超出时断链，进入范围后重建；需大于同轨间距（几何常量）以保留骨干；<=0 不限制 |
| `node.kind` | `linux`=sat-router（eBPF 转发面）；`sonic-vs`=SONiC 虚拟机镜像 |
| `node.routing` | 路由协议栈：`frr`（默认）/ `bird2` / `ocrm`；frr 与 bird2 均为编号 eBGP，内核 FIB 下发一致，eBPF 转发面无差异，bird2 内存占用更低，适合大规模星座；`ocrm` 为本体中心语义路由协议栈（双平面控制面，见第 14 节） |
| `node.xdp` | `true`（默认）= 加载 eBPF/XDP 转发面；`false` = 纯内核转发 + 入口脚本自动建立静态邻居表项。大规模（约 250+ 节点）必须 `false`：一是内核链路本地 ND 在该规模下失效，二是 XDP redirect 绕过 qdisc，会使 netem 链路时延失效 |
| `simulation.step_seconds` | 动态引擎每步推进的仿真秒数 |
| `simulation.speedup` | 时间加速比（墙钟 1s = 仿真 speedup s） |

配置示例：`config/constellation.yaml`（66 星默认）、`config/test-small.yaml`
（4×6=24 星测试构型）、`config/walker-8x12.yaml`（8×12=96 星 Delta 构型，
单星 4 链路）、`config/walker-3x4-bird.yaml`（3×4=12 星，BIRD2 控制面）、
`config/walker-16x16.yaml`（16×16=256 星，实测可稳定运行的最大规模，
`node.xdp: false` 内核转发模式）、`config/walker-16x20.yaml`（16×20=320 星，
会触发内核 7.0 大规模 netns 初始化缺陷，参考用）、
`config/walker-4x6-ocrm.yaml`（4×6=24 星，`node.routing: ocrm` 语义路由）、
`config/ocrm-test.yaml`（OCRM vs BGP 对比实验）。

## 8. Web 可视化面板

`web/server.py`（纯标准库，前端无 CDN 依赖，适配离线环境）：

- **星座地图**：等矩形投影世界地形底图（Natural Earth 陆地轮廓预处理为
  `web/static/world.json`，本地离屏渲染），各轨道面轨道轨迹按面配色虚线叠加
  （固定时刻沿相位采样的瞬时投影闭合曲线，卫星节点严格落在轨迹上）；
  星下点按轨道面着色，外环颜色反映 BGP 健康度（绿=全部 Established）；
  同轨/异轨链路绿/蓝色，down 链路红色虚线，极区断链纬度虚线标注；
  悬停看详情，点击卫星看节点详情
- **链路状态**：优先读动态引擎每步原子发布的 `<out>/link_state.json`
  （与实际下发的 netem 一致）；引擎离线时按轨道几何预测并标注
- **实时动画**：引擎在线时卫星位置/轨道线按
  `t_sim + 墙钟流逝×speedup` 实时推进（避免两次引擎步进间拓扑冻结），
  链路 up/down/时延仍以引擎快照为准（权威）
- **转发路径查询**：“路径”页签选源/目的卫星（或地图上 Shift+点击选源、
  Alt+点击选目的），服务端逐跳在容器内执行 `ip -6 route get` 实查转发面
  （反映内核 FIB 实际选路含 ECMP），面板显示逐跳表与传播时延估算，
  地图黄色粗线高亮路径并标注源 S/目的 D
  - **路径延迟/带宽实测**：路径可达时可一键实测：“测延迟 ping”（源容器内
    10 包 ping，报告 RTT 均值/最小/最大/抖动与丢包）、“测带宽 iperf3”
    （目的拉起一次性服务端，TCP 测 5/10/20s，报告收发 Mbps、链路利用率与
    重传；节点缺 iperf3 时自动从已装节点拷贝二进制，无供体时退化 apt 安装）
- **路由 update 事件流**：后台线程轮询全部节点 `show bgp summary json`，
  对比快照生成事件：会话建立/中断、收到前缀数变化、RIB 增减
- **节点详情**：实时查询 BGP 邻居表与 IPv6 路由表（含多路径下一跳反查为邻居节点名）

```bash
# 测试星座自定义启动 (需 docker 组权限)
python3 web/server.py --config config/test-small.yaml \
    --links out-test/links.json --port 8080
```

## 9. 常用调试命令

```bash
LAB=leo-demo
# BGP 状态 (FRR 模式)
docker exec clab-$LAB-sat0-0 vtysh -c "show bgp summary"
docker exec clab-$LAB-sat0-0 vtysh -c "show bgp ipv6 unicast"
# BGP 状态 (BIRD2 模式, node.routing: bird2)
docker exec clab-$LAB-sat0-0 birdc show protocols
docker exec clab-$LAB-sat0-0 birdc show route all
# 内核路由
docker exec clab-$LAB-sat0-0 ip -6 route
# eBPF 转发面统计 / XDP 挂载情况
docker exec clab-$LAB-sat0-0 /opt/satfwd/sat_fwd_loader stats
docker exec clab-$LAB-sat0-0 bpftool net show
# 当前链路 netem 参数
docker exec clab-$LAB-sat0-0 tc qdisc show dev eth1
# 手工下发 SRv6 源路由 (segment list 经 sat1-1)
docker exec clab-$LAB-sat0-0 ip -6 route replace fcbb:2:2::1/128 \
    encap seg6 mode encap segs fcbb:1:1::100,fcbb:2:2::1 dev eth1
```

## 10. 测试场景示例

1. **收敛测试**：`make dynamics` 运行中观察极区断链/恢复时 BGP 撤销与
   重新收敛耗时（`show bgp ipv6 unicast` + ping 丢包窗口）
2. **速率影响**：调整 `isl.rate_mbps` 后 `make gen && python3
   dynamics/link_dynamics.py --once`，用 iperf3 验证 ISL 限速
3. **SRv6 流量工程**：下发不同 segment list，对比默认 BGP 最短路
   与显式绕行路径的时延（ping RTT 应与轨道几何时延一致）
4. **构型对比**：Walker Delta vs Star（`raan_spread_deg` / `wrap_planes`），
   对比路由表规模与平均跳数
5. **建链距离约束**：调小 `isl.max_range_km` 观察超距断链后 BGP 绕行
   （Web 路径查询对比断链前后的转发路径与时延变化）

## 11. 目录结构

```
sat-network-lab/
├── config/
│   ├── constellation.yaml      # 默认星座 (66 星) 配置
│   ├── test-small.yaml         # 测试星座 (4x6=24 星) 配置
│   ├── walker-12x12.yaml       # 12x12=144 星, 75° 高倾角覆盖加拿大, speedup 2 倍速 (见快速开始「Demo: 12x12」)
│   ├── walker-16x16.yaml       # 16x16=256 星, 实测最大稳定规模 (node.xdp: false)
│   └── walker-16x20.yaml       # 16x20=320 星, 受内核缺陷限制 (见第 13 节)
├── topogen/                    # 拓扑与配置生成器
│   ├── orbit.py                # Walker 星座轨道递推
│   ├── isl.py                  # +Grid 6 链路 ISL 生成
│   ├── addressing.py           # IPv6 / SRv6 Locator / AS 规划
│   ├── frrgen.py               # FRR BGP 配置生成
│   ├── birdgen.py              # BIRD2 BGP 配置生成 (node.routing: bird2)
│   ├── clabgen.py              # containerlab 拓扑生成
│   └── generate.py             # CLI 入口
├── ebpf/                       # eBPF 转发面
│   ├── sat_fwd.bpf.c           # XDP: IPv6 快转 + SRv6 End
│   ├── sat_fwd_loader.c        # 加载器 (load/stats/unload)
│   └── Makefile
├── docker/                     # sat-router 镜像
│   ├── Dockerfile              # FRR/BIRD2 + eBPF 多阶段构建
│   ├── daemons                 # FRR daemon 使能
│   └── entrypoint.sh           # 接口等待/控制面(ROUTING 选择)/eBPF 启动编排
├── dynamics/link_dynamics.py   # 链路动态引擎 (每步发布 link_state.json)
├── web/                        # Web 可视化 (星座/链路/路由 update/路径)
│   ├── server.py               # API + BGP 轮询 + 路径实查 (纯标准库)
│   └── static/
│       ├── index.html          # 单文件 Canvas 前端 (无外部依赖)
│       └── world.json          # 陆地轮廓矢量数据 (离线地形底图)
├── ocrm/                       # OCRM 本体中心路由协议栈 (见第 14 节)
│   ├── node_daemon.py          # 每星守护进程 (链路监控/SPF/CFML 编排)
│   ├── protocol/router.py      # OCRMRouter: LSU/LSDB/SPF + OQR/ORD/CPA/OIN
│   ├── cfml/__init__.py        # 语义路由 -> 内核 FIB (原子 ip route)
│   ├── core/ontology.py        # 空间网络本体 (概念树/属性/公理)
│   ├── core/reasoning.py       # 三步本体推理引擎
│   ├── core/kv_cache.py        # 分布式 KV-Cache (老化/虚拟实例/双类刷新)
│   └── moqr/                   # H-OG-MOQR 多目标 QoS 进化优化 (可选)
├── scripts/
│   ├── install_deps.sh         # 依赖安装 (Docker/containerlab)
│   ├── deploy.sh               # 一键部署
│   ├── demo.sh                 # 96 星 demo 一键运行 (参数已固化)
│   ├── run_12x12.sh            # 12x12 构型 (144 星, 75° 覆盖加拿大, 2 倍速): deploy/start/status/verify/stop (端口 8084)
│   ├── teardown_12x12.sh       # 12x12 构型资源撤销 [--purge 连产物]
│   ├── test_3x4.sh             # 3x4 构型 (FRR) 自动化测试 (10 项断言, 含延迟/带宽实测)
│   ├── teardown_3x4.sh         # 3x4 构型 (FRR) 资源撤销
│   ├── run_3x4_bird.sh         # 3x4 构型 (BIRD2) 运行脚本 (仅拉起环境, 不带断言)
│   ├── test_3x4_bird.sh        # 3x4 构型 (BIRD2) 自动化测试 (同上, 端口 8083)
│   ├── teardown_3x4_bird.sh    # 3x4 构型 (BIRD2) 资源撤销
│   ├── test_4x6.sh             # 4x6 构型自动化测试 (同上, 端口 8082)
│   ├── teardown_4x6.sh         # 4x6 构型资源撤销
│   ├── run_16x16.sh            # 16x16 构型 (256 星) 一键运行 (端口 8087)
│   ├── teardown_16x16.sh       # 16x16 构型资源撤销
│   ├── run_16x20.sh            # 16x20 构型 (320 星) 一键运行 (端口 8086)
│   ├── teardown_16x20.sh       # 16x20 构型资源撤销
│   ├── test_ocrm_vs_bgp.sh     # OCRM vs BGP 对比实验 (4x6 星座)
│   └── verify.sh               # 验证脚本
└── Makefile
```

## 12. 关于 SONiC 模式的说明

`node.kind: sonic-vs` 时生成的拓扑使用 `docker-sonic-vs` 镜像，
frr.conf 挂载到 SONiC 集成 FRR 路径（`/etc/sonic/frr/frr.conf`），
用于验证与真实 SONiC 控制面的 BGP 互操作；由于 SONiC-VS 数据面走
SAI-VS 软件交换，eBPF 转发面与链路动态（容器内 tc）仅在默认的
`sat-router` 模式下完整可用。

---

## 13. 大规模部署注意事项

在 22 核 / 30GB / Ubuntu 26.04（内核 7.0.0-30）主机上对 144（12×12）、
256（16×16）、320（16×20）节点构型的实测结论与修复记录。

### 13.1 规模上限结论

| 规模 | 实测状态 |
|------|----------|
| ≤ 144 节点（12×12） | 全功能验证通过（默认 XDP 转发面即可） |
| **256 节点（16×16）** | **实测可完全收敛并稳定运行的最大规模**（需 `node.xdp: false`）：BGP 1024/1024 会话 100% Established，部署约 70s，增量步开销 0.2s，内存约 12GB |
| ≥ 320 节点（16×20） | 触发内核 7.0 确定性缺陷，约 10% 节点收发路径损坏，不可用（见 13.2.3） |

### 13.2 大规模下暴露的三个问题与修复

**1) 内核链路本地 ND 失效（已修复）**
约 250+ 节点时，容器内核对链路本地（`fe80::`）网关的邻居解析失效：
NS 报文无法发出（抓包确认），导致下一跳为链路本地地址的 BGP 路由
全部无法转发（`Ip6OutNoRoutes` 增长，多跳 ping 100% 丢包）。
修复：`entrypoint.sh` 在内核转发模式下以后台任务从**全局**邻居表项
按 EUI-64 推导链路本地地址，写入静态邻居表项（`PERMANENT`），
无需 ND 即可完成转发。

**2) XDP 转发绕过 netem 时延仿真（已修复）**
`bpf_redirect` 出包路径绕过 qdisc：一旦 XDP 的 `bpf_fib_lookup` 命中
（静态邻居表项建立后必然命中），中间跳的 netem 时延全部失效，
端到端 RTT 只剩首末两跳（18 跳路径仅 14ms）。小规模构型不受影响
（彼时邻居未解析，`bpf_fib_lookup` 失败回退内核慢路径）。
修复：`node.xdp: false` → 拓扑注入 `SAT_XDP=0`，入口脚本跳过 XDP 加载
并启用上述静态邻居兜底，纯内核转发下逐跳时延恢复与轨道几何一致
（实测 1 跳 18.1ms → 16 跳 276.3ms，线性）。

**3) 内核 7.0 大规模 netns 初始化缺陷（无用户态解）**
320 节点时约 29~32 个节点（~10%）的 veth 收发路径损坏
（`ip -s link` RX/TX 计数为零），BGP 会话确定性卡在 1022/1280；
接口 down/up、重置 qdisc、降低 `containerlab deploy --max-workers`
均无效；受损节点分布每次部署不同但数量恒定，疑似创建期资源耗尽
类缺陷。规避：规模控制在 256 节点及以下，或更换修复该缺陷的内核。

### 13.3 大规模调优参数（已固化进 16x16/16x20 配置与脚本）

- `simulation.step_seconds: 60`、`delay_eps_ms: 1.0`：降低 docker exec 风暴，
  增量步仅触达状态变化节点（256 节点典型 54~72 节点/步，约 0.2s）
- Web `--poll-interval` ≥ 90s：轮询为 8 线程并发，256 节点单轮约 1.5s
- 动态引擎 24 线程池 + 每节点命令合并为单次 `docker exec`
- 内存参考：约 35MB/节点（FRR + RIB 随 N 增长）
- 部署/销毁耗时随节点数近似线性（256 节点部署约 70s、销毁约 1~2 min）

---

## 14. OCRM 本体中心路由机制

**OCRM = Ontology-Centric Routing Mechanism**（本体中心路由机制，`ocrm/`，~4000 行），
是独立于 FRR/BIRD2 的自研"属性中心"路由协议栈：不按目的 IP 前缀转发，而是按
"我要找什么样的资源/节点"（算力、能量、带宽、寿命等**语义属性**）来发现与路由，
面向高动态空间网络。用 `node.routing: ocrm` 启用（见 `config/walker-4x6-ocrm.yaml`）。

### 14.1 分层架构

```
语义层   ocrm/core/ontology.py + reasoning.py + kv_cache.py
         空间网络本体 SNO=<概念树,数据属性,公理,实例>; 三步推理(概念匹配
         →公理求值深度≤3→约束满足, 含新鲜度老化); 每节点一个 KV-Cache
协议层   ocrm/protocol/router.py  (OCRMRouter, UDP :9900 逐跳)
         双平面控制面: ①拓扑平面 LSU洪泛→LSDB→本地SPF
                       ②语义平面 OQR/ORD/CPA/OIN (NDN 风格转发)
映射层   ocrm/cfml/__init__.py  (CFML)
         语义路由决策 → 内核 FIB (原子 `ip -6 route replace`);
         约束编译 / 条件ECMP / 更新限速
数据面   内核 IPv6 LPM 转发 CFML 下发的 /48 路由
优化层   ocrm/moqr/  (可选, 多目标 QoS 进化优化)
```

### 14.2 双平面控制面（核心设计）

**平面 ① 链路状态拓扑平面（保底可达性）**：链路监控线程每 0.3s 轮询 ISL
`carrier`，邻接变化或每 5s 周期即把本节点**邻接集合**作为 **LSU** 洪泛
（按源序列号去重、`lsu_stale_s` 容忍重启归零）；各节点维护 **LSDB** 并本地
跑 **SPF**（双向校验建边 + BFS 最短路 + 排序确定性破平，避免 FIB 抖动）得每目的首跳。

**平面 ② 语义属性平面（按属性路由，NDN 风格）**：

| 消息 | 全称 | 角色 | 类比 |
|------|------|------|------|
| **OQR** | Ontology Query Request | 查询"谁满足这些属性约束" | NDN Interest |
| **ORD** | Ontology Response Data | 返回满足者 + 路径向量 | NDN Data |
| **CPA** | CPD Potential Advertisement | 周期播报"经我可达的最优属性摘要" | Hello/LSA |
| **OIN** | Ontology Invalidation Notice | 紧急撤销"该个体已失效" | 紧急 Withdraw |
| **LSU** | Link State Update | 邻接同步 | LSA |

- **OQR 处理**：先查本地缓存；否则用 **CPD（按接口/按概念的属性摘要）** 引导转发，
  按 count 排序、次优接近时受控双播；无解则沿**概念层级向上回退**，最后才做有界
  环形搜索（半径<3，**永无界洪泛**）。
- **ORD 回程**：途经节点免费更新 CPD、学习路由（NDN 面包屑）、选择性接纳
  （R8 只缓存与待处理查询相关的实例），按配额聚合 top-k 后向所有待处理接口扇出。

### 14.3 高动态适配机制

- **新鲜度老化**：实例按 TTL 线性衰减；查询可带 `min_freshness` 门限，过老实例
  不作满足者——高动态资源（算力余量）查询应设 >0
- **轨道面聚合**：同一轨道面的 /48 若多数共享一个首跳，只下发一条 **/32 聚合路由**，
  例外卫星保留更精确 /48（LPM 保证正确性），大幅压缩 FIB（可经 `OCRM_AGGREGATE=0` 关）
- **虚拟实例（R2）**：星历推导的位置/链路寿命永不过期、永不外传，本地可信
- **公理联动失效**：公理触发 `__invalidate__`（链路将死/带宽为 0）→ 立即撤销本地
  FIB + 限跳洪泛 **OIN**，依赖节点即时清缓存，不等 TTL 过期

### 14.4 CFML —— 语义到转发的落地

- **原子安装**：`ip -6 route replace` 带多 `nexthop` 一次性原子写入
  （make-before-break，杜绝黑洞窗口）；`_SAFE_TOKEN` 正则防命令注入
- **条件 ECMP**：备选路径代价比 ≤ 2.0 才并入多路径
- **更新限速**：普通更新批量限速刷，拓扑驱动的更新走 `urgent` 立即下发
- 支持 `dry_run`（无 CAP_NET_ADMIN 时只记录不落地）

### 14.5 MOQR —— 多目标 QoS 路由（可选优化层）

`ocrm/moqr/` 为 **H-OG-MOQR** 进化优化器，处理"时延+带宽+算力+寿命+能耗"多目标权衡：
语义热启动 → 区域级本体推理走廊搜索 → 分段细化 → **区间鲁棒 Pareto 选择**
（`interval_robust_dominates` + TOPSIS）；用 NSGA-II 类进化算法 + 本体交叉/变异/
公理修复算子保证路径连通。产出满足 QoS 约束的路径，与基础可达性路由正交。

### 14.6 与 BGP 基线的本质区别

| 维度 | BGP/FRR 基线 | OCRM |
|------|-------------|------|
| 路由依据 | 目的前缀（可达性） | 属性约束（算力/能量/寿命） |
| 控制消息 | BGP Update（TCP） | OQR/ORD/CPA/OIN/LSU（UDP 逐跳） |
| 转发语义 | 前缀最长匹配 | 语义查询 + LPM 兜底 |
| FIB 压缩 | 无 | 轨道面 /32 聚合 |
| 失效响应 | MRAI/Keepalive | OIN 紧急洪泛 + 公理 |
| 适用场景 | 稳定骨干可达 | 资源发现 + 高动态任务路由 |

对比实验：`scripts/test_ocrm_vs_bgp.sh` + `config/ocrm-test.yaml`（4×6 星座，
收敛/开销/可扩展性至 1584 节点）。

### 14.7 评价与局限

**亮点**：双平面分离干净（拓扑保底 + 语义增值）；工程细节扎实（原子路由替换、
死 nonce 防环、重启序列号恢复、命令注入防护、确定性 SPF 破平）；FIB 聚合 +
紧急/批量双类更新利于扩展。

**局限**：
- UDP JSON 逐跳，控制面开销与时延高于 BGP 的 TCP 长连接
- 语义路由依赖实例新鲜度，`min_freshness` 调参敏感
- `node_daemon` 的算力/能量实例目前是随机数模拟（`_populate_instances`），非真实遥测
- 数据面同样受第 13 节内核大规模限制约束
