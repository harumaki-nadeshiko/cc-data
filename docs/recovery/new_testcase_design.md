# New CC-EP End-to-End Testcase Design

## Coverage gaps after TC1-TC12

The current suite validates basic remote miss, recall, invalidation, ping-pong, local upgrade, and barrier synchronization, but it still under-covers:

1. **Cross-line release/acquire ordering** after remote invalidation.
2. **Repeated mixed read/write waves** with 3 sharers and multiple re-sharing phases.
3. **Resource recovery** under RetryAck / PCrdGrant / credit-pressure conditions.
4. **Concurrent shared→exclusive upgrades** from two sharers on the same line.
5. **Writeback + DMA interaction** when remote readers race with memory-side updates.

Below are 5 new end-to-end tests targeting those gaps.

---

## TC13 — `remote_release_acquire_fence`

**Description**  
Cross-node invalidate + fence ordering on two DSM lines: `DATA` and `FLAG` under the same home node. Node1 first caches stale `DATA`, then Node0 updates `DATA`, executes a full fence, and publishes `FLAG=1`. Node1 spins on `FLAG` and only then re-reads `DATA`.

**Why this is new**  
TC8 proves invalidate-on-upgrade for one line. It does **not** prove release/acquire visibility across **two lines** with a stale shared copy on the data line.

**Protocol paths exercised**
- `ReadShared(DATA)` → sharer registration
- `ReadUnique/CleanUnique(DATA)` by writer → invalidate sharer
- `ReadUnique/WriteNoSnp(FLAG)` publish path
- `ReadShared(FLAG)` polling path
- HN-F invalidate ordering + UBCC grant/clear ordering across two lines

**Scenario sketch**
1. Node0 writes `DATA=0x1111` (home=Node2).
2. Node1 reads `DATA` and holds a shared copy.
3. Node0 executes `dmb sy`, writes `DATA=0x2222`, executes `dmb sy`, then writes `FLAG=1`.
4. Node1 spins on `FLAG` until it reads `1`, executes `dmb sy`, then reads `DATA`.

**Expected behavior**
- After Node1 observes `FLAG=1`, its next `DATA` read must be `0x2222`.
- `0x1111` after `FLAG=1` is an ordering bug: stale shared data survived publish.
- No deadlock or indefinite poll on `FLAG`.

**Python verifier logic sketch**
```python
def verify_tc13(reads, lines):
    flag_seen = any('[FLAG_SEEN] node=1 val=1' in l for l in lines)
    data_reads = [r for r in reads if r['node'] == 1 and r['home'] == 2]
    if not flag_seen:
        return False, 'TC13 FAILED: Node1 never observed FLAG=1', []
    if not data_reads:
        return False, 'TC13 FAILED: no final DATA read from Node1', []
    final_data = int(data_reads[-1]['actual'], 16)
    if final_data != 0x2222:
        return False, f'TC13 FAILED: final DATA=0x{final_data:X}, expected 0x2222', [data_reads[-1]]
    return True, 'TC13 PASSED: release/acquire ordering preserved', []
```

---

## TC14 — `three_node_multi_sharer_rw_wave`

**Description**  
Three-node mixed read/write wave on one DSM line with two full re-sharing phases.

**Why this is new**  
TC4 transfers ownership in a ring; TC6 creates multiple sharers once. Neither test stresses **G_M→G_S→G_M→G_S→G_M** with multiple sharers re-created between writes.

**Protocol paths exercised**
- Initial `ReadUnique` / write miss
- `ReadShared` from two other nodes → owner recall to shared
- Shared-sharer `CleanUnique` / upgrade path
- `INVALIDATE + GRANT_HANDSHAKE`
- Repeated `Clear/ClearAck` cycles

**Scenario sketch**
1. Node0 writes `V0=0x1001` to line X (home=Node2).
2. Node1 and Node2 read X → all readers must see `V0`.
3. Node1 writes `V1=0x2002` to X.
4. Node0 and Node2 read X → both must see `V1`.
5. Node2 writes `V2=0x3003` to X.
6. Node0 and Node1 read X → both must see `V2`.

**Expected behavior**
- No stale value from an earlier wave may reappear.
- Each write wave must become globally visible before the next read wave completes.
- Final state converges to `V2` on all nodes.

**Python verifier logic sketch**
```python
def verify_tc14(reads, lines):
    # Prefer phase tags in workload output, e.g. [PHASE_RD] step=2/4/6
    expect = {
        ('step2', 1): 0x1001, ('step2', 2): 0x1001,
        ('step4', 0): 0x2002, ('step4', 2): 0x2002,
        ('step6', 0): 0x3003, ('step6', 1): 0x3003,
    }
    seen = extract_phase_reads(lines)  # {(step,node): value}
    missing = [k for k in expect if k not in seen]
    if missing:
        return False, f'TC14 FAILED: missing reads {missing}', []
    bad = [(k, seen[k], expect[k]) for k in expect if seen[k] != expect[k]]
    if bad:
        return False, f'TC14 FAILED: stale/mismatched wave reads {bad}', []
    return True, 'TC14 PASSED: mixed multi-sharer waves serialized correctly', []
```

---

## TC15 — `retryack_pcrdgrant_credit_storm`

**Description**  
Stress recovery under resource pressure. All CPUs on all 3 nodes hammer a small remote-line set concurrently while the test config intentionally reduces TBE/network credit headroom.

**Why this is new**  
TC5 and TC10 stress correctness, but not **recovery semantics** under explicit `RetryAck`, `PCrdGrant`, and credit exhaustion pressure.

**Protocol paths exercised**
- `ReadNoSnp` / `WriteNoSnp` under backpressure
- HN-F `RetryAck` and `PCrdGrant`
- EP retry queue replay
- UBCC `BUSY/RETRY` + queued reissue
- `GRANT_HANDSHAKE` completion after delayed grant

**Test configuration notes**
- Run this TC with a dedicated “stress profile”: small HN-F TBE count, reduced Garnet VC/credit buffers, and small EP retry queue depth.
- Enable protocol log/counter output for `RetryAck`, `PCrdGrant`, retry-queue depth, and deadlock watchdog.

**Scenario sketch**
1. 12 participating CPUs (4 per node) simultaneously access 8 remote DSM lines on the same home node.
2. Each CPU alternates load/store for `N` rounds.
3. After the storm, one primary CPU per node reads back all 8 lines and prints final values.

**Expected behavior**
- The run must show **non-zero** `RetryAck` and `PCrdGrant` activity.
- Despite retries, all rounds complete and all final reads converge.
- No deadlock, no permanent BUSY, no dropped completion.

**Python verifier logic sketch**
```python
def verify_tc15(reads, lines):
    retry_cnt = sum('RetryAck' in l for l in lines)
    pcrd_cnt = sum('PCrdGrant' in l for l in lines)
    if retry_cnt == 0 or pcrd_cnt == 0:
        return False, f'TC15 FAILED: RetryAck={retry_cnt}, PCrdGrant={pcrd_cnt}', []
    if any('deadlock' in l.lower() or 'panic:' in l.lower() for l in lines):
        return False, 'TC15 FAILED: deadlock/panic under credit pressure', []
    final = extract_final_line_values(reads)  # {(line,node): value}
    if not final:
        return False, 'TC15 FAILED: no final convergence reads', []
    for line_id in all_test_lines:
        vals = {final[(line_id, n)] for n in (0,1,2)}
        if len(vals) != 1:
            return False, f'TC15 FAILED: line {line_id} diverged: {vals}', []
    return True, 'TC15 PASSED: retry/credit recovery preserved forward progress', []
```

---

## TC16 — `dual_shared_upgrade_race`

**Description**  
Two remote sharers race to upgrade the same shared line to exclusive/modified at the same time.

**Why this is new**  
TC8 has one upgrader; TC11 has one local upgrade. There is no current E2E test where **two sharers concurrently issue shared→exclusive upgrades** on the same line.

**Protocol paths exercised**
- Initial `ReadShared` by Node0 and Node1
- Competing `OuterUpgradeReq/Ack/Done` or `BUSY/RETRY`
- `UPGRADE_PENDING` serialization
- Invalidate of the losing sharer
- Final `ReadShared` convergence checks

**Scenario sketch**
1. Home node initializes X to `0x55`.
2. Node0 and Node1 both read X, becoming sharers.
3. Barrier release: Node0 stores `0xA0A0`, Node1 stores `0xB0B0` concurrently.
4. After both stores retire/retry, Node0, Node1, and Node2 all read X.

**Expected behavior**
- Exactly one serialization order is observed; final value must be in `{0xA0A0, 0xB0B0}`.
- All 3 nodes must agree on the same final value.
- No stale `0x55` may survive.
- Logs should show one upgrade blocked or retried while the other is in `UPGRADE_PENDING`.

**Python verifier logic sketch**
```python
def verify_tc16(reads, lines):
    legal = {0xA0A0, 0xB0B0}
    finals = collect_last_read_per_node(reads, nodes=(0,1,2))
    if len(finals) != 3:
        return False, 'TC16 FAILED: missing final reads from one or more nodes', []
    vals = set(finals.values())
    if len(vals) != 1:
        return False, f'TC16 FAILED: nodes disagree on final value {finals}', []
    val = next(iter(vals))
    if val not in legal:
        return False, f'TC16 FAILED: illegal final value 0x{val:X}', []
    if not any('UPGRADE_PENDING' in l or 'OuterUpgrade' in l for l in lines):
        return False, 'TC16 FAILED: no upgrade-path evidence in log', []
    return True, f'TC16 PASSED: concurrent upgrades serialized to 0x{val:X}', []
```

---

## TC17 — `writeback_dma_remote_read_overlap`

**Description**  
Combines dirty-owner writeback, remote read, and memory-side DMA overwrite on the same line.

**Why this is new**  
TC7 checks eviction then read. It does **not** cover the harder case where a remote read overlaps the writeback window and the line is later modified by DMA/home-memory-side traffic.

**Protocol paths exercised**
- Dirty remote owner write (`ReadUnique` / `WriteNoSnp`)
- Eviction / writeback / `NCBWrData` to home DDR4
- Remote read during or immediately after writeback
- DMA/home-memory overwrite
- Post-DMA remote read refill from home memory

**Test harness note**  
This TC needs a small test-only DMA hook in the Python harness, e.g. `dma_store(home_node, pa, value)` using a functional write into the home memory object between barriers.

**Scenario sketch**
1. Node0 writes `V1=0x12345678` to X (home=Node1), becoming dirty owner.
2. Node0 starts eviction/flush traffic on X.
3. Node2 reads X while writeback is draining → must see `V1`.
4. Harness injects DMA write `V2=0x87654321` to home DDR4 for X after writeback completion marker.
5. Node2 reads X again; Node0 also reads X again.

**Expected behavior**
- The first remote read returns `V1`.
- After DMA overwrite, all later reads return `V2`.
- No stale resurrection of pre-DMA cached value is allowed.

**Python verifier logic sketch**
```python
def verify_tc17(reads, lines):
    # Require workload markers that distinguish pre-DMA vs post-DMA reads.
    pre = extract_tagged_read(lines, tag='pre_dma_node2')
    post2 = extract_tagged_read(lines, tag='post_dma_node2')
    post0 = extract_tagged_read(lines, tag='post_dma_node0')
    if pre != 0x12345678:
        return False, f'TC17 FAILED: pre-DMA remote read got 0x{pre:X}', []
    if post2 != 0x87654321 or post0 != 0x87654321:
        return False, f'TC17 FAILED: post-DMA reads stale: node2=0x{post2:X}, node0=0x{post0:X}', []
    return True, 'TC17 PASSED: writeback + DMA + remote-read interaction correct', []
```

---

## Recommended implementation order

1. **TC16** — highest protocol risk, directly targets `UPGRADE_PENDING` serialization.  
2. **TC13** — strongest missing memory-ordering coverage.  
3. **TC15** — validates recovery/forward progress under pressure.  
4. **TC17** — best for catching stale-data source regressions.  
5. **TC14** — broad regression test for repeated mixed sharer/write waves.
