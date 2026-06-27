#!/bin/bash
# Kill all CC-EP processes and clean IPC endpoints
echo "[clean] Killing stale processes..."
for name in gem5.opt ubio.elf networksim barriermanager barrier_manager ub.elf ns.elf barrier.elf; do
    pids=$(pgrep -f "$name" 2>/dev/null || true)
    [ -n "$pids" ] && kill $pids 2>/dev/null && echo "  killed $name ($pids)"
done
sleep 1
echo "[clean] Removing IPC endpoints..."
rm -rf /tmp/ubio_n* /tmp/networksim_* /tmp/barrier_* 2>/dev/null
echo "[clean] Done"
