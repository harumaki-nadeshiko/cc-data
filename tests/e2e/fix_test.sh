#!/bin/bash
# Fixed multi-test: gem5 binds first, ubio connects, dontwait sends
set -e
rm -f /tmp/ubio_n0 /tmp/ubio_n1 /tmp/ubio_n2

# Build ubio
MODDIR=/workspace/gem5/modules/ubiomodule
EPDIR=$MODDIR/mem/ruby/protocol/chi/ep
for f in BackstoreSchemaA BackstoreSchemaC BackstoreOrganization BackstoreTypes; do
    [ -f "$MODDIR/${f}.hh" ] && ln -sf "$MODDIR/${f}.hh" "$EPDIR/${f}.hh" 2>/dev/null
done
SRCS="$MODDIR/UBCCController.cc $MODDIR/ResidentDir.cc $MODDIR/BackstoreSchemaA.cc $MODDIR/BackstoreSchemaC.cc"
g++ -std=c++17 -O0 -g -I$MODDIR -I$MODDIR/mem/ruby -I/workspace/gem5 -I/workspace/gem5/thirdparty/zeromq/include \
    /workspace/gem5/tools/ubio/ubio_main.cc /workspace/gem5/framework/Port.cc $SRCS \
    -L/workspace/gem5/thirdparty/zeromq/lib -lzmq -lpthread -o /tmp/ubio 2>/dev/null

# Start gem5 FIRST (it binds)
echo "=== Starting gem5 (binds) ==="
UBIO_PORT_ENABLE=-1 /workspace/gem5/gem5/build/ARM/gem5.opt \
    --outdir=/tmp/mp_fix --maxtick=80000000 \
    /workspace/gem5/tests/e2e/test_e2e.py --tc=1 \
    >/tmp/gem5fix.log 2>/tmp/gem5fix_err.log &
GEM5_PID=$!
sleep 3  # wait for gem5 to bind

# Start ubio (connects to bound endpoint)
echo "=== Starting ubio ==="
/tmp/ubio --gem5-ep="ipc:///tmp/ubio_n0" --node=0 2>/tmp/ubio_fix.log 1>/dev/null &
UBIO_PID=$!

# Wait for gem5 to finish or timeout
sleep 25
echo "=== ubio state ($(wc -l < /tmp/ubio_fix.log) lines) ==="
cat /tmp/ubio_fix.log | tail -20

kill $GEM5_PID $UBIO_PID 2>/dev/null; wait 2>/dev/null
echo "=== done ==="
