#!/bin/bash
# Run database-memory workloads across naive, spill-noopt, and spill-opt.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_TAG="${RUN_TAG:-database_perf_matrix_$(date +%Y%m%d_%H%M%S)}"
LOG_ROOT="${LOG_ROOT:-$ROOT_DIR/logs/$RUN_TAG}"
TIMEOUT_SEC="${TIMEOUT_SEC:-3600}"
mkdir -p "$LOG_ROOT"
MATRIX="$LOG_ROOT/matrix.tsv"
printf 'status\ttc\tprofile\ttopology\tlog_dir\n' >"$MATRIX"

run_case() {
    local tc="$1" profile="$2"
    local log_dir="$LOG_ROOT/$profile/tc$tc"
    local run_id="${RUN_TAG}_${profile}_tc${tc}"
    local policy perf_profile gem5_opts
    case "$profile" in
        naive)
            policy=naive
            perf_profile=naive
            gem5_opts='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0'
            ;;
        spill-noopt)
            policy=spill
            perf_profile=spill-noopt
            gem5_opts='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0'
            ;;
        spill-opt)
            policy=spill
            perf_profile=optimized
            gem5_opts='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1'
            ;;
        *)
            printf 'unknown profile: %s\n' "$profile" >&2
            return 2
            ;;
    esac

    mkdir -p "$log_dir"
    docker run --rm --network none \
        -v "$ROOT_DIR:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
        env E2E_RUN_ID="$run_id" LOG_BASE="${log_dir#$ROOT_DIR/}" \
            TIMEOUT_SEC="$TIMEOUT_SEC" EP_SUPERVISOR=1 \
            EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 \
            EP_TRACE_PERF=off EP_PERF_PROFILE="$perf_profile" \
            UBCC_POLICY="$policy" UBCC_OPTS="--dir-overflow-policy=$policy" \
            EP_GEM5_OPTS="$gem5_opts" \
        bash tests/e2e/run_multi.sh --1s "$tc"
    local docker_status=$?
    local verify_log="$log_dir/verify_tc${tc}.log"
    if [ "$docker_status" -eq 0 ] && python3 - "$verify_log" "$tc" <<'PY'
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
tc = sys.argv[2]
lines = path.read_text(errors="replace").splitlines() if path.exists() else []
raise SystemExit(0 if lines and lines[-1] == f">>> TC{tc} PASSED <<<" else 1)
PY
    then
        printf 'PASS\t%s\t%s\t--1s\t%s\n' "$tc" "$profile" "$log_dir" >>"$MATRIX"
        return 0
    fi
    printf 'FAIL\t%s\t%s\t--1s\t%s\n' "$tc" "$profile" "$log_dir" >>"$MATRIX"
    return 1
}

failures=0
for tc in ${TC_LIST:-142 143 144}; do
    for profile in ${PROFILE_LIST:-naive spill-noopt spill-opt}; do
        run_case "$tc" "$profile" || failures=$((failures + 1))
    done
done

python3 "$ROOT_DIR/scripts/summarize_database_perf_matrix.py" "$LOG_ROOT" \
    >"$LOG_ROOT/summary.json"
printf 'Database matrix complete: %s (%d failed cases)\n' "$LOG_ROOT" "$failures"
exit "$failures"
