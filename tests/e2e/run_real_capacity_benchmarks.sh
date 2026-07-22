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
    local tc="$1" topology="$2" policy="$3"
    local label="tc${tc}_${policy}"
    LOG_BASE="$OUT_DIR/$label" UBCC_POLICY="$policy" \
        TIMEOUT_SEC="${REAL_CAPACITY_TIMEOUT_SEC:-7200}" \
        bash "$RUNNER" "$topology" "$tc" 2>&1 | tee "$OUT_DIR/$label.runner.log"
}

for tc in 131 132; do
    run_case "$tc" --1s naive
    run_case "$tc" --1s spill
done
run_case 133 --8n1s naive
run_case 133 --8n1s spill
run_case 134 --8n2s naive
run_case 134 --8n2s spill
