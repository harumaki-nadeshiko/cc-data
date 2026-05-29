#!/bin/bash
# E2E Test Runner — compile all workloads and run TC1-TC4
# Usage: ./tests/e2e/run_all_e2e.sh [--tc N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKLOAD_DIR="${SCRIPT_DIR}/workloads"
GEM5_BIN="${SCRIPT_DIR}/../../gem5/build/ARM/gem5.opt"

if [ ! -x "${GEM5_BIN}" ]; then
    echo "ERROR: gem5 binary not found: ${GEM5_BIN}"
    exit 1
fi

# ── Compile workloads ────────────────────────────────────────────
echo "=== Compiling E2E workloads ==="
CC="aarch64-linux-gnu-gcc"
CFLAGS="-static -O0 -g -I${WORKLOAD_DIR}"

for src in "${WORKLOAD_DIR}"/e2e_tc[1-9]*_*.c e2e_tc10_*.c; do
    if [ ! -f "$src" ]; then continue; fi
    base=$(basename "$src" .c)
    elf="${WORKLOAD_DIR}/${base}.elf"
    if [ "$src" -nt "$elf" ] 2>/dev/null || [ ! -f "$elf" ]; then
        echo "  Compiling: $base"
        ${CC} ${CFLAGS} -o "$elf" "$src"
    else
        echo "  Up-to-date: $base"
    fi
done

echo ""
echo "=== Running E2E Tests ==="

TC="${1:---all}"

if [ "$TC" == "--all" ]; then
    # Run all TC1-TC10
    for tc in 1 2 3 4 5 6 7 8 9 10; do
        echo ""
        echo "--- TC${tc} ---"
        "${GEM5_BIN}" --outdir="m5out/e2e/tc${tc}" \
            "${SCRIPT_DIR}/test_e2e.py" --tc=${tc}
    done
elif [[ "$TC" =~ ^[1-9]$|^10$ ]]; then
    echo ""
    echo "--- TC${TC} ---"
    "${GEM5_BIN}" --outdir="m5out/e2e/tc${TC}" \
        "${SCRIPT_DIR}/test_e2e.py" --tc=${TC}
else
    echo "Usage: $0 [--all | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10]"
    echo "  Default: --all"
    exit 1
fi

echo ""
echo "Done."
