#!/bin/bash
# Default-profile correctness sweep for every registered TC >= 90.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_TAG="${RUN_TAG:-tc90_default_sweep_$(date +%Y%m%d)}"
LOG_ROOT="${LOG_ROOT:-$ROOT_DIR/logs/$RUN_TAG}"
TIMEOUT_SEC="${TIMEOUT_SEC:-3600}"
mkdir -p "$LOG_ROOT"
MANIFEST="$LOG_ROOT/sweep.tsv"
printf 'tc\ttopology\tstatus\tlog_dir\n' >"$MANIFEST"

run_case() {
    local tc="$1" topology="$2"
    local log_dir="$LOG_ROOT/tc$tc"
    local run_id="${RUN_TAG}_tc${tc}"
    printf 'RUN\t%s\t%s\t-\t%s\n' "$tc" "$topology" "$log_dir" >>"$MANIFEST"
    if docker run --rm --network none \
        -v "$ROOT_DIR:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
        env E2E_RUN_ID="$run_id" LOG_BASE="${log_dir#$ROOT_DIR/}" \
            TIMEOUT_SEC="$TIMEOUT_SEC" EP_SUPERVISOR=1 \
            EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 \
            EP_TRACE_PERF=off \
        bash tests/e2e/run_multi.sh "$topology" "$tc"; then
        printf 'PASS\t%s\t%s\tPASS\t%s\n' "$tc" "$topology" "$log_dir" >>"$MANIFEST"
    else
        printf 'FAIL\t%s\t%s\tFAIL\t%s\n' "$tc" "$topology" "$log_dir" >>"$MANIFEST"
        return 1
    fi
}

run_group() {
    local topology="$1"; shift
    local tc
    for tc in "$@"; do
        run_case "$tc" "$topology" || exit 1
    done
}

run_group --8n1s 90 91 92 93 94 133
run_group --8n2s 95 96 97 98 99 100 101 134
run_group --1s 102 110 111 112 113 114 115 116 117 118 119 120 121 122 123 124 125 126 127 128 129 130 131 132 200 201 202 203
run_group --2n1s 210 211 212 213 214 215 216 218 219
printf 'Default correctness sweep complete: %s\n' "$LOG_ROOT"
