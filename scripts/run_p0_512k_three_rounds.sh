#!/bin/bash
# Run the 512 KiB P0 matrix in three rounds, notifying after every round.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE_TAG="${RUN_TAG:-p0_512k_three_rounds_$(date +%Y%m%d_%H%M%S)}"
NTFY_URL="${NTFY_URL:-https://ntfy.sh/nonoka-can-fly-nonoka-can-fly}"
CASE_TIMEOUT_SEC="${CASE_TIMEOUT_SEC:-10800}"
STALL_TIMEOUT_SEC="${STALL_TIMEOUT_SEC:-1800}"

notify() {
    curl -fsS -d "$1" "$NTFY_URL" >/dev/null || true
}

run_round() {
    local round_id="$1" description="$2"
    shift 2
    local round_root="$ROOT_DIR/logs/${BASE_TAG}/${round_id}"
    local start end elapsed status summary
    mkdir -p "$round_root"
    start=$(date +%s)
    env RUN_TAG="${BASE_TAG}_${round_id}" LOG_ROOT="$round_root" \
        CASE_TIMEOUT_SEC="$CASE_TIMEOUT_SEC" \
        STALL_TIMEOUT_SEC="$STALL_TIMEOUT_SEC" \
        MAX_PARALLEL=3 \
        "$@" \
        python3 "$ROOT_DIR/scripts/run_p0_512k_matrix.py" \
        >"$round_root/coordinator.log" 2>&1
    status=$?
    end=$(date +%s)
    elapsed=$((end - start))
    summary=$(python3 "$ROOT_DIR/scripts/summarize_p0_512k_round.py" \
        "$round_root" "$description" "$elapsed" 2>&1)
    notify "$summary"
    return "$status"
}

overall=0
notify "P0 512KiB matrix started: three rounds, at most 3 workers with unrestricted CPU affinity."

run_round round1 "Round 1 TC131-TC134" \
    LEGACY_TC_LIST="131 132 133 134" \
    PORTABLE_TC_LIST="" INCLUDE_3N1S=0 MULTI_TOPOLOGY_LIST="" \
    PRESSURE_LEVELS=150 PROFILE_LIST="naive spill-noopt optimized" || overall=1

run_round round2 "Round 2 TC142-TC147 2N/3N" \
    LEGACY_TC_LIST="" PORTABLE_TC_LIST="142 143 144 145 146 147" \
    INCLUDE_3N1S=1 MULTI_TOPOLOGY_LIST="2n1s 3n2s" \
    PRESSURE_LEVELS=150 PROFILE_LIST="naive spill-noopt optimized" || overall=1

run_round round3 "Round 3 TC142-TC147 8N" \
    LEGACY_TC_LIST="" PORTABLE_TC_LIST="142 143 144 145 146 147" \
    INCLUDE_3N1S=0 MULTI_TOPOLOGY_LIST="8n1s 8n2s" \
    PRESSURE_LEVELS=150 PROFILE_LIST="naive spill-noopt optimized" || overall=1

notify "P0 512KiB three-round matrix finished. Root: logs/${BASE_TAG}; overall=$overall"
exit "$overall"
