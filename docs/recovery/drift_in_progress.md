# Clock Drift Diagnosis — In Progress (2026-06-28)

## State
- Port syncWindow merged into syncInterval (default 100000 ticks = 100ns).
- Port linkLatency default = 10000 ticks = 10ns.
- nsim internal latency = 100000 ticks = 100ns (topo3.json).
- EPSNF retry = 1,600,000 ticks.
- safeTs clamp implemented: when safeTs <= curTick, schedule at curTick() (no +syncInterval bypass).

## Observations
- Message timestamps are perfectly coherent end-to-end (0.27M total delay for 2×nsim+6×ZMQ).
- But gem5 curTick drifts 3.3M ahead of ubio during the same wall-clock interval.
- After clamp fix, node 2 loops at `CLK-SYNC curT=243M rxt=243M safeT=243M` identically.
- EPSNF retry at 76.3M never fires because gem5 stuck at 75.1M.

## Open
- Why doesn't safeTs advance past curTick when peer is at 264M? _lastRxT should carry 264M.
