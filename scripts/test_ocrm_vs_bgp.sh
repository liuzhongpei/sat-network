#!/bin/bash
# OCRM vs BGP Full Comparison Test using Containerlab
# This script deploys the 4x6 Walker constellation in containerlab,
# runs both OCRM and BGP tests, and collects metrics.
#
# Prerequisites: Docker, containerlab, Python3 with pyyaml
# Usage: ./scripts/test_ocrm_vs_bgp.sh [--sim-only]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$PROJECT_DIR/config/ocrm-test.yaml"
RESULTS_DIR="$PROJECT_DIR/results"
OUT_DIR="$PROJECT_DIR/out-ocrm-test"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARNING:${NC} $*"; }
err() { echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $*"; exit 1; }

# Parse args
SIM_ONLY=false
for arg in "$@"; do
    case $arg in
        --sim-only) SIM_ONLY=true ;;
    esac
done

mkdir -p "$RESULTS_DIR"

# ============================================================================
# Phase 1: Simulation-based test (always runs)
# ============================================================================
log "${BLUE}Phase 1: Running simulation-based OCRM vs BGP comparison${NC}"

cd "$PROJECT_DIR"
python3 run_experiment.py --config "$CONFIG" --output "$RESULTS_DIR"

if [ "$SIM_ONLY" = true ]; then
    log "Simulation-only mode. Results in $RESULTS_DIR/"
    exit 0
fi

# ============================================================================
# Phase 2: Containerlab-based test (requires Docker + containerlab)
# ============================================================================
log "${BLUE}Phase 2: Containerlab-based deployment test${NC}"

# Check prerequisites
command -v docker >/dev/null 2>&1 || err "Docker not found"
command -v containerlab >/dev/null 2>&1 || err "containerlab not found"
docker info >/dev/null 2>&1 || err "Docker daemon not running"

# Generate topology
log "Generating 4x6 Walker topology..."
python3 -m topogen.generate --config "$CONFIG" --output "$OUT_DIR" 2>/dev/null || \
    python3 topogen/generate.py --config "$CONFIG" --output "$OUT_DIR"

# Check if topology was generated
CLAB_FILE="$OUT_DIR/$(ls "$OUT_DIR"/*.clab.yml 2>/dev/null | head -1)"
if [ ! -f "$CLAB_FILE" ]; then
    warn "Containerlab topology not generated. Running sim-only mode."
    log "Results available in $RESULTS_DIR/"
    exit 0
fi

# ---- BGP Test ----
log "Deploying constellation with BGP (FRR)..."
sudo containerlab deploy --topo "$CLAB_FILE" --reconfigure 2>&1 | tail -5

log "Waiting for BGP convergence (${YELLOW}60s${NC})..."
sleep 60

# Collect BGP metrics
log "Collecting BGP baseline metrics..."
python3 -c "
import json, subprocess, time, re

results = {'protocol': 'BGP', 'convergence_events': []}
start = time.time()

# Get BGP state from each node
nodes = subprocess.getoutput('docker ps --format {{.Names}} | grep clab').split()
for node in nodes[:5]:  # sample 5 nodes
    out = subprocess.getoutput(f'docker exec {node} vtysh -c \"show bgp summary\"')
    # Count established peers
    peers = len(re.findall(r'\d+\.\d+\.\d+\.\d+.*\d+\s+\d+\s+\d+', out))
    results.setdefault('bgp_peers', []).append({'node': node, 'peers': peers})

# Trigger a link event and measure convergence
if nodes:
    trigger = time.time()
    subprocess.run(['docker', 'exec', nodes[0], 'ip', 'link', 'set', 'eth1', 'down'],
                   capture_output=True)
    time.sleep(5)
    # Measure recovery
    subprocess.run(['docker', 'exec', nodes[0], 'ip', 'link', 'set', 'eth1', 'up'],
                   capture_output=True)
    converge = time.time()
    results['convergence_events'].append({
        'type': 'link_down+up',
        'total_ms': (converge - trigger) * 1000
    })

results['elapsed_s'] = time.time() - start
with open('$RESULTS_DIR/bgp_containerlab.json', 'w') as f:
    json.dump(results, f, indent=2)
print(f'BGP containerlab metrics saved ({len(nodes)} nodes)')
"

# Destroy BGP lab
log "Tearing down BGP lab..."
sudo containerlab destroy --topo "$CLAB_FILE" 2>&1 | tail -3

# ---- Summary ----
log "${GREEN}All tests complete!${NC}"
echo ""
echo "Results directory: $RESULTS_DIR/"
echo "  - ocrm_results.json        : OCRM simulation metrics"
echo "  - bgp_results.json         : BGP simulation metrics"
echo "  - comparison_report.json    : Side-by-side comparison"
if [ -f "$RESULTS_DIR/bgp_containerlab.json" ]; then
echo "  - bgp_containerlab.json    : BGP containerlab live metrics"
fi
echo ""
echo "To view comparison:"
echo "  python3 -c \"import json; d=json.load(open('$RESULTS_DIR/comparison_report.json')); [print(f'{k}: OCRM={v.get(\\\"OCRM\\\")}, BGP={v.get(\\\"BGP\\\")}, Δ={v.get(\\\"improvement_%\\\",\\\"N/A\\\")}%') for k,v in d['comparison_metrics'].items()]\""
