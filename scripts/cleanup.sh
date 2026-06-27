#!/bin/bash
for name in gem5.opt ubio.elf networksim barrier_manager ub.elf ns.elf barrier.elf; do
    pgrep -f "$name" | xargs -r kill 2>/dev/null
done
sleep 1
rm -rf /tmp/ubio_n* /tmp/networksim_* /tmp/barrier_* 2>/dev/null
echo "[cleanup] done"
