#!/bin/bash
# Run TLC on a TLA+ model and clean up generated artifacts
# Usage: ./run_tlc.sh <model.tla> <config.cfg> [timeout_secs]
set -eo pipefail

MODEL="${1:?need model.tla}"
CFG="${2:?need config.cfg}"
TIMEOUT="${3:-120}"
WORKERS="${TLC_WORKERS:-8}"

DIR="$(cd $(dirname $0) && pwd)"
JAR="$DIR/tla2tools.jar"
MODEL_BASE="${MODEL%.tla}"
META_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tlc_${MODEL_BASE}_XXXXXX")"
LOG="${TLC_LOG:-${TMPDIR:-/tmp}/tlc_${MODEL_BASE}_$$.log}"

cleanup() {
  rm -rf "$META_DIR"
}
trap cleanup EXIT

cd "$DIR"

echo "=== TLC: $MODEL ==="
timeout "$TIMEOUT" java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC \
  -config "$CFG" -workers "$WORKERS" -metadir "$META_DIR" \
  -noGenerateSpecTE -teSpecOutDir "$META_DIR" "$MODEL" \
  2>&1 | tee "$LOG"

echo "=== Done. Log: $LOG ==="
