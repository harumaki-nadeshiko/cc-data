#!/bin/bash
# Run inside Docker: ubcc-dev:mold
# Precondition: gem5, ubio, networksim, test ELF already compiled

set -e
ROOT_DIR=/workspace/gem5

SIZES=(4 8 256 512 1024 2048)
NOPS=(0 64 256)
TIMEOUT=300

PASSED_COMBOS=()
RESULTS_FILE="/tmp/tc2_sweep_results.txt"
> "$RESULTS_FILE"

run_one() {
    local skb=$1 nops=$2
    local tag="sz${skb}_n${nops}"
    echo "=== [$tag] start $(date +%H:%M:%S) ==="
    
    export EVICT_SIZE_KB="$skb" EVICT_NOPS="$nops"
    
    timeout ${TIMEOUT} bash "$ROOT_DIR/tests/e2e/run_multi.sh" 2 > "/tmp/tc2_${tag}.log" 2>&1
    local rc=$?
    
    if [ $rc -eq 124 ]; then
        echo "[$tag] TIMEOUT" | tee -a "$RESULTS_FILE"
        return
    fi
    
    if grep -q "TC2 PASSED" "/tmp/tc2_${tag}.log" 2>/dev/null; then
        echo "[$tag] PASSED" | tee -a "$RESULTS_FILE"
        PASSED_COMBOS+=("$skb $nops")
    elif grep -q "TC2 FAILED" "/tmp/tc2_${tag}.log" 2>/dev/null; then
        echo "[$tag] FAILED" | tee -a "$RESULTS_FILE"
    else
        echo "[$tag] UNKNOWN" | tee -a "$RESULTS_FILE"
    fi
}

echo "=== TC2 Sweep: ${#SIZES[@]} sizes × ${#NOPS[@]} nops ==="
echo ""

for skb in "${SIZES[@]}"; do
    for nops in "${NOPS[@]}"; do
        run_one "$skb" "$nops"
    done
done

echo ""
echo "=== Summary ==="
cat "$RESULTS_FILE"
echo ""
if [ ${#PASSED_COMBOS[@]} -gt 0 ]; then
    echo "PASSED combinations:"
    for c in "${PASSED_COMBOS[@]}"; do echo "  $c"; done
else
    echo "No PASSED combinations."
fi
