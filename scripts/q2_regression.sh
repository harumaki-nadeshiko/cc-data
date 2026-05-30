#!/usr/bin/env bash
set -euo pipefail

REPORT="/workspace/reports/q2-regression-results.txt"
OUTDIR_BASE="/workspace/tmp"

rm -f "$REPORT"
mkdir -p "$OUTDIR_BASE"

for test_name in test_minimal test_e2e test_connect_direct test_port_diag test_port_final; do
  echo "=== $test_name ===" >> "$REPORT"
  
  TEST_SCRIPT="/workspace/tests/e2e/${test_name}.py"
  OUTDIR="/workspace/tmp/${test_name}"
  mkdir -p "$OUTDIR"

  TC_FLAG=""
  if [ "$test_name" = "test_e2e" ]; then
    TC_FLAG="--tc 1"
  fi

  taskset -c 0-31 /workspace/gem5/build/ARM/gem5.opt \
    --outdir="$OUTDIR" \
    "$TEST_SCRIPT" \
    $TC_FLAG 2>&1 | \
    grep -E "SIM_CAUSE|Instantiate done|SENTINEL|Segmentation|fatal" >> "$REPORT" || true

  EXIT_VAL=${PIPESTATUS[0]}
  echo "EXIT=$EXIT_VAL" >> "$REPORT"
  echo "" >> "$REPORT"
done

echo "Regression run complete. Results in $REPORT"
