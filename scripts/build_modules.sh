#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOD="$ROOT/modules/ubiomodule"
for f in BackstoreSchemaA BackstoreSchemaC BackstoreOrganization BackstoreTypes; do
    [ -f "$MOD/${f}.hh" ] && ln -sf "$MOD/${f}.hh" "$MOD/mem/ruby/protocol/chi/ep/${f}.hh" 2>/dev/null
done
CXXFLAGS="-std=c++17 -O2 -I$MOD -I$MOD/mem/ruby -I$ROOT -I$ROOT/thirdparty/zeromq/include"
LDFLAGS="-L$ROOT/thirdparty/zeromq/lib -lzmq -lpthread"
PSRC="$ROOT/framework/Port.cc"
echo "[build] ubio"
SRCS="$MOD/UBCCController.cc $MOD/ResidentDir.cc $MOD/BackstoreSchemaA.cc $MOD/BackstoreSchemaC.cc $MOD/NodeAddressMap.cc"
g++ $CXXFLAGS $MOD/ubio_main.cc $PSRC $SRCS $LDFLAGS -o /tmp/ubio.elf
echo "[build] networksim"
g++ $CXXFLAGS $ROOT/modules/networksim/networksim_main.cc $PSRC $LDFLAGS -o "$ROOT/modules/networksim/networksim"
echo "[build] barrier_manager"
mkdir -p "$ROOT/modules/barrier"
g++ $CXXFLAGS $ROOT/modules/barrier/barrier_main.cc $PSRC $LDFLAGS -o "$ROOT/modules/barrier/barrier_manager"
echo "[build] done"
ls -lh /tmp/ubio.elf "$ROOT/modules/networksim/networksim" "$ROOT/modules/barrier/barrier_manager"
