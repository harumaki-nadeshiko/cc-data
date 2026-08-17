#!/bin/bash
# Build and run the independent HA core without gem5 or UBIO.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${HA_HOST_TEST_OUT:-/tmp/cc-ep-ha-controller-tests}"
CXX="${CXX:-g++}"
mkdir -p "$OUT_DIR"

COMMON=(
    "$ROOT_DIR/modules/hamodule/FlatBitmapDirectory.cc"
    "$ROOT_DIR/modules/hamodule/HAController.cc"
)
CXXFLAGS=(-std=c++17 -O2 -Wall -Wextra -Werror -pedantic -I"$ROOT_DIR")

"$CXX" "${CXXFLAGS[@]}" "$ROOT_DIR/tools/ha_controller_reference_test.cc" \
    "${COMMON[@]}" -o "$OUT_DIR/ha_controller_reference_test"
"$OUT_DIR/ha_controller_reference_test"

"$CXX" "${CXXFLAGS[@]}" "$ROOT_DIR/tools/ha_controller_manifest_main.cc" \
    "$ROOT_DIR/modules/hamodule/FlatBitmapDirectory.cc" \
    -o "$OUT_DIR/ha_controller_manifest"

HA_CONTROLLER_MANIFEST_BIN="$OUT_DIR/ha_controller_manifest" \
    python3 "$ROOT_DIR/tests/ha_controller/test_startup_manifest.py"
