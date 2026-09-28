#!/bin/bash
# Execute only in ubcc-dev:ubuntu20.04 --network none.
set -euo pipefail
ROOT=/mnt/data2/cgc/cc-ep-ha-node-20260909
cd "$ROOT"
bash scripts/build_all.sh > ha-evidence/native-final-build.log 2>&1
# Absolute output target avoids scons -C interpreting a relative target against
# the invocation directory. All outputs remain in this isolated worktree.
scons -C gem5 "$ROOT/build/ARM/gem5.opt" -j16 > ha-evidence/gem5-final-build.log 2>&1
mkdir -p gem5/build
test -L gem5/build/ARM || ln -s ../../build/ARM gem5/build/ARM
sha256sum build/bin/ubio build/bin/networksim build/bin/barrier_manager \
    gem5/build/ARM/gem5.opt > ha-evidence/gate-binary-sha256.txt
EP_HA_PROFILE=ha-vi OURCC_CLEAR_PROFILE=ack EP_CPU_MODEL=o3 \
EP_SEQUENCER_MAX_OUTSTANDING=16 EP_L3_SIZE=256KiB EP_L3_ASSOC=16 \
L3_PRESSURE_LEVEL=0 EP_TRACE_PERF=sample TIMEOUT_SEC=300 \
E2E_RUN_ID=ha_node_tc228_p0_g4 LOG_BASE="$ROOT/ha-evidence/tc228-p0-g4" \
GEM5_DEBUG_FLAGS=RubyEP GEM5_DEBUG_START=46000000 \
    bash tests/e2e/run_multi.sh --2n1s 228 > ha-evidence/gate-runner-g4.log 2>&1
