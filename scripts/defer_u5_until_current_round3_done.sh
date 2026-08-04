#!/bin/bash
# Pause Round 3 dispatch, let current cases finish, then run U5 alone.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COORDINATOR_PID="$1"
shift
CURRENT_CONTAINERS=("$@")
LOG_ROOT="$ROOT_DIR/logs/u5_tc143_8n2s_naive_diag_deferred_20260804"
CONTROL_LOG="$LOG_ROOT/deferred_control.log"

mkdir -p "$LOG_ROOT"

resume_coordinator() {
    if kill -0 "$COORDINATOR_PID" 2>/dev/null; then
        kill -CONT "$COORDINATOR_PID" 2>/dev/null || true
    fi
}
trap resume_coordinator EXIT INT TERM HUP

printf '%s pause Round 3 coordinator pid=%s current=%s\n' \
    "$(date --iso-8601=seconds)" "$COORDINATOR_PID" \
    "${CURRENT_CONTAINERS[*]}" >>"$CONTROL_LOG"
kill -STOP "$COORDINATOR_PID"

while true; do
    running=0
    for container in "${CURRENT_CONTAINERS[@]}"; do
        state=$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)
        if [ "$state" = "true" ]; then
            running=1
        fi
    done
    [ "$running" -eq 1 ] || break
    sleep 30
done

printf '%s current Round 3 cases exited; rebuilding UBIO for U5\n' \
    "$(date --iso-8601=seconds)" >>"$CONTROL_LOG"
if ! bash "$ROOT_DIR/scripts/build_ubio.sh" >>"$LOG_ROOT/build_ubio.log" 2>&1; then
    printf '%s U5 rebuild failed\n' "$(date --iso-8601=seconds)" >>"$CONTROL_LOG"
    exit 1
fi

printf '%s starting exclusive U5 diagnostic retry\n' \
    "$(date --iso-8601=seconds)" >>"$CONTROL_LOG"
env RUN_TAG=u5_tc143_8n2s_naive_diag_deferred_20260804 \
    LOG_ROOT="$LOG_ROOT" \
    LEGACY_TC_LIST='' PORTABLE_TC_LIST='143' \
    INCLUDE_3N1S=0 MULTI_TOPOLOGY_LIST='8n2s' \
    PRESSURE_LEVELS=150 PROFILE_LIST='naive' \
    CASE_FILTER='tc143_8n2s_naive_p150' \
    PRIORITY_CASE='tc143_8n2s_naive_p150' \
    MAX_PARALLEL=1 CASE_TIMEOUT_SEC=14400 STALL_TIMEOUT_SEC=1800 \
    DISK_FLOOR_GB=80 \
    python3 "$ROOT_DIR/scripts/run_p0_512k_matrix.py" \
    >>"$LOG_ROOT/coordinator.log" 2>&1
status=$?

printf '%s U5 retry finished status=%s; resume Round 3\n' \
    "$(date --iso-8601=seconds)" "$status" >>"$CONTROL_LOG"
exit "$status"
