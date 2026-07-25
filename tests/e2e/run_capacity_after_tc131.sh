#!/bin/bash
# Run the remaining real-capacity policy comparisons after TC131 drains.
# Every case is intentionally serial: the 8-node cases alone oversubscribe a
# 32-core host because each gem5/UBIO/networksim process has busy PDES threads.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RUNNER="$ROOT_DIR/tests/e2e/run_multi.sh"
OUT_DIR="${CAPACITY_AFTER_TC131_LOG_DIR:-$ROOT_DIR/logs/capacity_after_tc131_$(date +%Y%m%d_%H%M%S)}"
WAIT_SEC="${TC131_WAIT_POLL_SEC:-60}"
mkdir -p "$OUT_DIR"

tc131_active() {
    docker ps --format '{{.Names}}' | rg -q '^tc131-' && return 0
    ps -eo args= | rg -q 'tc131-.*rerun|tc131-(naive-noopt|spill-noopt|spill-opt)' && return 0
    return 1
}

while tc131_active; do
    printf '[scheduler] waiting for TC131 profiles (%s)\n' "$(date --iso-8601=seconds)" | tee -a "$OUT_DIR/scheduler.log"
    sleep "$WAIT_SEC"
done

run_case() {
    local tc="$1" topology="$2" policy="$3"
    local label="tc${tc}_${policy}"
    local run_id="capacity-${label}-$(date +%Y%m%d_%H%M%S)"
    printf '[scheduler] starting %s (%s)\n' "$label" "$(date --iso-8601=seconds)" | tee -a "$OUT_DIR/scheduler.log"
    E2E_RUN_ID="$run_id" LOG_BASE="$OUT_DIR/$label" UBCC_POLICY="$policy" \
        TIMEOUT_SEC="${REAL_CAPACITY_TIMEOUT_SEC:-7200}" \
        PROGRESS_WATCHDOG_SEC=0 E2E_MEMORY_MONITOR_SEC="${E2E_MEMORY_MONITOR_SEC:-30}" \
        bash "$RUNNER" "$topology" "$tc" 2>&1 | tee "$OUT_DIR/$label.runner.log"
    printf '[scheduler] finished %s (%s)\n' "$label" "$(date --iso-8601=seconds)" | tee -a "$OUT_DIR/scheduler.log"
}

run_case 132 --1s naive
run_case 132 --1s spill
run_case 133 --8n1s naive
run_case 133 --8n1s spill
run_case 134 --8n2s naive
run_case 134 --8n2s spill
