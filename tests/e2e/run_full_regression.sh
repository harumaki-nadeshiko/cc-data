#!/bin/bash
# Run every registered E2E testcase except TC9. Batches remain serial because
# workload.elf, IPC endpoints, and build/run are shared by every testcase.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

# The host intentionally does not carry gem5's runtime library set. Re-exec
# inside the project-standard isolated image so every testcase sees the same
# environment as the targeted E2E runs.
if [ "${FULL_REGRESSION_IN_CONTAINER:-0}" != "1" ]; then
    exec docker run --rm --network none \
        -e FULL_REGRESSION_IN_CONTAINER=1 \
        -e FULL_REGRESSION_TIMEOUT_SEC="${FULL_REGRESSION_TIMEOUT_SEC:-900}" \
        -e FULL_REGRESSION_TIMEOUT_SEC_TC98="${FULL_REGRESSION_TIMEOUT_SEC_TC98:-1800}" \
        -e FULL_REGRESSION_TIMEOUT_SEC_TC128="${FULL_REGRESSION_TIMEOUT_SEC_TC128:-1800}" \
        -v "$ROOT_DIR:/workspace" \
        -w /workspace \
        ubcc-dev:ubuntu20.04 \
        bash tests/e2e/run_full_regression.sh
fi

RUNNER="$ROOT_DIR/tests/e2e/run_multi.sh"
REGISTRY="$ROOT_DIR/tests/e2e/test_e2e.py"
LOG_DIR="${FULL_REGRESSION_LOG_DIR:-$ROOT_DIR/logs/full_regression_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$LOG_DIR"

readarray -t ALL_TCS < <(python3 - "$REGISTRY" <<'PY'
import re
import sys

text = open(sys.argv[1]).read()
start = re.search(r"^TESTCASES\s*=\s*\{", text, re.M)
end = text.find("\n}", start.end())
ns = {}
exec(text[start.start():end + 2], ns)
for tc in sorted(ns["TESTCASES"]):
    if tc not in (9, 131, 132, 133, 134):
        print(tc)
PY
)

TWO_SOCKET=(32 33 34 35 39 81)
TWO_NODE_ONE_SOCKET=(210 211 212 213 214 215 216 217 218 219 220 221 222 223 224 225 226 227)
EIGHT_NODE_ONE_SOCKET=(82 90 91 92 93 94 133)
EIGHT_NODE_TWO_SOCKET=(95 96 97 98 99 100 101 134)

contains_tc() {
    local needle="$1"; shift
    local tc
    for tc in "$@"; do
        [ "$tc" = "$needle" ] && return 0
    done
    return 1
}

ONE_SOCKET=()
for tc in "${ALL_TCS[@]}"; do
    if contains_tc "$tc" "${TWO_SOCKET[@]}" || \
       contains_tc "$tc" "${TWO_NODE_ONE_SOCKET[@]}" || \
       contains_tc "$tc" "${EIGHT_NODE_ONE_SOCKET[@]}" || \
       contains_tc "$tc" "${EIGHT_NODE_TWO_SOCKET[@]}"; then
        continue
    fi
    ONE_SOCKET+=("$tc")
done

run_batch() {
    local name="$1"; shift
    local topo="$1"; shift
    local -a tcs=("$@")
    if [ "${#tcs[@]}" -eq 0 ]; then
        return
    fi
    printf '=== %s: %s ===\n' "$name" "${tcs[*]}" | tee "$LOG_DIR/${name}.log"
    if ! TIMEOUT_SEC="${FULL_REGRESSION_TIMEOUT_SEC:-900}" \
         TIMEOUT_SEC_TC98="${FULL_REGRESSION_TIMEOUT_SEC_TC98:-1800}" \
         TIMEOUT_SEC_TC128="${FULL_REGRESSION_TIMEOUT_SEC_TC128:-1800}" \
         bash "$RUNNER" "$topo" "${tcs[@]}" 2>&1 | \
         tee -a "$LOG_DIR/${name}.log"; then
        BATCH_FAILURES+=("$name")
    fi
}

BATCH_FAILURES=()
printf 'Started: %s\n' "$(date --iso-8601=seconds)" | tee "$LOG_DIR/summary.log"
printf 'TCs: %s\n' "${ALL_TCS[*]}" | tee -a "$LOG_DIR/summary.log"

run_batch "1s" "--1s" "${ONE_SOCKET[@]}"
run_batch "2s" "--2s" "${TWO_SOCKET[@]}"
run_batch "2n1s" "--2n1s" "${TWO_NODE_ONE_SOCKET[@]}"
run_batch "8n1s" "--8n1s" "${EIGHT_NODE_ONE_SOCKET[@]}"
run_batch "8n2s" "--8n2s" "${EIGHT_NODE_TWO_SOCKET[@]}"

printf 'Finished: %s\n' "$(date --iso-8601=seconds)" | tee -a "$LOG_DIR/summary.log"
if [ "${#BATCH_FAILURES[@]}" -ne 0 ]; then
    printf 'Failed batches: %s\n' "${BATCH_FAILURES[*]}" | tee -a "$LOG_DIR/summary.log"
    exit 1
fi
