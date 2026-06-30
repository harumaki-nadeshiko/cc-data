#!/bin/bash
# Legacy single-process baseline runner (one gem5 builds ALL nodes).
# Used to confirm the env-based num_nodes refactor didn't regress the
# pre-split behavior. node-id defaults to -1 (build all nodes).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
GEM5_BIN="$ROOT_DIR/gem5/build/ARM/gem5.opt"
UBIO_BIN="$ROOT_DIR/build/bin/ubio"
NSIM_BIN="$ROOT_DIR/build/bin/networksim"
BARRIER_BIN="$ROOT_DIR/build/bin/barrier_manager"
TS=$(date +%Y%m%d_%H%M%S); LOG="$ROOT_DIR/logs/legacy_$TS"; mkdir -p "$LOG"
TC="${1:-2}"
rm -rf /workspace/gem5/shared_ipc/ipc_* 2>/dev/null || true
mkdir -p /workspace/gem5/shared_ipc
PIDS=""
cleanup(){ for p in $PIDS; do kill $p 2>/dev/null||true; done; }
trap cleanup EXIT
"$BARRIER_BIN" 3 >"$LOG/barrier.log" 2>&1 & PIDS="$PIDS $!"; sleep 1
"$NSIM_BIN" "$ROOT_DIR/tools/networksim/topo3.json" >"$LOG/nsim.log" 2>&1 & PIDS="$PIDS $!"; sleep 1
OUT="$ROOT_DIR/m5out/legacy/tc$TC"; mkdir -p "$OUT"
UBIO_PORT_ENABLE=-1 "$GEM5_BIN" --outdir="$OUT" "$SCRIPT_DIR/test_e2e.py" --tc=$TC \
    >"$LOG/gem5.log" 2>"$LOG/gem5.err" & GPID=$!; PIDS="$PIDS $GPID"
for i in $(seq 1 90); do
    grep -q "STEP5.*Port enabled" "$LOG/gem5.log" 2>/dev/null && break
    kill -0 $GPID 2>/dev/null || { echo "gem5 died early"; tail -5 "$LOG/gem5.err"; exit 1; }
    sleep 1
done
for nid in 0 1 2; do
    "$UBIO_BIN" --node=$nid >"$LOG/ubio_n$nid.log" 2>&1 & PIDS="$PIDS $!"
done
echo "legacy: waiting for gem5 (tc$TC)..."
w=0; while kill -0 $GPID 2>/dev/null; do sleep 1; w=$((w+1)); [ $w -ge 300 ] && { echo "TIMEOUT"; break; }; done
wait $GPID 2>/dev/null; ec=$?
grep -q "PASSED" "$LOG/gem5.log" && echo "  TC$TC PASSED" || { echo "  TC$TC FAILED/NORESULT (ec=$ec)"; grep -E ">>> TC|FAILED|PASSED" "$LOG/gem5.log" | tail -3; }
echo "log: $LOG"
