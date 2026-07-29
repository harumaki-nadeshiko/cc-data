#!/bin/bash
# Detach the host-side profile matrix while retaining a durable runner log.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_TAG="${RUN_TAG:-tc90_perf_matrix_$(date +%Y%m%d)}"
LOG_ROOT="${LOG_ROOT:-$ROOT_DIR/logs/$RUN_TAG}"
mkdir -p "$LOG_ROOT"
exec bash "$ROOT_DIR/scripts/run_tc90_perf_matrix.sh" \
    >"$LOG_ROOT/matrix_runner.log" 2>&1
