#!/bin/bash
# Run registered testcases with fewer than three historical PASS sentinels.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_TAG="${RUN_TAG:-low_frequency_correctness_$(date +%Y%m%d_%H%M%S)}"
LOG_ROOT="${LOG_ROOT:-$ROOT_DIR/logs/$RUN_TAG}"
TIMEOUT_SEC="${TIMEOUT_SEC:-900}"

if [ "${LOW_FREQ_IN_CONTAINER:-0}" != "1" ]; then
    exec docker run --rm --network none \
        -e LOW_FREQ_IN_CONTAINER=1 \
        -e RUN_TAG="$RUN_TAG" \
        -e LOG_ROOT="/workspace/${LOG_ROOT#$ROOT_DIR/}" \
        -e TIMEOUT_SEC="$TIMEOUT_SEC" \
        -v "$ROOT_DIR:/workspace" -w /workspace \
        ubcc-dev:ubuntu20.04 \
        bash scripts/run_low_frequency_correctness_queue.sh
fi

mkdir -p "$LOG_ROOT"
MATRIX="$LOG_ROOT/matrix.tsv"
TARGETS="$LOG_ROOT/targets.tsv"
if [ ! -f "$MATRIX" ]; then
    printf 'tc\tprofile\ttopology\tstatus\tlog_dir\n' >"$MATRIX"
fi

# Selection rule: registered testcase with fewer than three historical
# `>>> TCx PASSED <<<` sentinels before this queue was created. TC128 and
# TC141 are included because their recent focused reruns are not represented
# consistently by the historical sentinel scan.
if [ ! -f "$TARGETS" ]; then
    printf 'tc\tprofile\ttopology\thistorical_passes\tselection_rule\n' >"$TARGETS"
fi

historical_passes() {
    local tc="$1"
    rg -l ">>> TC${tc} PASSED <<<" "$ROOT_DIR/logs" -g '*.log' 2>/dev/null | wc -l
}

topology_for_tc() {
    case "$1" in
        32|33|34|35|39|81) printf '%s\n' --2s ;;
        82) printf '%s\n' --8n1s ;;
        *) printf '%s\n' --1s ;;
    esac
}

register_target() {
    local tc="$1" profile="$2" topology="$3" rule="$4"
    if ! grep -q "^${tc}[[:space:]]${profile}[[:space:]]${topology}[[:space:]]" "$TARGETS"; then
        printf '%s\t%s\t%s\t%s\t%s\n' \
            "$tc" "$profile" "$topology" "$(historical_passes "$tc")" "$rule" \
            >>"$TARGETS"
    fi
}

already_passed() {
    local tc="$1" profile="$2" topology="$3"
    grep -q "^${tc}[[:space:]]${profile}[[:space:]]${topology}[[:space:]]PASS[[:space:]]" \
        "$MATRIX" 2>/dev/null
}

run_case() {
    local tc="$1" profile="$2" topology="$3"
    local label="tc${tc}_${profile}"
    local log_dir="$LOG_ROOT/$label"
    local run_id="${RUN_TAG}_${label}"
    local policy="spill"
    [ "$profile" = "naive" ] && policy="naive"
    if already_passed "$tc" "$profile" "$topology"; then
        return 0
    fi
    mkdir -p "$log_dir"
    printf '%s\t%s\t%s\tRUNNING\t%s\n' "$tc" "$profile" "$topology" "$log_dir" >>"$MATRIX"

    env E2E_RUN_ID="$run_id" LOG_BASE="$log_dir" TIMEOUT_SEC="$TIMEOUT_SEC" \
        EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 \
        EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off \
        EP_PERF_PROFILE="$profile" UBCC_POLICY="$policy" \
        bash "$ROOT_DIR/tests/e2e/run_multi.sh" "$topology" "$tc"
    local status=$?
    local verified=0
    if [ "$status" -eq 0 ] && [ "$tc" -eq 9 ] && \
       grep -q "Page table fault when accessing virtual address 0xfffff8000000" \
           "$log_dir/gem5_tc9_node0/stderr.log" 2>/dev/null; then
        verified=1
    elif [ "$status" -eq 0 ] && [ -f "$log_dir/verify_tc${tc}.log" ] && \
         [ "$(tail -n 1 "$log_dir/verify_tc${tc}.log")" = ">>> TC${tc} PASSED <<<" ]; then
        verified=1
    fi
    if [ "$verified" -eq 1 ]; then
        printf '%s\t%s\t%s\tPASS\t%s\n' "$tc" "$profile" "$topology" "$log_dir" >>"$MATRIX"
        return 0
    fi
    printf '%s\t%s\t%s\tFAIL\t%s\n' "$tc" "$profile" "$topology" "$log_dir" >>"$MATRIX"
    return 1
}

failures=0
for tc in $(seq 1 54) 63 64 80 81 82 84 85; do
    topology="$(topology_for_tc "$tc")"
    register_target "$tc" optimized "$topology" pass_count_lt_3
done
register_target 128 spill-noopt --1s recent_focused_rerun_audit
register_target 141 spill-noopt --1s pass_count_lt_3
register_target 141 optimized --1s pass_count_lt_3
printf 'expected_targets\t64\n' >"$LOG_ROOT/queue_manifest.txt"

for tc in $(seq 1 54) 63 64 80 81 82 84 85; do
    topology="$(topology_for_tc "$tc")"
    run_case "$tc" optimized "$topology" || failures=$((failures + 1))
done
run_case 128 spill-noopt --1s || failures=$((failures + 1))
run_case 141 spill-noopt --1s || failures=$((failures + 1))
run_case 141 optimized --1s || failures=$((failures + 1))

printf 'queue\t-\t-\tCOMPLETE failures=%d\t%s\n' "$failures" "$LOG_ROOT" >>"$MATRIX"
exit "$failures"
