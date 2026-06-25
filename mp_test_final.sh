#!/bin/bash
# Multi-process test with send timeout fix
set -e
rm -f /tmp/ubio_n0 /tmp/ubio_n1 /tmp/ubio_n2

# Build both
MODDIR=/workspace/gem5/modules/ubiomodule
EPDIR=$MODDIR/mem/ruby/protocol/chi/ep
for f in BackstoreSchemaA BackstoreSchemaC BackstoreOrganization BackstoreTypes; do
    [ -f "$MODDIR/${f}.hh" ] && ln -sf "$MODDIR/${f}.hh" "$EPDIR/${f}.hh" 2>/dev/null
done
SRCS="$MODDIR/UBCCController.cc $MODDIR/ResidentDir.cc $MODDIR/BackstoreSchemaA.cc $MODDIR/BackstoreSchemaC.cc"
g++ -std=c++17 -O0 -g -I$MODDIR -I$MODDIR/mem/ruby -I/workspace/gem5 -I/workspace/gem5/thirdparty/zeromq/include \
    /workspace/gem5/tools/ubio/ubio_main.cc /workspace/gem5/framework/Port.cc $SRCS \
    -L/workspace/gem5/thirdparty/zeromq/lib -lzmq -lpthread -o /tmp/ubio 2>/dev/null

# Start gem5 (binds), wait for Port creation
UBIO_PORT_ENABLE=-1 /workspace/gem5/gem5/build/ARM/gem5.opt \
    --outdir=/tmp/mp_ok --maxtick=80000000 \
    /workspace/gem5/tests/e2e/test_e2e.py --tc=1 \
    >/tmp/mp_gem5_out.log 2>/tmp/mp_gem5_err.log &
GEM5_PID=$!

echo "Waiting for gem5 to bind..."
for i in $(seq 1 60); do
    if grep -q "STEP5.*Port enabled" /tmp/mp_gem5_err.log 2>/dev/null; then
        echo "Gem5 ready after ${i}s"
        break
    fi
    sleep 1
done

# Start ubio n0 (connects)
/tmp/ubio --gem5-ep="ipc:///tmp/ubio_n0" --node=0 2>/tmp/mp_ubio.log 1>/dev/null &
UBIO_PID=$!
echo "Ubio started, pid=$UBIO_PID"

# Monitor: check for bidirectional communication
for i in $(seq 1 30); do
    sleep 1
    U_LINES=$(wc -l < /tmp/mp_ubio.log)
    G_RECV=$(grep -c "PORT-RECV-GOT" /tmp/mp_gem5_err.log 2>/dev/null || echo 0)
    echo "t+${i}s: ubio=${U_LINES} lines, gem5_recv=${G_RECV}"
    if grep -q "RECV-GOT" /tmp/mp_ubio.log 2>/dev/null; then
        echo "*** UBIO RECEIVED MESSAGE FROM GEM5! ***"
        break
    fi
done

echo "=== ubio log tail ==="
tail -20 /tmp/mp_ubio.log
echo "=== gem5 recv ==="
grep "PORT-RECV-GOT" /tmp/mp_gem5_err.log | head -5

kill $GEM5_PID $UBIO_PID 2>/dev/null; wait 2>/dev/null
