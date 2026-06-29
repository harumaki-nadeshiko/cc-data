#!/bin/bash
# Build ubio binary, consuming libframework.a.
# Prerequisite: build/framework/lib/libframework.a (run build_framework.sh first).
# Produces: build/bin/ubio
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FW_LIB="$ROOT/build/framework/lib/libframework.a"
FW_INC="$ROOT/build/framework/include"
MOD="$ROOT/modules/ubiomodule"
ZMQ_INC="$ROOT/thirdparty/zeromq/include"
ZMQ_LIB="$ROOT/thirdparty/zeromq/lib"
OUT="$ROOT/build/bin"

[ -f "$FW_LIB" ] || { echo "ERROR: $FW_LIB missing. Run scripts/build_framework.sh first." >&2; exit 1; }
mkdir -p "$OUT"

CXXFLAGS="-std=c++17 -O2 -Wall -pthread -I$MOD -I$MOD/mem/ruby -I$FW_INC -I$ROOT -I$ZMQ_INC"
LDFLAGS="-L$ZMQ_LIB -lzmq -lpthread"

SRCS="$MOD/UBCCController.cc $MOD/ResidentDir.cc $MOD/BackstoreSchemaA.cc $MOD/BackstoreSchemaC.cc $MOD/NodeAddressMap.cc"

g++ $CXXFLAGS "$ROOT/tools/ubio/ubio_main.cc" $SRCS "$FW_LIB" $LDFLAGS -o "$OUT/ubio"
echo "[build_ubio] $(ls -lh "$OUT/ubio" | awk '{print $5}') -> $OUT/ubio"
