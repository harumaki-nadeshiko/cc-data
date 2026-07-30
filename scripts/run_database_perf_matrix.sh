#!/bin/bash
# Run portable large workloads across topology and directory-policy profiles.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_TAG="${RUN_TAG:-portable_large_matrix_$(date +%Y%m%d_%H%M%S)}"
LOG_ROOT="${LOG_ROOT:-$ROOT_DIR/logs/$RUN_TAG}"
TIMEOUT_SEC="${TIMEOUT_SEC:-3600}"
mkdir -p "$LOG_ROOT"
MATRIX="$LOG_ROOT/matrix.tsv"
printf 'status\ttc\ttopology\tprofile\tlog_dir\n' >"$MATRIX"

run_case() {
    local tc="$1" topology="$2" profile="$3"
    local log_dir="$LOG_ROOT/$topology/$profile/tc$tc"
    local profile_code token run_id
    case "$profile" in
        naive) profile_code=n ;;
        spill-noopt) profile_code=s ;;
        spill-opt) profile_code=o ;;
        *) profile_code=x ;;
    esac
    # Unix-domain socket paths are limited to roughly 108 bytes. Keep the
    # human-readable hierarchy in LOG_ROOT, but use a fixed-short IPC run ID.
    token="$(printf '%s' "${RUN_TAG}_${topology}_${profile}_tc${tc}" | cksum | cut -d' ' -f1)"
    run_id="pl_${token}_${topology}_${profile_code}_${tc}"
    local policy perf_profile gem5_opts
    case "$profile" in
        naive)
            policy=naive; perf_profile=naive
            gem5_opts='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0'
            ;;
        spill-noopt)
            policy=spill; perf_profile=spill-noopt
            gem5_opts='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0'
            ;;
        spill-opt)
            policy=spill; perf_profile=optimized
            gem5_opts='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1'
            ;;
        *) printf 'unknown profile: %s\n' "$profile" >&2; return 2 ;;
    esac

    mkdir -p "$log_dir"
    docker run --rm --network none \
        -v "$ROOT_DIR:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
        env E2E_RUN_ID="$run_id" LOG_BASE="${log_dir#$ROOT_DIR/}" \
            TIMEOUT_SEC="$TIMEOUT_SEC" EP_SUPERVISOR=1 \
            EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 \
            EP_SUPERVISOR_DISK_FREE_GB="${EP_SUPERVISOR_DISK_FREE_GB:-50}" \
            EP_TRACE_PERF=off EP_PERF_PROFILE="$perf_profile" \
            UBCC_POLICY="$policy" UBCC_OPTS="--dir-overflow-policy=$policy" \
            EP_GEM5_OPTS="$gem5_opts" \
        bash tests/e2e/run_multi.sh "--$topology" "$tc"
    local status=$?
    local verify_log="$log_dir/verify_tc${tc}.log"
    if [ "$status" -eq 0 ] && python3 - "$verify_log" "$tc" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1]); tc = sys.argv[2]
lines = path.read_text(errors="replace").splitlines() if path.exists() else []
raise SystemExit(0 if lines and lines[-1] == f">>> TC{tc} PASSED <<<" else 1)
PY
    then
        printf 'PASS\t%s\t%s\t%s\t%s\n' "$tc" "$topology" "$profile" "$log_dir" >>"$MATRIX"
        return 0
    fi
    printf 'FAIL\t%s\t%s\t%s\t%s\n' "$tc" "$topology" "$profile" "$log_dir" >>"$MATRIX"
    return 1
}

failures=0
for topology in ${TOPOLOGY_LIST:-1s 2s 8n1s 8n2s}; do
    for tc in ${TC_LIST:-142 143 144 145 146 147}; do
        for profile in ${PROFILE_LIST:-spill-noopt}; do
            run_case "$tc" "$topology" "$profile" || failures=$((failures + 1))
        done
    done
done

python3 "$ROOT_DIR/scripts/summarize_database_perf_matrix.py" "$LOG_ROOT" \
    >"$LOG_ROOT/summary.json"
printf 'Portable large matrix complete: %s (%d failed cases)\n' "$LOG_ROOT" "$failures"
exit "$failures"
