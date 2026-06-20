# TC46+ design: multi-beat recall data coverage

## Background

FV-7 found that `EPRNFController::recvDataMsg()` currently does:

```cpp
it->second.recallDataBlk = msg->getdataBlk();
```

for each `CompData` beat, so a multi-beat recall can overwrite previously captured bytes instead of assembling the full 64B line.

Also, `CHI_ubcc_framework.py` currently binds controller `data_channel_size` to `params.data_width`:

- `EPSNFController(... data_channel_size=params.data_width)` at lines 278-279
- `MetaRNFController(... data_channel_size=params.data_width)` at lines 330-331
- `EPRNFController(... data_channel_size=params.data_width)` at lines 354-355
- `for cntrl in all_cntrls: cntrl.data_channel_size = params.data_width` at line 438

The default `NoC_Params.data_width` is currently `32`, so the default system is only 2 beats per 64B line. This TC requires forcing it to `16` so recall returns **4 CHI data beats**.

---

## 1. Test design (how to trigger multi-beat)

### Proposed testcase

- **TC46**: `e2e_tc46_multibeat_recall_readshared`
- Optional follow-on: **TC47** mirror the same idea on `ReadUnique` recall, but TC46 should land first.

### Required harness/config change

- Set `NoC_Params.data_width = 16` for this testcase run.
- Because `data_channel_size` is derived from `params.data_width`, this makes each 64B line transfer as **4 beats × 16B**.

### Workload shape

Use one 64B-aligned DSM line `X` homed on **Node0**.

1. **Phase A: seed home memory with an old full-line pattern**
   - Node0 writes 8 distinct 64-bit words to `X+0, +8, +16, ... +56`.
   - Example old pattern:
     - `O0=0x0000000000000046`
     - `O1=0x1111111111111146`
     - `O2=0x2222222222222246`
     - ...
     - `O7=0x7777777777777746`

2. **Phase B: create dirty owner with a different per-beat pattern**
   - Node1 performs 8 `dsm_store64()` writes to the same line with a new pattern:
     - `N0=0xAAA0000000000046`
     - `N1=0xBBB1111111111146`
     - `N2=0xCCC2222222222246`
     - `N3=0xDDD3333333333346`
     - `N4=0xEEE4444444444446`
     - `N5=0xFFF5555555555546`
     - `N6=0x1236666666666646`
     - `N7=0x4567777777777746`
   - This should leave Node1 as dirty owner (`G_M(owner=Node1)`) while home memory still holds the old `O*` values.

3. **Phase C: force read recall**
   - Node2 issues loads for all 8 words of `X`.
   - This must go through:
     - `EPBackend::handleRemoteMiss()` on requester
     - home UBCC returning `recallNeeded=true`
     - owner `EPBackend::handleRecallRequest()`
     - `EPRNFController::startReadShared()`
     - HN-F `CompData` beats back to EP-RNF
     - recall response to UBCC
     - grant sourced from `RecallBuffer`

4. **Phase D: post-recall confirmation**
   - Node0 reads all 8 words again.
   - This checks that the recalled line installed into home memory is also complete, not just the requester grant.

### Why this reliably exposes the bug

With `data_channel_size=16`, each recall returns 4 beats. If EP-RNF overwrites instead of merges:

- bytes from the last beat (`48..63`) may be correct,
- earlier bytes (`0..47`) will likely come back as stale home-memory data, zero, or otherwise corrupted.

Because each 16B region has a unique signature, the failure points directly to which beats were lost.

---

## 2. Expected protocol flow

### Steady-state setup

1. Node0 initializes `X` with `O0..O7`.
2. Node1 stores `N0..N7` to `X`, becoming dirty owner.

### Recall path under test

3. Node2 issues `ReadShared` miss on `X`.
4. Node2 local HN-F sends `ReadNoSnp` to EP-SNF.
5. Requester-side `EPBackend::handleRemoteMiss()` forwards to home UBCC.
6. Home UBCC sees `G_M(owner=Node1)` and returns `recallNeeded=true`.
7. Requester-side backend sends `OuterRecallMsg` to owner Node1.
8. Owner-side `EPBackend::handleRecallRequest()` calls `EPRNFController::startReadShared(ownerLocalPa, cb)`.
9. Owner EP-RNF sends CHI `ReadShared` to owner-local HN-F.
10. HN-F recalls data from the owner path and returns **4 `CompData` beats**.
11. EP-RNF must assemble beat0+beat1+beat2+beat3 into one 64B `recallDataBlk`.
12. On final beat, EP-RNF sends one `CompAck` and finishes the CHI txn.
13. `finishChiTxn()` pushes the assembled line into `EPBackend::_recallCaptureDataBlock`.
14. Owner backend sends `OuterRecallResponse` with data payload to home UBCC.
15. Home UBCC stores data into `OutstandingRequest::dataBuf` and later creates `GRANT_HANDSHAKE(dataSource=RecallBuffer)`.
16. Requester-side grant returns the recalled 64B line to Node2.
17. Node2 reads must observe `N0..N7` exactly.
18. Node0 reread should also observe `N0..N7` after the home install.

---

## 3. Verification strategy (how to detect data corruption)

### Functional checks

Check all 8 returned 64-bit words, not just one scalar.

For Node2 and Node0 final reads:

- expected: `N0..N7`
- forbidden:
  - any old `O*` word,
  - any zero-filled word,
  - any mixed line where only the last 16B matches.

### Strong corruption signature

Map the 64B line into four 16B beat regions:

- Beat0: words 0-1
- Beat1: words 2-3
- Beat2: words 4-5
- Beat3: words 6-7

Expected failure pattern for the current bug:

- Beat3 correct
- Beat0/1/2 stale or zero

That makes the testcase diagnostic, not just pass/fail.

### Recommended workload-visible outputs

Emit one line per observed word on Node2 and Node0, or at minimum emit four per-beat pass/fail markers:

- `beat0_ok = (w0==N0 && w1==N1)`
- `beat1_ok = (w2==N2 && w3==N3)`
- `beat2_ok = (w4==N4 && w5==N5)`
- `beat3_ok = (w6==N6 && w7==N7)`

The testcase should fail if any beat-level check fails.

### Optional negative/positive comparison

- Run once with default `data_width=32` (2-beat baseline).
- Run again with `data_width=16` (4-beat target).
- The bug is much easier to expose in the 4-beat run.

---

## 4. Instrumentation points needed

Minimum recommended instrumentation:

1. **`EPRNFController::recvDataMsg()`**
   - log `PA`, op (`ReadShared`/`ReadUnique`), `beatsReceived`, `beatsExpected`
   - log `msg->m_bitMask` coverage or byte range for each beat
   - log a small digest of the received chunk

2. **`EPRNFController::finishChiTxn()`**
   - log final assembled `recallDataBlk` digest before calling `setRecallCaptureData()`

3. **`EPBackend::setRecallCaptureData()`**
   - log full-line digest and `valid` bit

4. **`EPBackend::sendRecallResponse()`**
   - log response digest written to home memory

5. **`UBCCController::processRecallResponse()`**
   - log digest copied into `OutstandingRequest::dataBuf`

6. **`UBCCController::copyOutstandingGrantData()` / `UBRouter` ReadResp path**
   - log digest of `RecallBuffer` data carried into requester grant

7. **Requester-side `EPBackend::populateGrantData()` and/or `EPSNFController` grant `CompData` send**
   - log final grant-line digest delivered back to requester

### Suggested log format

Use one stable prefix, for example:

```text
[RECALL-MB] pa=<pa> stage=<stage> beat=<n>/<N> mask=<range> hash=<h>
```

and for final full-line capture:

```text
[RECALL-MB-FINAL] pa=<pa> words=<w0,w1,w2,w3,w4,w5,w6,w7>
```

---

## 5. Note: requires config change (`data_channel_size`)

Yes — this TC is only meaningful if the run forces a multi-beat data path.

- Current framework behavior: `data_channel_size = params.data_width`
- Current default: `data_width = 32`
- Required for TC46: `data_width = 16`

Without that change, the testcase still exercises recall, but not the targeted **4-beat recall assembly** hazard.

## Recommended acceptance criteria

- TC46 passes with `data_width=16`
- logs show exactly **4 recall `CompData` beats** on the owner EP-RNF path
- Node2 final read returns all `N0..N7`
- Node0 reread also returns all `N0..N7`
- no beat-level mismatch, no stale `O*` residue, no zero-filled regions
