#!/bin/bash
# Build and run the host-only ResidentDir capacity waiter liveness regression.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${CAPACITY_WAITER_TEST_OUT:-/tmp/cc-ep-capacity-waiter-test}"
mkdir -p "$OUT_DIR"

g++ -std=c++17 -O2 -I"$ROOT_DIR" \
    "$ROOT_DIR/tools/capacity_waiter_liveness_test.cc" \
    "$ROOT_DIR/modules/ubiomodule/UBCCController.cc" \
    "$ROOT_DIR/modules/ubiomodule/ResidentDir.cc" \
    "$ROOT_DIR/modules/ubiomodule/BackstoreSchemaH64.cc" \
    "$ROOT_DIR/modules/ubiomodule/NodeAddressMap.cc" \
    "$ROOT_DIR/framework/Log.cc" \
    -o "$OUT_DIR/capacity_waiter_liveness_test"

"$OUT_DIR/capacity_waiter_liveness_test"
