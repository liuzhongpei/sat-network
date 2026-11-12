# -*- coding: utf-8 -*-
"""将 7 张原理图栅格化为 PNG（白底细线，Noto Sans CJK SC），供 Word 嵌入。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch, FancyArrowPatch, Polygon
import matplotlib.font_manager as fm

FONT = "Noto Sans CJK SC"
plt.rcParams["axes.unicode_minus"] = False

OUT = "/home/gf/Project/sat-network-lab/docs/paper/figures/png"
import os
os.makedirs(OUT, exist_ok=True)


def newfig(w, h):
    fig = plt.figure(figsize=(w / 100.0, h / 100.0), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.invert_yaxis()  # 与 SVG 一致：y 向下
    ax.axis("off")
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")
    return fig, ax


def box(ax, x, y, w, h, fc="#fbfbfb", ec="#444", lw=1.0, r=6, ls="-"):
    if r <= 0:
        ax.add_patch(Rectangle((x, y), w, h, fc=fc, ec=ec, lw=lw, ls=ls, zorder=2))
    else:
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                     boxstyle=f"round,pad=0,rounding_size={r}",
                     fc=fc, ec=ec, lw=lw, ls=ls, zorder=2))


def diamond(ax, cx, cy, d, fc="#fbfbfb", ec="#444", lw=1.0):
    h = d * 0.7071
    ax.add_patch(Polygon([(cx - h, cy), (cx, cy - h), (cx + h, cy), (cx, cy + h)],
                 closed=True, fc=fc, ec=ec, lw=lw, zorder=2))


def txt(ax, x, y, s, size=11, color="#111", ha="center", va="center", bold=False, rot=0):
    ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va,
            fontweight="bold" if bold else "normal", family=FONT, rotation=rot, zorder=4)


def arrow(ax, x1, y1, x2, y2, lw=1.0, color="#555", ls="-", both=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
                 arrowstyle="<|-|>" if both else "-|>",
                 mutation_scale=11, lw=lw, color=color, linestyle=ls, zorder=3))


def line(ax, x1, y1, x2, y2, lw=1.0, color="#555", ls="-"):
    ax.plot([x1, x2], [y1, y2], lw=lw, color=color, ls=ls, zorder=3, solid_capstyle="round")


# ============ fig1 总体架构 ============
def fig1():
    fig, ax = newfig(860, 620)
    txt(ax, 40, 42, "图 1  星载路由器数字样机及星座仿真验证系统总体架构", 17, "#111", "left", bold=True)
    bands = [(66, 46), (128, 62), (206, 46), (268, 188), (472, 54)]
    names = ["① 配置层", "② 生成层", "③ 部署层", "④ 运行层", "⑤ 观测层"]
    for (y, h), n in zip(bands, names):
        box(ax, 150, y, 560, h)
        txt(ax, 162, y + 16, n, 13, "#111", "left", bold=True)
    txt(ax, 242, 84, "config/constellation.yaml：轨道参数 · ISL 速率/建链距离 · 节点类型 · 仿真参数", 11, "#222", "left")
    txt(ax, 242, 148, "topogen/：orbit 轨道递推 → isl +Grid 链路 → addressing 编址 → frr/clab 配置生成", 11, "#222", "left")
    txt(ax, 242, 166, "（产物：out/links.json 虚拟拓扑 · out/<lab>.clab.yml · frr.conf）", 10, "#555", "left")
    txt(ax, 242, 226, "docker/ sat-router 镜像（FRR + BIRD2 + eBPF 多阶段构建）＋ containerlab deploy（每星一容器 · veth 组网）", 11, "#222", "left")
    # ④ 运行层：两个子模块，每个子模块内部再嵌套子件
    box(ax, 170, 292, 258, 150, fc="#f5f8fb")
    txt(ax, 180, 306, "每星容器 sat-router（×N）", 11, "#111", "left", bold=True)
    for x, y, w, h in [(182, 316, 234, 24), (182, 344, 234, 24), (182, 372, 234, 24)]:
        box(ax, x, y, w, h, fc="#ffffff", r=3)
    txt(ax, 299, 328, "控制面 FRR / BIRD2 / OCRM", 9.5, "#333")
    txt(ax, 299, 356, "统一南向接口 → 内核 IPv6 FIB", 9.5, "#333")
    txt(ax, 299, 384, "eBPF/XDP 转发面（SRv6 End）", 9.5, "#333")
    arrow(ax, 299, 341, 299, 343); arrow(ax, 299, 369, 299, 371)
    txt(ax, 182, 412, "ISL 接口 eth1..eth6（veth，挂 XDP）", 9, "#555", "left")
    box(ax, 452, 292, 248, 150, fc="#f5f8fb")
    txt(ax, 462, 306, "链路动态引擎 dynamics/", 11, "#111", "left", bold=True)
    for i, s in enumerate(["轨道递推 → 时延/通断", "三重判据（极区/视距/距离）", "下发 tc netem delay·rate + ip link", "link_state.json（原子发布）"]):
        txt(ax, 462, 326 + i * 15, s, 9.5, "#333", "left")
    txt(ax, 462, 408, "→ 与每星容器构成虚实同步闭环", 9, "#888", "left")
    arrow(ax, 428, 360, 450, 360, both=True)
    txt(ax, 439, 350, "虚实", 8.5, "#666")
    txt(ax, 242, 492, "web/：状态 API · BGP 事件流 · 路径实查（ip route get）· Canvas 星座/链路可视化", 11, "#222", "left")
    for y1, y2 in [(112, 130), (190, 208), (252, 270)]:
        arrow(ax, 430, y1, 430, y2)
    arrow(ax, 430, 456, 430, 472)
    arrow(ax, 730, 340, 730, 492, both=True)
    txt(ax, 742, 416, "虚实路由同步（数字孪生闭环）", 10, "#333", "center", rot=90)
    txt(ax, 150, 560, "运行层每星一台容器承载真实路由协议栈与 eBPF 转发面，容器内部按“控制面→内核 FIB→转发面”分层；链路动态引擎将时变虚拟拓扑同步为真实网络状态，观测层读取并可视化。", 10, "#555", "left")
    fig.savefig(f"{OUT}/fig1-arch.png", bbox_inches="tight", pad_inches=0.05)


# ============ fig2 组件化路由协议栈 ============
def fig2():
    fig, ax = newfig(820, 520)
    txt(ax, 40, 42, "图 2  组件化路由协议栈（可插拔控制面 → 统一内核 FIB → 同一数据面）", 17, "#111", "left", bold=True)
    cols = [58, 304, 550]
    heads = [("FRR", [("bgpd / ospfd", "BGP 决策 · OSPF SPF"), ("zebra", "RIB 选路 → 内核 FIB")]),
             ("BIRD2", [("bird（BGP）", "ECMP merge paths"), ("kernel 协议", "direct / static / kernel")]),
             ("OCRM（自研）", [("本体推理引擎", "概念匹配 · 公理 · 约束"), ("CFML 映射层", "语义→FIB · KV-Cache · /32 聚合")])]
    for cx, (name, subs) in zip(cols, heads):
        box(ax, cx, 88, 212, 170)
        txt(ax, cx + 16, 106, name, 13, "#111", "left", bold=True)
        sy = 120
        for (sn, sd) in subs:
            box(ax, cx + 10, sy, 192, 40, fc="#ffffff", r=3)
            txt(ax, cx + 19, sy + 12, sn, 10, "#333", "left", bold=True)
            txt(ax, cx + 19, sy + 27, sd, 8.8, "#666", "left")
            sy += 52
        txt(ax, cx + 16, 246, "编号 eBGP（/127）", 9.5, "#888", "left")
    txt(ax, 58, 78, "每星一个容器，同一份 eBPF 数据面；控制面内部各自分层，最终统一为内核 FIB 写入", 10.5, "#555", "left")
    for cx in cols:
        arrow(ax, cx + 106, 258, cx + 106, 280)
    box(ax, 150, 284, 520, 54, fc="#eef2f7")
    txt(ax, 410, 304, "统一南向接口：内核 IPv6 FIB", 12.5, "#111", bold=True)
    txt(ax, 410, 322, "zebra 下发 ｜ bird kernel 协议 ｜ CFML 原子 ip -6 route replace", 10.5, "#444")
    arrow(ax, 410, 338, 410, 372)
    box(ax, 150, 376, 520, 54)
    txt(ax, 410, 396, "同一可编程数据平面：eBPF/XDP 转发面", 12.5, "#111", bold=True)
    txt(ax, 410, 414, "bpf_fib_lookup 直查内核 FIB ｜ SRv6 End 源路由（控制面/转发面解耦）", 10.5, "#444")
    txt(ax, 150, 462, "注：另有 sonic-vs 节点模式，验证与真实 SONiC 控制面（SAI-VS 软件交换）的 BGP 互操作。", 10.5, "#555", "left")
    fig.savefig(f"{OUT}/fig2-stack.png", bbox_inches="tight", pad_inches=0.05)


# ============ fig3 可编程数据平面 ============
def fig3():
    fig, ax = newfig(820, 560)
    txt(ax, 40, 42, "图 3  可编程数据平面（eBPF/XDP）处理流程", 17, "#111", "left", bold=True)
    box(ax, 120, 66, 164, 36)
    txt(ax, 202, 84, "收包（XDP 挂载 eth1..eth6）", 11.5, "#111")
    for cy in (140, 214, 284):
        diamond(ax, 202, cy, 104)
    txt(ax, 202, 142, "IPv6 ?", 11)
    txt(ax, 202, 210, "链路本地/", 11)
    txt(ax, 202, 226, "组播 ?", 11)
    txt(ax, 202, 286, "本地 Locator ?", 11)
    box(ax, 108, 360, 188, 62)
    txt(ax, 202, 378, "普通 IPv6 快转", 12, bold=True)
    txt(ax, 202, 396, "bpf_fib_lookup 直查内核 FIB", 10.5, "#333")
    txt(ax, 202, 410, "（BGP 路由经 zebra 已入 FIB）", 10.5, "#333")
    box(ax, 108, 462, 188, 50)
    txt(ax, 202, 480, "MAC 改写 · hop_limit--", 11.5, bold=True)
    txt(ax, 202, 496, "bpf_redirect 出接口", 11.5, bold=True)
    box(ax, 462, 120, 170, 34); txt(ax, 547, 137, "XDP_PASS（交内核协议栈）", 10.5, "#333")
    box(ax, 462, 188, 170, 34); txt(ax, 547, 205, "XDP_PASS（BGP/ND/RA）", 10.5, "#333")
    box(ax, 462, 250, 210, 50)
    txt(ax, 567, 266, "SRv6 End 处理", 11, bold=True)
    txt(ax, 567, 284, "SL--，DA ← SegList[SL]", 10, "#333")
    box(ax, 462, 330, 210, 34); txt(ax, 567, 347, "XDP_PASS（最终段/本地交付）", 10.5, "#333")
    box(ax, 462, 462, 170, 50)
    txt(ax, 547, 480, "XDP_PASS", 11, "#333")
    txt(ax, 547, 496, "（内核慢路径兜底）", 10.5, "#333")
    # 主脊柱箭头
    for (y1, y2) in [(102, 126), (174, 198), (246, 270), (306, 358), (422, 460)]:
        arrow(ax, 202, y1, 202, y2)
    # 分支箭头
    arrow(ax, 273, 140, 458, 137)
    arrow(ax, 273, 214, 458, 205)
    arrow(ax, 273, 284, 458, 275)
    arrow(ax, 567, 300, 567, 328)
    arrow(ax, 296, 400, 458, 487)
    # S1 继续 FIB 回线
    line(ax, 567, 300, 567, 406)
    line(ax, 567, 406, 300, 406)
    arrow(ax, 300, 406, 300, 389)
    txt(ax, 366, 132, "否", 10, "#888")
    txt(ax, 366, 202, "是", 10, "#888")
    txt(ax, 366, 270, "是", 10, "#888")
    txt(ax, 596, 300, "非 SRH / 最终段", 10, "#888")
    txt(ax, 386, 392, "未命中", 10, "#888")
    txt(ax, 452, 400, "SL>0 继续 FIB 转发", 10, "#888")
    txt(ax, 202, 318, "否（非本地 Locator）", 10, "#888")
    txt(ax, 108, 542, "本地 Locator LPM map（/48，加载器写入）用于判定 End SID；统计 map（per-CPU）pin 到 bpffs，供观测层实时读取（数据平面可视化遥测）。", 10.5, "#555", "left")
    fig.savefig(f"{OUT}/fig3-dataplane.png", bbox_inches="tight", pad_inches=0.05)


# ============ fig4 控制面数据分发服务与虚拟拓扑 ============
def fig4():
    fig, ax = newfig(820, 420)
    txt(ax, 40, 42, "图 4  控制面数据分发服务与虚拟拓扑（借鉴 SDN 思想）", 17, "#111", "left", bold=True)
    box(ax, 64, 62, 740, 84)
    box(ax, 64, 162, 740, 118)
    box(ax, 64, 296, 740, 82)
    txt(ax, 80, 84, "① 虚拟拓扑生成（时变拓扑建模）", 12.5, "#111", "left", bold=True)
    txt(ax, 80, 184, "② 数据分发服务（南向闭环 · 借鉴 SDN 南向接口）", 12.5, "#111", "left", bold=True)
    txt(ax, 80, 318, "③ 统一拓扑视图（北向 · 观测/验证面）", 12.5, "#111", "left", bold=True)
    # ① 行
    b1 = [(80, 98, 136), (236, 98, 136), (392, 98, 136), (548, 98, 148), (716, 98, 72)]
    for x, y, w in b1:
        box(ax, x, y, w, 34)
    txt(ax, 148, 108, "配置 yaml", 10.5); txt(ax, 148, 120, "（轨道/ISL/仿真参数）", 10.5)
    txt(ax, 304, 112, "orbit 轨道递推", 10.5)
    txt(ax, 460, 112, "isl +Grid 链路", 10.5)
    txt(ax, 622, 106, "addressing 编址", 10.5); txt(ax, 622, 120, "（/127·AS·SRv6 Locator）", 10.5)
    txt(ax, 752, 112, "links.json", 10.5)
    for (x1, x2) in [(216, 234), (372, 390), (528, 546), (696, 714)]:
        arrow(ax, x1, 115, x2, 115)
    # ② 行
    box(ax, 80, 200, 168, 34); txt(ax, 164, 212, "链路动态引擎", 10.5); txt(ax, 164, 224, "（每仿真步推进）", 10.5)
    box(ax, 276, 200, 214, 34); txt(ax, 383, 217, "三重判据：极区断链/视距/建链距离", 10.5)
    box(ax, 518, 200, 220, 34); txt(ax, 628, 212, "下发真实网络状态", 10.5); txt(ax, 628, 224, "tc netem delay·rate + ip link", 10.5)
    box(ax, 80, 248, 320, 26); txt(ax, 240, 261, "→ 每星容器真实转发面（veth/netns）", 10.5)
    box(ax, 432, 248, 306, 26); txt(ax, 585, 261, "→ link_state.json（原子发布 tmp+rename）", 10.5)
    arrow(ax, 248, 217, 274, 217); arrow(ax, 262, 217, 290, 217)  # 简化：一段
    arrow(ax, 490, 217, 516, 217)
    arrow(ax, 628, 234, 628, 246)
    # ③ 行
    box(ax, 80, 336, 170, 34); txt(ax, 165, 353, "统一拓扑状态 link_state.json", 10.5)
    box(ax, 272, 336, 230, 34); txt(ax, 387, 353, "北向 API /api（meta·state·bgp·path）", 10.5)
    box(ax, 524, 336, 260, 34); txt(ax, 654, 346, "星座轨迹/链路通断/BGP 事件流", 10.5); txt(ax, 654, 360, "转发路径实查 + 延迟/带宽实测", 10.5)
    arrow(ax, 250, 353, 270, 353); arrow(ax, 502, 353, 522, 353)
    arrow(ax, 434, 146, 434, 160); arrow(ax, 434, 280, 434, 294)
    txt(ax, 80, 404, "南向把虚拟时延/通断同步为真实 netem/接口状态；北向以统一 link_state.json 提供一致拓扑视图，供观测与转发验证。", 10.5, "#555", "left")
    fig.savefig(f"{OUT}/fig4-distribution.png", bbox_inches="tight", pad_inches=0.05)


# ============ fig5 虚实路由同步 ============
def fig5():
    fig, ax = newfig(820, 480)
    txt(ax, 40, 42, "图 5  虚实路由同步：虚实同步 + 转发控制同步（数字孪生闭环）", 17, "#111", "left", bold=True)
    box(ax, 60, 88, 290, 196); box(ax, 470, 88, 310, 196)
    txt(ax, 205, 106, "虚拟侧：星座动力学模型（数字孪生）", 12.5, "#111", bold=True)
    txt(ax, 625, 106, "实物侧：容器化路由器数字样机", 12.5, "#111", bold=True)
    vs = ["Walker 轨道递推（ECEF 星历）", "三重判据：极区断链·视距·建链距离", "时变虚拟拓扑 links.json"]
    for i, s in enumerate(vs):
        box(ax, 74, 118 + i * 34, 262, 26, fc="#ffffff", r=3)
        txt(ax, 205, 131 + i * 34, s, 9.8, "#333")
        if i < len(vs) - 1:
            arrow(ax, 205, 144 + i * 34, 205, 150 + i * 34)
    txt(ax, 76, 220, "→ 每步推进 · 256 节点实时演算", 9, "#888", "left")
    ps = ["控制面 FRR / BIRD2 / OCRM（真实协议）", "内核 IPv6 FIB", "eBPF/XDP 转发面（SRv6 End）"]
    for i, s in enumerate(ps):
        box(ax, 484, 118 + i * 34, 282, 26, fc="#ffffff", r=3)
        txt(ax, 625, 131 + i * 34, s, 9.8, "#333")
        if i < len(ps) - 1:
            arrow(ax, 625, 144 + i * 34, 625, 150 + i * 34)
    txt(ax, 486, 220, "→ 6 路 ISL · 真实 BGP 撤销 / 自愈", 9, "#888", "left")
    arrow(ax, 350, 162, 468, 162)
    arrow(ax, 468, 212, 350, 212)
    txt(ax, 409, 150, "虚实同步（下发）：时延/通断 → tc netem + ip link up/down", 10, "#444")
    txt(ax, 409, 228, "观测回读（校验）：BGP 状态 / 转发路径采集", 10, "#444")
    txt(ax, 60, 368, "转发控制同步：控制面选路 → 转发面 FIB 一致性（控制/转发解耦）", 12.5, "#111", "left", bold=True)
    b4 = [(76, 388, 150, "bgpd / bird / OCRM 选路"), (250, 388, 170, "zebra / CFML 下发"),
          (444, 388, 150, "内核 IPv6 FIB"), (618, 388, 150, "eBPF bpf_fib_lookup 命中")]
    for x, y, w, s in b4:
        box(ax, x, y, w, 34)
        txt(ax, x + w / 2, y + 17, s, 10.5)
    for x1, x2 in [(226, 248), (420, 442), (594, 616)]:
        arrow(ax, x1, 405, x2, 405)
    txt(ax, 76, 448, "控制面路由经 zebra/CFML 下发内核后，eBPF 数据面用 bpf_fib_lookup 直接命中，无需自维护路由 map——保证数字样机“测的是真实路由”。", 10.5, "#555", "left")
    fig.savefig(f"{OUT}/fig5-sync.png", bbox_inches="tight", pad_inches=0.05)


# ============ fig6 原型系统 ============
def fig6():
    fig, ax = newfig(820, 520)
    txt(ax, 40, 42, "图 6  原型系统实现：转发面（eBPF）/ 控制面（容器）/ 虚实同步 / 模拟器", 17, "#111", "left", bold=True)
    box(ax, 36, 78, 224, 268)
    txt(ax, 48, 98, "单个星载路由器容器（sat-router）", 11.5, "#111", "left", bold=True)
    bb = [(48, 112, 200, 34), (48, 158, 200, 34), (48, 204, 200, 44), (48, 260, 200, 34)]
    for x, y, w, h in bb:
        box(ax, x, y, w, h)
    txt(ax, 148, 129, "控制面：FRR bgpd（BGP/OSPF）", 10.5)
    txt(ax, 148, 175, "内核 IPv6 FIB（zebra 下发）", 10.5)
    txt(ax, 148, 222, "eBPF/XDP 转发面", 10.5)
    txt(ax, 148, 238, "（IPv6 快转 + SRv6 End）", 10.5)
    txt(ax, 148, 277, "ISL 接口 eth1..eth6（veth）", 10.5)
    for y1, y2 in [(146, 156), (192, 202), (248, 258)]:
        arrow(ax, 148, y1, 148, y2)
    txt(ax, 48, 322, "eBPF 统计 map → 观测层（数据平面遥测）", 9.5, "#888", "left")
    txt(ax, 48, 338, "ROUTING: frr | bird2 | ocrm 三选一", 9.5, "#888", "left")
    txt(ax, 388, 72, "星载路由器星座（每星一容器）", 12, "#111", bold=True)
    for (x1, y1, x2, y2) in [(390, 120, 390, 280), (540, 120, 540, 280),
                             (390, 120, 540, 120), (390, 200, 540, 200), (390, 280, 540, 280),
                             (390, 200, 540, 120), (390, 200, 540, 280)]:
        line(ax, x1, y1, x2, y2, lw=0.8)
    for cx, cy in [(390, 120), (390, 200), (390, 280), (540, 120), (540, 200), (540, 280)]:
        ax.add_patch(Rectangle((cx - 9, cy - 9), 18, 18, fc="white", ec="#444", lw=1.2, zorder=5))
    txt(ax, 465, 320, "真实 BGP + eBPF 转发；+Grid 6 路 ISL", 10.5, "#333")
    txt(ax, 465, 334, "（同轨前/后向 + 异轨左/右向）", 10.5, "#333")
    box(ax, 606, 88, 188, 96); box(ax, 606, 212, 188, 96)
    txt(ax, 618, 106, "链路动态引擎 dynamics/", 11.5, "#111", "left", bold=True)
    for i, s in enumerate(["轨道递推 → 时延/通断", "tc netem delay·rate", "+ ip link up/down", "→ link_state.json 发布"]):
        txt(ax, 618, 126 + i * 13, s, 9.8, "#333", "left")
    txt(ax, 618, 230, "Web 观测层 web/", 11.5, "#111", "left", bold=True)
    for i, s in enumerate(["BGP 事件流 / 路径实查", "延迟·带宽实测", "Canvas 星座/链路可视化", "（零依赖 · 离线）"]):
        txt(ax, 618, 250 + i * 13, s, 9.8, "#333", "left")
    arrow(ax, 604, 136, 548, 136, ls="--"); arrow(ax, 548, 260, 604, 260, ls="--")
    txt(ax, 570, 128, "下发", 9, "#666", "right"); txt(ax, 570, 274, "采集", 9, "#666", "right")
    txt(ax, 36, 436, "部署：containerlab deploy（每星一容器，veth 组网）；镜像 Dockerfile 多阶段构建（FRR + BIRD2 + eBPF 编译产物 + iperf3）。", 10.5, "#555", "left")
    txt(ax, 36, 454, "虚实同步机制：仿真步 → 引擎把虚拟时延/通断下发为真实 netem/接口状态 → 真实路由协议收敛 → Web 读回，形成闭环。", 10.5, "#555", "left")
    txt(ax, 36, 486, "部署规模：144（12×12，eBPF 全功能）/ 256（16×16，完全收敛稳定）/ 320（16×20，暴露内核缺陷）；控制面可扩展至 1584 节点。", 10, "#333", "left")
    fig.savefig(f"{OUT}/fig6-prototype.png", bbox_inches="tight", pad_inches=0.05)


# ============ fig7 可扩展性（算力路由） ============
def fig7():
    fig, ax = newfig(820, 360)
    txt(ax, 40, 42, "图 7  可扩展性（以算力路由为例）：FIB 轨道面聚合与规模刻画", 17, "#111", "left", bold=True)
    txt(ax, 40, 70, "(a) FIB 轨道面聚合（O(N)→O(P)）", 12, "#111", "left", bold=True)
    box(ax, 40, 86, 150, 176); box(ax, 254, 86, 150, 176)
    txt(ax, 52, 102, "平面 p（N 颗卫星）", 11, "#111", "left", bold=True)
    txt(ax, 266, 102, "轨道面聚合后", 11, "#111", "left", bold=True)
    for i, s in enumerate(["sat0 /48（算力属性）", "sat1 /48", "sat2 /48", "… satN /48"]):
        box(ax, 52, 116 + i * 29, 126, 22, r=3)
        txt(ax, 115, 127 + i * 29, s, 9.5)
    box(ax, 266, 128, 126, 30, r=3)
    txt(ax, 329, 140, "/32 聚合（含 N 个 /48）", 9.5)
    txt(ax, 329, 152, "＝ 1 条路由", 8.8, "#666")
    box(ax, 266, 186, 126, 22, r=3)
    txt(ax, 329, 197, "例外卫星保留 /48", 9.5)
    arrow(ax, 190, 174, 252, 174)
    txt(ax, 221, 166, "聚合", 9, "#666")
    txt(ax, 40, 282, "每节点 FIB：由装全网 N 条 /48 降为装 P 个轨道面 /32（＋少量例外），P 为轨道面数；LPM 保证正确性。", 10, "#555", "left")
    txt(ax, 40, 298, "控制面路由开销：0.03%（24 节点）→ 0.05%（1584 节点）——随规模亚线性增长。", 10, "#555", "left")
    txt(ax, 440, 70, "(b) 每节点 FIB 表项 vs 网络规模（对数轴）", 12, "#111", "left", bold=True)
    line(ax, 452, 100, 452, 280); line(ax, 452, 280, 780, 280)
    for y, lab in [(235, "10"), (190, "100"), (145, "1000"), (100, "10⁴")]:
        txt(ax, 446, y, lab, 9, "#666", "right")
        line(ax, 452, y, 780, y, lw=0.7, color="#ccc")
    txt(ax, 446, 284, "1", 9, "#666", "right")
    for x, lab in [(468, "24"), (568, "144"), (662, "256"), (768, "1584")]:
        txt(ax, x, 296, lab, 9.5, "#444")
    txt(ax, 616, 312, "网络规模 N（节点数）· 纵轴＝每节点 FIB 表项（条）", 9.5, "#555")
    # 聚合前 O(N)
    pts1 = [(468, 218), (568, 183), (662, 172), (768, 136)]
    ax.plot([p[0] for p in pts1], [p[1] for p in pts1], color="#444", lw=1.3)
    for x, y in pts1:
        ax.plot([x], [y], "o", ms=3.5, color="#444")
    # 聚合后 O(P)
    pts2 = [(468, 253), (568, 231), (662, 226), (768, 210)]
    ax.plot([p[0] for p in pts2], [p[1] for p in pts2], color="#444", lw=1.3, ls="--")
    for x, y in pts2:
        ax.plot([x], [y], "o", ms=3.5, color="#444")
    line(ax, 500, 336, 520, 336, lw=1.3); txt(ax, 526, 336, "聚合前：每星 /48（O(N)）", 10, "#333", "left")
    line(ax, 646, 336, 666, 336, lw=1.3, ls="--"); txt(ax, 672, 336, "聚合后：每面 /32（O(P)）", 10, "#333", "left")
    fig.savefig(f"{OUT}/fig7-scale.png", bbox_inches="tight", pad_inches=0.05)


for fn in (fig1, fig2, fig3, fig4, fig5, fig6, fig7):
    fn()
print("done ->", OUT)