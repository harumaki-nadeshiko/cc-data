#!/bin/bash
# Multi-Process E2E Test Runner
# Starts ubio processes + gem5 with Port IPC, runs TC1-11, reports results
# Usage: ./tests/e2e/run_multi.sh [--all | 1 | 2 | ... | 11]

set -euo pipefail
shopt -s nullglob 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
GEM5_BIN="$ROOT_DIR/gem5/build/ARM/gem5.opt"
UBIO_BIN="/tmp/ubio.elf"
WORKLOAD_DIR="$SCRIPT_DIR/workloads"
MODULES_DIR="$ROOT_DIR/modules/ubiomodule"
EP_DIR="$MODULES_DIR/mem/ruby/protocol/chi/ep"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_BASE="$ROOT_DIR/logs/$TIMESTAMP"

UBIO_PIDS=""
GEM5_PIDS=""

die() { echo "FATAL: $*" >&2; exit 1; }

# ── Cleanup: kill everything ───────────────────────────────────────
cleanup() {
    local all_pids="${UBIO_PIDS:-} ${GEM5_PIDS:-} ${NSIM_PID:-}"
    [ -z "${all_pids// /}" ] && return
    echo "[cleanup] Terminating all processes..."
    for pid in ${all_pids}; do
        kill $pid 2>/dev/null || true
    done
    for pid in ${all_pids}; do
        wait $pid 2>/dev/null || true
    done
    echo "[cleanup] Done"
}
trap cleanup EXIT

# ── Build ubio ─────────────────────────────────────────────────────
compile_ubio() {
    echo "[build] Compiling ubio..."
    for f in BackstoreSchemaA BackstoreSchemaC BackstoreOrganization BackstoreTypes; do
        [ -f "$MODULES_DIR/${f}.hh" ] && ln -sf "$MODULES_DIR/${f}.hh" "$EP_DIR/${f}.hh" 2>/dev/null
    done
    local srcs="$MODULES_DIR/UBCCController.cc $MODULES_DIR/ResidentDir.cc $MODULES_DIR/BackstoreSchemaA.cc $MODULES_DIR/BackstoreSchemaC.cc $MODULES_DIR/NodeAddressMap.cc"
    g++ -std=c++17 -O2 \
        -I"$MODULES_DIR" -I"$MODULES_DIR/mem/ruby" \
        -I"$ROOT_DIR" -I"$ROOT_DIR/thirdparty/zeromq/include" \
        "$ROOT_DIR/tools/ubio/ubio_main.cc" "$ROOT_DIR/framework/Port.cc" $srcs \
        -L"$ROOT_DIR/thirdparty/zeromq/lib" -lzmq -lpthread -o "$UBIO_BIN" 2>/dev/null
    [ -x "$UBIO_BIN" ] || die "ubio build failed"
    echo "[build] ubio: $(ls -lh "$UBIO_BIN" | awk '{print $5}')"
}

# ── Compile workloads ──────────────────────────────────────────────
compile_workloads() {
    local cc="aarch64-linux-gnu-gcc" cflags="-static -O0 -g -I${WORKLOAD_DIR}"
    for src in "$WORKLOAD_DIR"/e2e_tc[1-9]*_*.c e2e_tc10_*.c e2e_tc_local_upgrade.c; do
        [ -f "$src" ] || continue
        local base; base=$(basename "$src" .c)
        local elf="${WORKLOAD_DIR}/${base}.elf"
        [ "$src" -nt "$elf" ] 2>/dev/null || [ ! -f "$elf" ] && $cc $cflags -o "$elf" "$src"
    done
}

# ── Start gem5 first, wait for Port bind, then ubio ─────────────────
start_all() {
    # Clean up stale IPC endpoints
    rm -rf /tmp/ubio_n* /tmp/networksim_*

    # Start networksim first (must bind before anyone connects)
    local TOPO="$ROOT_DIR/tools/networksim/topo3.json"
    local NSIM_BIN="$ROOT_DIR/modules/networksim/networksim"
    if [ -x "$NSIM_BIN" ]; then
        echo "[launch] Starting networksim..."
        "$NSIM_BIN" "$TOPO" >"${LOG_BASE}/nsim.log" 2>&1 &
        NSIM_PID=$!
        sleep 1
    else
        echo "[launch] WARNING: no networksim binary at $NSIM_BIN"
    fi

    # Start gem5 in background, wait for Port creation
    echo "[launch] Starting gem5..."
    UBIO_PORT_ENABLE=-1 "$GEM5_BIN" \
        --outdir="${ROOT_DIR}/m5out/e2e_mp/tc${TC_NUM}" \
        "$SCRIPT_DIR/test_e2e.py" --tc=${TC_NUM} \
        >"${LOG_BASE}/gem5_tc${TC_NUM}/stdout.log" \
        2>"${LOG_BASE}/gem5_tc${TC_NUM}/stderr.log" &
    GEM5_PID=$!
    
    # Wait for gem5 to bind Port (STEP5 message)
    echo "[launch] Waiting for gem5 to bind..."
    local gem5_stdout="${LOG_BASE}/gem5_tc${TC_NUM}/stdout.log"
    for i in $(seq 1 60); do
        if grep -q "STEP5.*Port enabled" "$gem5_stdout" 2>/dev/null; then
            echo "[launch] Gem5 Port bound after ${i}s"
            break
        fi
        if ! kill -0 $GEM5_PID 2>/dev/null; then
            echo "[launch] Gem5 exited early!"
            return 1
        fi
        sleep 1
    done
    
    # Now start ubio (connects to already-bound endpoints)
    for nid in 0 1 2; do
        local ep="ipc:///tmp/ubio_n${nid}"
        local netep="ipc:///tmp/networksim_m${nid}_p1"
        local logdir="${LOG_BASE}/ubio_n${nid}"
        mkdir -p "$logdir"
        "$UBIO_BIN" --gem5-ep="$ep" --net-ep="$netep" --node="$nid" \
            >"$logdir/stdout.log" 2>"$logdir/stderr.log" &
        UBIO_PIDS="$UBIO_PIDS $!"
        echo "[launch] ubio n${nid} pid=$! log=$logdir"
    done
    echo "[launch] $(echo $UBIO_PIDS | wc -w) ubio processes running"
}

# ── Gem5 watchdog: poll until gem5 exits, then trigger cleanup ─────
watchdog() {
    local gem5_pid=$1
    local start=$(date +%s)
    while kill -0 $gem5_pid 2>/dev/null; do
        sleep 2
        local elapsed=$(($(date +%s) - start))
        # Print status every 30s
        if [ $((elapsed % 30)) -lt 2 ] && [ $elapsed -gt 0 ]; then
            echo "[watchdog] gem5 running ${elapsed}s..."
        fi
    done
    echo "[watchdog] gem5 (pid=$gem5_pid) exited after ${elapsed}s"
    # Kill all ubio processes — gem5 is done
    for pid in $UBIO_PIDS; do
        kill $pid 2>/dev/null || true
    done
}

# ── Run single TC ──────────────────────────────────────────────────
run_tc() {
    local tc=$1
    TC_NUM=$tc
    local logdir="${LOG_BASE}/gem5_tc${tc}"
    local outdir="${ROOT_DIR}/m5out/e2e_mp/tc${tc}"
    rm -rf "$outdir" "$logdir"
    mkdir -p "$outdir" "$logdir"

    echo ""
    echo "=== TC${tc} (multi-process) ==="

    start_all
    local gem5_pid=$GEM5_PID

    # Wait for gem5 with timeout
    local timeout_val=300
    local waited=0
    local ec=0
    while kill -0 $gem5_pid 2>/dev/null; do
        sleep 1
        waited=$((waited + 1))
        if [ $waited -ge $timeout_val ]; then
            echo "  TIMEOUT after ${timeout_val}s"
            kill $gem5_pid 2>/dev/null || true
            ec=124
            break
        fi
    done
    if [ $ec -ne 124 ]; then
        wait $gem5_pid 2>/dev/null && ec=$? || ec=$?
    fi

    # Kill ubio after gem5 done
    for pid in $UBIO_PIDS; do kill $pid 2>/dev/null || true; done
    sleep 0.5

    # Parse result
    if grep -q "PASSED" "$logdir/stdout.log" 2>/dev/null; then
        echo "  TC${tc} PASSED"; return 0
    elif grep -q "FAILED" "$logdir/stdout.log" 2>/dev/null; then
        echo "  TC${tc} FAILED"; return 1
    else
        case $ec in
            0)   echo "  TC${tc} NO RESULT" ;;
            124) echo "  TC${tc} TIMEOUT" ;;
            *)   echo "  TC${tc} CRASHED (exit=$ec)" ;;
        esac
        return 1
    fi
}

# ── Main ───────────────────────────────────────────────────────────
echo "=== Multi-Process E2E Runner ==="
echo "Timestamp: $TIMESTAMP"
echo "Log base:  $LOG_BASE"
mkdir -p "$LOG_BASE"

compile_workloads
compile_ubio

TC="${1:---all}"
PASS=0; FAIL=0

run_tests() {
    for tc in "$@"; do
        if run_tc $tc; then ((PASS++)); else ((FAIL++)); fi
    done
}

if [ "$TC" == "--all" ]; then
    run_tests 1 2 3 4 5 6 7 8 9 10 11
else
    run_tests $TC
fi

echo ""
echo "=== Results: $PASS pass, $FAIL fail ==="
echo "Logs: $LOG_BASE"
