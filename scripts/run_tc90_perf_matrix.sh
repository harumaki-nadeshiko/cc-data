#!/bin/bash
# Run the applicable naive/spill-noopt/spill-opt matrix for TC116+ workloads.
# Policy-specialized regressions deliberately omit profiles that do not exercise
# their asserted protocol path; every omission is recorded in matrix.tsv.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_TAG="${RUN_TAG:-tc90_perf_matrix_$(date +%Y%m%d)}"
LOG_ROOT="${LOG_ROOT:-$ROOT_DIR/logs/$RUN_TAG}"
TIMEOUT_SEC="${TIMEOUT_SEC:-3600}"
mkdir -p "$LOG_ROOT"
MATRIX="$LOG_ROOT/matrix.tsv"
printf 'tc\tprofile\ttopology\tstatus\tlog_dir\n' >"$MATRIX"

run_case() {
    local tc="$1" profile="$2" topology="$3"
    local log_dir="$LOG_ROOT/$profile/tc$tc"
    local run_id="${RUN_TAG}_${profile}_tc${tc}"
    local policy gem5_opts
    case "$profile" in
        naive) policy=naive; gem5_opts='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' ;;
        spill-noopt) policy=spill; gem5_opts='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' ;;
        spill-opt) policy=spill; gem5_opts='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' ;;
        *) echo "unknown profile: $profile" >&2; exit 2 ;;
    esac
    mkdir -p "$log_dir"
    printf 'RUN\t%s\t%s\t%s\t-\t%s\n' "$tc" "$profile" "$topology" "$log_dir" >>"$MATRIX"
    if docker run --rm --network none \
        -v "$ROOT_DIR:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
        env E2E_RUN_ID="$run_id" LOG_BASE="${log_dir#$ROOT_DIR/}" \
            TIMEOUT_SEC="$TIMEOUT_SEC" EP_SUPERVISOR=1 \
            EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 \
            EP_TRACE_PERF=off EP_PERF_PROFILE="${profile/spill-opt/optimized}" \
            UBCC_POLICY="$policy" UBCC_OPTS="--dir-overflow-policy=$policy" \
            EP_GEM5_OPTS="$gem5_opts" \
        bash tests/e2e/run_multi.sh "$topology" "$tc"; then
        printf 'PASS\t%s\t%s\t%s\tPASS\t%s\n' "$tc" "$profile" "$topology" "$log_dir" >>"$MATRIX"
    else
        printf 'FAIL\t%s\t%s\t%s\tFAIL\t%s\n' "$tc" "$profile" "$topology" "$log_dir" >>"$MATRIX"
        return 1
    fi
}

run_profiles() {
    local topology="$1"; shift
    local tc
    for tc in "$@"; do
        run_case "$tc" naive "$topology" || return 1
        run_case "$tc" spill-noopt "$topology" || return 1
        run_case "$tc" spill-opt "$topology" || return 1
    done
}

run_spill_profiles() {
    local topology="$1"; shift
    local tc
    for tc in "$@"; do
        printf 'SKIP\t%s\tnaive\t%s\tN/A: spill-path regression\t-\n' "$tc" "$topology" >>"$MATRIX"
        run_case "$tc" spill-noopt "$topology" || return 1
        run_case "$tc" spill-opt "$topology" || return 1
    done
}

# Policy-neutral 3-node regressions and benchmarks.
run_profiles --1s 116 117 118 119 120 121 122 123 124 130 131 132 || exit 1
# Spill/onload regressions only assert the H64 spill path.
run_spill_profiles --1s 125 126 127 128 129 201 202 203 || exit 1
# TC200 asserts the naive dirty-Recall path.
run_case 200 naive --1s || exit 1
printf 'SKIP\t200\tspill-noopt\t--1s\tN/A: naive-Recall regression\t-\n' >>"$MATRIX"
printf 'SKIP\t200\tspill-opt\t--1s\tN/A: naive-Recall regression\t-\n' >>"$MATRIX"
# Fixed-topology tests.
run_profiles --8n1s 133 || exit 1
run_profiles --8n2s 134 || exit 1
run_profiles --2n1s 210 211 212 213 214 215 216 218 219 || exit 1

python3 "$ROOT_DIR/scripts/summarize_tc90_perf_matrix.py" "$LOG_ROOT" >"$LOG_ROOT/summary.json"
printf 'Matrix complete: %s\n' "$LOG_ROOT"
