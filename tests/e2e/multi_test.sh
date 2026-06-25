#!/bin/bash
# Multi-process E2E test launcher with process monitoring
# Usage: bash multi_test.sh <TC_NUMBER> [timeout_seconds]

TC=${1:-1}
TIMEOUT=${2:-120}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GEM5_BIN="${SCRIPT_DIR}/../../gem5/build/ARM/gem5.opt"
UBIO_BIN="/tmp/ubio"
OUTDIR="${SCRIPT_DIR}/../../m5out/e2e/tc${TC}"
MONITOR_LOG="${OUTDIR}/ps_monitor.log"

rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

echo "=== Multi-Process E2E Test: TC${TC} ==="
echo "Starting at $(date)"

# Start ubio processes (one per node)
echo "[launcher] Starting ubio node 0..."
UBIO_PORT_ENABLE=-1 "${UBIO_BIN}" \
    --gem5-ep="ipc:///tmp/ubio_n0" \
    --node=0 \
    --gem5-bind &
PID_UBIO0=$!

echo "[launcher] Starting ubio node 1..."
UBIO_PORT_ENABLE=-1 "${UBIO_BIN}" \
    --gem5-ep="ipc:///tmp/ubio_n1" \
    --node=1 \
    --gem5-bind &
PID_UBIO1=$!

echo "[launcher] Starting ubio node 2..."
UBIO_PORT_ENABLE=-1 "${UBIO_BIN}" \
    --gem5-ep="ipc:///tmp/ubio_n2" \
    --node=2 \
    --gem5-bind &
PID_UBIO2=$!

# Start process monitor in background
(
    echo "=== Process Monitor Started at $(date) ===" 
    for i in $(seq 1 100); do
        echo "--- tick $i $(date) ---" >> "$MONITOR_LOG"
        ps aux --forest 2>/dev/null | grep -E "gem5|ubio|PID|%CPU" >> "$MONITOR_LOG"
        top -b -n1 2>/dev/null | head -15 >> "$MONITOR_LOG"
        sleep 1
    done
    echo "=== Process Monitor Ended at $(date) ===" >> "$MONITOR_LOG"
) &
PID_MONITOR=$!

echo "[launcher] PIDs: ubio0=$PID_UBIO0 ubio1=$PID_UBIO1 ubio2=$PID_UBIO2 monitor=$PID_MONITOR"
echo "[launcher] Starting gem5 (timeout=${TIMEOUT}s)..."

# Run gem5 with UBIO_PORT_ENABLE
UBIO_PORT_ENABLE=-1 timeout "${TIMEOUT}" "${GEM5_BIN}" \
    --outdir="$OUTDIR" \
    "${SCRIPT_DIR}/test_e2e.py" --tc="${TC}" &
PID_GEM5=$!

echo "[launcher] gem5 PID=$PID_GEM5"
echo "[launcher] ps snapshot:"
ps aux | grep -E "gem5|ubio" | grep -v grep

# Wait for gem5
wait $PID_GEM5 2>/dev/null
GEM5_EXIT=$?

echo "[launcher] gem5 exited with code=$GEM5_EXIT"

# Cleanup
kill $PID_UBIO0 $PID_UBIO1 $PID_UBIO2 $PID_MONITOR 2>/dev/null
wait 2>/dev/null

echo "[launcher] Checking test output..."
strings "${OUTDIR}"/simout_n0 2>/dev/null | head -5

echo "[launcher] Process monitor saved to ${MONITOR_LOG}"
echo "=== Done ==="
