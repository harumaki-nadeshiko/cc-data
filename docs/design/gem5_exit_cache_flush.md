# Design: gem5 Exit-Time Cache Line Flush-Back

**Status:** Proposed  
**Author:** Auto-generated from codebase analysis  
**Date:** 2026-07-12  

---

## 1. Problem Statement

### 1.1 What Happens When gem5 Exits With Dirty Lines

In the multi-process distributed cache coherence simulation, each gem5 process
(one per node) owns L1/L2 caches and an `EPBackend` that tracks per-line
coherence state in `_requesterLines` (a `std::map<uint64_t,
RequesterLineEntry>`). Lines granted in Modified (`R_M`) or Exclusive (`R_E`)
state may hold dirty data that has not been written back to the home UBCC
directory.

When a gem5 process exits (all threads halted via `exitImpl()` in
`gem5/src/sim/syscall_emul.cc:127`), the exit path is:

1. `exitImpl()` calls `tc->halt()` on all thread contexts (line 226)
2. When `activeContexts == 0`, it calls `exitSimLoop("exiting with last active
   thread context", ...)` (line 250)
3. The Python layer calls `doExitCleanup()` (`gem5/src/sim/core.cc:153`) which
   processes the exit callback queue
4. The UBAdapter exit callback (registered at
   `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:93-98`) fires:
   ```cpp
   registerExitCallback([portToClose, nodeForLog]() {
       portToClose->terminate();
   });
   ```
5. `Port::terminate()` (`framework/Port.cc:97-112`) sends a best-effort
   `TERMINATE` MemMessage to the ubio peer, then releases ZMQ sockets
6. The gem5 process dies

**The problem:** Between steps 2 and 4, no cache flush occurs. The
`_requesterLines` map in `EPBackend` may contain entries in states `R_M` or
`R_E` whose dirty data resides only in the local gem5 process's caches (L1/L2
Ruby `CacheMemory` and/or `SimpleMemory` backing store). Once the process dies,
this data is irrecoverably lost.

### 1.2 Why This Matters for Correctness

When another node subsequently requests the same cache line, the home UBCC
directory (`UBCCController`) still records the now-dead node as the
owner/sharer. The home issues a `RecallReq` to the dead node. Since the gem5
process for that node no longer exists:

- The `RecallReq` is sent via ubio's network port but the gem5 peer's ZMQ
  socket is closed
- ubio receives `TERMINATE` and sets `doneFlag = true`
  (`modules/ubiomodule/ubio_main.cc:707-709`), but the directory entry remains
  stale
- The requesting node's `handleRemoteMiss()` hangs waiting for a `ReadResp`
  that will never arrive (the recall to the dead owner never completes)
- **Result:** Deadlock or data corruption (stale/zero data served)

### 1.3 Current Mitigation and Its Limitations

The current workaround is to use **explicit barriers** in workloads
(`sync_wait` with cross-node masks, implemented via `SyncWaitManager` +
`BarrierReached`/`BarrierRelease` messages through ubio). The barrier ensures
all nodes reach a synchronization point where dirty lines have been written
back through normal program execution before any node exits.

**Limitations:**
- Requires workload cooperation; not all workloads have natural barrier points
- Fragile: a programming error (missing barrier) silently causes deadlock
- Cannot handle asymmetric exit (e.g., one node finishes early while others
  continue accessing shared data that the exiting node modified)
- Does not scale to heterogeneous workloads where different nodes run different
  programs

---

## 2. Architecture Analysis

### 2.1 gem5 Exit Callback Chain

```
Thread exit (syscall exit/exit_group)
  -> exitImpl()                           [syscall_emul.cc:127]
     -> tc->halt() for all threads        [syscall_emul.cc:226]
     -> activeContexts == 0?
        -> exitSimLoop(...)               [syscall_emul.cc:250]
           -> Python atexit
              -> doExitCleanup()          [core.cc:153]
                 -> exitCallbacks().process()
                    -> UBAdapter callback  [UBAdapter.cc:93-98]
                       -> Port::terminate()  [Port.cc:97]
                          -> ZMQ send TERMINATE
                          -> _releaseSockets()
```

The `registerExitCallback()` mechanism (`core.cc:143-146`) uses a
`CallbackQueue` (LIFO). All registered callbacks fire synchronously inside
`doExitCleanup()`. Currently, the only callback registered by the coherence
subsystem is UBAdapter's `terminate()` call.

### 2.2 Where Dirty Lines Live

Dirty lines exist at three conceptual levels:

#### 2.2.1 EPBackend `_requesterLines`
(`gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:772`)

```cpp
std::map<uint64_t, RequesterLineEntry> _requesterLines;
```

Each entry tracks the **outer (global) coherence state** for a DSM line:
- `RequesterLineState::R_M` -- Modified owner (dirty, exclusive)
- `RequesterLineState::R_E` -- Exclusive owner (clean exclusive, may have been
  silently upgraded to dirty by local writes)
- `RequesterLineState::R_S` -- Shared reader
- `RequesterLineState::R_I` -- Invalid (post-writeback/evict)
- `RequesterLineState::R_WAIT_GRANT` -- Pending grant

Lines in `R_M` **definitely** need writeback. Lines in `R_E` **may** need
writeback if the local cache promoted E->M via a silent upgrade (CHI
`CleanUnique` / local write hit).

The `RequesterLineEntry` contains: `lineAddr` (local PA), `epoch`, `reqId`,
`homeNode`, and `state`.

**This is the authoritative source** of which lines this node holds with outer
permissions that the home UBCC needs to know about.

#### 2.2.2 Ruby L1/L2 CacheMemory
(`gem5/src/mem/ruby/structures/CacheMemory.hh`)

The actual cache tags and data blocks. Entries have `AccessPermission`
(`Read_Only`, `Read_Write`, etc.) and a `DataBlock`. The
`CacheMemory::recordCacheContents()` method (`CacheMemory.cc:440-476`)
iterates all sets/ways and identifies `Read_Write` entries as dirty (used for
checkpoint warm-up).

However, Ruby CacheMemory operates at the **local CHI protocol level** (within
the node's own HN-F domain). It does not know about the outer UBCC protocol.
Iterating it directly does not provide the home-node information needed for
WritebackReq.

#### 2.2.3 SimpleMemory (DDR4 backing store)

The `SimpleMemory` instance that backs the node's local DRAM. For DSM lines
where this node is the home, data is authoritative. For remote-home lines, the
backing store may or may not reflect the latest dirty data (writes go to
L1/L2, not through to DRAM for dirty lines).

### 2.3 Normal Writeback Flow

During normal operation, writebacks are triggered by:
1. **L2 eviction** -- CHI `WriteNoSnp` from HN-F to `EPSNFController`
2. **EPSNFController** calls `EPBackend::handleWriteback(line_pa, keepAsClean)`
   (`EPSNFController.cc:531`, `EPBackend.cc:1288`)
3. **EPBackend** translates the local PA to home PA, looks up epoch from
   `_requesterLines`, and calls `UBAdapter::sendWritebackReq()`
   (`EPBackend.cc:1364-1365`)
4. **UBAdapter** constructs a `CoherenceMessage` with type `WritebackReq` and
   sends it via `transportSend()` through the framework `Port`
   (`UBAdapter.cc:395-418`)
5. **ubio** receives the message, dispatches to
   `UBCCController::processWriteback()` (`ubio_main.cc:484-498`)
6. **UBCC** validates epoch/owner, updates directory state (G_I or G_E), returns
   `WritebackResp` (`UBCCController.cc:1637-1721`)
7. **UBAdapter** receives `WritebackResp`, updates `_readyResponses` cache

### 2.4 Message Flow (Normal Writeback)

```
gem5 (EPSNFController)          UBAdapter            Port/ZMQ           ubio          UBCC
     |                              |                    |                |              |
     |--handleWriteback(pa)-------->|                    |                |              |
     |                              |--WritebackReq----->|----ZMQ send--->|              |
     |                              |                    |                |--processWB-->|
     |                              |                    |                |              |--update dir
     |                              |                    |<---ZMQ recv----|<-WB Resp-----|
     |                              |<--WritebackResp----|                |              |
     |<----return ok/pending--------|                    |                |              |
```

---

## 3. Proposed Solution: Exit-Time Cache Flush

### Option A: EPBackend-Level Flush via `_requesterLines`

**Concept:** Before the UBAdapter exit callback calls `terminate()`, iterate
`EPBackend::_requesterLines` and send a `WritebackReq` for every line in `R_M`
or `R_E` state. Wait for all `WritebackResp` acknowledgments, then call
`terminate()`.

**Mechanism:**

1. Add a new method `EPBackend::flushAllDirtyLines()` that iterates
   `_requesterLines`:
   ```cpp
   for (auto &kv : _requesterLines) {
       if (kv.second.state == RequesterLineState::R_M ||
           kv.second.state == RequesterLineState::R_E) {
           handleWriteback(kv.second.lineAddr, /*keepAsClean=*/false);
       }
   }
   ```

2. Modify the UBAdapter exit callback to call `flushAllDirtyLines()` first:
   ```cpp
   registerExitCallback([backend, portToClose, nodeForLog]() {
       backend->flushAllDirtyLines();  // NEW: flush before terminate
       portToClose->terminate();
   });
   ```

3. The flush must be **synchronous** -- each `WritebackReq` must complete
   (receive `WritebackResp`) before the Port is torn down. Since the exit
   callback runs outside the gem5 event loop, this requires a blocking
   poll-drain loop on the Port.

**Pros:**
- Directly addresses the problem at the correct abstraction layer
  (`_requesterLines` is the authoritative tracker of outer permissions)
- Reuses existing `handleWriteback()` / `sendWritebackReq()` infrastructure
- UBCC sees standard WritebackReq messages -- no protocol changes needed
- Lines in `R_S` and `R_I` are correctly skipped (no writeback needed)
- Home-node information (`homeNode`, `epoch`) is available in the entry

**Cons:**
- Exit callback runs outside the gem5 event loop; `curTick()` is frozen.
  UBAdapter's async response path (event-driven `wakeup()`) will not fire.
  Must use synchronous `transportRecv()` polling or a manual drain loop.
- Lines in `R_E` may not actually be dirty if no local write occurred after the
  exclusive grant. Writing them back is conservative but correct (a clean
  writeback to a line you own is harmless).
- Must handle the case where `handleWriteback()` returns `-2` (async pending).
  In the exit callback, we can switch to synchronous mode.
- Port must remain alive during the entire flush; `terminate()` must not be
  called until all responses are received.
- For large working sets, the flush could take significant wall-clock time.

### Option B: Ruby CacheMemory-Level Flush

**Concept:** Use `RubySystem::memWriteback()` (the existing cooldown mechanism,
`RubySystem.cc:224-301`) which iterates all cache controllers'
`recordCacheContents()`, collects dirty entries, and issues `FlushReq` packets
through the Ruby protocol.

**Mechanism:**

1. Call `RubySystem::memWriteback()` from the exit callback
2. This deschedules all events, creates a `CacheRecorder`, iterates L1/L2
   `CacheMemory` entries with `AccessPermission_Read_Write`, and issues
   `FlushReq` packets one at a time via `Sequencer::makeRequest()`

**Pros:**
- Reuses existing gem5 infrastructure for cache dumping
- Captures actual dirty data from L1/L2 (not just metadata)
- Works at the CHI protocol level -- flushes propagate through HN-F

**Cons:**
- **Does not work for the outer protocol.** `FlushReq` only writes data to the
  local node's DRAM backing store. It does NOT generate `WritebackReq` messages
  to remote home UBCCs. The UBCC directory still thinks the dead node is the
  owner.
- The cooldown mechanism requires the gem5 event loop to be running
  (`simulate()` is called). This is problematic in the exit callback context.
- `memWriteback()` has a warning that "continuing simulation afterwards may not
  always work as intended" -- it is designed for checkpoint, not live exit.
- CacheMemory entries do not contain `homeNode` or `epoch` information needed
  for `WritebackReq` construction.
- `RubySystem::memWriteback()` is protocol-specific (originally for MOESI
  Hammer) and may not correctly handle CHI protocol states.

**Verdict:** Option B is **not viable** for this problem. It flushes to local
DRAM but does not notify remote UBCC directories.

### Option C: UBCC Directory-Level Invalidation on TERMINATE

**Concept:** When ubio receives `TERMINATE` from a gem5 process, the UBCC
directory marks all lines owned by the departing node as "orphaned." On
subsequent access to an orphaned line, serve data from the DRAM backing store
(backstore) instead of issuing a `RecallReq` to the dead node.

**Mechanism:**

1. In `ubio_main.cc`, when `TERMINATE` is received (line 707-709), invoke a new
   `UBCCController::handleNodeDeparture(nodeId)` method
2. This method iterates the `ResidentDir` and for each line where the departing
   node is recorded as owner (`sharersMask` has the node bit set and state is
   `G_M` or `G_E`):
   - Transition state to `G_I` (or a new `G_ORPHANED` state)
   - Clear the node's bit from `sharersMask`
   - Mark `residentDirty = false` (data is lost; backstore is best-effort)
3. On next access, the UBCC serves the line from home DRAM (backstore data)
   instead of recalling from the dead owner

**Pros:**
- No changes needed on the gem5 side
- Handles the case where gem5 crashes or is killed (TERMINATE was best-effort)
- Simple to implement in UBCC

**Cons:**
- **Data loss.** If the dead node had modified the cache line but the dirty data
  was never written to the home DRAM, the backstore contains stale data.
  Serving stale data is a **silent correctness violation** -- much worse than a
  deadlock.
- Does not scale: the UBCC must iterate its entire directory to find all lines
  owned by the departing node. With `ResidentDir` using a Bloom filter + LRU
  cache, this is an O(N) scan.
- Introduces a new "orphaned" concept into the UBCC state machine, adding
  complexity to all future protocol changes.
- UBCC on node X only knows about lines whose **home** is node X. Lines homed
  on other nodes but owned by the departing node are tracked by those other
  nodes' UBCCs -- we would need to broadcast the departure to ALL UBCCs.
- The `TERMINATE` message is best-effort (`zmq::send_flags::dontwait` in
  `Port::terminate()`). If it is lost, orphan cleanup never happens.

**Verdict:** Option C trades a deadlock for silent data corruption. This is
**not acceptable** for a correctness-critical coherence system.

---

## 4. Recommended Approach: Option A (EPBackend-Level Flush)

Option A is the only approach that:
- Uses the correct metadata (`_requesterLines` with home-node and epoch info)
- Generates proper `WritebackReq` messages through the existing protocol
- Preserves UBCC directory consistency (standard writeback processing)
- Does not introduce data loss

### 4.1 Detailed Design

#### 4.1.1 New Method: `EPBackend::flushAllDirtyLines()`

**File:** `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` (and `.hh`)

```cpp
void EPBackend::flushAllDirtyLines()
{
    std::vector<std::pair<uint64_t, RequesterLineEntry>> dirtyLines;
    for (auto &kv : _requesterLines) {
        if (kv.second.state == RequesterLineState::R_M ||
            kv.second.state == RequesterLineState::R_E) {
            dirtyLines.push_back(kv);
        }
    }

    fprintf(stderr, "[EXIT-FLUSH] node=%d dirty_lines=%zu\n",
            _nodeId, dirtyLines.size());

    for (auto &[linePa, entry] : dirtyLines) {
        flushOneLine(linePa, entry);
    }
}
```

#### 4.1.2 New Method: `EPBackend::flushOneLine()`

This is a **synchronous** writeback that blocks until the `WritebackResp` is
received. It cannot use the async event-driven path because the gem5 event loop
is not running during exit callbacks.

```cpp
void EPBackend::flushOneLine(uint64_t linePa,
                              const RequesterLineEntry &entry)
{
    int homeNode = entry.homeNode;
    if (homeNode < 0) homeNode = homeNodeCrossNode(linePa);
    if (homeNode < 0) return;  // Cannot determine home; skip

    uint64_t offset = _addrMap.dsmOffset(linePa);
    int homeSocket = _addrMap.homeSocket(_nodeId, linePa);
    if (homeSocket < 0) homeSocket = 0;
    uint64_t homePa = _addrMap.buildDsmPA(homeNode, homeNode,
                                           offset, homeSocket);

    // Send WritebackReq synchronously through adapter socket 0
    UBAdapter *adapter = getUBAdapter(0);
    if (!adapter || !adapter->port()) return;

    // Use synchronous send + blocking recv
    adapter->sendWritebackReqSync(
        homePa, _nodeId, entry.epoch, false /*keepAsClean*/,
        homeNode, homeSocket);
}
```

#### 4.1.3 New Method: `UBAdapter::sendWritebackReqSync()`

A synchronous variant of `sendWritebackReq()` that blocks on
`transportRecv()` instead of returning `-2`:

```cpp
int UBAdapter::sendWritebackReqSync(uint64_t homePa, int requesterNode,
                                     uint64_t epochVal, bool keepAsClean,
                                     int homeNode, int homeSocket)
{
    CoherenceMessage req;
    // ... (same as sendWritebackReq lines 395-412)
    req.h.type = CoherenceMessageType::WritebackReq;
    // ... fill fields ...

    if (!transportSend(req)) return -1;

    // BLOCKING: poll Port until WritebackResp arrives
    // Use a generous timeout since we're shutting down
    if (!transportRecv(CoherenceMessageType::WritebackResp, req.h.reqId)) {
        fprintf(stderr, "[EXIT-FLUSH] node=%d WB timeout PA=0x%lx\n",
                _nodeId, homePa);
        return -1;
    }
    return _lastResponse.b.writebackResp.success ? 1 : 0;
}
```

#### 4.1.4 Modified Exit Callback in `UBAdapter::init()`

**File:** `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:91-98`

Change from:
```cpp
registerExitCallback([portToClose, nodeForLog]() {
    portToClose->terminate();
});
```

To:
```cpp
EPBackend *backendPtr = /* ... obtained during init ... */;
registerExitCallback([portToClose, backendPtr, nodeForLog]() {
    fprintf(stderr,
        "[UBADAPTER-EXIT] node=%d flushing dirty lines before TERMINATE\n",
        nodeForLog);
    if (backendPtr) {
        backendPtr->flushAllDirtyLines();
    }
    fprintf(stderr,
        "[UBADAPTER-EXIT] node=%d flush complete, sending TERMINATE\n",
        nodeForLog);
    portToClose->terminate();
});
```

**Challenge:** At the time `init()` registers the exit callback, `_backend` is
not yet bound (it is bound later in `EPBackend::init()` via
`adapter->bindBackend(this)`). Two solutions:

- **Option 1:** Capture `this` (the UBAdapter) and access `_backend` through
  it at callback time. The callback fires after all init is complete, so
  `_backend` will be set.
  ```cpp
  UBAdapter *self = this;
  registerExitCallback([self, portToClose, nodeForLog]() {
      if (self->_backend) self->_backend->flushAllDirtyLines();
      portToClose->terminate();
  });
  ```

- **Option 2:** Register the exit callback in `EPBackend::init()` instead of
  `UBAdapter::init()`, after the backend-adapter binding is complete.

Option 1 is simpler and preserves the existing callback registration site.

### 4.2 Message Flow Diagram (Exit-Time Flush)

```
gem5 process exiting
  |
  v
doExitCleanup()  -->  exitCallbacks().process()
  |
  v
UBAdapter exit callback fires
  |
  v
EPBackend::flushAllDirtyLines()
  |
  |  for each line in _requesterLines where state == R_M or R_E:
  |
  |    EPBackend::flushOneLine(linePa, entry)
  |      |
  |      v
  |    UBAdapter::sendWritebackReqSync(homePa, ...)
  |      |                                   ubio process        UBCC
  |      |--[WritebackReq]-->Port--ZMQ-send-->|                    |
  |      |                                    |--processWriteback->|
  |      |                                    |                    |-update dir
  |      |                                    |<--WritebackResp----|
  |      |<-[WritebackResp]--Port--ZMQ-recv---|                    |
  |      |
  |      v  (next line)
  |
  v
Port::terminate()
  |--[TERMINATE]-->ZMQ-->ubio
  |
  v
gem5 process exits
```

### 4.3 Ordering Constraints

1. **All WritebackResp must be received before terminate().** If terminate() is
   called while writebacks are in flight, the ZMQ sockets are destroyed and
   responses are lost. The UBCC may have processed the writeback (directory
   updated) or may not have (directory still shows dead node as owner).

2. **Each WritebackReq must complete before the next is sent** (in the
   synchronous variant). This is because `transportRecv()` uses
   `_lastResponse` as a single-slot buffer. Alternatively, a batch approach
   could send all WritebackReqs and then drain all responses, but this
   requires tracking multiple in-flight requests.

3. **Port sync/heartbeat.** The Port's PDES synchronization requires periodic
   `emitSync()` calls so the peer (ubio) can advance its clock. During the
   flush, the gem5 event loop is stopped, so `emitSync()` must be called
   explicitly before each `transportRecv()` poll, or the ubio side may stall
   waiting for gem5's sync. The `transportRecv()` method already polls in a
   tight loop, but a call to `_port->emitSync(curTick())` before each
   `WritebackReq` ensures the ubio peer can process it.

4. **curTick() is frozen.** The exit callback runs after `exitSimLoop()`, so
   simulated time does not advance. All WritebackReq messages will carry the
   same `enqueueTick` and `readyTick`. This is acceptable because:
   - The UBCC's `processWriteback()` does not use ticks for state decisions
     (it uses epoch and owner checks)
   - The Port's `allocateSendBuffer()` stamps `hdr.timestamp = curTick() +
     linkLatency`, which is fine for delivery ordering

### 4.4 Edge Cases

#### 4.4.1 WritebackReq Fails (UBCC Rejects)

The UBCC may reject a writeback for:
- **Stale epoch:** `checkEpochForLine()` fails (`UBCCController.cc:1667-1674`).
  This can happen if another node already recalled and invalidated this line
  while the exiting node was shutting down. **Safe to skip** -- the line is no
  longer owned by this node in the directory.
- **Owner mismatch:** `ownerNode != requesterNode` (`UBCCController.cc:1680-
  1687`). Same reasoning -- the directory has already been updated. **Safe to
  skip.**
- **Line busy:** `isLineBusy()` returns true (`UBCCController.cc:1658-1664`).
  A concurrent operation is in progress. **Should retry** after a brief delay,
  or skip with a warning (the concurrent operation will eventually complete
  and the directory will be consistent).

**Recommendation:** Log rejections but do not retry. If the UBCC rejects a
writeback, it means the directory no longer considers this node the owner, so
no flush is needed for that line. The only exception is "line busy," which
should be retried a limited number of times.

#### 4.4.2 Home UBCC Is on the Same Node

When `homeNode == _nodeId`, the WritebackReq still goes through the
UBAdapter -> Port -> ubio -> UBCC path. In the multi-process split, ubio runs
as a separate process for each (node, socket) pair, so there is always a
Port-based transport. No special handling is needed.

#### 4.4.3 ubio Has Already Exited

If the ubio process for the home node has already exited (e.g., both gem5 and
ubio for node 0 exit before node 1's gem5 tries to flush), the `Port::send()`
will fail (ZMQ send to a closed socket). The `transportSend()` call returns
false, and the WritebackReq is dropped.

**Impact:** The directory entry for that line is now orphaned (the home UBCC
process is gone). However, since the home ubio is also gone, no future
`RecallReq` can be generated for lines homed on that node. This is a
**non-issue** -- if the home ubio is dead, no one can request the line.

**Exception:** If another ubio (on a different node) is the home for a line
owned by the exiting gem5, the home ubio may still be alive. The exiting gem5
sends the WritebackReq to its local ubio, which routes it through networksim
to the home ubio. This works as long as the local ubio and networksim are still
running.

**Recommendation:** Register the exit callback at a priority that fires before
any ubio/networksim shutdown. In the current architecture, ubio exits when it
receives `TERMINATE` from gem5 (line 707-709), and networksim exits when all
ubios have terminated. So the ordering is naturally correct: gem5 flushes,
then sends `TERMINATE`, then ubio processes the flushes (they were sent before
`TERMINATE`) and then processes `TERMINATE` and exits.

#### 4.4.4 Multi-Socket Flush

With `_numSockets > 1`, each socket has its own UBAdapter. The flush must
iterate all sockets and use the appropriate adapter for each line's home
socket. The `homeSocket` is derivable from the line PA via
`_addrMap.homeSocket()`.

#### 4.4.5 PDES Synchronization During Flush

The gem5 event loop is stopped during the exit callback. The Port's sync
mechanism relies on `emitSync()` calls to advance the peer's safe timestamp.
During the flush:
- Each `transportSend()` call implicitly sends a message with a timestamp,
  which advances the peer's `_lastRxT`
- `transportRecv()` polls in a loop calling `_port->recv()`, which processes
  incoming messages and updates local `_lastRxT`
- The ubio side's main loop continues processing messages as long as its gem5
  Port is alive (no `TERMINATE` received yet)

**Potential issue:** If the gem5 side enters `transportRecv()` and the ubio
side is blocked waiting for a sync from gem5 (because gem5's `_lastSyncTs` is
stale), a deadlock can occur. **Mitigation:** Call `_port->emitSync(curTick())`
before entering the `transportRecv()` poll loop in `sendWritebackReqSync()`.

### 4.5 Impact on PDES Synchronization

The flush adds WritebackReq/WritebackResp round trips before `TERMINATE`. Each
round trip is bounded by `syncInterval + linkLatency` (currently 100,000 +
100,000 = 200,000 ps per hop). For N dirty lines, the flush takes approximately
`N * 2 * 200,000 ps` of simulated time worth of wall-clock ZMQ round trips.

In practice:
- Typical workloads have O(100-1000) dirty lines per node
- Each ZMQ round trip takes ~10-100 us wall-clock time
- Total flush time: ~1-100 ms wall-clock, which is negligible compared to
  simulation time

---

## 5. Implementation Plan

### 5.1 Files to Modify

| # | File | Change | Effort |
|---|------|--------|--------|
| 1 | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | Add `flushAllDirtyLines()` and `flushOneLine()` declarations | Small |
| 2 | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | Implement `flushAllDirtyLines()` and `flushOneLine()` | Medium |
| 3 | `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh` | Add `sendWritebackReqSync()` declaration | Small |
| 4 | `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` | Implement `sendWritebackReqSync()`; modify exit callback in `init()` to call flush before terminate | Medium |
| 5 | `framework/Port.cc` | (Optional) Add `emitSyncExplicit()` for use outside event loop | Small |

**Total estimated effort:** 1-2 days for implementation, 1-2 days for testing.

### 5.2 Detailed Code Locations

#### `EPBackend.hh` -- Add declarations after line 497 (near `handleEvict`)

```cpp
/** Exit-time flush: write back all dirty lines to home UBCCs.
 *  Called synchronously from the exit callback before Port::terminate(). */
void flushAllDirtyLines();

private:
void flushOneLine(uint64_t linePa, const RequesterLineEntry &entry);
```

#### `EPBackend.cc` -- Add implementations after `handleEvict()` (~line 1477)

Implement `flushAllDirtyLines()` as described in section 4.1.1, and
`flushOneLine()` as described in section 4.1.2.

#### `UBAdapter.hh` -- Add declaration after line 91 (near `sendWritebackReq`)

```cpp
/** Synchronous writeback for exit-time flush. Blocks until response. */
int sendWritebackReqSync(uint64_t homePa, int requesterNode,
                          uint64_t epochVal, bool keepAsClean,
                          int homeNode, int homeSocket);
```

#### `UBAdapter.cc` -- Implement `sendWritebackReqSync()` and modify `init()`

- `sendWritebackReqSync()`: ~lines 383-439 (after existing `sendWritebackReq`)
  -- same as `sendWritebackReq` but uses blocking `transportRecv()` instead of
  returning `-2` for the async path.
- `init()` exit callback: modify lines 91-98 to call flush before terminate.

### 5.3 Testing Strategy

#### Unit Tests

1. **TC-EXIT-1: Basic flush on exit.** Two-node setup. Node 0 writes to
   remote lines (home = node 1), establishing `R_M` state. Node 0 exits.
   Verify:
   - WritebackReq messages are observed on the Port (stderr log markers)
   - UBCC directory on node 1 transitions from `G_M(owner=0)` to
     `G_I(owner=-1)` for all flushed lines
   - No deadlock on node 1 when it accesses those lines afterward

2. **TC-EXIT-2: Flush with R_E lines.** Same as TC-EXIT-1 but with
   exclusive-clean lines. Verify that `R_E` lines are also written back
   (conservative but correct).

3. **TC-EXIT-3: Flush with stale rejections.** Node 0 has `R_M` lines, but
   node 1 has already recalled them (race condition). The WritebackReq is
   rejected by UBCC (stale epoch). Verify that the flush proceeds without
   deadlock or crash.

4. **TC-EXIT-4: Multi-socket flush.** Two-socket configuration. Lines span
   both sockets. Verify that each socket's adapter handles its lines correctly.

5. **TC-EXIT-5: No dirty lines.** Node 0 exits with only `R_S` and `R_I`
   lines. Verify that no WritebackReq is sent and terminate() is called
   immediately.

#### Integration Tests

6. **TC-EXIT-6: End-to-end without barriers.** Run a multi-node workload where
   one node exits early (asymmetric exit). Without barriers, verify that other
   nodes can still access all shared data correctly after the early exit.

7. **TC-EXIT-7: Crash recovery.** Kill a gem5 process with SIGKILL (no exit
   callback fires). Verify that the system eventually times out / detects the
   dead node (this tests the bounds of Option A; Option C would be needed for
   true crash tolerance, but that is out of scope).

### 5.4 Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `transportRecv()` hangs (ubio not processing) | gem5 exit hangs indefinitely | Add a timeout (e.g., 30s wall-clock). On timeout, log warning and proceed to `terminate()`. Dirty lines may be lost but the process exits cleanly. |
| Large number of dirty lines causes slow exit | Wall-clock delay on exit | Log progress (`[EXIT-FLUSH] node=%d flushed %d/%d`). Consider batching: send all WritebackReqs first, then drain all responses (requires multi-slot response tracking). |
| `curTick()` frozen breaks UBCC epoch logic | WritebackReq rejected | UBCC's `processWriteback()` does not check ticks, only epoch and owner. Frozen curTick is not a problem. |
| Race between flush and concurrent recall | Writeback and recall for same line in flight | UBCC's `isLineBusy()` check handles this. If busy, skip and rely on the concurrent operation to resolve the directory state. |
| Multi-process ubio routing (cross-node) | WritebackReq for remote home must transit networksim | This works with the existing transport: gem5 -> local ubio -> networksim -> home ubio -> UBCC. The flush just adds more messages to the same path. |
| Exit callback ordering with other callbacks | Flush must run before terminate | Both are registered by the same UBAdapter::init(). Move flush before terminate in the same callback lambda. No ordering issue. |

### 5.5 Future Enhancements

1. **Batch flush:** Send all WritebackReqs in a burst, then drain all
   responses. Requires extending `_readyResponses` tracking to handle
   multiple concurrent writeback transactions. Could reduce wall-clock flush
   time from O(N * RTT) to O(RTT + N * send_time).

2. **Data payload in WritebackReq:** Currently, the WritebackReq does not
   carry the dirty data (data is assumed to be in the home DRAM backing
   store or recalled separately). For true dirty data transfer on exit, the
   WritebackReq should include the 64-byte data payload read from the local
   L1/L2 cache. This requires extending `CoherenceMessage::WritebackReq` to
   include a data field and having `flushOneLine()` do a
   `functionalRead()` from the local Ruby cache to retrieve the dirty data.

3. **Graceful crash handling (Option C hybrid):** For robustness against
   process crashes (SIGKILL), implement a UBCC-side timeout that transitions
   stale owner entries to a safe state after a configurable period without
   heartbeats from the owner node. This is complementary to Option A (which
   handles graceful exit) and requires separate design work.
