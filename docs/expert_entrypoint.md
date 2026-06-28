# Expert Entrypoint — Clock Sync & Multi-Process E2E Integration

## 1. Project Overview

**cc-ep** is a gem5-based full-system cache-coherence simulator where the coherence protocol
(EP/EPSNF) is implemented across multiple processes:

| Process | Role | Source |
|---------|------|--------|
| **gem5** | CPU simulation + Ruby cache hierarchy + EPSNF controller | `gem5/src/mem/ruby/protocol/chi/ep/` |
| **ubio** (×3) | UBCC directory + backstore, one per node | `tools/ubio/ubio_main.cc` |
| **networksim** (nsim) | Cross-node message routing with configurable latency | `modules/networksim/networksim_main.cc` |
| **barrier** | Cross-node barrier coordination | `tools/barrier/barrier_main.cc` |

Processes communicate via **ZMQ IPC PAIR sockets** through the `framework/Port` abstraction.
All time synchronisation uses CONTROL_SYNC heartbeat messages and the `safeTs` sliding window.

## 2. Target Architecture Reference

`docs/all.cpp` is the reference target architecture for the time-sync subsystem.
Key patterns from that reference:

- **Adapter::syncEvent** — a periodic event that calls `processSyncAndReceive()`.
  Scheduled in `startup()` at `curTick()`. Every cycle:
  1. `emitSync(t)` — sends CONTROL_SYNC with local time
  2. `waitForUbsimAdvance(t)` — polls ZMQ until `safeTs > curT`, then returns next safe tick
  3. `schedule(syncEvent, safeT)` — re-schedules at the safe bound

- **waitForUbsimAdvance** — busy-waits: calls `pollAbs`, checks `safeTs(curT)`.
  If `safeT <= curT`, yields thread. When `safeT > curT`, returns that value.
  This is the core mechanism that keeps gem5 from advancing past the peer's clock.

- **BasePort::receive** — future messages (`t > curT`) are cached in `pendingRx`.
  Past/current messages are delivered immediately. The `receiveTimestamp()` returns
  `pending ? pendingT : lastRxT`.

- **TimeSync::safeTs** — returns `min(receiveTimestamp(), lastSyncTs + syncInterval)`.
  The smaller of the peer's latest timestamp and own last sync + window.

## 3. Key Files

### Framework (shared by all processes)
| File | Role |
|------|------|
| `framework/Port.hh` | Port class — duplex ZMQ, `sendAllocateBuffer`, `send`, `recv`, `emitSync`, `safeTs` |
| `framework/Port.cc` | Implementation — `_lastRxT`, `_lastSyncTs`, `_syncInterval`, `_linkLatency` |

### gem5 side
| File | Role |
|------|------|
| `gem5/src/.../ep/UBAdapter.{hh,cc}` | Port wrapper in gem5. `_responseCheckEvent` periodic wakeup. `wakeup()` polls Port + checkResponseCallbacks. |
| `gem5/src/.../ep/EPSNFController.{hh,cc}` | EPSNF state machine. `wakeup()` processes retry queue + calls `_backend->wakeup()`. Retry via `scheduleEvent`. |
| `gem5/src/.../ep/EPBackend.{hh,cc}` | Backend logic: `handleRemoteMiss`, `sendClear`, `handleGrant`. Owns UBAdapter refs. `wakeup()` polls all adapters. |
| `gem5/src/.../system/Sequencer.py` | `deadlock_threshold` parameter |

### Native side
| File | Role |
|------|------|
| `tools/ubio/ubio_main.cc` | ubio main loop: `emitSync` → `pollAndProcess` → `safeTs` advancement |
| `modules/networksim/networksim_main.cc` | nsim: `step()` → recv + FIFO drain, `run()` adds `safeTs` sync after each step |
| `tools/networksim/topo3.json` | nsim topology links (src→dst latency = 100000 ticks = 100ns) |
| `tools/barrier/barrier_main.cc` | BarrierManager main loop |

### Test
| File | Role |
|------|------|
| `tests/e2e/run_multi.sh` | Launcher: builds native modules, starts all processes, watchdog |
| `tests/e2e/workloads/e2e_tc2_remote_read.c` | TC2 workload: node 0 dsm_store + dsm_load, node 1 dsm_load + verify |
| `tests/e2e/test_e2e.py` | Test verification (checks READ_VAL emissions) |

## 4. Current Defaults (after v5)

```cpp
// framework/Port.hh
static constexpr uint64_t kDefaultSyncInterval = 100000;  // 100ns
static constexpr uint64_t kDefaultLinkLatency  = 10000;   // 10ns (10,000 ticks)

// All Port constructors use these defaults — no explicit params needed.
// syncWindow has been merged into syncInterval (only one parameter now).
```

| Component | syncInterval | linkLatency | notes |
|-----------|-------------|-------------|-------|
| all Ports | 100000 (default) | 10000 (default) | unified |
| nsim internal | 100000 (topo JSON) | — | per-link FIFO delay |
| EPSNF retry | 1600000 (Cycles) | — | 16×100ns |
| deadlock_threshold | 500000000 (Cycles) | — | large |

## 5. Docker Operations

```
IMAGE: ubcc-dev:ubuntu20.04
MOUNT: -v /mnt/data2/cgc/cc-ep:/workspace/gem5
WORKDIR: -w /workspace/gem5
```

### Build gem5
```bash
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace/gem5 -w /workspace/gem5 \
  ubcc-dev:ubuntu20.04 bash -c '
cd /workspace/gem5/gem5
# force-rebuild specific objects if needed:
# rm -f build/ARM/gem5.build/mem/ruby/protocol/chi/ep/UBAdapter.o
scons build/ARM/gem5.opt -j32
'
```

### Build native modules (manual — run_multi.sh also does this)
```bash
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace/gem5 -w /workspace/gem5 \
  ubcc-dev:ubuntu20.04 bash -c '
ROOT=/workspace/gem5 MOD=$ROOT/modules/ubiomodule
CXX="-std=c++17 -O2 -I$MOD -I$MOD/mem/ruby -I$ROOT -I$ROOT/thirdparty/zeromq/include"
LD="-L$ROOT/thirdparty/zeromq/lib -lzmq -lpthread"
SRCS="$MOD/UBCCController.cc $MOD/ResidentDir.cc  $MOD/BackstoreSchemaA.cc $MOD/BackstoreSchemaC.cc $MOD/NodeAddressMap.cc"
g++ $CXX $ROOT/tools/ubio/ubio_main.cc $ROOT/framework/Port.cc $SRCS $LD -o /tmp/ubio.elf
g++ $CXX $ROOT/modules/networksim/networksim_main.cc $ROOT/framework/Port.cc $LD -o $ROOT/modules/networksim/networksim
g++ $CXX $ROOT/tools/barrier/barrier_main.cc $ROOT/framework/Port.cc $LD -o $ROOT/modules/barrier/barrier_manager
echo done
'
```

### Build workload
```bash
cd tests/e2e/workloads && aarch64-linux-gnu-gcc -static -O0 -g -I. -o e2e_tc2_remote_read.elf e2e_tc2_remote_read.c
```

### Run TC2
```bash
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace/gem5 -w /workspace/gem5 \
  ubcc-dev:ubuntu20.04 bash -c '
ROOT=/workspace/gem5
mkdir -p $ROOT/shared_ipc && rm -rf $ROOT/shared_ipc/ipc_*
cd $ROOT/tests/e2e/workloads && aarch64-linux-gnu-gcc -static -O0 -g -I. -o e2e_tc2_remote_read.elf e2e_tc2_remote_read.c
touch e2e_tc2_remote_read.elf
cd $ROOT && bash tests/e2e/run_multi.sh 2
'
```

### Read logs (on host after test)
```
Log base: /mnt/data2/cgc/cc-ep/logs/<timestamp>/
├── gem5_tc2/
│   ├── stdout.log        — gem5 simulation output (EPSNF-RECV, CLEAR traces, etc.)
│   └── stderr.log        — gem5 diagnostic (GEM5-SEND, CLK-SYNC, CLEAR-RESP, PORT-SEND/RECV, etc.)
├── ubio_n0/stderr.log    — ubio node 0 messages (ports, recv ReadReq, TRACE-2/4, UBIO-LOOP)
├── ubio_n1/stderr.log    — ubio node 1 messages
├── ubio_n2/stderr.log    — ubio node 2 messages
├── nsim.log              — networksim STAT (tick, recv, fwd, fifo)
└── barrier.log           — BarrierManager
```

Key grep patterns:
```
GEM5-SEND          — ReadReq was sent by gem5 UBAdapter
stored ReadResp    — ReadResp received and cached
CLEAR-SEND         — ClearReq was sent (EPBackend::sendClear)
CLEAR-RESP         — ClearResp received (UBAdapter::handleResponse)
PORT-SEND           — any message sent through Port
PORT-RECV           — any message received through Port
CLK-SYNC            — clock sync status (curT, rxt, safeT) per heartbeat
Deadlock            — Sequencer deadlock detected
emit_after_wr       — TC2 workload store completed
READ_VAL            — TC2 workload load+verify completed
PASS / FAIL         — test verdict
UBIO-LOOP            — ubio main loop tick progress
NSIM-STAT            — nsim message receive/forward/fifo
```

## 6. Clock Sync Architecture (Current State)

### Message timestamp flow
`Port::sendAllocateBuffer(tick)` stamps each message with `tick + _linkLatency`.
At each hop the timestamp accumulates the sender's local time + link delay.

### safeTs
```cpp
safeTs(curT) = min(receiveTimestamp(), lastSyncTs + syncInterval)
```
Where `receiveTimestamp()` = `_pending ? _pendingT : _lastRxT`.

### CONTROL_SYNC (heartbeat)
- `emitSync(tick)` sends a CONTROL_SYNC message with `timestamp = tick + linkLatency`
- On receive: `_lastSyncTs = max(_lastSyncTs, msg.timestamp)`
- Rate-limited: skipped if `tick - _lastSyncTs < _syncInterval`

### UBAdapter::wakeup scheduling
```cpp
uint64_t safeT = _port->safeTs(curTick());
uint64_t nextT = safeT > curTick() ? safeT : curTick();
if (_responseCheckEvent.scheduled())
    reschedule(_responseCheckEvent, nextT);
else
    schedule(_responseCheckEvent, nextT);
```
- When peer is ahead: `safeT > curTick()` → jump to `safeT` (peer time)
- When peer is behind: `safeT <= curTick()` → stay at `curTick()` (no advancement)
- The `startup()` function arms this event at tick 0 for ALL 3 gem5 nodes

### EPSNF retry
- `scheduleEvent(Cycles(1600000))` in EPSNFController::wakeup
- retry queue calls `handleRemoteMiss` → `sendReadReq` → checks `_readyResponses`
- If grant found: `handleGrant` → `sendClear` → ClearReq sent
- `_backend->wakeup()` (now wired to poll all UBAdapters) runs BEFORE retry queue

### Native side loops
- **ubio**: `emitSync` → `pollAndProcess(gem5Port + netPort)` → `tick = max(safeTs, tick) else ++tick`
- **nsim**: `step()` → `_tick++` → recv + FIFO drain → after step: `_tick = max(safeTs, _tick)`
- **barrier**: `emitSync` → `recv` → `tick = max(safeTs, tick) else ++tick`

## 7. Current Bottleneck

**The clocks of gem5, ubio, and nsim diverge despite CONTROL_SYNC exchange.**

Observed in TC2 test:

| Step | Node | local tick | msg_ts | Note |
|------|------|-----------|--------|------|
| 1 | gem5 send ReadReq | 71,567,500 | 71,577,500 | +10000 linkLatency |
| 2 | ubio0 recv | **71,577,500** | 71,577,500 | **Sync OK** — ubio0 clock = gem5 time |
| 3 | ubio0→nsim | 71,577,500 | 71,587,500 | +10000 |
| 4 | nsim recv | 71,587,501 | 71,587,500 | **Sync OK** |
| 5 | nsim FIFO→ubio1 | 71,688,364 | 71,698,364 | 100ns FIFO |
| 6 | ubio1→nsim(Rsp) | 71,698,364 | 71,708,364 | **Sync OK** |
| 7 | nsim FIFO→ubio0 | 71,816,592 | 71,826,592 | 100ns FIFO |
| 8 | ubio0→gem5(Rsp) | 71,826,592 | 71,836,592 | **Sync OK** |
| **9** | **gem5 recv** | **75,137,500** | 71,836,592 | **GAP = 3.3M ticks!** |

The message timestamps are perfect (269,092 ticks ≈ 0.27ms end-to-end, matching 2×nsim+6×ZMQ).
**But gem5's `curTick()` drifted 3.3M ticks ahead of ubio's clock.**

Root cause chain:
1. Three gem5 nodes all have `startup()` heartbeat loops firing from tick 0.
2. Each heartbeat does emitSync → recv → reschedule at `max(safeTs, curTick)`.
3. The `safeTs` was being bypassed by the old `curTick() + 10000` fallback (fixed in v5).
4. With the clamp fix, nodes now stop at `safeTs` bound. But `safeTs` returns `curTick()`
   (because `receiveTimestamp()` = own last message). Node loops at the same tick.
5. Node 2 gets stuck: `CLK-SYNC node=2 curT=243610000 rxt=243610000 safeT=243610000`
   — repeating identically forever (many log lines at same tick). Node 0 stays at 75M.
6. EPSNF retry at 76.3M never fires because gem5 is stuck at 75.1M.

**Key open question**: why does `safeTs` return `curTick()` instead of a value > curTick?
The `_lastSyncTs` is set by own `emitSync`, `syncBound = _lastSyncTs + 100000`.
The `receiveTimestamp()` should be updated by incoming CONTROL_SYNC from the peer.
If peer is at 264M, `rxt = 264M`, and `safeTs = min(264M, 75.1M+100000) = 75.2M`.
So safeTs should return 75.2M > 75.1M and advance time. **Why doesn't it?**

Hypothesis: the PORT-RECV logs show CONTROL_SYNC only from src=2:0 (node 2), not from
src=0 or src=1. Possible ZMQ socket cross-connection or the heartbeat from node 0/1 not
reaching node 2's Port. Or the PORT-RECV log counter was exhausted (now removed in v5).

## 8. Pending Work

Three design documents are started but incomplete in `docs/design/`:

| File | Content |
|------|---------|
| `docs/design/decoupling.md` | Gem5 decoupling from UBCC (A/B dual version plan) |
| `docs/design/build_system.md` | Build system integration (native + gem5) |
| `docs/design/port_refactoring.md` | Port A/B duplex refactoring plan |

## 9. Constraints

- **No reverts.** All changes must be forward-only.
- **Test correctness criterion**: TC2 completes (no deadlock), exits cleanly, emits `READ_VAL`
  or `emit_after_wr`, and verify_tc2 reports `TC2 PASSED`.
- **Timeout**: each test run uses `timeout` (currently 60–300s). If timeout kills the test,
  count as FAILED but do NOT treat as blocking.
- **Compilation**: gem5 via scons, native modules via direct g++. Always force-rebuild
  modified object files (`rm -f build/ARM/gem5.build/.../<file>.o`) before scons.
- **IPC endpoints**: must be cleaned (`rm -rf shared_ipc/ipc_*`) between runs.
