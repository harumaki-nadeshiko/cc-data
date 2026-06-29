#!/bin/bash
# Build all native binaries (ubio, networksim, barrier_manager).
# Prerequisite: libframework.a (auto-runs build_framework.sh if missing).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"

[ -f "$ROOT/build/framework/lib/libframework.a" ] || bash "$DIR/build_framework.sh"
bash "$DIR/build_ubio.sh"
bash "$DIR/build_networksim.sh"
bash "$DIR/build_barrier.sh"
echo "[build_all] done -> build/bin/{ubio,networksim,barrier_manager}"
