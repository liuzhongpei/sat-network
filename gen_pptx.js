const pptxgen = require("pptxgenjs");

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE"; // 16:9
pptx.author = "sat-network-lab";
pptx.title = "卫星网络路由协议测试系统";

// ── Color palette ──
const C = {
  navy: "1F4E79",
  blue: "2E75B6",
  accent: "4472C4",
  light: "D6E4F0",
  white: "FFFFFF",
  gray: "595959",
  lightGray: "F2F2F2",
  green: "548235",
  orange: "BF8F00",
  red: "C00000",
};

// ── Helpers ──
function addSlideNumber(slide, num, total) {
  slide.addText(`${num} / ${total}`, {
    x: 11.8, y: 7.0, w: 1.2, h: 0.3,
    fontSize: 10, color: "A0A0A0", align: "right",
  });
}

function addFooter(slide) {
  slide.addText("卫星网络路由协议测试系统  |  sat-network-lab", {
    x: 0.4, y: 7.0, w: 6, h: 0.3,
    fontSize: 9, color: "A0A0A0",
  });
}

function contentSlide(title, slideNum, totalSlides) {
  const slide = pptx.addSlide();
  // Top bar
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: 0, w: 13.33, h: 0.08, fill: { color: C.navy },
  });
  // Title
  slide.addText(title, {
    x: 0.5, y: 0.25, w: 12.3, h: 0.65,
    fontSize: 26, bold: true, color: C.navy,
    fontFace: "Arial",
  });
  // Subtle line under title
  slide.addShape(pptx.ShapeType.rect, {
    x: 0.5, y: 0.95, w: 12.3, h: 0.02, fill: { color: C.light },
  });
  addFooter(slide);
  addSlideNumber(slide, slideNum, totalSlides);
  return slide;
}

const TOTAL = 12;

// ═══════════════════════════════════════════════════════
// Slide 1: Title
// ═══════════════════════════════════════════════════════
{
  const slide = pptx.addSlide();
  // Full navy background
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: 0, w: 13.33, h: 7.5, fill: { color: C.navy },
  });
  // Accent stripe
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: 3.0, w: 13.33, h: 0.06, fill: { color: C.blue },
  });
  // Title
  slide.addText("卫星网络路由协议测试系统", {
    x: 1.0, y: 1.5, w: 11.33, h: 1.2,
    fontSize: 40, bold: true, color: C.white,
    fontFace: "Arial", align: "center",
  });
  // Subtitle
  slide.addText("sat-network-lab", {
    x: 1.0, y: 2.5, w: 11.33, h: 0.5,
    fontSize: 22, color: "8DB4E2",
    fontFace: "Arial", align: "center",
  });
  // Description
  slide.addText("基于 containerlab 的 LEO 卫星星座路由协议测试平台", {
    x: 1.5, y: 3.5, w: 10.33, h: 0.6,
    fontSize: 20, color: C.white,
    fontFace: "Arial", align: "center",
  });
  // Tags
  const tags = ["FRR / BIRD2 控制面", "eBPF/XDP + SRv6 转发面", "Walker 星座建模", "Web 实时可视化"];
  tags.forEach((tag, i) => {
    slide.addText(tag, {
      x: 1.5 + i * 2.7, y: 4.5, w: 2.5, h: 0.45,
      fontSize: 13, color: C.white,
      fontFace: "Arial", align: "center",
      shape: pptx.ShapeType.roundRect,
      fill: { color: C.blue }, rectRadius: 0.1,
    });
  });
  // Bottom info
  slide.addText("SONiC 架构对齐  ·  IPv6 全网实装  ·  链路动态仿真  ·  自动化验证", {
    x: 1.5, y: 5.5, w: 10.33, h: 0.5,
    fontSize: 14, color: "8DB4E2",
    fontFace: "Arial", align: "center",
  });
}

// ═══════════════════════════════════════════════════════
// Slide 2: Background & Motivation
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("LEO 星座规模扩张，路由协议验证亟需真实环境", 2, TOTAL);
  // Left column - problem
  slide.addText("背景与挑战", {
    x: 0.5, y: 1.2, w: 5.5, h: 0.45,
    fontSize: 20, bold: true, color: C.navy, fontFace: "Arial",
  });
  const challenges = [
    { text: "LEO 星座节点数快速增长（Starlink 6000+、铱星 66）", bullet: true },
    { text: "星间链路动态通断，路由需实时收敛", bullet: true },
    { text: "传统仿真器缺乏真实路由控制面与转发面", bullet: true },
    { text: "SONiC 等白盒交换机架构尚未在卫星场景验证", bullet: true },
  ];
  slide.addText(challenges, {
    x: 0.7, y: 1.75, w: 5.5, h: 2.5,
    fontSize: 17, color: C.gray, fontFace: "Arial",
    lineSpacingMultiple: 1.5, valign: "top",
  });

  // Right column - solution
  slide.addText("我们的方案", {
    x: 6.8, y: 1.2, w: 5.5, h: 0.45,
    fontSize: 20, bold: true, color: C.green, fontFace: "Arial",
  });
  const solutions = [
    { text: "containerlab 容器化：每颗卫星 = 一个真实容器", bullet: true },
    { text: "FRR/BIRD2 控制面 + eBPF 转发面，与生产网络一致", bullet: true },
    { text: "Walker 星座轨道递推，ISL 距离/时延/通断实时计算", bullet: true },
    { text: "Web 面板实时观测 BGP 收敛、路径追踪、性能实测", bullet: true },
  ];
  slide.addText(solutions, {
    x: 7.0, y: 1.75, w: 5.5, h: 2.5,
    fontSize: 17, color: C.gray, fontFace: "Arial",
    lineSpacingMultiple: 1.5, valign: "top",
  });

  // Divider line
  slide.addShape(pptx.ShapeType.rect, {
    x: 6.5, y: 1.3, w: 0.02, h: 3.0, fill: { color: C.light },
  });

  // Key insight box
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 4.8, w: 12.3, h: 1.2,
    fill: { color: "EBF1FA" }, rectRadius: 0.1,
    line: { color: C.accent, width: 1 },
  });
  slide.addText("核心价值：在真实容器化路由器上运行 BGP + eBPF，结合轨道力学驱动的链路动态，实现端到端的路由协议验证与性能评估", {
    x: 0.8, y: 4.9, w: 11.7, h: 1.0,
    fontSize: 17, color: C.navy, fontFace: "Arial",
    valign: "middle", align: "center",
  });
}

// ═══════════════════════════════════════════════════════
// Slide 3: System Architecture (5 Layers)
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("系统分五层：配置 → 生成 → 部署 → 运行 → 观测", 3, TOTAL);

  const layers = [
    { name: "配置层", desc: "constellation.yaml\n轨道参数 / ISL 速率 / 节点类型", color: "1F4E79", y: 1.2 },
    { name: "生成层", desc: "topogen/ Python 模块\n轨道递推 → ISL → 编址 → 配置 → 拓扑", color: "2E75B6", y: 2.4 },
    { name: "部署层", desc: "sat-router 镜像 (FRR+eBPF)\ncontainerlab deploy", color: "4472C4", y: 3.6 },
    { name: "运行层", desc: "每星一个容器: FRR 控制面 + eBPF 转发面\n链路动态引擎: tc netem 实时调参", color: "548235", y: 4.8 },
    { name: "观测层", desc: "Web 面板: 星座地图 / BGP 事件流 / 路径追踪 / 性能实测", color: "BF8F00", y: 6.0 },
  ];

  layers.forEach((l, i) => {
    // Layer box
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 0.5, y: l.y, w: 3.0, h: 0.95,
      fill: { color: l.color }, rectRadius: 0.08,
    });
    slide.addText(l.name, {
      x: 0.5, y: l.y, w: 3.0, h: 0.95,
      fontSize: 18, bold: true, color: C.white,
      fontFace: "Arial", align: "center", valign: "middle",
    });
    // Description
    slide.addText(l.desc, {
      x: 3.8, y: l.y, w: 5.5, h: 0.95,
      fontSize: 14, color: C.gray, fontFace: "Arial", valign: "middle",
    });
    // Arrow (except last)
    if (i < layers.length - 1) {
      slide.addText("▼", {
        x: 1.7, y: l.y + 0.9, w: 0.6, h: 0.35,
        fontSize: 14, color: C.accent, fontFace: "Arial", align: "center",
      });
    }
  });

  // Right side - key outputs
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 9.8, y: 1.2, w: 3.0, h: 5.75,
    fill: { color: C.lightGray }, rectRadius: 0.1,
    line: { color: C.light, width: 1 },
  });
  slide.addText("产物", {
    x: 9.8, y: 1.3, w: 3.0, h: 0.4,
    fontSize: 16, bold: true, color: C.navy, fontFace: "Arial", align: "center",
  });
  const outputs = [
    ".clab.yml 拓扑文件",
    "frr.conf / bird.conf",
    "links.json 链路清单",
    "sat-router Docker 镜像",
    "link_state.json 实时状态",
    "Web API + Canvas 前端",
  ];
  outputs.forEach((o, i) => {
    slide.addText("• " + o, {
      x: 10.0, y: 1.8 + i * 0.75, w: 2.6, h: 0.6,
      fontSize: 12, color: C.gray, fontFace: "Arial", valign: "top",
    });
  });
}

// ═══════════════════════════════════════════════════════
// Slide 4: Constellation Modeling & ISL
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("Walker 星座圆轨道递推驱动 +Grid 星间链路拓扑", 4, TOTAL);

  // Left: constellation model
  slide.addText("Walker 星座模型", {
    x: 0.5, y: 1.2, w: 5.5, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const constellationItems = [
    "参数: 轨道面数 P, 每面卫星数 N, 高度, 倾角, RAAN 分布, 相位因子 F",
    "圆轨道递推: 任意时刻卫星 ECEF 位置、星下点（含地球自转修正）",
    "星间距离 → 光速传播时延实时计算",
    "视距判断: 连线距地心最近点 vs 地球半径 + 余量",
  ];
  slide.addText(constellationItems.map(t => ({ text: t, bullet: true })), {
    x: 0.7, y: 1.7, w: 5.5, h: 2.5,
    fontSize: 15, color: C.gray, fontFace: "Arial",
    lineSpacingMultiple: 1.4, valign: "top",
  });

  // Right: ISL topology
  slide.addText("+Grid 星间链路 (ISL)", {
    x: 6.8, y: 1.2, w: 5.5, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });

  // ISL table
  const islRows = [
    [{ text: "接口", options: { bold: true, color: C.white, fill: { color: C.navy } } },
     { text: "方向", options: { bold: true, color: C.white, fill: { color: C.navy } } },
     { text: "对端卫星", options: { bold: true, color: C.white, fill: { color: C.navy } } },
     { text: "构型", options: { bold: true, color: C.white, fill: { color: C.navy } } }],
    ["eth1", "前向", "(p, s+1)", "4/6"],
    ["eth2", "后向", "(p, s-1)", "4/6"],
    ["eth3", "左向1", "(p-1, s)", "4/6"],
    ["eth4", "左向2", "(p-1, s+1)", "仅 6"],
    ["eth5", "右向1", "(p+1, s)", "4/6"],
    ["eth6", "右向2", "(p+1, s-1)", "仅 6"],
  ];
  slide.addTable(islRows, {
    x: 6.8, y: 1.7, w: 5.8, h: 2.8,
    fontSize: 12, color: C.gray, fontFace: "Arial",
    border: { type: "solid", pt: 0.5, color: C.light },
    colW: [1.0, 1.0, 1.8, 1.0],
    align: "center", valign: "middle",
  });

  // Bottom: addressing
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 5.0, w: 12.3, h: 1.5,
    fill: { color: C.lightGray }, rectRadius: 0.1,
  });
  slide.addText("地址与 AS 规划 (plane=p, sat=s)", {
    x: 0.7, y: 5.1, w: 5, h: 0.4,
    fontSize: 16, bold: true, color: C.navy, fontFace: "Arial",
  });
  const addrItems = [
    "AS 号: 4200000000 + p×1000 + s",
    "Loopback: fcbb:p:s::1/128",
    "SRv6 Locator: fcbb:p:s::/48,  End SID: fcbb:p:s::100",
    "ISL 互联: 每链路 /127 编号 eBGP (fd00:100::/64 顺序分配)",
  ];
  addrItems.forEach((a, i) => {
    slide.addText(a, {
      x: 0.9 + (i % 2) * 6.0, y: 5.55 + Math.floor(i / 2) * 0.45, w: 5.8, h: 0.4,
      fontSize: 13, color: C.gray, fontFace: "Arial",
    });
  });
}

// ═══════════════════════════════════════════════════════
// Slide 5: Router Architecture (SONiC-aligned)
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("路由器架构对齐 SONiC：容器化控制面 + eBPF 转发面", 5, TOTAL);

  // Control plane section
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 1.2, w: 5.8, h: 3.5,
    fill: { color: "EBF1FA" }, rectRadius: 0.1,
    line: { color: C.accent, width: 1 },
  });
  slide.addText("控制面 (容器内)", {
    x: 0.7, y: 1.3, w: 5.4, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const ctrlItems = [
    { text: "FRR 模式 (默认)", options: { bold: true, fontSize: 15, color: C.navy } },
    { text: "与 SONiC 的 BGP 容器完全相同\n编号 eBGP (/127), zebra 下发内核 FIB", options: { fontSize: 13, color: C.gray, bullet: true } },
    { text: "BIRD2 模式 (可选)", options: { bold: true, fontSize: 15, color: C.navy } },
    { text: "等价编号 eBGP + ECMP merge paths\n内存占用更低，适合大规模星座", options: { fontSize: 13, color: C.gray, bullet: true } },
  ];
  slide.addText(ctrlItems, {
    x: 0.9, y: 1.8, w: 5.2, h: 2.7,
    fontSize: 14, fontFace: "Arial", lineSpacingMultiple: 1.3, valign: "top",
  });

  // Forwarding plane section
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 6.8, y: 1.2, w: 5.8, h: 3.5,
    fill: { color: "E8F5E9" }, rectRadius: 0.1,
    line: { color: C.green, width: 1 },
  });
  slide.addText("转发面 (eBPF/XDP)", {
    x: 7.0, y: 1.3, w: 5.4, h: 0.4,
    fontSize: 18, bold: true, color: C.green, fontFace: "Arial",
  });
  const fwdItems = [
    { text: "XDP 挂载于 eth1..eth6 (ISL 接口)", options: { bullet: true, fontSize: 13, color: C.gray } },
    { text: "IPv6 快转: bpf_fib_lookup 查内核 FIB\n控制面/转发面完全解耦", options: { bullet: true, fontSize: 13, color: C.gray } },
    { text: "SRv6 End 行为: LPM 命中本地 Locator\nSL--, DA ← Segment List[SL]", options: { bullet: true, fontSize: 13, color: C.gray } },
    { text: "统计计数: RX/FWD/SRV6_END/PASS/DROP", options: { bullet: true, fontSize: 13, color: C.gray } },
  ];
  slide.addText(fwdItems, {
    x: 7.2, y: 1.8, w: 5.2, h: 2.7,
    fontSize: 14, fontFace: "Arial", lineSpacingMultiple: 1.3, valign: "top",
  });

  // Bottom: data path flow
  slide.addText("报文转发数据通路", {
    x: 0.5, y: 5.0, w: 5, h: 0.4,
    fontSize: 16, bold: true, color: C.navy, fontFace: "Arial",
  });
  // Flow steps
  const steps = [
    "XDP 收包",
    "非 IPv6 → 内核",
    "本地 SID + SRH?",
    "End: SL--, DA 替换",
    "bpf_fib_lookup",
    "MAC 改写 + redirect",
  ];
  steps.forEach((s, i) => {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 0.5 + i * 2.1, y: 5.5, w: 1.9, h: 0.7,
      fill: { color: i === 4 || i === 5 ? C.navy : C.blue },
      rectRadius: 0.08,
    });
    slide.addText(s, {
      x: 0.5 + i * 2.1, y: 5.5, w: 1.9, h: 0.7,
      fontSize: 11, color: C.white, fontFace: "Arial",
      align: "center", valign: "middle",
    });
    if (i < steps.length - 1) {
      slide.addText("→", {
        x: 0.5 + i * 2.1 + 1.85, y: 5.55, w: 0.3, h: 0.6,
        fontSize: 14, color: C.navy, fontFace: "Arial", align: "center",
      });
    }
  });
}

// ═══════════════════════════════════════════════════════
// Slide 6: eBPF/XDP Forwarding Plane Details
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("eBPF/XDP 转发面：IPv6 快转与 SRv6 End 行为一体化", 6, TOTAL);

  // Left: XDP program logic
  slide.addText("XDP 程序逻辑 (sat_fwd.bpf.c)", {
    x: 0.5, y: 1.2, w: 6, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });

  const xdpSteps = [
    { num: "1", title: "协议过滤", desc: "非 IPv6 / link-local / 组播 → XDP_PASS 交内核\n(BGP、ND、RA 等控制面报文不受影响)" },
    { num: "2", title: "本地 SID 匹配", desc: "LPM Trie 查询目的地址是否命中本星 Locator (/48)\n命中 → 进入 SRv6 处理; 未命中 → 普通快转" },
    { num: "3", title: "SRv6 End 行为", desc: "检查 SRH (nexthdr=43) 且 segments_left > 0\nSL--, DA ← Segment List[SL], 继续 FIB 转发" },
    { num: "4", title: "IPv6 快转", desc: "bpf_fib_lookup 查内核 FIB (BGP 路由经 zebra 下发)\n改写 MAC, hop_limit--, bpf_redirect 到出接口" },
  ];
  xdpSteps.forEach((s, i) => {
    slide.addShape(pptx.ShapeType.ellipse, {
      x: 0.7, y: 1.8 + i * 1.25, w: 0.4, h: 0.4,
      fill: { color: C.navy },
    });
    slide.addText(s.num, {
      x: 0.7, y: 1.8 + i * 1.25, w: 0.4, h: 0.4,
      fontSize: 14, bold: true, color: C.white, fontFace: "Arial", align: "center", valign: "middle",
    });
    slide.addText(s.title, {
      x: 1.3, y: 1.78 + i * 1.25, w: 2.5, h: 0.4,
      fontSize: 15, bold: true, color: C.navy, fontFace: "Arial",
    });
    slide.addText(s.desc, {
      x: 1.3, y: 2.15 + i * 1.25, w: 5.2, h: 0.8,
      fontSize: 12, color: C.gray, fontFace: "Arial", lineSpacingMultiple: 1.2,
    });
  });

  // Right: key design decisions
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 7.0, y: 1.2, w: 5.8, h: 5.3,
    fill: { color: C.lightGray }, rectRadius: 0.1,
  });
  slide.addText("设计要点", {
    x: 7.2, y: 1.3, w: 5.4, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const designPoints = [
    { title: "控制面/转发面解耦", desc: "BGP 选路 → zebra 下发 FIB → bpf_fib_lookup 直接命中\n无需自维护路由 map，BGP 收敛即刻生效" },
    { title: "Generic XDP 兼容 veth", desc: "containerlab veth 不支持 native XDP\n采用 SKB(generic) 模式挂载" },
    { title: "安全边界检查", desc: "SRH 段列表越界防护 (SL > first_segment → DROP)\n报文边界验证防止畸形包攻击" },
    { title: "统计可观测", desc: "PERCPU_ARRAY map 记录 5 类计数\nsat_fwd_loader stats 命令实时查询" },
  ];
  designPoints.forEach((d, i) => {
    slide.addText(d.title, {
      x: 7.4, y: 1.85 + i * 1.15, w: 5.0, h: 0.35,
      fontSize: 14, bold: true, color: C.blue, fontFace: "Arial",
    });
    slide.addText(d.desc, {
      x: 7.4, y: 2.2 + i * 1.15, w: 5.0, h: 0.7,
      fontSize: 12, color: C.gray, fontFace: "Arial", lineSpacingMultiple: 1.2,
    });
  });
}

// ═══════════════════════════════════════════════════════
// Slide 7: Link Dynamics Engine
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("链路动态引擎按轨道递推实时驱动 ISL 通断与时延变化", 7, TOTAL);

  // Triple criteria
  slide.addText("链路通断三重判据", {
    x: 0.5, y: 1.2, w: 5, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });

  const criteria = [
    { num: "①", title: "极区断链", desc: "异轨链路任一端 |纬度| > polar_cutoff_lat_deg\n→ 关闭左/右向链路", color: C.red },
    { num: "②", title: "视距遮挡", desc: "两星连线距地心最近点 < 地球半径 + los_margin_km\n→ 被地球遮挡，链路 down", color: C.orange },
    { num: "③", title: "最大建链距离", desc: "星间距离 > max_range_km\n→ 终端功率/捕获能力限制，链路 down", color: C.blue },
  ];
  criteria.forEach((c, i) => {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 0.5, y: 1.8 + i * 1.3, w: 6.0, h: 1.1,
      fill: { color: C.white },
      line: { color: c.color, width: 1.5 },
      rectRadius: 0.08,
    });
    slide.addText(c.num + " " + c.title, {
      x: 0.7, y: 1.85 + i * 1.3, w: 5.5, h: 0.35,
      fontSize: 15, bold: true, color: c.color, fontFace: "Arial",
    });
    slide.addText(c.desc, {
      x: 0.7, y: 2.2 + i * 1.3, w: 5.5, h: 0.6,
      fontSize: 12, color: C.gray, fontFace: "Arial", lineSpacingMultiple: 1.2,
    });
  });

  // Right: engine workflow
  slide.addText("引擎步进流程", {
    x: 7.0, y: 1.2, w: 5, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const engineSteps = [
    "t_sim += step_seconds × speedup",
    "对每条 ISL: distance_km() → 传播时延",
    "三重判据 → 通/断决策",
    "状态翻转 → ip link set up/down (两端)",
    "up 链路 → tc netem delay <ms> rate <mbps>",
    "增量下发: 仅时延变化 > delay_eps_ms 时更新",
    "原子发布 link_state.json (tmp + rename)",
    "链路 down → BGP 会话断 → 全网重收敛",
  ];
  engineSteps.forEach((s, i) => {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 7.0, y: 1.75 + i * 0.65, w: 5.8, h: 0.5,
      fill: { color: i === 7 ? "FDE8E8" : C.lightGray },
      rectRadius: 0.06,
    });
    slide.addText(`${i + 1}.  ${s}`, {
      x: 7.2, y: 1.75 + i * 0.65, w: 5.4, h: 0.5,
      fontSize: 12, color: i === 7 ? C.red : C.gray, fontFace: "Arial", valign: "middle",
    });
  });

  // Bottom note
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 5.9, w: 12.3, h: 0.7,
    fill: { color: "FFF8E1" }, rectRadius: 0.08,
  });
  slide.addText("性能优化: 每节点合并为单次 docker exec，线程池 (24 线程) 并发下发，大幅降低大星座下的步进开销", {
    x: 0.7, y: 5.9, w: 11.9, h: 0.7,
    fontSize: 13, color: C.orange, fontFace: "Arial", valign: "middle",
  });
}

// ═══════════════════════════════════════════════════════
// Slide 8: Web Visualization Dashboard
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("Web 可视化面板提供星座地图、BGP 事件流与路径实测", 8, TOTAL);

  const features = [
    {
      title: "星座地图",
      items: "等矩形投影世界地形底图\n轨道轨迹按面配色，卫星节点落在轨迹上\n星下点按轨道面着色，外环反映 BGP 健康度\n同轨/异轨链路绿/蓝，down 链路红色虚线",
      x: 0.5, y: 1.2,
    },
    {
      title: "BGP 事件流",
      items: "后台线程轮询 show bgp summary json\n对比快照生成事件:\n  会话建立/中断、前缀数变化、RIB 增减\n前端以 since 游标增量拉取",
      x: 4.6, y: 1.2,
    },
    {
      title: "转发路径查询",
      items: "选源/目的卫星 → 服务端逐跳实查\nip -6 route get (内核 FIB 真实选路)\n含 ECMP 哈希结果，环路检测保护\n地图黄色粗线高亮路径",
      x: 8.7, y: 1.2,
    },
  ];
  features.forEach((f) => {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: f.x, y: f.y, w: 3.8, h: 3.2,
      fill: { color: C.lightGray }, rectRadius: 0.1,
    });
    slide.addText(f.title, {
      x: f.x + 0.2, y: f.y + 0.1, w: 3.4, h: 0.4,
      fontSize: 16, bold: true, color: C.navy, fontFace: "Arial",
    });
    slide.addText(f.items, {
      x: f.x + 0.2, y: f.y + 0.55, w: 3.4, h: 2.5,
      fontSize: 12, color: C.gray, fontFace: "Arial", lineSpacingMultiple: 1.3, valign: "top",
    });
  });

  // Bottom: performance measurement
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.5, y: 4.7, w: 12.3, h: 1.8,
    fill: { color: "EBF1FA" }, rectRadius: 0.1,
    line: { color: C.accent, width: 1 },
  });
  slide.addText("路径性能实测", {
    x: 0.7, y: 4.8, w: 5, h: 0.4,
    fontSize: 16, bold: true, color: C.navy, fontFace: "Arial",
  });
  // Two columns
  slide.addText("测延迟 (ping)", {
    x: 0.9, y: 5.25, w: 5.5, h: 0.35,
    fontSize: 14, bold: true, color: C.blue, fontFace: "Arial",
  });
  slide.addText("源容器内 10 包 ping\n报告 RTT min/avg/max/mdev 与丢包率\n通过 /api/measure?kind=ping 调用", {
    x: 0.9, y: 5.6, w: 5.5, h: 0.8,
    fontSize: 12, color: C.gray, fontFace: "Arial", lineSpacingMultiple: 1.2,
  });
  slide.addText("测带宽 (iperf3)", {
    x: 6.8, y: 5.25, w: 5.5, h: 0.35,
    fontSize: 14, bold: true, color: C.blue, fontFace: "Arial",
  });
  slide.addText("目的拉起一次性服务端，TCP 测 5/10/20s\n报告收发 Mbps、链路利用率与重传\n缺 iperf3 时自动从已装节点拷贝二进制", {
    x: 6.8, y: 5.6, w: 5.5, h: 0.8,
    fontSize: 12, color: C.gray, fontFace: "Arial", lineSpacingMultiple: 1.2,
  });

  // Tech note
  slide.addText("技术特点: 纯 Python 标准库 HTTP 服务，前端零 CDN 依赖，适配离线环境", {
    x: 0.5, y: 6.6, w: 12.3, h: 0.3,
    fontSize: 11, color: C.accent, fontFace: "Arial", align: "center",
  });
}

// ═══════════════════════════════════════════════════════
// Slide 9: Testing & Verification Framework
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("三层验证体系：五步验证 + 10 项自动化断言 + 多构型覆盖", 9, TOTAL);

  // Left: verify.sh
  slide.addText("verify.sh 五步验证", {
    x: 0.5, y: 1.2, w: 5.5, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const verifySteps = [
    "① BGP 邻居状态 (show bgp summary)",
    "② 路由表规模 (show bgp ipv6 unicast statistics)",
    "③ 端到端 ping (自动选最远节点)",
    "④ SRv6 源路由下发与 ping 验证",
    "⑤ eBPF 转发统计 (sat_fwd_loader stats)",
  ];
  verifySteps.forEach((v, i) => {
    slide.addText(v, {
      x: 0.7, y: 1.75 + i * 0.5, w: 5.5, h: 0.45,
      fontSize: 14, color: C.gray, fontFace: "Arial", valign: "middle",
    });
  });

  // Right: automated test assertions
  slide.addText("自动化测试 (10 项断言)", {
    x: 6.8, y: 1.2, w: 5.5, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const assertions = [
    ["T1", "容器数 = 节点总数"],
    ["T2", "引擎状态发布，无超距违规"],
    ["T3", "BGP Established = 2×up 链路"],
    ["T4", "端到端 ping 0% 丢包"],
    ["T5", "SRv6 encap 绕行无丢包"],
    ["T6", "eBPF 转发面统计可读"],
    ["T7", "Web API 在线 + 路径可达"],
    ["T8", "镜像自带 iperf3"],
    ["T9", "延迟实测 RTT > 0, 0% 丢包"],
    ["T10", "带宽实测 > 10 Mbps"],
  ];
  slide.addTable(
    [
      [
        { text: "ID", options: { bold: true, color: C.white, fill: { color: C.navy } } },
        { text: "断言内容", options: { bold: true, color: C.white, fill: { color: C.navy } } },
      ],
      ...assertions.map(a => [
        { text: a[0], options: { align: "center", bold: true, color: C.blue } },
        { text: a[1], options: { color: C.gray } },
      ]),
    ],
    {
      x: 6.8, y: 1.7, w: 5.8, h: 4.5,
      fontSize: 11, fontFace: "Arial",
      border: { type: "solid", pt: 0.5, color: C.light },
      colW: [0.8, 5.0],
      valign: "middle",
    }
  );

  // Bottom: test scenarios
  slide.addText("典型测试场景", {
    x: 0.5, y: 4.6, w: 5.5, h: 0.4,
    fontSize: 16, bold: true, color: C.navy, fontFace: "Arial",
  });
  const scenarios = [
    "收敛测试: 极区断链/恢复 → BGP 撤销与重收敛",
    "速率影响: 调整 ISL rate_mbps → iperf3 验证",
    "SRv6 流量工程: 默认最短路 vs 显式绕行路径时延对比",
    "构型对比: Walker Delta vs Star 路由表规模与平均跳数",
    "建链距离: 调小 max_range_km → BGP 绕行路径变化",
  ];
  scenarios.forEach((s, i) => {
    slide.addText("• " + s, {
      x: 0.7, y: 5.1 + i * 0.35, w: 5.5, h: 0.35,
      fontSize: 11, color: C.gray, fontFace: "Arial",
    });
  });
}

// ═══════════════════════════════════════════════════════
// Slide 10: Supported Constellation Configurations
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("支持多种典型星座构型：从 12 星验证到 96 星演示", 10, TOTAL);

  // Configuration table
  const configRows = [
    [
      { text: "构型", options: { bold: true, color: C.white, fill: { color: C.navy } } },
      { text: "P×N", options: { bold: true, color: C.white, fill: { color: C.navy } } },
      { text: "总节点", options: { bold: true, color: C.white, fill: { color: C.navy } } },
      { text: "高度(km)", options: { bold: true, color: C.white, fill: { color: C.navy } } },
      { text: "倾角(°)", options: { bold: true, color: C.white, fill: { color: C.navy } } },
      { text: "ISL/星", options: { bold: true, color: C.white, fill: { color: C.navy } } },
      { text: "类型", options: { bold: true, color: C.white, fill: { color: C.navy } } },
      { text: "控制面", options: { bold: true, color: C.white, fill: { color: C.navy } } },
    ],
    ["铱星 (默认)", "6×11", "66", "780", "86.4", "6", "Walker Star", "FRR"],
    ["测试构型", "3×4", "12", "3000", "53", "4", "Walker Delta", "FRR"],
    ["4×6 测试", "4×6", "24", "3000", "53", "4", "Walker Delta", "FRR"],
    ["Starlink 演示", "8×12", "96", "550", "53", "4", "Walker Delta", "FRR"],
    ["BIRD2 对比", "3×4", "12", "3000", "53", "4", "Walker Delta", "BIRD2"],
  ];
  slide.addTable(configRows, {
    x: 0.5, y: 1.3, w: 12.3, h: 3.0,
    fontSize: 13, fontFace: "Arial",
    border: { type: "solid", pt: 0.5, color: C.light },
    colW: [1.8, 0.9, 0.9, 1.2, 1.0, 0.9, 1.8, 1.0],
    align: "center", valign: "middle",
  });

  // Key parameters
  slide.addText("关键配置参数 (constellation.yaml)", {
    x: 0.5, y: 4.6, w: 6, h: 0.4,
    fontSize: 16, bold: true, color: C.navy, fontFace: "Arial",
  });

  const params = [
    ["raan_spread_deg", "360 = Walker Delta, 180 = Walker Star"],
    ["links_per_sat", "4 = 前/后/左/右; 6 = 另加斜向 2 条"],
    ["max_range_km", "最大建链距离, <=0 不限制"],
    ["polar_cutoff_lat_deg", "极区断链纬度阈值 (异轨链路)"],
    ["step_seconds / speedup", "仿真步长与时间加速比"],
  ];
  slide.addTable(
    [
      [
        { text: "参数", options: { bold: true, color: C.white, fill: { color: C.blue } } },
        { text: "说明", options: { bold: true, color: C.white, fill: { color: C.blue } } },
      ],
      ...params.map(p => [
        { text: p[0], options: { bold: true, color: C.navy, fontSize: 12 } },
        { text: p[1], options: { color: C.gray, fontSize: 12 } },
      ]),
    ],
    {
      x: 0.5, y: 5.1, w: 12.3, h: 1.6,
      fontFace: "Arial",
      border: { type: "solid", pt: 0.5, color: C.light },
      colW: [3.5, 8.8],
      valign: "middle",
    }
  );
}

// ═══════════════════════════════════════════════════════
// Slide 11: Demo & Quick Start
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("一键部署到可视化验证：make 驱动的全流程体验", 11, TOTAL);

  // Quick start flow
  slide.addText("快速开始", {
    x: 0.5, y: 1.2, w: 5, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const quickSteps = [
    { cmd: "make deploy", desc: "生成 → 建镜像 → clab deploy → 初始化链路" },
    { cmd: "make dynamics", desc: "链路动态引擎 (前台, Ctrl-C 停止)" },
    { cmd: "make web", desc: "Web 面板 http://127.0.0.1:8080" },
    { cmd: "make verify", desc: "五步验证: BGP/ping/SRv6/eBPF" },
    { cmd: "make destroy", desc: "销毁 lab" },
  ];
  quickSteps.forEach((q, i) => {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 0.5, y: 1.75 + i * 0.7, w: 2.8, h: 0.55,
      fill: { color: C.navy }, rectRadius: 0.06,
    });
    slide.addText(q.cmd, {
      x: 0.5, y: 1.75 + i * 0.7, w: 2.8, h: 0.55,
      fontSize: 14, bold: true, color: C.white, fontFace: "Arial",
      align: "center", valign: "middle",
    });
    slide.addText(q.desc, {
      x: 3.5, y: 1.75 + i * 0.7, w: 4.0, h: 0.55,
      fontSize: 12, color: C.gray, fontFace: "Arial", valign: "middle",
    });
    if (i < quickSteps.length - 1) {
      slide.addText("↓", {
        x: 1.6, y: 2.25 + i * 0.7, w: 0.4, h: 0.2,
        fontSize: 12, color: C.accent, fontFace: "Arial", align: "center",
      });
    }
  });

  // Right: Demo script
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 7.8, y: 1.2, w: 5.0, h: 4.0,
    fill: { color: "EBF1FA" }, rectRadius: 0.1,
    line: { color: C.accent, width: 1 },
  });
  slide.addText("96 星 Demo (scripts/demo.sh)", {
    x: 8.0, y: 1.3, w: 4.6, h: 0.4,
    fontSize: 16, bold: true, color: C.navy, fontFace: "Arial",
  });
  const demoCmds = [
    "bash scripts/demo.sh deploy",
    "bash scripts/demo.sh start",
    "bash scripts/demo.sh status",
    "bash scripts/demo.sh stop",
    "bash scripts/demo.sh destroy",
  ];
  const demoDesc = [
    "部署 lab (生成拓扑+建镜像+clab deploy)",
    "后台启动链路动态引擎 + Web 面板",
    "查看引擎步进/链路通断/Web 状态",
    "停止引擎与 Web (容器保留)",
    "销毁 lab",
  ];
  demoCmds.forEach((c, i) => {
    slide.addText(c, {
      x: 8.2, y: 1.85 + i * 0.55, w: 4.4, h: 0.3,
      fontSize: 11, bold: true, color: C.navy, fontFace: "Courier New",
    });
    slide.addText(demoDesc[i], {
      x: 8.2, y: 2.12 + i * 0.55, w: 4.4, h: 0.25,
      fontSize: 10, color: C.gray, fontFace: "Arial",
    });
  });

  // Bottom: environment requirements
  slide.addText("环境要求", {
    x: 0.5, y: 5.6, w: 5, h: 0.4,
    fontSize: 16, bold: true, color: C.navy, fontFace: "Arial",
  });
  const envItems = [
    "Ubuntu 22.04+ (eBPF/XDP 默认支持)",
    "Docker + containerlab (>= 0.40)",
    "Python 3 + PyYAML",
    "24 节点常规笔记本即可; 96 节点建议 8 核 / 10GB+",
  ];
  envItems.forEach((e, i) => {
    slide.addText("• " + e, {
      x: 0.7 + (i % 2) * 6.2, y: 6.05 + Math.floor(i / 2) * 0.4, w: 6.0, h: 0.35,
      fontSize: 12, color: C.gray, fontFace: "Arial",
    });
  });
}

// ═══════════════════════════════════════════════════════
// Slide 12: Summary & Future Work
// ═══════════════════════════════════════════════════════
{
  const slide = contentSlide("已实现端到端验证闭环，未来将扩展更多协议与规模", 12, TOTAL);

  // Summary
  slide.addText("系统成果", {
    x: 0.5, y: 1.2, w: 5, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const achievements = [
    { title: "真实路由环境", desc: "每颗卫星 = 真实容器，运行 FRR/BIRD2 + eBPF，与生产网络一致" },
    { title: "轨道力学驱动", desc: "Walker 星座递推 + 三重判据，ISL 通断/时延/速率实时变化" },
    { title: "SONiC 架构对齐", desc: "控制面/转发面解耦，eBPF 直接查内核 FIB，BGP 收敛即刻生效" },
    { title: "全栈可观测", desc: "Web 面板实时展示星座/链路/BGP/路径/性能，零依赖可离线" },
    { title: "自动化验证", desc: "10 项断言覆盖容器/引擎/BGP/SRv6/eBPF/Web/性能，多构型可切换" },
  ];
  achievements.forEach((a, i) => {
    slide.addText("✓  " + a.title, {
      x: 0.7, y: 1.7 + i * 0.7, w: 2.8, h: 0.35,
      fontSize: 14, bold: true, color: C.green, fontFace: "Arial",
    });
    slide.addText(a.desc, {
      x: 0.7, y: 2.0 + i * 0.7, w: 5.5, h: 0.35,
      fontSize: 12, color: C.gray, fontFace: "Arial",
    });
  });

  // Future work
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 6.8, y: 1.2, w: 5.8, h: 5.3,
    fill: { color: C.lightGray }, rectRadius: 0.1,
  });
  slide.addText("未来方向", {
    x: 7.0, y: 1.3, w: 5.4, h: 0.4,
    fontSize: 18, bold: true, color: C.navy, fontFace: "Arial",
  });
  const futureItems = [
    { title: "更大规模星座", desc: "200+ 节点扩展，优化引擎步进性能\n引入分层拓扑聚合与 BGP 路由反射器" },
    { title: "更多路由协议", desc: "OSPFv3、IS-IS for IPv6 对比测试\nPBR (Policy-Based Routing) 策略路由" },
    { title: " mobility 场景", desc: "卫星切换 (handover) 模拟\n星地链路 (feeder link) 建模" },
    { title: "流量工程", desc: "SRv6 TE policy 自动下发\n基于 BGP-LS 的全网流量优化" },
    { title: "与真实硬件集成", desc: "FPGA 卫星载荷半实物仿真\n硬件在环 (HIL) 端到端验证" },
  ];
  futureItems.forEach((f, i) => {
    slide.addText("→  " + f.title, {
      x: 7.2, y: 1.85 + i * 0.95, w: 5.0, h: 0.3,
      fontSize: 14, bold: true, color: C.blue, fontFace: "Arial",
    });
    slide.addText(f.desc, {
      x: 7.2, y: 2.15 + i * 0.95, w: 5.0, h: 0.55,
      fontSize: 11, color: C.gray, fontFace: "Arial", lineSpacingMultiple: 1.2,
    });
  });
}

// ── Generate ──
pptx.writeFile({ fileName: "/home/gf/Project/sat-network-lab/卫星网络路由协议测试系统.pptx" })
  .then(() => console.log("PPTX created successfully!"))
  .catch(err => console.error("Error:", err));
