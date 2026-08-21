# TC134 ordinary writeback wakeup bug

## Symptom

TC134 spill-noopt stopped in the share phase. Eight requesters repeatedly read
the same Home PA while no writer entered `window_pressure`:

```text
PA=0x10010bc0
outstanding=0
resident_waiters=8
residentResult=Queued
fixed reqId retries reached 524288
```

The directory used only 4096 of 57344 entries, so this was not a capacity-full
condition. There were no tuple mismatches, unknown Clears, or stale eviction
completions.

## Root Cause

A normal data-bearing writeback in spill mode transitions the resident entry to
`G_I`, marks metadata dirty, sets `wbPending`, and starts an H64 upsert. Its
durable completion calls `onBackstoreWriteAck()` with:

```text
evictionPending=0
async=0
```

That branch only refreshed the derived pin and returned. It did not clear
`wbPending`, clear matching `residentDirty`, or replay readers retained behind
the metadata write. Every later access therefore remained in the
`MetadataWriteback` waiter queue forever.

## Fix

For a durable ordinary writeback ack with no eviction or async owner:

1. clear `residentDirty` if the snapshot epoch still matches;
2. clear `wbPending` in all cases because that I/O generation completed;
3. refresh the derived pin;
4. replay same-PA resident waiters and same-set capacity waiters;
5. keep the resident entry; do not apply eviction removal semantics.

The focused regression creates a `G_M` owner, performs a normal dirty
writeback, queues a ReadShared behind `wbPending`, and requires the ack to
produce a new pushed grant. Before the fix it failed at `wbPending == false`.

Startup identification:

```text
[UBCC-PROTOCOL-BUILD] revision=20260821-writeback-wakeup-v1
ordinaryWritebackWakeup=1
```
