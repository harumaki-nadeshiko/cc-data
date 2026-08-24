#!/usr/bin/env bash
# Complete default-profile E2E sweep: one isolated Docker run per registered TC.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${COMPLETE_REGRESSION_TAG:-complete_regression_$(date +%Y%m%d_%H%M%S)}"
LOG_ROOT="${COMPLETE_REGRESSION_LOG_ROOT:-$ROOT/logs/$TAG}"
IMAGE="${UBCC_DOCKER_IMAGE:-ubcc-dev:ubuntu20.04}"
MATRIX="$LOG_ROOT/matrix.tsv"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1
mkdir -p "$LOG_ROOT"
if [ ! -f "$MATRIX" ]; then
    printf 'tc\ttopology\tcpu\tstatus\tlog_dir\n' >"$MATRIX"
fi

mapfile -t TCS < <(docker run --rm --network none \
    -v "$ROOT:/workspace" -w /workspace "$IMAGE" python3 -c \
    'import sys;sys.path.insert(0,"tests/e2e");from test_e2e import TESTCASES;print("\n".join(map(str,sorted(TESTCASES))))')

topology_for() {
    case "$1" in
        32|33|34|35|39|81) printf '%s\n' --2s ;;
        82|90|91|92|93|94|131|133) printf '%s\n' --8n1s ;;
        95|96|97|98|99|100|101|134) printf '%s\n' --8n2s ;;
        160) printf '%s\n' --16n1s ;;
        210|211|212|213|214|215|216|217|218|219|220|221|222|223|224|225|226|227|228|229|230|231|232|233|234|235) printf '%s\n' --2n1s ;;
        *) printf '%s\n' --1s ;;
    esac
}

cpu_for() {
    case "$1" in
        98|134|300|301|302|303) printf '%s\n' o3 ;;
        *) printf '%s\n' timing ;;
    esac
}

timeout_for() {
    case "$1" in
        98) printf '%s\n' 21600 ;;
        131|132|133) printf '%s\n' 10800 ;;
        134) printf '%s\n' 14400 ;;
        142|143|144|145|146|147|224) printf '%s\n' 7200 ;;
        128|160) printf '%s\n' 3600 ;;
        *) printf '%s\n' 1800 ;;
    esac
}

already_passed() {
    local tc="$1"
    grep -q "^${tc}[[:space:]].*[[:space:]]PASS[[:space:]]" "$MATRIX" 2>/dev/null
}

failures=0
for tc in "${TCS[@]}"; do
    topology="$(topology_for "$tc")"
    cpu="$(cpu_for "$tc")"
    timeout="$(timeout_for "$tc")"
    log_dir="$LOG_ROOT/tc${tc}"
    run_id="${TAG}_tc${tc}"
    if already_passed "$tc"; then
        continue
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '%s\t%s\t%s\t%s\n' "$tc" "$topology" "$cpu" "$timeout"
        continue
    fi
    mkdir -p "$log_dir"
    printf '%s\t%s\t%s\tRUNNING\t%s\n' "$tc" "$topology" "$cpu" "$log_dir" >>"$MATRIX"
    printf '=== TC%s topology=%s cpu=%s timeout=%ss ===\n' "$tc" "$topology" "$cpu" "$timeout" | tee "$log_dir/driver.log"
    extra_env=()
    [ "$cpu" = o3 ] && extra_env+=(EP_SEQUENCER_MAX_OUTSTANDING=16)
    if docker run --rm --network none \
        --name "cc-ep-${TAG//[^A-Za-z0-9_.-]/-}-tc${tc}" \
        -v "$ROOT:/workspace" \
        -v "$ROOT/gem5/gem5:/workspace/gem5" \
        -v "/mnt/data2/cgc/.local/lib:/workspace/thirdparty/zeromq/lib:ro" \
        -e LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
        -w /workspace "$IMAGE" env \
        E2E_RUN_ID="$run_id" LOG_BASE="/workspace/${log_dir#$ROOT/}" \
        EP_CPU_MODEL="$cpu" EP_TRACE_PERF=sample TIMEOUT_SEC="$timeout" \
        "${extra_env[@]}" \
        bash tests/e2e/run_multi.sh "$topology" "$tc" \
        >>"$log_dir/driver.log" 2>&1; then
        printf '%s\t%s\t%s\tPASS\t%s\n' "$tc" "$topology" "$cpu" "$log_dir" >>"$MATRIX"
    else
        printf '%s\t%s\t%s\tFAIL\t%s\n' "$tc" "$topology" "$cpu" "$log_dir" >>"$MATRIX"
        failures=$((failures + 1))
    fi
done

if [ "$DRY_RUN" -eq 1 ]; then
    exit 0
fi
printf 'complete\t-\t-\tCOMPLETE failures=%d\t%s\n' "$failures" "$LOG_ROOT" >>"$MATRIX"
exit "$failures"
