#!/bin/bash
# Build barrier_manager binary, consuming libframework.a.
# Produces: build/bin/barrier_manager
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FW_LIB="$ROOT/build/framework/lib/libframework.a"
FW_INC="$ROOT/build/framework/include"
ZMQ_INC="$ROOT/thirdparty/zeromq/include"
ZMQ_LIB="$ROOT/thirdparty/zeromq/lib"
OUT="$ROOT/build/bin"

[ -f "$FW_LIB" ] || { echo "ERROR: $FW_LIB missing. Run scripts/build_framework.sh first." >&2; exit 1; }
mkdir -p "$OUT"

CXXFLAGS="-std=c++17 -O2 -Wall -pthread -I$FW_INC -I$ROOT -I$ZMQ_INC"
LDFLAGS="-L$ZMQ_LIB -lzmq -lpthread"

g++ $CXXFLAGS "$ROOT/tools/barrier/barrier_main.cc" "$FW_LIB" $LDFLAGS -o "$OUT/barrier_manager"
echo "[build_barrier] $(ls -lh "$OUT/barrier_manager" | awk '{print $5}') -> $OUT/barrier_manager"
