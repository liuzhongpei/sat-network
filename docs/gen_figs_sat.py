# -*- coding: utf-8 -*-
"""sat-network-lab OCRM panels in ReSUN style (figlib)."""
import sys, os
sys.path.insert(0, "/home/gf/Downloads/ReSUN")
from figlib import *  # noqa

OUT = "/home/gf/Project/sat-network-lab/docs/build"
os.makedirs(OUT, exist_ok=True)
W, H = 6.05, 3.3

# ================= panel a: OCRM 分层架构 =================
c = Canvas(W, H)
c.panel_title("a", "星载本体算力路由栈（OCRM）分层架构", "Layered Architecture")

c.box(0.15, 0.52, 5.75, 2.42, fill=BLUE_PALE, outline=NAVY, lw=1.6, radius=0.07)
c.text(0.27, 0.66, "星载节点：属性中心路由（按算力/能量/带宽/寿命等语义属性路由）", size=9.5,
       bold=True, color=NAVY, anchor="lm")

# ① 语义层
c.module(0.28, 0.92, 1.62, 1.6, "① 语义层", fill=NAVY_FILL, tsize=9.5, label_pos="tl", lw=1.5)
for i, t in enumerate(["SNO 本体：概念树·属性·公理", "三步推理机（公理深度 ≤3）", "KV-Cache（5000 条/节点）"]):
    c.chip(0.36, 1.24 + i * 0.36, 1.46, 0.3, t, size=6.8, lw=1.0)

# ② 协议层（双平面）
c.module(2.05, 0.92, 2.05, 1.6, "② 协议层（双平面控制面）", fill=GOLD_FILL, tsize=9, label_pos="tl", lw=1.5)
c.chip(2.13, 1.24, 1.89, 0.3, "拓扑平面：LSU 洪泛 → LSDB → SPF", size=6.8, lw=1.0)
c.chip(2.13, 1.64, 0.89, 0.36, "OQR 查询", size=7, lw=1.0)
c.chip(3.13, 1.64, 0.89, 0.36, "ORD 应答", size=7, lw=1.0)
c.chip(2.13, 2.08, 0.89, 0.36, "CPA 播报", size=7, lw=1.0)
c.chip(3.13, 2.08, 0.89, 0.36, "OIN 撤销", size=7, lw=1.0)
c.note(2.13, 1.56, "语义平面（NDN 风格，UDP 逐跳）", size=6.5, color=GRAY)

# ③④ 映射与转发
c.module(4.25, 0.92, 1.52, 1.6, "③④ 映射与转发", fill=TEAL_FILL, tsize=9, label_pos="tl", lw=1.5)
for i, t in enumerate(["CFML：约束编译", "原子 FIB 下发", "条件 ECMP · 限速", "内核 LPM · SRv6"]):
    c.chip(4.32, 1.24 + i * 0.32, 1.38, 0.26, t, size=6.8, lw=1.0)

# ⑤ 优化层
c.module(0.28, 2.62, 5.49, 0.26, "⑤ 优化层 MOQR：多目标 QoS 进化优化（时延/带宽/算力/寿命/能耗 · 区间鲁棒 Pareto 选择）",
         fill=PURPLE_FILL, tsize=7.5, label_pos="tl", lw=1.2)

# 层间箭头
c.arrow(1.9, 1.7, 2.05, 1.7, lw=1.8, head=0.07)
c.arrow(4.1, 1.7, 4.25, 1.7, lw=1.8, head=0.07)
c.arrow(5.4, 2.52, 5.4, 2.62, lw=1.2, color=GRAY, dashed=True, head=0.05)

# 手工图例（含紫色优化层）
lg = [(NAVY_FILL, "控制/语义层"), (GOLD_FILL, "协议层"), (TEAL_FILL, "映射/转发"), (PURPLE_FILL, "优化层(可选)")]
lx = 0.9
for f, lab in lg:
    c.box(lx, 3.05, 0.22, 0.13, fill=f, outline=NAVY, lw=1.2, radius=0.02)
    c.text(lx + 0.26, 3.115, lab, size=7.5, color=INK, anchor="lm")
    lx += 0.3 + c.text_w(lab, 7.5) + 0.12
c.save(f"{OUT}/fig1_arch.png")

# ================= panel b: 语义路由处理流程 =================
c = Canvas(W, H)
c.panel_title("b", "语义路由关键处理流程", "Query-Driven Routing Flow")

c.box(0.15, 0.52, 5.75, 2.42, fill=BLUE_PALE, outline=NAVY, lw=1.6, radius=0.07)
c.text(0.27, 0.66, "查询驱动的语义路由工作流（NDN 风格，永无界洪泛）", size=9.5, bold=True, color=NAVY, anchor="lm")

# 行1：OQR 查询转发
c.module(0.28, 0.88, 5.49, 1.0, "查询转发（OQR）", fill=NAVY_FILL, tsize=9, label_pos="tl", lw=1.5)
r1 = ["OQR 查询到达", "缓存命中？", "CPD 引导转发", "受控双播", "概念层级回退", "有界环形搜索 r<3"]
for i, t in enumerate(r1):
    x = 0.4 + i * 0.9
    c.chip(x, 1.22, 0.8, 0.5, t, size=7, lw=1.1)
    if i < len(r1) - 1:
        c.arrow(x + 0.8, 1.47, x + 0.9, 1.47, lw=1.5, head=0.06)
c.note(1.85, 1.94, "是", size=6.8, color=TEAL)
c.note(2.16, 1.34, "否", size=6.8, color=GRAY)

# 行2：ORD 回程
c.module(0.28, 2.02, 5.49, 0.62, "数据回程（ORD）：面包屑学习", fill=TEAL_FILL, tsize=9, label_pos="tl", lw=1.5)
r2 = ["途经更新 CPD", "路由学习", "选择性接纳 R8", "top-k 聚合", "扇出待处理接口"]
for i, t in enumerate(r2):
    x = 0.4 + i * 1.06
    c.chip(x, 2.3, 0.95, 0.26, t, size=6.8, lw=1.0)
    if i < len(r2) - 1:
        c.arrow(x + 0.95, 2.43, x + 1.06, 2.43, lw=1.4, head=0.05)
# 命中分支竖箭头
c.arrow(1.75, 1.88, 1.75, 2.02, lw=1.6, head=0.06)

# 行3：异常与周期
c.module(0.28, 2.76, 5.49, 0.18, "",
         fill=CORAL_FILL, tsize=7, label_pos="tl", lw=1.0)
c.text(0.4, 2.85, "异常与周期：公理触发失效 → OIN 限跳洪泛撤销 → 邻居清缓存/FIB 即时撤销 ｜ CPA 周期播报最优属性摘要（CPD）",
       size=7, color=INK, anchor="lm")

c.save(f"{OUT}/fig2_flow.png")

# ================= panel c: 模拟器五层架构 =================
c = Canvas(W, H)
c.panel_title("c", "卫星网络模拟器架构", "Five-Layer Emulation Stack")

c.box(0.15, 0.52, 5.75, 2.5, fill=WHITE, outline=NAVY, lw=1.6, radius=0.07)
c.text(0.27, 0.66, "containerlab 星座仿真：配置 → 生成 → 部署 → 运行 → 观测", size=9.5,
       bold=True, color=NAVY, anchor="lm")

layers = [
    ("① 配置层", NAVY_FILL, [("constellation.yaml：轨道面/高度/倾角 · ISL 速率/建链距离 · 节点类型", 4.35)]),
    ("② 生成层", GOLD_FILL, [("orbit 递推", 0.95), ("isl +Grid 6 链路", 1.15), ("addressing", 0.85), ("frrgen/clabgen", 1.15)]),
    ("③ 部署层", NAVY_FILL, [("sat-router 镜像（FRR + eBPF 多阶段构建）", 2.1), ("containerlab deploy", 1.6)]),
    ("④ 运行层", TEAL_FILL, [("卫星容器 ×N", 0.9), ("FRR/BIRD2 控制面", 1.05), ("eBPF/XDP IPv6+SRv6", 1.2), ("dynamics 引擎", 0.95)]),
    ("⑤ 观测层", PURPLE_FILL, [("web server（BGP 轮询·路径实查）", 2.1), ("Canvas 星座地图 · 事件流", 1.85)]),
]
for li, (name, f, chips) in enumerate(layers):
    y = 0.88 + li * 0.42
    c.module(0.28, y, 5.49, 0.32, "", fill=f, lw=1.3)
    c.text(0.38, y + 0.16, name, size=8, bold=True, color=NAVY, anchor="lm")
    cx = 1.28
    for t, w in chips:
        c.chip(cx, y + 0.055, w, 0.21, t, size=6.5, lw=0.9)
        cx += w + 0.1
    if li < len(layers) - 1:
        c.arrow(3.0, y + 0.32, 3.0, y + 0.42, lw=1.5, head=0.06)

c.note(0.28, 2.95, "链路通断三重判据：极区截断 · 视距遮挡 · 最大建链距离", size=6.5, color=GRAY, anchor="lm")
c.save(f"{OUT}/fig3_sim.png")

# ================= panel d: OCRM vs BGP 对比测试 =================
c = Canvas(W, H)
c.panel_title("d", "OCRM 与 BGP 基线对比测试", "OCRM vs BGP Baseline")

c.box(0.15, 0.52, 5.75, 2.48, fill=BLUE_PALE, outline=NAVY, lw=1.6, radius=0.07)
c.text(0.27, 0.66, "对比实验结果", size=9.5, bold=True, color=NAVY, anchor="lm")
c.note(5.78, 0.66, "Walker 4×6 · 24 节点 · 300 s · 故障率 0.02/s", size=7, anchor="rm")

cards = [
    (0.3, 0.88, "平均收敛时间", "OCRM 533.7 ms", "BGP 46,671 ms", "↓ 98.9%"),
    (3.06, 0.88, "路径稳定性指数 PSI", "OCRM 0.552", "BGP 0.123", "↑ 348.8%"),
    (0.3, 1.9, "路由开销（带宽占比）", "OCRM 0.03%", "BGP 0.12%", "↓ 75.0%"),
    (3.06, 1.9, "KV-Cache 命中率", "OCRM 72.0%", "BGP 0（无缓存）", "本地推理 49.5%"),
]
for x, y, name, v1, v2, tag in cards:
    c.box(x, y, 2.66, 0.92, fill=WHITE, outline=NAVY, lw=1.3, radius=0.06)
    c.text(x + 0.12, y + 0.16, name, size=8.5, bold=True, color=NAVY, anchor="lm")
    c.text(x + 0.12, y + 0.44, v1, size=9, bold=True, color=TEAL, anchor="lm")
    c.text(x + 0.12, y + 0.66, v2, size=8, color=GRAY, anchor="lm")
    tw = c.text_w(tag, 7.5) + 0.14
    c.box(x + 2.66 - tw - 0.1, y + 0.1, tw, 0.22, fill=GOLD, outline=GOLD, lw=0, radius=0.04)
    c.text(x + 2.66 - tw / 2 - 0.1, y + 0.21, tag, size=7.5, bold=True, color=WHITE, anchor="mm")

c.note(0.3, 2.93, "扩展验证：24 → 1584 节点 ｜ MOQR：收敛 224 ms · PSI 0.568 · 路由开销 0.05%",
       size=7.5, color=INK, anchor="lm")
c.save(f"{OUT}/fig4_bench.png")

print("sat figs done")

# ================= panel e: OQR 逐跳处理算法 =================
c = Canvas(W, H)
c.panel_title("e", "OQR 查询处理算法（逐跳）", "Per-Hop Query Algorithm")

c.box(0.15, 0.52, 5.75, 2.42, fill=BLUE_PALE, outline=NAVY, lw=1.6, radius=0.07)
c.text(0.27, 0.66, "查询逐跳处理：防环 → 本地推理 → 转发决策 → 应答", size=9.5, bold=True, color=NAVY, anchor="lm")

cols = [
    ("接收与防环", NAVY_FILL, ["UDP :9900 接收", "死 nonce 查重", "命中 → 丢弃", "登记 nonce"]),
    ("本地解析", NAVY_FILL, ["查 KV-Cache", "三步推理 d≤3", "新鲜度门限", "约束满足判定"]),
    ("转发决策", GOLD_FILL, ["CPD 摘要排序", "最优单播", "次优受控双播", "层级回退/环形搜索"]),
    ("应答构造", TEAL_FILL, ["构造 ORD", "满足者集合", "路径向量", "top-k 扇出"]),
]
for ci, (name, f, chips) in enumerate(cols):
    x = 0.28 + ci * 1.42
    c.module(x, 0.92, 1.32, 1.7, name, fill=f, tsize=8.5, label_pos="tl", lw=1.4)
    for i, t in enumerate(chips):
        c.chip(x + 0.07, 1.26 + i * 0.33, 1.18, 0.27, t, size=6.5, lw=1.0)
    if ci < len(cols) - 1:
        c.arrow(x + 1.32, 1.75, x + 1.42, 1.75, lw=1.6, head=0.06)

# 分支：命中直接应答（col2 顶部 → col4 顶部）
c.dashed_line(2.39, 0.92, 2.39, 0.8, color=TEAL, lw=1.5)
c.dashed_line(2.39, 0.8, 5.23, 0.8, color=TEAL, lw=1.5)
c.arrow(5.23, 0.8, 5.23, 0.92, color=TEAL, lw=1.5, dashed=True, head=0.06)
c.note(3.8, 0.73, "缓存命中 → 本地推理直接应答（不经转发）", size=6.5, color=TEAL)
c.note(3.86, 1.62, "未命中", size=6.5, color=GRAY)

c.note(0.28, 2.86, "搜索边界：层级回退与环形搜索半径 r<3，永无界洪泛；nonce 保留防重复处理",
       size=7, color=INK, anchor="lm")
c.save(f"{OUT}/fig5_oqr.png")

# ================= panel f: 拓扑保底 / CFML 落地 / 失效联动 =================
c = Canvas(W, H)
c.panel_title("f", "路由落地与失效联动算法", "FIB Programming & Invalidation")

c.box(0.15, 0.52, 5.75, 2.42, fill=BLUE_PALE, outline=NAVY, lw=1.6, radius=0.07)
c.text(0.27, 0.66, "拓扑平面保底 → CFML 原子落地 → 公理联动失效", size=9.5, bold=True, color=NAVY, anchor="lm")

# 拓扑平面
c.module(0.28, 0.92, 1.72, 1.86, "拓扑平面（保底）", fill=NAVY_FILL, tsize=8.5, label_pos="tl", lw=1.4)
t1 = ["邻接监测 0.3s 轮询", "变化/5s LSU 洪泛", "LSDB（序列号去重）", "确定性 SPF 破平", "每目的首跳表"]
for i, t in enumerate(t1):
    c.chip(0.36, 1.24 + i * 0.31, 1.56, 0.26, t, size=6.5, lw=1.0)
    if i < len(t1) - 1:
        c.arrow(1.14, 1.5 + i * 0.31, 1.14, 1.55 + i * 0.31, lw=1.2, head=0.05)

# CFML
c.module(2.15, 0.92, 1.85, 1.86, "CFML 路由落地", fill=GOLD_FILL, tsize=8.5, label_pos="tl", lw=1.4)
t2 = ["语义决策→约束编译", "轨道面聚合 /48→/32", "原子替换 make-before-break", "条件 ECMP（比 ≤2.0）", "urgent / 批量限速"]
for i, t in enumerate(t2):
    c.chip(2.23, 1.24 + i * 0.31, 1.69, 0.26, t, size=6.3, lw=1.0)
c.arrow(2.0, 1.85, 2.15, 1.85, lw=1.6, head=0.06)
c.note(2.02, 1.66, "首跳", size=6.5, color=TEAL)

# 失效联动
c.module(4.15, 0.92, 1.62, 1.86, "失效联动", fill=CORAL_FILL, tsize=8.5, label_pos="tl", lw=1.4)
t3 = ["公理触发失效", "本地 FIB 即时撤销", "OIN 限跳洪泛", "邻居即时清缓存"]
for i, t in enumerate(t3):
    c.chip(4.23, 1.24 + i * 0.31, 1.46, 0.26, t, size=6.5, lw=1.0)
c.arrow(4.15, 2.2, 4.0, 2.2, color=CORAL, lw=1.5, dashed=True, head=0.06)
c.note(4.05, 2.38, "撤销", size=6.5, color=CORAL)

# MOQR 条
c.module(0.28, 2.86, 5.49, 0.18, "",
         fill=PURPLE_FILL, tsize=7, label_pos="tl", lw=1.0)
c.text(0.4, 2.95, "可选 MOQR 优化：语义热启动 → 本体走廊搜索 → 分段细化 → 区间鲁棒 Pareto（TOPSIS）+ NSGA-II 进化算子",
       size=6.3, color=INK, anchor="lm")

c.save(f"{OUT}/fig6_fib.png")

print("sat figs e/f done")
