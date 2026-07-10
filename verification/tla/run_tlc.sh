#!/bin/bash
# Run TLC on a TLA+ model and clean up generated artifacts
# Usage: ./run_tlc.sh <model.tla> <config.cfg> [timeout_secs]
set -e

MODEL="${1:?need model.tla}"
CFG="${2:?need config.cfg}"
TIMEOUT="${3:-120}"

DIR="$(cd $(dirname $0) && pwd)"
JAR="$DIR/tla2tools.jar"

cd "$DIR"

# Clean stale artifacts before run
rm -f *TTrace_*.tla *TTrace_*.bin
rm -rf states/

echo "=== TLC: $MODEL ==="
timeout "$TIMEOUT" java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC \
  -config "$CFG" -workers "$(nproc)" "$MODEL" 2>&1 | tee "/tmp/tlc_${MODEL%.tla}.log"

# Clean after run
rm -f *TTrace_*.tla *TTrace_*.bin
rm -rf states/

echo "=== Done. Cleaned artifacts. ==="
