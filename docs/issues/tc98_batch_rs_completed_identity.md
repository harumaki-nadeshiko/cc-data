# TC98 Timing Batch-RS completed identity bug

## Symptom

TC98 8n2s with `TimingSimpleCPU` and the RubySequencer model default of 16
timed out after 1800 seconds. The home retained an old read transaction while
the requester had advanced through an intervening upgrade to a new ReadUnique:

```text
old R   reqId=144115188075855876
R+1     reqId=144115188075855877
new R+2 reqId=144115188075855878
```

The failure produced 24,246 `UBCC-GRANT-RETRY-TUPLE-MISMATCH` events. The
directory remained resident and pinned; there was no unknown Clear, stale
eviction completion, or Upgrade terminal failure.

## Root Cause

`replayPendingRequesters()` has a Batch-RS fast path for a queued shared read
against `G_S`. It commits immediately, creates an accepted Clear tombstone, and
pushes the grant without creating a live outstanding request.

Unlike the normal Clear commit path, the Batch-RS path did not call:

```cpp
retireCommittedReadWaiters(tempOst);
recordCompletedReadIdentity(tempOst);
```

A delayed duplicate of the committed read could therefore arrive while another
requester owned the PA and enter `_pendingRequesters`. The original Clear was
correctly acknowledged from the tombstone, but that replay did not own or remove
the queued duplicate. After the tombstone expired, the duplicate was replayed,
committed a second time, and created a grant that the requester no longer owned
and would never Clear. The new `R+2` then remained BUSY behind that ghost grant.

The violated invariant was:

> Once `(PA, requester node, socket, reqId)` has committed, later copies of that
> ReadReq may be suppressed idempotently but may never re-enter a pending queue
> or create another live grant.

## Fix

The Batch-RS commit path now performs the same completed-read retirement as the
normal Clear path before creating its tombstone. No Clear matching rule,
outstanding mismatch policy, container, or capacity was changed.

Startup identification:

```text
[UBCC-PROTOCOL-BUILD] revision=20260821-batch-rs-completion-v1
batchRsCompletionIdentity=1
```

## Verification

The focused controller regression first demonstrated the old failure as two
Batch-RS commits and two grant pushes for one reqId. After the fix, the delayed
copy hits `UBCC-COMPLETED-READ-DUPLICATE` and is dropped before queue admission.

End-to-end results:

```text
Timing + model-default sequencer 16:
  before: 1800s TIMEOUT, tuple mismatch=24246
  after:  PASS, 16/16 r12, 16/16 MATCH, tuple mismatch=0, 25/25 exits

O3 + explicit sequencer 16:
  after:  PASS, 16/16 r12, 16/16 MATCH, issues=none, 25/25 exits
```

The fix addresses a protocol lifecycle defect intrinsic to UBCC; it does not
depend on the remote framework, host architecture, or libzmq version.
