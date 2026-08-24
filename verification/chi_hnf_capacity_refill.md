# CHI HN-F capacity/refill model

`tla/chi_hnf_capacity_refill.tla` is a deliberately small model of the HN-F
corner case where directory metadata outlives cache data. Two addresses share
one cache-data slot and one TBE slot. Address `B` initially owns the data slot;
filling `A` evicts `B`, then replacing `A` removes only its cache data and
leaves the metadata-only `EP` (EP-RNF) sentinel.

The directed execution is:

1. downstream fill `A` and complete it;
2. replace `A`, with replacement-TBE `dataValid` captured before eviction;
3. issue `ReadUnique A` while `{EP}` is the sole directory entry;
4. drop the stale sentinel and fetch a clean shared line from downstream;
5. publish that shared grant so the outer `WAITING_CLEAR` transaction retires;
6. complete the EP-RNF upgrade/invalidation barrier;
7. send completion data only after the TBE has a complete line and unique auth;
8. run `CheckCacheFill` after completion and write the line into the L3 slot.

`SendCompDataHasCompleteData` checks that every completion has both request-TBE
`dataValid`, a full valid-byte mask, and completed unique authorization. The
model keeps downstream data arrival, outer shared-grant publication, the EP-RNF
upgrade barrier, and the later L3 fill as separate stages. `DirectedPathCover`
requires the complete sequence to be reached. `BuggySoleEP=TRUE` enables the old
ordering that schedules only `SendCompData`; `chi_hnf_capacity_refill_buggy.cfg`
must produce a `SendCompDataHasCompleteData` counterexample, while the fixed cfg
passes.

The source-contract test `tests/scripts/test_chi_hnf_ep_refill_contract.py`
checks the corresponding SLICC fallback structurally without modifying gem5.
