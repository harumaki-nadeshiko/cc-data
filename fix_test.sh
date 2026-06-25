#!/bin/bash
# Final multi-test: send timeout + gem5 binds first + long init wait
set -e
rm -f /tmp/ubio_n0

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
echo "[build] ubio OK"

# Start gem5 first (binds), wait for init
echo "[start] gem5..."
UBIO_PORT_ENABLE=-1 /workspace/gem5/gem5/build/ARM/gem5.opt \
    --outdir=/tmp/mp_final --maxtick=80000000 \
    /workspace/gem5/tests/e2e/test_e2e.py --tc=1 >/dev/null 2>/tmp/gem5_err.log &
GEM5_PID=$!

# Wait for gem5 to initialize (STEP5 messages indicate Port is created)
echo "[wait] gem5 init..."
for i in $(seq 1 30); do
    if grep -q "STEP5.*Port enabled" /tmp/gem5_err.log 2>/dev/null; then
        echo "[wait] gem5 ready after ${i}s"
        break
    fi
    sleep 1
done

# Start ubio (connects to already-bound endpoint)
echo "[start] ubio..."
/tmp/ubio --gem5-ep="ipc:///tmp/ubio_n0" --node=0 2>/tmp/ubio_final.log 1>/dev/null &
UBIO_PID=$!

# Run for a while
sleep 20

echo "=== ubio debug ($(wc -l < /tmp/ubio_final.log) lines) ==="
grep -E "RECV-GOT|RECV-DBG|UBIO-LOOP|SEND-ERR|ubio.*recv" /tmp/ubio_final.log | tail -30

echo "=== gem5 recv ==="
grep -E "PORT-RECV-GOT|PORT-RECV-DBG" /tmp/gem5_err.log | tail -10

kill $GEM5_PID $UBIO_PID 2>/dev/null; wait 2>/dev/null
echo "[done]"
