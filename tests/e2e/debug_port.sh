#!/bin/bash
# Quick multi-process debug test
set -e
MODDIR=/workspace/gem5/modules/ubiomodule
EPDIR=$MODDIR/mem/ruby/protocol/chi/ep
for f in BackstoreSchemaA BackstoreSchemaC BackstoreOrganization BackstoreTypes; do
    [ -f "$MODDIR/${f}.hh" ] && ln -sf "$MODDIR/${f}.hh" "$EPDIR/${f}.hh" 2>/dev/null
done
SRCS="$MODDIR/UBCCController.cc $MODDIR/ResidentDir.cc $MODDIR/BackstoreSchemaA.cc $MODDIR/BackstoreSchemaC.cc"
g++ -std=c++17 -O0 -g -I$MODDIR -I$MODDIR/mem/ruby -I/workspace/gem5 -I/workspace/gem5/thirdparty/zeromq/include \
    /workspace/gem5/tools/ubio/ubio_main.cc /workspace/gem5/framework/Port.cc $SRCS \
    -L/workspace/gem5/thirdparty/zeromq/lib -lzmq -lpthread -o /tmp/ubio 2>/dev/null
echo "[build] ok"

rm -f /tmp/ubio_n0 /tmp/ubio_n1 /tmp/ubio_n2
/tmp/ubio --gem5-ep="ipc:///tmp/ubio_n0" --node=0 2>/tmp/ubio_dbg.log 1>/dev/null &
UBIO_PID=$!
sleep 1

UBIO_PORT_ENABLE=-1 timeout 20 /workspace/gem5/gem5/build/ARM/gem5.opt \
    --outdir=/tmp/qtest --maxtick=80000000 \
    /workspace/gem5/tests/e2e/test_e2e.py --tc=1 >/dev/null 2>&1 || true

kill $UBIO_PID 2>/dev/null; wait 2>/dev/null
echo "=== ubio debug ($(wc -l < /tmp/ubio_dbg.log) lines) ==="
cat /tmp/ubio_dbg.log | head -30
