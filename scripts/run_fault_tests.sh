#!/bin/bash
# Run bounded fault smoke and qualification tests in a 4-core container.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-all}"
CPU_SET="${FAULT_CPU_SET:-6-9}"
RUN_STAMP="${FAULT_RUN_STAMP:-$(date +%Y%m%d_%H%M%S)}"

case "$MODE" in
    q1)
        exec python3 "$ROOT_DIR/scripts/run_fault_qualification.py" --qualification Q1
        ;;
    expanded|q1-q5)
        exec python3 "$ROOT_DIR/scripts/run_fault_qualification.py"
        ;;
    legacy)
        TC_LIST="47 48 49 110 111"
        RUN_NAME="fault_legacy_${RUN_STAMP}"
        ;;
    smoke)
        TC_LIST="117 118 119"
        RUN_NAME="fault_smoke_${RUN_STAMP}"
        ;;
    qualification)
        TC_LIST="148"
        RUN_NAME="fault_qualification_${RUN_STAMP}"
        ;;
    level2)
        TC_LIST="149 150 151 152 153 154 155"
        RUN_NAME="fault_level2_${RUN_STAMP}"
        ;;
    loss)
        TC_LIST="156 157 158 159"
        RUN_NAME="fault_loss_${RUN_STAMP}"
        ;;
    all)
        TC_LIST="47 48 49 110 111 117 118 119 148 149 150 151 152 153 154 155 156 157 158 159"
        RUN_NAME="fault_all_${RUN_STAMP}"
        ;;
    *)
        echo "usage: $0 {q1|expanded|q1-q5|legacy|smoke|qualification|level2|loss|all}" >&2
        exit 2
        ;;
esac

docker run --rm --name "ubcc-${RUN_NAME}" --network none \
    --cpuset-cpus "$CPU_SET" --cpuset-mems 0 --init \
    -v "$ROOT_DIR:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
    env E2E_RUN_ID="$RUN_NAME" LOG_BASE="logs/$RUN_NAME" \
        TIMEOUT_SEC=1200 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=30 \
        EP_SUPERVISOR_PROGRESS_STALL_SEC=600 \
    bash tests/e2e/run_multi.sh --1s $TC_LIST

echo "Results: logs/$RUN_NAME"
