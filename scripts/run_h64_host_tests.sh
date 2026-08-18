#!/bin/bash
# Build and run the host-only H64 correctness suites without gem5.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${H64_HOST_TEST_OUT:-/tmp/cc-ep-h64-tests}"
mkdir -p "$OUT_DIR"

CXXFLAGS="-std=c++17 -O2 -I$ROOT_DIR"
COMMON="$ROOT_DIR/modules/ubiomodule/UBCCController.cc $ROOT_DIR/modules/ubiomodule/ResidentDir.cc $ROOT_DIR/modules/ubiomodule/BackstoreSchemaH64.cc $ROOT_DIR/modules/ubiomodule/NodeAddressMap.cc $ROOT_DIR/framework/Log.cc"

g++ $CXXFLAGS "$ROOT_DIR/tools/resident_dir_n16_test.cc" \
    "$ROOT_DIR/modules/ubiomodule/ResidentDir.cc" \
    "$ROOT_DIR/modules/ubiomodule/BackstoreSchemaH64.cc" \
    "$ROOT_DIR/framework/Log.cc" \
    -o "$OUT_DIR/resident_dir_n16_test"
"$OUT_DIR/resident_dir_n16_test"

g++ $CXXFLAGS "$ROOT_DIR/tools/h64_host_phase3_test.cc" \
    "$ROOT_DIR/modules/ubiomodule/BackstoreHostH64.cc" \
    "$ROOT_DIR/modules/ubiomodule/BackstoreSchemaH64.cc" \
    "$ROOT_DIR/framework/Log.cc" \
    -o "$OUT_DIR/h64_host_phase3_test"
"$OUT_DIR/h64_host_phase3_test"

g++ $CXXFLAGS "$ROOT_DIR/tools/h64_joint_bloom_rebuild_test.cc" $COMMON \
    -o "$OUT_DIR/h64_joint_bloom_rebuild_test"
"$OUT_DIR/h64_joint_bloom_rebuild_test"
