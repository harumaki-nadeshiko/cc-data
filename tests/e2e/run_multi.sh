#!/bin/bash
# Multi-Process E2E Test Runner (per-node gem5 split)
#
# Process topology for N nodes:
#   barrier_manager (1)  + networksim (1) + ubio_n{0..N-1} (N)
#   + gem5 --node-id={0..N-1} (N)   => total 2N+2 processes
#
# Each gem5 process builds & runs ONLY its own node's RubySystem/CPUs.
# Cross-node DSM coherence flows over IPC (UBAdapter->Port->ubio->nsim).
# Verification is done at the orchestrator layer after all gem5 finish,
# aggregating each process's per-node simout file.
#
# Usage: ./tests/e2e/run_multi.sh [--all | <tc> ...]

set -euo pipefail
shopt -s nullglob 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
GEM5_BIN="$ROOT_DIR/gem5/build/ARM/gem5.opt"
UBIO_BIN="$ROOT_DIR/build/bin/ubio"
NSIM_BIN="$ROOT_DIR/build/bin/networksim"
BARRIER_BIN="$ROOT_DIR/build/bin/barrier_manager"
WORKLOAD_DIR="$SCRIPT_DIR/workloads"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_BASE="$ROOT_DIR/logs/$TIMESTAMP"

# System dimensions (defaults). NUM_SOCKETS may be raised per dual-socket TC.
NUM_NODES="${NUM_NODES:-3}"
NUM_SOCKETS=1

UBIO_PIDS=""
GEM5_PIDS=""
NSIM_PID=""
BARRIER_PID=""

die() { echo "FATAL: $*" >&2; exit 1; }

# ── Cleanup: kill everything ───────────────────────────────────────
cleanup() {
    local all_pids="${UBIO_PIDS:-} ${GEM5_PIDS:-} ${NSIM_PID:-} ${BARRIER_PID:-}"
    [ -z "${all_pids// /}" ] && return
    for pid in ${all_pids}; do kill $pid 2>/dev/null || true; done
    for pid in ${all_pids}; do wait $pid 2>/dev/null || true; done
}
trap cleanup EXIT

# ── Ensure native binaries exist (built by scripts/build_all.sh) ────
ensure_tools() {
    local missing=""
    [ -x "$UBIO_BIN" ]      || missing="$missing ubio"
    [ -x "$NSIM_BIN" ]      || missing="$missing networksim"
    [ -x "$BARRIER_BIN" ]   || missing="$missing barrier_manager"
    [ -x "$GEM5_BIN" ]      || missing="$missing gem5.opt"
    if [ -n "$missing" ]; then
        echo "FATAL: missing binaries:$missing" >&2
        echo "请先运行: scripts/build_framework.sh && scripts/build_all.sh，并编译 gem5" >&2
        exit 1
    fi
    echo "[tools] ubio/nsim/barrier/gem5 OK"
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

# ── Determine sockets for a TC (dual-socket cases force 2) ──────────
sockets_for_tc() {
    case "$1" in
        32|33|34|35|39) echo 2 ;;
        *)              echo 1 ;;
    esac
}

# ── Debug fault-injection rules per TC (ubio-side) ──────────────────
# Format: name:type:src:dst:pa:action[:delayTicks[:matchCount]]
# Multiple rules separated by ';'. Consumed by ubio via UBIO_FAULT_RULES.
fault_rules_for_tc() {
    case "$1" in
        47) echo "tc47_dup_clear:ClearReq:1:0:0:dup::1" ;;
        48) echo "tc48_dup_inv_ack:InvalidateAck:2:0:0:dup::1" ;;
        49) echo "tc49_dup_inv_ack:InvalidateAck:1:0:0:dup::1" ;;
        *)  echo "" ;;
    esac
}

# ── Start infra (barrier, nsim, ubio) + N per-node gem5 processes ───
start_all() {
    rm -rf /tmp/ubio_n* /tmp/networksim_* /tmp/barrier_* \
           /workspace/gem5/shared_ipc/ipc_* 2>/dev/null || true
    mkdir -p /workspace/gem5/shared_ipc 2>/dev/null || true

    # 1) BarrierManager (must bind before anyone connects)
    echo "[launch] BarrierManager (nodes=$NUM_NODES)..."
    "$BARRIER_BIN" "$NUM_NODES" >"${LOG_BASE}/barrier.log" 2>&1 &
    BARRIER_PID=$!
    sleep 1

    # 2) networksim (must bind before anyone connects)
    # Socket-plane topology: each (node, socket) pair is a network module with
    # global id gid = node*NUM_SOCKETS + socket. nsim is a crossbar (routes by
    # dst_module), so a full mesh among all gids (port 1) suffices for
    # connectivity; latency is uniform here (NUMA refinement is a later step).
    local TOPO="${LOG_BASE}/topo.json"
    local NMOD=$((NUM_NODES * NUM_SOCKETS))
    {
        printf '{"links": ['
        local first=1 a b
        for a in $(seq 0 $((NMOD - 1))); do
            for b in $(seq $((a + 1)) $((NMOD - 1))); do
                [ $first -eq 1 ] && first=0 || printf ','
                printf '\n  [%d, 1, %d, 1, 100000]' "$a" "$b"
            done
        done
        printf '\n]}\n'
    } > "$TOPO"
    echo "[launch] networksim (modules=$NMOD: ${NUM_NODES} nodes x ${NUM_SOCKETS} sockets)..."
    "$NSIM_BIN" "$TOPO" >"${LOG_BASE}/nsim.log" 2>&1 &
    NSIM_PID=$!
    sleep 1

    # 3) Start N per-node gem5 processes (each binds its own node's Port)
    GEM5_PIDS=""
    GEM5_PID_ARR=()
    for nid in $(seq 0 $((NUM_NODES - 1))); do
        local gdir="${LOG_BASE}/gem5_tc${TC_NUM}_node${nid}"
        local gout="${ROOT_DIR}/m5out/e2e_mp/tc${TC_NUM}/node${nid}"
        mkdir -p "$gdir" "$gout"
        echo "[launch] gem5 node=$nid (sockets=$NUM_SOCKETS)..."
        "$GEM5_BIN" \
            --outdir="$gout" \
            "$SCRIPT_DIR/test_e2e.py" --tc=${TC_NUM} \
            --node-id=${nid} --num-nodes=${NUM_NODES} --num-sockets=${NUM_SOCKETS} \
            >"${gdir}/stdout.log" 2>"${gdir}/stderr.log" &
        local pid=$!
        GEM5_PID_ARR+=($pid)
        GEM5_PIDS="$GEM5_PIDS $pid"
    done

    # Wait until ALL gem5 processes have bound their Port (STEP5 marker)
    echo "[launch] waiting for all $NUM_NODES gem5 to bind Port..."
    for nid in $(seq 0 $((NUM_NODES - 1))); do
        local gout_log="${LOG_BASE}/gem5_tc${TC_NUM}_node${nid}/stdout.log"
        local bound=0
        for i in $(seq 1 90); do
            # Each node binds one Port per socket; wait for all NUM_SOCKETS.
            if [ "$(grep -c "STEP5.*Port enabled" "$gout_log" 2>/dev/null)" -ge "$NUM_SOCKETS" ]; then
                bound=1; break
            fi
            # if this node's gem5 died early, abort
            if ! kill -0 ${GEM5_PID_ARR[$nid]} 2>/dev/null; then
                echo "[launch] gem5 node=$nid exited before binding!"
                return 1
            fi
            sleep 1
        done
        [ $bound -eq 1 ] && echo "[launch]   node=$nid bound" \
            || { echo "[launch] node=$nid bind TIMEOUT"; return 1; }
    done

    # 4) Start N*K ubio processes — one per (node, socket) plane. Each is the
    #    home directory + router for DSM(node, socket); gid = node*K + socket.
    local fault_rules
    fault_rules=$(fault_rules_for_tc "$TC_NUM")
    [ -n "$fault_rules" ] && echo "[launch] fault rules (TC${TC_NUM}): $fault_rules"
    UBIO_PIDS=""
    for nid in $(seq 0 $((NUM_NODES - 1))); do
        for sid in $(seq 0 $((NUM_SOCKETS - 1))); do
            local logdir="${LOG_BASE}/ubio_n${nid}_s${sid}"
            mkdir -p "$logdir"
            # UBCC_NUM_SOCKETS lets ubio compute gid=node*K+socket (must match
            # gem5/nsim addressing); --socket selects which DSM plane this ubio
            # is the home directory for.
            UBCC_NUM_NODES="$NUM_NODES" UBCC_NUM_SOCKETS="$NUM_SOCKETS" \
            UBIO_FAULT_RULES="$fault_rules" \
            "$UBIO_BIN" --node="$nid" --socket="$sid" \
                >"$logdir/stdout.log" 2>"$logdir/stderr.log" &
            UBIO_PIDS="$UBIO_PIDS $!"
        done
    done
    local n_ubio; n_ubio=$(echo $UBIO_PIDS | wc -w)
    echo "[launch] $n_ubio ubio (${NUM_NODES}x${NUM_SOCKETS}) + $NUM_NODES gem5 running"
    echo "[launch] total processes: $((NUM_NODES * NUM_SOCKETS + NUM_NODES + 2)) (N*K ubio + N gem5 + nsim + barrier)"
}

# ── Run single TC ──────────────────────────────────────────────────
run_tc() {
    local tc=$1
    TC_NUM=$tc
    NUM_SOCKETS=$(sockets_for_tc "$tc")
    rm -rf "${ROOT_DIR}/m5out/e2e_mp/tc${tc}"
    mkdir -p "${ROOT_DIR}/m5out/e2e_mp/tc${tc}"

    echo ""
    echo "=== TC${tc} (multi-process split: ${NUM_NODES} nodes, ${NUM_SOCKETS} sockets) ==="

    start_all || { echo "  TC${tc} LAUNCH FAILED"; return 1; }

    # Wait for all gem5 processes (with timeout)
    local timeout_val=600 waited=0 ec=0
    local alive=1
    while [ $alive -ne 0 ]; do
        alive=0
        for pid in $GEM5_PIDS; do
            if kill -0 $pid 2>/dev/null; then alive=1; fi
        done
        [ $alive -eq 0 ] && break
        sleep 1; waited=$((waited + 1))
        if [ $waited -ge $timeout_val ]; then
            echo "  TC${tc} TIMEOUT after ${timeout_val}s"
            for pid in $GEM5_PIDS; do kill $pid 2>/dev/null || true; done
            ec=124; break
        fi
    done

    # Collect each gem5 exit code
    local gem5_fail=0
    for pid in $GEM5_PIDS; do
        if wait $pid 2>/dev/null; then :; else
            local rc=$?
            [ "$rc" != "0" ] && gem5_fail=1
        fi
    done

    # Kill infra
    for pid in $UBIO_PIDS; do kill $pid 2>/dev/null || true; done
    kill ${NSIM_PID:-} ${BARRIER_PID:-} 2>/dev/null || true
    sleep 0.5

    if [ $ec -eq 124 ]; then echo "  TC${tc} TIMEOUT"; return 1; fi

    # ── Orchestrator-layer aggregated verification ─────────────────
    # If any gem5 exited non-zero, this testcase is a hard FAIL even
    # if partial simout data appears to satisfy content checks.
    if [ $gem5_fail -ne 0 ]; then
        echo "  TC${tc} CRASHED (a gem5 node exited non-zero)"
        return 1
    fi

    local simouts=()
    for nid in $(seq 0 $((NUM_NODES - 1))); do
        local f="${ROOT_DIR}/m5out/e2e_mp/tc${tc}/node${nid}/simout_n${nid}"
        # Pass expected paths (including missing ones) so verifier can
        # detect truncated/missing per-node outputs.
        simouts+=("$f")
    done

    # Collect ubio logs so the verifier can see [UBFAULT] fault-injection
    # evidence (fault injection lives in the ubio processes, one per plane).
    local faultlogs=()
    for nid in $(seq 0 $((NUM_NODES - 1))); do
        for sid in $(seq 0 $((NUM_SOCKETS - 1))); do
            faultlogs+=("${LOG_BASE}/ubio_n${nid}_s${sid}/stderr.log")
        done
    done

    local vlog="${LOG_BASE}/verify_tc${tc}.log"
    if python3 "$SCRIPT_DIR/test_e2e.py" --verify-split --tc=${tc} \
            --simout "${simouts[@]}" --fault-log "${faultlogs[@]}" >"$vlog" 2>&1; then
        echo "  TC${tc} PASSED"
        return 0
    else
        if grep -q ">>> TC${tc} FAILED <<<" "$vlog" 2>/dev/null; then
            echo "  TC${tc} FAILED"
        elif [ $gem5_fail -ne 0 ]; then
            echo "  TC${tc} CRASHED (a gem5 node exited non-zero)"
        else
            echo "  TC${tc} NO RESULT"
        fi
        echo "    verify log: $vlog"
        return 1
    fi
}

# ── Main ───────────────────────────────────────────────────────────
echo "=== Multi-Process E2E Runner (per-node gem5 split) ==="
echo "Timestamp: $TIMESTAMP   nodes=$NUM_NODES"
echo "Log base:  $LOG_BASE"
mkdir -p "$LOG_BASE"

compile_workloads
ensure_tools

TC="${1:---all}"
PASS=0; FAIL=0

run_tests() {
    for tc in "$@"; do
        if run_tc $tc; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
    done
}

if [ "$TC" == "--all" ]; then
    run_tests 1 2 3 4 5 6 7 8 10 11
else
    run_tests "$@"
fi

echo ""
echo "=== Results: $PASS pass, $FAIL fail ==="
echo "Logs: $LOG_BASE"
