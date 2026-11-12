"""+Grid 星间链路(ISL)拓扑生成.

每颗卫星 4 或 6 条 ISL (links_per_sat), 接口约定:
  eth1 前向  -> 同轨道面下一颗   (p, s+1)
  eth2 后向  -> 同轨道面上一颗   (p, s-1)
  eth3 左向1 -> 左邻轨道面同相位 (p-1, s)
  eth4 左向2 -> 左邻轨道面下一颗 (p-1, s+1)   [仅 6 链路]
  eth5 右向1 -> 右邻轨道面同相位 (p+1, s)
  eth6 右向2 -> 右邻轨道面上一颗 (p+1, s-1)   [仅 6 链路]

links_per_sat=4 时为经典 +Grid (前/后/左/右各 1), 不生成斜向链路 eth4/eth6.

链路两端接口配对关系 (保证全网一致, 无重复):
  A.eth1 <-> B.eth2   (前向 <-> 后向)
  A.eth5 <-> B.eth3   (右向1 <-> 左向1)
  A.eth6 <-> B.eth4   (右向2 <-> 左向2)
"""

IF_FORWARD = "eth1"
IF_BACKWARD = "eth2"
IF_LEFT1 = "eth3"
IF_LEFT2 = "eth4"
IF_RIGHT1 = "eth5"
IF_RIGHT2 = "eth6"

ISL_IFACES = [IF_FORWARD, IF_BACKWARD, IF_LEFT1, IF_LEFT2, IF_RIGHT1, IF_RIGHT2]


def node_name(plane, sat):
    """containerlab 节点名."""
    return "sat%d-%d" % (plane, sat)


def build_isl_links(planes, sats_per_plane, wrap_planes=True,
                    links_per_sat=6):
    """生成全部 ISL 链路列表.

    每条链路: {a, a_if, a_ps, b, b_if, b_ps, type}
    type: intra(同轨) / inter(异轨).
    links_per_sat: 4=前/后/左/右各 1; 6=另加斜向左 2/右 2.
    """
    if links_per_sat not in (4, 6):
        raise ValueError("links_per_sat 仅支持 4 或 6, 得到 %r"
                         % links_per_sat)
    diagonal = links_per_sat == 6
    links = []
    for p in range(planes):
        for s in range(sats_per_plane):
            s_next = (s + 1) % sats_per_plane
            s_prev = (s - 1) % sats_per_plane
            # 同轨前向链路: (p,s).eth1 <-> (p,s+1).eth2
            # (少于 3 星时环退化: 1 星自环 / 2 星双重边, 只保留一条)
            if s_next != s and (sats_per_plane > 2 or s < s_next):
                links.append({
                    "a": node_name(p, s), "a_if": IF_FORWARD, "a_ps": [p, s],
                    "b": node_name(p, s_next), "b_if": IF_BACKWARD,
                    "b_ps": [p, s_next], "type": "intra",
                })
            # 异轨链路只由左侧面向右侧面发起, 避免重复
            # (少于 3 面时面环同样退化, 不生成环绕链路)
            p_right = p + 1
            if p_right >= planes:
                if not wrap_planes or planes <= 2:
                    continue
                p_right = 0
            # 右向1: (p,s).eth5 <-> (p+1,s).eth3
            links.append({
                "a": node_name(p, s), "a_if": IF_RIGHT1, "a_ps": [p, s],
                "b": node_name(p_right, s), "b_if": IF_LEFT1,
                "b_ps": [p_right, s], "type": "inter",
            })
            # 右向2: (p,s).eth6 <-> (p+1,s-1).eth4   [仅 6 链路构型]
            if diagonal:
                links.append({
                    "a": node_name(p, s), "a_if": IF_RIGHT2, "a_ps": [p, s],
                    "b": node_name(p_right, s_prev), "b_if": IF_LEFT2,
                    "b_ps": [p_right, s_prev], "type": "inter",
                })
    return links


def node_ifaces(plane, sat, planes, wrap_planes=True, links_per_sat=6):
    """该节点实际使用的 ISL 接口列表 (Star 构型边缘面缺左/右向)."""
    ifaces = [IF_FORWARD, IF_BACKWARD]
    wrap = wrap_planes and planes > 2   # 与 build_isl_links 退化处理一致
    has_left = wrap or plane > 0
    has_right = wrap or plane < planes - 1
    if has_left:
        ifaces += [IF_LEFT1, IF_LEFT2] if links_per_sat == 6 else [IF_LEFT1]
    if has_right:
        ifaces += [IF_RIGHT1, IF_RIGHT2] if links_per_sat == 6 \
            else [IF_RIGHT1]
    return ifaces
