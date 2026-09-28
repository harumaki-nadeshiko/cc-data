#!/bin/bash
# Run only inside ubcc-dev:ubuntu20.04 with --network none.
set -euo pipefail
topo=${1:?topology}; pressure=${2:?pressure}; arm=${3:-ha-vi}; tc=${4:-229}
tag="${CHI_GATE_PREFIX:-invalidatefix}-${topo}-p${pressure}-${arm}-tc${tc}"
root=/mnt/data2/cgc/cc-ep-ha-node-20260909
export LD_LIBRARY_PATH="$root/thirdparty/zeromq/lib:${LD_LIBRARY_PATH:-}"
export EP_HA_PROFILE="$arm" OURCC_CLEAR_PROFILE=ack
if [ "$arm" = ubcc ]; then export OURCC_CLEAR_PROFILE=lossless-oneway; fi
export EP_CPU_MODEL=o3 EP_SEQUENCER_MAX_OUTSTANDING=16
export EP_L3_SIZE=256KiB EP_L3_ASSOC=16 L3_PRESSURE_LEVEL="$pressure"
export METRIC3_L3_SEED=0 EP_DSM_DATA_DELAY_PS=68000
export EP_SYNC_INTERVAL_PS=2500 EP_LINK_LATENCY_PS=2500
export EP_TRACE_PERF=full EP_TRACE_CHAIN_OUTPUT=0
export EP_PERF_PROFILE=spill-noopt UBCC_POLICY=spill
export UBCC_OPTS=--dir-overflow-policy=spill
export EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0'
export METRIC3_L3_EXPERIMENT_MODE=l3-only L3_DIRECTORY_PRESSURE_LINES=0
export E2E_RUN_ID="${CHI_RUN_PREFIX:-if}${topo}p${pressure}${arm:0:1}t${tc}" E2E_IPC_DIR=/tmp/ifgate
export LOG_BASE="$root/ha-evidence/$tag" TIMEOUT_SEC=1200
mkdir -p "$LOG_BASE"
bash tests/e2e/run_multi.sh "--$topo" "$tc" > "$LOG_BASE/runner.log" 2>&1
