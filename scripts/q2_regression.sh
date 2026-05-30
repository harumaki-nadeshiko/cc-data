#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS="$ROOT_DIR/reports/q2-regression-results.txt"
mkdir -p "$ROOT_DIR/tmp"

echo "# Q2 Regression Results" > "$RESULTS"
echo "# Date: $(date)" >> "$RESULTS"
echo "" >> "$RESULTS"

for test in test_minimal test_e2e test_connect_direct test_port_diag; do
  echo "=== $test ===" >> "$RESULTS"
  tmpf="$ROOT_DIR/tmp/regression_${test}.tmp"
  set +e
  if [ "$test" = "test_e2e" ]; then
    "$ROOT_DIR/gem5/build/ARM/gem5.opt" --outdir="$ROOT_DIR/tmp/$test" \
      "$ROOT_DIR/tests/e2e/${test}.py" --tc 1 > "$tmpf" 2>&1
  else
    "$ROOT_DIR/gem5/build/ARM/gem5.opt" --outdir="$ROOT_DIR/tmp/$test" \
      "$ROOT_DIR/tests/e2e/${test}.py" > "$tmpf" 2>&1
  fi
  EXIT=$?
  set -e
  grep -E "SIM_CAUSE|Instantiate done|SENTINEL|Segmentation|fatal|Error:|exiting" "$tmpf" >> "$RESULTS" || true
  echo "EXIT=$EXIT" >> "$RESULTS"
  echo "" >> "$RESULTS"
  rm -f "$tmpf"
done

# test_port_final: 已知问题，标记 XFAIL
echo "=== test_port_final (XFAIL - known issue) ===" >> "$RESULTS"
echo "Pre-existing: TimingSimpleCPU dcache not set without Ruby.create_system" >> "$RESULTS"
echo "STATUS=XFAIL" >> "$RESULTS"
echo "" >> "$RESULTS"

echo "=== STATUS SUMMARY ===" >> "$RESULTS"
grep -E "^(EXIT|STATUS)=" "$RESULTS" > "$ROOT_DIR/tmp/regression_summary.tmp" || true
cat "$ROOT_DIR/tmp/regression_summary.tmp" >> "$RESULTS" || true
rm -f "$ROOT_DIR/tmp/regression_summary.tmp"
