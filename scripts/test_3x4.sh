#!/bin/bash
# 3x4 星座自动化测试: 部署 -> 启动引擎/Web -> 等收敛 -> 断言 -> 汇总
#
# 用法:  bash scripts/test_3x4.sh [--skip-deploy]
#   --skip-deploy  lab 已在运行时跳过部署阶段, 只跑测试
# 退出码: 0=全部通过, 1=有失败项
# 资源撤销: bash scripts/teardown_3x4.sh
set -u
cd "$(dirname "$0")/.."

CONFIG=config/walker-3x4.yaml
OUT=out-3x4
LAB=sat-3x4
NODES=12
WEB_PORT=8081              # 避开 8080, 与其他构型面板共存
DYN_LOG=/tmp/dynamics-3x4.log
WEB_LOG=/tmp/web-3x4.log
CONVERGE_WAIT=90           # BGP 收敛等待上限 (s)

PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

# ---------- 阶段 1: 部署 ----------
if [ "${1:-}" != "--skip-deploy" ]; then
    echo "==> 1/3 部署 ${LAB} (生成拓扑 -> 镜像 -> containerlab)"
    python3 -m topogen.generate --config "${CONFIG}" --out "${OUT}"
    docker build -q -t sat-router:latest -f docker/Dockerfile . >/dev/null
    containerlab deploy -t "${OUT}/${LAB}.clab.yml" --reconfigure >/dev/null
fi

echo "==> 2/3 启动链路动态引擎 + Web 面板"
pkill -f "link_dynamics.py --config ${CONFIG}" 2>/dev/null || true
pkill -f "web/server.py --config ${CONFIG}" 2>/dev/null || true
sleep 1
nohup python3 dynamics/link_dynamics.py --config "${CONFIG}" \
    --links "${OUT}/links.json" > "${DYN_LOG}" 2>&1 &
nohup python3 web/server.py --config "${CONFIG}" --links "${OUT}/links.json" \
    --port "${WEB_PORT}" --poll-interval 10 > "${WEB_LOG}" 2>&1 &

echo "==> 等待 BGP 收敛 (上限 ${CONVERGE_WAIT}s)"
DST_LO="fcbb:1:2::1"       # 对角最远节点 sat1-2
# 先等引擎首步发布 (初始超距断链下发完成), 避免断言撞上重收敛窗口
for i in $(seq 30); do
    [ -f "${OUT}/link_state.json" ] \
        && [ "${OUT}/link_state.json" -nt "${OUT}/links.json" ] && break
    sleep 1
done
# 再等路由稳态: 连续 3 次 ping 通才开始断言
streak=0
for i in $(seq "${CONVERGE_WAIT}"); do
    if docker exec "clab-${LAB}-sat0-0" ping -6 -c 1 -W 1 "${DST_LO}" \
            >/dev/null 2>&1; then
        streak=$((streak+1)); [ "${streak}" -ge 3 ] && break
    else
        streak=0
    fi
    sleep 1
done

echo "==> 3/3 测试断言"

# T1: 容器全部 running
n=$(docker ps --format '{{.Names}}' | grep -c "clab-${LAB}-" || true)
[ "$n" -eq "${NODES}" ] && ok "T1 容器数 ${n}/${NODES} running" \
                        || bad "T1 容器数 ${n}/${NODES}"

# T2: 引擎状态发布且无超距在线链路
T2=$(python3 - <<EOF
import json, time
try:
    d = json.load(open("${OUT}/link_state.json"))
    up = sum(1 for l in d["links"] if l["up"])
    down = len(d["links"]) - up
    vio = sum(1 for l in d["links"]
              if l["up"] and l["distance_km"] > d["max_range_km"] > 0)
    fresh = time.time() - d["wall_time"] < 60
    print("OK up=%d down=%d 超距违规=%d 新鲜=%s" % (up, down, vio, fresh)
          if vio == 0 and fresh and up > 0 else
          "BAD up=%d down=%d vio=%d fresh=%s" % (up, down, vio, fresh))
except Exception as e:
    print("BAD %s" % e)
EOF
)
case "$T2" in OK*) ok "T2 引擎状态: ${T2#OK }";; *) bad "T2 引擎状态: ${T2#BAD }";; esac

# T3: BGP 会话数 == 2 x up链路数 (每条 up 链路两端各一会话)
# 个别会话可能正处重建窗口, 重试至多 4 次 (每次重读链路状态)
T3=$(python3 - <<EOF
import json, subprocess, time
for attempt in range(4):
    d = json.load(open("${OUT}/link_state.json"))
    want = 2 * sum(1 for l in d["links"] if l["up"])
    est = 0
    for i in range(3):      # p in 0..2
        for j in range(4):  # s in 0..3
            r = subprocess.run(["docker","exec","clab-${LAB}-sat%d-%d"%(i,j),
                "vtysh","-c","show bgp summary json"],capture_output=True,timeout=15)
            p = json.loads(r.stdout)["ipv6Unicast"]["peers"]
            est += sum(1 for v in p.values() if v["state"]=="Established")
    if est == want:
        break
    time.sleep(8)
print(("OK" if est==want else "BAD") + " est=%d want=%d" % (est, want))
EOF
)
case "$T3" in OK*) ok "T3 BGP 会话: ${T3#OK }";; *) bad "T3 BGP 会话: ${T3#BAD }";; esac

# T4: 端到端连通 (最远节点, 0% 丢包)
if docker exec "clab-${LAB}-sat0-0" ping -6 -c 4 -i 0.3 "${DST_LO}" \
        | grep -q " 0% packet loss"; then
    ok "T4 端到端 ping sat0-0 -> sat1-2 无丢包"
else
    bad "T4 端到端 ping 有丢包"
fi

# T5: SRv6 源路由绕行 (经 sat2-0 End SID)
# 出接口按当前 FIB 最优路径动态选取: 写死接口在链路动态下可能首跳
# 回程折返 (generic XDP 同口折返丢包), 导致偶发失败
SEG_DEV=$(docker exec "clab-${LAB}-sat0-0" ip -6 route get fcbb:2:0::100 \
    2>/dev/null | grep -oP 'dev \K\S+' | head -1)
docker exec "clab-${LAB}-sat0-0" ip -6 route replace "${DST_LO}/128" \
    encap seg6 mode encap segs "fcbb:2:0::100,${DST_LO}" \
    dev "${SEG_DEV:-eth1}" 2>/dev/null
if docker exec "clab-${LAB}-sat0-0" ping -6 -c 4 -i 0.3 "${DST_LO}" \
        | grep -q " 0% packet loss"; then
    ok "T5 SRv6 encap 绕行 sat2-0 无丢包"
else
    bad "T5 SRv6 encap 绕行有丢包"
fi
# 注意: 内核对 seg6 encap 路由 replace 实为追加, 单次 del 只删一条,
# 必须循环删净, 否则残留 seg6 路由会劫持后续 T9 的 loopback 源 ping
docker exec "clab-${LAB}-sat0-0" sh -c \
    "while ip -6 route del ${DST_LO}/128 2>/dev/null; do :; done"

# T6: eBPF 转发面已加载且有 IPv6 收包计数
if docker exec "clab-${LAB}-sat2-0" /opt/satfwd/sat_fwd_loader stats \
        2>/dev/null | grep -q "rx_ipv6"; then
    ok "T6 eBPF 转发面统计可读 (sat2-0)"
else
    bad "T6 eBPF 转发面无统计"
fi

# T7: Web API (engine 在线 + 路径实查可达)
T7=$(python3 - <<EOF
import json, urllib.request
try:
    s = json.load(urllib.request.urlopen(
        "http://127.0.0.1:${WEB_PORT}/api/state", timeout=10))
    p = json.load(urllib.request.urlopen(
        "http://127.0.0.1:${WEB_PORT}/api/path?src=sat0-0&dst=sat1-2",
        timeout=30))
    okk = s["engine"] and p["reached"]
    print(("OK" if okk else "BAD") + " engine=%s path=%s (%d跳)"
          % (s["engine"], p["reached"], len(p["hops"])))
except Exception as e:
    print("BAD %s" % e)
EOF
)
case "$T7" in OK*) ok "T7 Web API: ${T7#OK }";; *) bad "T7 Web API: ${T7#BAD }";; esac

# T8: iperf3 已固化进镜像 (节点无需运行时安装)
if docker exec "clab-${LAB}-sat0-0" which iperf3 >/dev/null 2>&1; then
    ok "T8 镜像自带 iperf3"
else
    bad "T8 镜像缺 iperf3 (需重建: docker build -t sat-router:latest -f docker/Dockerfile .)"
fi

# T9: 路径延迟实测 API (/api/measure kind=ping)
T9=$(python3 - <<EOF
import json, urllib.request
try:
    d = json.load(urllib.request.urlopen(
        "http://127.0.0.1:${WEB_PORT}/api/measure"
        "?src=sat0-0&dst=sat1-2&kind=ping", timeout=60))
    okk = "error" not in d and d["loss_pct"] == 0 and d.get("rtt_avg", 0) > 0
    print(("OK" if okk else "BAD") + " " + (
        "rtt_avg=%.1fms 丢包=%s%%" % (d.get("rtt_avg", -1),
                                       d.get("loss_pct", "?"))
        if "error" not in d else d["error"]))
except Exception as e:
    print("BAD %s" % e)
EOF
)
case "$T9" in OK*) ok "T9 延迟实测: ${T9#OK }";; *) bad "T9 延迟实测: ${T9#BAD }";; esac

# T10: 路径带宽实测 API (/api/measure kind=bw, TCP 5s)
# 可能撞上链路切换窗口压低吞吐, 重试至多 3 次
T10=$(python3 - <<EOF
import json, time, urllib.request
for attempt in range(3):
    try:
        d = json.load(urllib.request.urlopen(
            "http://127.0.0.1:${WEB_PORT}/api/measure"
            "?src=sat0-0&dst=sat1-2&kind=bw&secs=5", timeout=120))
    except Exception as e:
        d = {"error": str(e)}
    if "error" not in d and d["mbps_recv"] > 10:
        break
    time.sleep(5)
okk = "error" not in d and d["mbps_recv"] > 10
print(("OK" if okk else "BAD") + " " + (
    "收 %.1f Mbps / 限速 %g Mbps, 重传 %s"
    % (d.get("mbps_recv", -1), d.get("rate_mbps", 0),
       d.get("retrans", "?"))
    if "error" not in d else d["error"]))
EOF
)
case "$T10" in OK*) ok "T10 带宽实测: ${T10#OK }";; *) bad "T10 带宽实测: ${T10#BAD }";; esac

echo "=============================================="
echo "结果: PASS=${PASS} FAIL=${FAIL}   (面板 http://127.0.0.1:${WEB_PORT})"
echo "资源撤销: bash scripts/teardown_3x4.sh"
[ "${FAIL}" -eq 0 ]
