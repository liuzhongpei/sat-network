const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";

const NAVY = "1F3864";
const INK = "1E283C";
const MUTE = "6E7682";
const BG = "EEF3FA";
const F = "Microsoft YaHei";

const FIGS = "/home/gf/Project/sat-network-lab/docs/build";

function textW(s, pt) {
  let w = 0;
  for (const ch of s) w += ch.charCodeAt(0) < 0x2E80 ? 0.155 : 0.345;
  return w * pt / 24;
}
function title(s, zh, en) {
  s.addText(zh, { x: 0.5, y: 0.26, w: 9.5, h: 0.52, margin: 0, fontFace: F, fontSize: 24, bold: true, color: NAVY, align: "left" });
  if (en) s.addText(en, { x: 0.75 + textW(zh, 24), y: 0.36, w: 12.55 - textW(zh, 24), h: 0.4, margin: 0,
    fontFace: F, fontSize: 14, color: MUTE, align: "left" });
}
function topText(s, txt) {
  s.addText(txt, { x: 0.5, y: 0.9, w: 12.34, h: 0.62, margin: 0, fontFace: F,
    fontSize: 12, color: INK, align: "left", valign: "top", lineSpacingMultiple: 1.15 });
}
function fig(s, file, x) {
  s.addImage({ path: `${FIGS}/${file}`, x, y: 1.62, w: 6.05, h: 3.3 });
}
function summaryCard(s, txt) {
  s.addShape(pres.ShapeType.rect, { x: 0.5, y: 5.42, w: 12.34, h: 1.72, fill: { color: BG }, line: { color: BG, width: 0 } });
  s.addShape(pres.ShapeType.roundRect, { x: 0.72, y: 5.62, w: 0.78, h: 0.34, rectRadius: 0.06, fill: { color: NAVY }, line: { color: NAVY, width: 0 } });
  s.addText("小结", { x: 0.72, y: 5.62, w: 0.78, h: 0.34, margin: 0, fontFace: F, fontSize: 12, bold: true, color: "FFFFFF", align: "center", valign: "middle" });
  s.addText(txt, { x: 0.72, y: 6.08, w: 11.9, h: 0.98, margin: 0, fontFace: F, fontSize: 11.5, color: INK,
    align: "left", valign: "top", lineSpacingMultiple: 1.18 });
}

// ================= Slide 1 =================
const s1 = pres.addSlide();
s1.background = { color: "FFFFFF" };
title(s1, "星载本体算力路由架构：OCRM 关键算法与流程", "Ontology-Centric Routing Mechanism for LEO Networks");
topText(s1, "OCRM（Ontology-Centric Routing Mechanism）是自研属性中心路由栈：不按目的 IP 前缀，而按算力、能量、带宽、寿命等语义属性进行资源发现与路由。分层设计：语义层维护空间网络本体 SNO 与 KV-Cache，三步推理（概念匹配→公理求值≤3→约束满足）；协议层为双平面控制面——拓扑平面 LSU 洪泛+本地 SPF 保底可达，语义平面 OQR/ORD/CPA/OIN 按 NDN 风格逐跳转发；CFML 将语义决策原子写入内核 FIB（条件 ECMP、更新限速）；可选 MOQR 层做多目标 QoS 进化优化。");
fig(s1, "fig1_arch.png", 0.5);
fig(s1, "fig2_flow.png", 6.78);
summaryCard(s1, "特点：① 语义属性路由——查询驱动的资源发现取代目的前缀匹配，面向在轨算力等任务型流量；② 双平面解耦——拓扑平面保证任意时刻可达性，语义平面按需增值，互不干扰；③ 以本地计算换全局通信——KV-Cache 命中率 72%、本地推理占比 49.5%，路由开销仅 0.03% 带宽占比；④ 高动态适配——实例新鲜度老化、虚拟实例（星历推导永不过期）、轨道面 /32 聚合压缩 FIB、公理联动 OIN 紧急撤销；⑤ 工程稳健——原子路由替换杜绝黑洞窗口、死 nonce 防环、命令注入防护、确定性 SPF 破平。");

// ================= Slide 2 =================
const s2 = pres.addSlide();
s2.background = { color: "FFFFFF" };
title(s2, "卫星网络模拟器架构与对比测试", "Containerlab Emulation Stack & OCRM vs BGP");
topText(s2, "模拟器基于 containerlab 构建真实网络栈：配置层（constellation.yaml）→ 生成层（Walker 轨道递推、+Grid 每星 6 条 ISL、IPv6/AS 规划、FRR/clab 配置生成）→ 部署层（sat-router 镜像：FRR+eBPF 多阶段构建）→ 运行层（每星一容器：FRR/BIRD2 控制面 + eBPF/XDP IPv6+SRv6 转发面；链路动态引擎按极区/视距/建链距离三重判据通断 ISL）→ 观测层（Web：星座地图、BGP 事件流、路径实查）。对比实验：Walker 4×6（24 节点）、300 s、故障率 0.02/s，与 BGP/FRR 基线对比。");
fig(s2, "fig3_sim.png", 0.5);
fig(s2, "fig4_bench.png", 6.78);
summaryCard(s2, "对比结果：OCRM 平均收敛 533.7 ms（BGP 46,671 ms，改善 98.9%）；路径稳定性 PSI 0.552（BGP 0.123，提升 348.8%，路径平均持续 165.5 s vs 36.9 s）；路由开销 0.03%（BGP 0.12%，降低 75%），以更高的节点本地 CPU/内存开销换取数量级的收敛与稳定性收益——契合「星载算力富余、星间带宽紧张」的取舍方向；KV-Cache 命中率 72% 与论文 72.6% 吻合，规模可扩展验证至 1584 节点；MOQR 优化层进一步将收敛降至 224 ms、PSI 升至 0.568。");

// ================= Slide 3 =================
const s3 = pres.addSlide();
s3.background = { color: "FFFFFF" };
title(s3, "OCRM 算法流程细节", "Algorithm Details: Query Processing & FIB Programming");
topText(s3, "OQR 逐跳处理四阶段：① 接收与防环（死 nonce 查重、处理前登记）；② 本地解析——查 KV-Cache，命中则三步推理（概念匹配→公理求值≤3→约束满足 + min_freshness 校验）直接应答；③ 未命中则转发决策——CPD 摘要按 count 排序，最优单播、次优受控双播，无解沿概念层级回退、再做 r<3 有界环形搜索；④ 应答构造——ORD 携满足者与路径向量回程，途经节点更新 CPD 并面包屑学习。拓扑平面 0.3s 监测邻接、变化或 5s 洪泛 LSU，确定性 SPF 得首跳；CFML 轨道面聚合后原子写 FIB；公理失效即撤 FIB 并限跳洪泛 OIN。");
fig(s3, "fig5_oqr.png", 0.5);
fig(s3, "fig6_fib.png", 6.78);
summaryCard(s3, "算法性质：① 有界性——语义搜索仅有层级回退与 r<3 环形搜索两种回退，失效撤销限跳洪泛，任何情况下不产生无界洪泛；② 确定性——SPF 以排序破平消除等价路径导致的 FIB 抖动，LSU 序列号去重并容忍重启归零；③ 原子性——CFML 以 ip -6 route replace 一次性写入多下一跳（make-before-break），切换无黑洞窗口；④ 防环——死 nonce 检查 + 受控双播双重保障；⑤ 本地 O(1) 应答——KV-Cache（5000 条/节点）+ 深度≤3 推理使 49.5% 的查询在本地完成，不产生网络控制流量。");

pres.writeFile({ fileName: "/home/gf/Project/sat-network-lab/docs/OCRM_sat.pptx" })
  .then(() => console.log("written"));
