#!/bin/bash
# Serial naive-vs-spill comparisons for the long real-capacity workloads.
# Spill uses 512KB SRAM / 60KB Bloom / 4KB index. Naive disables Bloom and
# devotes its entire metadata budget to resident directory entries.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RUNNER="$ROOT_DIR/tests/e2e/run_multi.sh"
OUT_DIR="${REAL_CAPACITY_LOG_DIR:-$ROOT_DIR/logs/real_capacity_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$OUT_DIR"

run_case() {
    local tc="$1" topology="$2" label="$3" policy="$4" batch_rs="$5"
    local bloom=61440
    [ "$policy" = naive ] && bloom=0
    LOG_BASE="$OUT_DIR/tc${tc}_${label}" EP_PERF_PROFILE="$label" \
        EP_CPU_MODEL=timing UBCC_POLICY="$policy" \
        UBCC_OPTS="--dir-overflow-policy=$policy --bloom-bytes=$bloom --sram-bytes=524288 --ways=0 --set-bits=0 --batch-rs=$batch_rs" \
        TIMEOUT_SEC="${REAL_CAPACITY_TIMEOUT_SEC:-7200}" \
        bash "$RUNNER" "$topology" "$tc" 2>&1 | tee "$OUT_DIR/tc${tc}_${label}.runner.log"
}

# TC131 is the formal M1/M2 workload. Keep topology, timing CPU and workload
# identical across all three profiles; only directory policy/optimization moves.
run_case 131 --8n1s naive-noopt naive 0
run_case 131 --8n1s spill-noopt spill 0
run_case 131 --8n1s spill-opt spill 1

# Supporting capacity/correctness evidence.
for tc in 132 133; do
    run_case "$tc" --8n1s naive-noopt naive 0
    run_case "$tc" --8n1s spill-noopt spill 0
done
run_case 134 --8n2s naive-noopt naive 0
run_case 134 --8n2s spill-noopt spill 0
