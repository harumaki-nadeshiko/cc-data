#include "BackstoreHostH64.hh"
#include "BackstoreSchemaH64.hh"

#include <cstdio>
#include <cstring>
#include <algorithm>

namespace cc { namespace glob {

BackstoreHostH64::BackstoreHostH64(const H64HostConfig& cfg, MetaRNFClientIF* metaRNF)
    : _cfg(cfg), _metaRNF(metaRNF)
{
    _groupCtrlsSize = cfg.num_groups;
    _groupCtrls.reset(new GroupCtrlCache[cfg.num_groups]);
    for (size_t i = 0; i < cfg.num_groups; ++i) _groupCtrls[i] = {false, 0, 0, 0};
    for (int i = 0; i < kMaxSlots; ++i) _slots[i].state = SlotState::Free;
}

BackstoreHostH64::~BackstoreHostH64() = default;

size_t BackstoreHostH64::flatBucketIdx(size_t g, size_t b) const { return g * _cfg.buckets_per_group + b; }

size_t BackstoreHostH64::tableBucketOffset(size_t g, size_t b) const {
    return _cfg.bucketDataOffset(g, b);
}

size_t BackstoreHostH64::groupForPa(uint64_t pa) const {
    return BackstoreSchemaH64::groupForPaStatic(pa, _cfg.num_groups, _cfg.hash_seed);
}

size_t BackstoreHostH64::homeBucketForPa(uint64_t pa) const {
    return BackstoreSchemaH64::homeBucketForPaStatic(pa, _cfg.buckets_per_group, _cfg.hash_seed);
}

BackstoreHostH64::BucketState*
BackstoreHostH64::findBucketState(size_t flatIdx) {
    for (auto& state : _bucketStates) {
        if (state.valid && state.flatIdx == flatIdx) return &state;
    }
    return nullptr;
}

const BackstoreHostH64::BucketState*
BackstoreHostH64::findBucketState(size_t flatIdx) const {
    for (const auto& state : _bucketStates) {
        if (state.valid && state.flatIdx == flatIdx) return &state;
    }
    return nullptr;
}

BackstoreHostH64::BucketState*
BackstoreHostH64::acquireBucketState(size_t flatIdx) {
    if (BucketState* state = findBucketState(flatIdx)) return state;
    for (auto& state : _bucketStates) {
        if (!state.valid) {
            state = BucketState();
            state.valid = true;
            state.flatIdx = flatIdx;
            return &state;
        }
    }
    return nullptr;
}

void
BackstoreHostH64::releaseBucketStateIfIdle(size_t flatIdx) {
    BucketState* state = findBucketState(flatIdx);
    if (state && !state->locked && state->waiterCount == 0) {
        *state = BucketState();
    }
}

void
BackstoreHostH64::enqueueRmwCredit(int slotIdx) {
    if (_rmwCreditCount >= kMaxSlots) {
        _slots[slotIdx].result.status = BackstoreStatus::IoError;
        completeSlot(slotIdx);
        return;
    }
    int pos = (_rmwCreditHead + _rmwCreditCount) % kMaxSlots;
    _rmwCreditWaiters[pos] = slotIdx;
    ++_rmwCreditCount;
    _slots[slotIdx].state = SlotState::RmwCreditPending;
}

void
BackstoreHostH64::resumeRmwCredits() {
    while (_rmwCreditCount > 0 && activeRmwCount() < _cfg.max_active_rmw) {
        int slotIdx = _rmwCreditWaiters[_rmwCreditHead];
        _rmwCreditHead = (_rmwCreditHead + 1) % kMaxSlots;
        --_rmwCreditCount;
        if (slotIdx < 0 || slotIdx >= kMaxSlots ||
            _slots[slotIdx].state != SlotState::RmwCreditPending) {
            continue;
        }
        startRmwWrite(slotIdx);
    }
}

// ---- Bounded slot allocator ----

int BackstoreHostH64::allocSlot() {
    if (_slotCount >= kMaxSlots) return -1;
    for (int i = 0; i < kMaxSlots; ++i) {
        if (_slots[i].state == SlotState::Free) {
            _slots[i] = TxnSlot(); // fresh init
            _slots[i].state = SlotState::Probing;
            _slotCount++;
            return i;
        }
    }
    return -1;
}

void BackstoreHostH64::freeSlot(int idx) {
    if (idx < 0 || idx >= kMaxSlots) return;
    if (_slots[idx].state != SlotState::Free) {
        _slots[idx].state = SlotState::Free;
        _slotCount--;
    }
}

// Introspection
int BackstoreHostH64::activeSlotCount() const { return _slotCount; }
int BackstoreHostH64::activeRmwCount() const {
    int c = 0;
    for (const auto& state : _bucketStates) if (state.valid && state.locked) ++c;
    return c;
}
int BackstoreHostH64::bucketWaiterCount(size_t flatIdx) const {
    const BucketState* state = findBucketState(flatIdx);
    return state ? state->waiterCount : 0;
}
bool BackstoreHostH64::isBucketLocked(size_t flatIdx) const {
    const BucketState* state = findBucketState(flatIdx);
    return state && state->locked;
}

BackstoreStatus BackstoreHostH64::mapMetaRNFStatus(MetaRNFLineStatus ms) {
    switch (ms) {
        case MetaRNFLineStatus::Ok: return BackstoreStatus::Ok;
        case MetaRNFLineStatus::RetryableBusy: return BackstoreStatus::RetryableBusy;
        case MetaRNFLineStatus::IoError: return BackstoreStatus::IoError;
        case MetaRNFLineStatus::Corrupt: return BackstoreStatus::Corrupt;
        case MetaRNFLineStatus::RangeError: return BackstoreStatus::IoError;
        case MetaRNFLineStatus::InvalidArgument: return BackstoreStatus::InvalidArgument;
    }
    return BackstoreStatus::IoError;
}

// ---- Bounded same-PA duplicate check (no std::map) ----
// Scan all non-Free slots for matching linePa. Returns true if PA already pending.
// This is a member function so it can access TxnSlot type.

bool BackstoreHostH64::isPaBusy(uint64_t linePa) const {
    for (int i = 0; i < kMaxSlots; ++i) {
        if (_slots[i].state != SlotState::Free && _slots[i].linePa == linePa) {
            return true;
        }
    }
    return false;
}

static void replyBusy(uint64_t pa, BackstoreOp op, uint64_t snapEpoch,
                      std::function<void(const BackstoreCompletion&)>& cb) {
    BackstoreCompletion r; r.linePa = pa; r.op = op;
    r.status = BackstoreStatus::RetryableBusy; r.snapshotEpoch = snapEpoch;
    if (cb) cb(r);
}

// ============================================================
// Public API
// ============================================================

void BackstoreHostH64::lookup(uint64_t linePa,
    std::function<void(const BackstoreCompletion&)> completion) {
    if (isPaBusy(linePa)) {
        replyBusy(linePa, BackstoreOp::Lookup, 0, completion); return;
    }
    int si = allocSlot();
    if (si < 0) { replyBusy(linePa, BackstoreOp::Lookup, 0, completion); return; }
    _slots[si].linePa = linePa;
    _slots[si].op = BackstoreOp::Lookup;
    _slots[si].snapshotEpoch = 0;
    _slots[si].cb = std::move(completion);
    ensureGroupControl(si);
}

void
BackstoreHostH64::scanGroupLive(
    size_t groupIdx, std::function<void(const H64SlotEntry&)> onLive,
    std::function<void(BackstoreStatus)> completion)
{
    if (groupIdx >= _cfg.num_groups) {
        if (completion) completion(BackstoreStatus::InvalidArgument);
        return;
    }
    for (int i = 0; i < kMaxGroupScans; ++i) {
        if (_groupScans[i].active)
            continue;
        GroupScan &scan = _groupScans[i];
        scan = GroupScan();
        scan.active = true;
        scan.groupIdx = groupIdx;
        scan.onLive = std::move(onLive);
        scan.completion = std::move(completion);
        _metaRNF->readLine(_cfg.groupControlOffset(groupIdx),
            [this, i](MetaRNFLineStatus st, const uint8_t *data64) {
                onGroupScanControl(i, st, data64);
            });
        return;
    }
    if (completion) completion(BackstoreStatus::RetryableBusy);
}

void
BackstoreHostH64::onGroupScanControl(int scanIdx, MetaRNFLineStatus st,
                                     const uint8_t *data64)
{
    if (scanIdx < 0 || scanIdx >= kMaxGroupScans || !_groupScans[scanIdx].active)
        return;
    if (st != MetaRNFLineStatus::Ok || !data64) {
        completeGroupScan(scanIdx, mapMetaRNFStatus(st));
        return;
    }
    H64GroupControl ctrl;
    ctrl.loadFrom(data64);
    if (!ctrl.valid() || ctrl.active_bucket_count > _cfg.buckets_per_group) {
        completeGroupScan(scanIdx, BackstoreStatus::Corrupt);
        return;
    }
    _groupScans[scanIdx].activeBuckets = ctrl.active_bucket_count;
    _groupScans[scanIdx].nextBucket = 0;
    readGroupScanBucket(scanIdx);
}

void
BackstoreHostH64::readGroupScanBucket(int scanIdx)
{
    GroupScan &scan = _groupScans[scanIdx];
    if (scan.nextBucket >= scan.activeBuckets) {
        completeGroupScan(scanIdx, BackstoreStatus::Ok);
        return;
    }
    const size_t bucket = scan.nextBucket;
    _metaRNF->readLine(tableBucketOffset(scan.groupIdx, bucket),
        [this, scanIdx](MetaRNFLineStatus st, const uint8_t *data64) {
            onGroupScanBucket(scanIdx, st, data64);
        });
}

void
BackstoreHostH64::onGroupScanBucket(int scanIdx, MetaRNFLineStatus st,
                                    const uint8_t *data64)
{
    if (scanIdx < 0 || scanIdx >= kMaxGroupScans || !_groupScans[scanIdx].active)
        return;
    if (st != MetaRNFLineStatus::Ok || !data64) {
        completeGroupScan(scanIdx, mapMetaRNFStatus(st));
        return;
    }
    H64BucketLine bucket;
    std::memcpy(&bucket, data64, sizeof(bucket));
    if (!bucket.hdrValid()) {
        completeGroupScan(scanIdx, BackstoreStatus::Corrupt);
        return;
    }
    GroupScan &scan = _groupScans[scanIdx];
    for (int slot = 0; slot < static_cast<int>(kSlotsPerBucket); ++slot) {
        H64SlotEntry entry;
        H64Codec::unpack(bucket.slotAt(slot), entry);
        if (!checkIntegrity(entry) || entry.state == H64SlotState::RESERVED) {
            completeGroupScan(scanIdx, BackstoreStatus::Corrupt);
            return;
        }
        if (entry.state == H64SlotState::LIVE && scan.onLive)
            scan.onLive(entry);
    }
    ++scan.nextBucket;
    readGroupScanBucket(scanIdx);
}

void
BackstoreHostH64::completeGroupScan(int scanIdx, BackstoreStatus status)
{
    GroupScan &scan = _groupScans[scanIdx];
    auto completion = std::move(scan.completion);
    scan = GroupScan();
    if (completion)
        completion(status);
}

void BackstoreHostH64::upsert(uint64_t linePa, UBCCMESIState state,
    uint64_t sharersMask, uint64_t epoch,
    std::function<void(const BackstoreCompletion&)> completion) {
    if (isPaBusy(linePa)) {
        replyBusy(linePa, BackstoreOp::Upsert, epoch, completion); return;
    }
    int si = allocSlot();
    if (si < 0) { replyBusy(linePa, BackstoreOp::Upsert, epoch, completion); return; }
    _slots[si].linePa = linePa;
    _slots[si].op = BackstoreOp::Upsert;
    _slots[si].snapshotEpoch = epoch;
    _slots[si].cb = std::move(completion);
    _slots[si].upsertEntry.pa = linePa;
    _slots[si].upsertEntry.mesi = state;
    _slots[si].upsertEntry.sharers = static_cast<uint16_t>(sharersMask & 0xFFFF);
    _slots[si].upsertEntry.epoch = static_cast<uint32_t>(epoch & 0xFFFFFF);
    _slots[si].upsertEntry.state = H64SlotState::LIVE;
    _slots[si].upsertEntry.integrity = H64Codec::computeIntegrity(
        linePa, static_cast<uint8_t>(state), static_cast<uint8_t>(H64SlotState::LIVE),
        static_cast<uint16_t>(sharersMask & 0xFFFF), static_cast<uint32_t>(epoch & 0xFFFFFF));
    _slots[si].upsertDataReady = true;
    ensureGroupControl(si);
}

void BackstoreHostH64::erase(uint64_t linePa, uint64_t deleteEpoch,
    std::function<void(const BackstoreCompletion&)> completion) {
    if (isPaBusy(linePa)) {
        replyBusy(linePa, BackstoreOp::Erase, deleteEpoch, completion); return;
    }
    int si = allocSlot();
    if (si < 0) { replyBusy(linePa, BackstoreOp::Erase, deleteEpoch, completion); return; }
    _slots[si].linePa = linePa;
    _slots[si].op = BackstoreOp::Erase;
    _slots[si].snapshotEpoch = deleteEpoch;
    _slots[si].cb = std::move(completion);
    ensureGroupControl(si);
}

// ============================================================
// Group control record lifecycle
// ============================================================

void BackstoreHostH64::ensureGroupControl(int slotIdx) {
    auto& txn = _slots[slotIdx];
    txn.groupIdx = groupForPa(txn.linePa);
    if (txn.groupIdx >= _groupCtrlsSize) {
        txn.result.status = BackstoreStatus::IoError;
        txn.result.linePa = txn.linePa; txn.result.op = txn.op;
        completeSlot(slotIdx); return;
    }

    auto& gc = _groupCtrls[txn.groupIdx];

    if (gc.valid) {
        txn.activeBuckets = gc.active_bucket_count;
        if (txn.activeBuckets == 0) txn.activeBuckets = _cfg.buckets_per_group;
        startProbe(slotIdx);
        return;
    }

    // Control not yet cached.  Initiate read (multiple slots may do this
    // independently — reading a control record is idempotent).
    txn.state = SlotState::WaitingControl;
    uint64_t ctrlOffset = _cfg.groupControlOffset(txn.groupIdx);

    if (_debugEnabled) {
        std::fprintf(stderr, "[DEBUG-H64-CTRL-READ] pa=0x%lx group=%zu ctrlOffset=%lu\n",
                     txn.linePa, txn.groupIdx, ctrlOffset);
    }

    _metaRNF->readLine(ctrlOffset, [this, slotIdx](MetaRNFLineStatus st, const uint8_t* data64) {
        onGroupControlRead(slotIdx, st, data64);
    });
}

void BackstoreHostH64::onGroupControlRead(int slotIdx, MetaRNFLineStatus st, const uint8_t* data64) {
    auto& txn = _slots[slotIdx];
    auto& gc = _groupCtrls[txn.groupIdx];

    if (_debugEnabled) std::fprintf(stderr, "[DEBUG-H64-CTRL-READ-CB] slot=%d st=%d group=%zu pa=0x%lx\n",
                 slotIdx, (int)st, txn.groupIdx, txn.linePa);

    if (st != MetaRNFLineStatus::Ok) {
        if (_debugEnabled) std::fprintf(stderr, "[DEBUG-H64-CTRL-READ-FAIL] slot=%d st=%d -> completeSlot\n",
                     slotIdx, (int)st);
        txn.result.status = mapMetaRNFStatus(st);
        txn.result.linePa = txn.linePa; txn.result.op = txn.op;
        completeSlot(slotIdx); return;
    }

    H64GroupControl ctrl;
    if (data64) ctrl.loadFrom(data64);

    if (!ctrl.valid()) {
        if (_debugEnabled) std::fprintf(stderr, "[DEBUG-H64-CTRL-INIT] slot=%d group=%zu -> writeLine\n",
                     slotIdx, txn.groupIdx);
        // Uninitialized — write a default control record
        H64GroupControl fresh;
        fresh.active_bucket_count = static_cast<uint32_t>(_cfg.buckets_per_group);
        fresh.salt = (txn.groupIdx * 0x9e3779b97f4a7c15ULL) ^ 0x12345678ULL;
        fresh.generation = 1;

        if (_debugEnabled) {
            std::fprintf(stderr, "[DEBUG-H64-CTRL-INIT] group=%zu active=%u salt=0x%lx gen=%u\n",
                         txn.groupIdx, fresh.active_bucket_count, fresh.salt, fresh.generation);
        }

        uint8_t raw[64];
        fresh.storeTo(raw);
        uint64_t ctrlOffset = _cfg.groupControlOffset(txn.groupIdx);

        _metaRNF->writeLine(ctrlOffset, raw,
            [this, slotIdx](MetaRNFLineStatus wst) {
            auto& tx2 = _slots[slotIdx];
            auto& gc2 = _groupCtrls[tx2.groupIdx];
            if (wst == MetaRNFLineStatus::Ok) {
                gc2.valid = true;
                gc2.active_bucket_count = static_cast<uint32_t>(_cfg.buckets_per_group);
                gc2.salt = (tx2.groupIdx * 0x9e3779b97f4a7c15ULL) ^ 0x12345678ULL;
                gc2.generation = 1;
            }
            // Even on write failure, proceed with configured active count
            tx2.activeBuckets = _cfg.buckets_per_group;
            startProbe(slotIdx);
        });
        return;
    }

    // Valid control record found
    gc.valid = true;
    gc.active_bucket_count = ctrl.active_bucket_count;
    gc.salt = ctrl.salt;
    gc.generation = ctrl.generation;
    txn.activeBuckets = ctrl.active_bucket_count;
    if (txn.activeBuckets == 0) txn.activeBuckets = _cfg.buckets_per_group;

    if (_debugEnabled) {
        std::fprintf(stderr, "[DEBUG-H64-CTRL-OK] group=%zu active=%u salt=0x%lx gen=%u\n",
                     txn.groupIdx, gc.active_bucket_count, gc.salt, gc.generation);
    }

    startProbe(slotIdx);
}

// ============================================================
// Probe state machine
// ============================================================

void BackstoreHostH64::startProbe(int slotIdx) {
    auto& txn = _slots[slotIdx];
    uint64_t pa = txn.linePa;
    txn.homeBucket = homeBucketForPa(pa);
    txn.probeIdx = 0;
    txn.tombstoneSeen = false; txn.tombstoneSlot = -1;
    txn.emptySeen = false; txn.emptySlot = -1;
    if (txn.activeBuckets == 0) txn.activeBuckets = _cfg.buckets_per_group;

    size_t bucketIdx = (txn.homeBucket + txn.probeIdx) % txn.activeBuckets;
    size_t bucketOff = tableBucketOffset(txn.groupIdx, bucketIdx);

    if (_debugEnabled) std::fprintf(stderr, "[DEBUG-H64-PROBE-START] slot=%d pa=0x%lx group=%zu homeBucket=%zu probe=%zu bucketOff=%zu active=%zu\n",
                 slotIdx, pa, txn.groupIdx, txn.homeBucket, txn.probeIdx, bucketOff, txn.activeBuckets);

    if (!h64BucketOffsetInRange(bucketOff, _cfg.metadata_socket_lines * 64ULL)) {
        txn.result.status = BackstoreStatus::IoError;
        txn.result.linePa = pa; txn.result.op = txn.op;
        completeSlot(slotIdx); return;
    }

    txn.state = SlotState::Probing;
    _metaRNF->readLine(bucketOff, [this, slotIdx](MetaRNFLineStatus st, const uint8_t* data64) {
        onProbeBucketRead(slotIdx, st, data64);
    });
}

void BackstoreHostH64::onProbeBucketRead(int slotIdx, MetaRNFLineStatus st, const uint8_t* data64) {
    auto& txn = _slots[slotIdx];
    uint64_t pa = txn.linePa;

    if (_debugEnabled) std::fprintf(stderr, "[DEBUG-H64-PROBE-READ-CB] slot=%d st=%d probe=%zu pa=0x%lx\n",
                 slotIdx, (int)st, txn.probeIdx, pa);

    if (st == MetaRNFLineStatus::RetryableBusy) {
        // Gem5 MetaRNF rejected (TBE full, buffer full). Retry the same
        // read after a short deferral; do NOT abort the transaction.
        // Use the same logical offset computed for this probe step.
        size_t bucketIdx = (txn.homeBucket + txn.probeIdx) % txn.activeBuckets;
        size_t bucketOff = tableBucketOffset(txn.groupIdx, bucketIdx);
        _metaRNF->readLine(bucketOff, [this, slotIdx](MetaRNFLineStatus st2, const uint8_t* d2) {
            onProbeBucketRead(slotIdx, st2, d2);
        });
        return;
    }
    if (st != MetaRNFLineStatus::Ok) {
        txn.result.status = mapMetaRNFStatus(st);
        txn.result.linePa = pa; txn.result.op = txn.op;
        completeSlot(slotIdx); return;
    }

    H64BucketLine bucket;
    if (data64) std::memcpy(&bucket, data64, 64);

    size_t bucketIdx = (txn.homeBucket + txn.probeIdx) % txn.activeBuckets;

    // All-zero bucket → valid EMPTY
    bool allZero = true;
    if (data64) for (size_t i = 0; i < 64; ++i) if (data64[i]) { allZero = false; break; }
    if (allZero) {
        txn.emptySeen = true; txn.emptyBucket = bucketIdx; txn.emptySlot = 0;
        if (txn.op == BackstoreOp::Lookup) {
            txn.result.status = BackstoreStatus::Ok; txn.result.found = false;
            completeSlot(slotIdx);
        } else if (txn.op == BackstoreOp::Erase) {
            txn.result.status = BackstoreStatus::AlreadyAbsent;
            completeSlot(slotIdx);
        } else {
            txn.rmwBucket.clear();
            uint8_t p[12]; H64Codec::pack(txn.upsertEntry, p);
            std::memcpy(txn.rmwBucket.slotAt(0), p, 12);
            txn.rmwBucket.setLiveCount(1);
            txn.rmwBucketIdx = bucketIdx;
            txn.rmwFlatIdx = flatBucketIdx(txn.groupIdx, bucketIdx);
            txn.rmwReadDone = true;
            startRmwWrite(slotIdx);
        }
        return;
    }

    bool hdrValid = bucket.hdrValid();
    bool anyCorrupt = false;

    for (int s = 0; s < (int)kSlotsPerBucket; ++s) {
        if (!hdrValid) { anyCorrupt = true; break; }
        H64SlotEntry slot;
        H64Codec::unpack(bucket.slotAt(s), slot);
        if (slot.state == H64SlotState::RESERVED) { anyCorrupt = true; break; }
        if (slot.state != H64SlotState::EMPTY && !checkIntegrity(slot)) { anyCorrupt = true; break; }
        if (slot.state == H64SlotState::HASH_TOMBSTONE && !txn.tombstoneSeen) {
            txn.tombstoneSeen = true; txn.tombstoneBucket = bucketIdx; txn.tombstoneSlot = s;
        }
        if (slot.state == H64SlotState::EMPTY && !txn.emptySeen) {
            txn.emptySeen = true; txn.emptyBucket = bucketIdx; txn.emptySlot = s;
        }
        if (slot.state == H64SlotState::LIVE && slot.pa == pa) {
            txn.result.found = true; txn.result.linePa = pa; txn.result.op = txn.op;
            txn.result.state = slot.mesi; txn.result.sharersMask = slot.sharers;
            txn.result.epoch = slot.epoch; txn.result.existed = true;

            if (txn.op == BackstoreOp::Lookup) {
                txn.result.status = BackstoreStatus::Ok;
                completeSlot(slotIdx); return;
            }
            if (txn.op == BackstoreOp::Upsert) {
                if (txn.snapshotEpoch < slot.epoch) {
                    txn.result.status = BackstoreStatus::StaleEpoch;
                    completeSlot(slotIdx); return;
                }
                txn.rmwBucket = bucket; txn.rmwBucketIdx = bucketIdx;
                txn.rmwFlatIdx = flatBucketIdx(txn.groupIdx, bucketIdx);
                txn.rmwReadDone = true;
                uint8_t p[12]; H64Codec::pack(txn.upsertEntry, p);
                std::memcpy(txn.rmwBucket.slotAt(s), p, 12);
                startRmwWrite(slotIdx); return;
            }
            if (txn.op == BackstoreOp::Erase) {
                if (txn.snapshotEpoch < slot.epoch) {
                    txn.result.status = BackstoreStatus::StaleEpoch;
                    completeSlot(slotIdx); return;
                }
                txn.rmwBucket = bucket; txn.rmwBucketIdx = bucketIdx;
                txn.rmwFlatIdx = flatBucketIdx(txn.groupIdx, bucketIdx);
                txn.rmwReadDone = true;
                slot.state = H64SlotState::HASH_TOMBSTONE;
                slot.integrity = H64Codec::computeIntegrity(
                    slot.pa, static_cast<uint8_t>(slot.mesi),
                    static_cast<uint8_t>(H64SlotState::HASH_TOMBSTONE),
                    slot.sharers, slot.epoch);
                uint8_t p[12]; H64Codec::pack(slot, p);
                std::memcpy(txn.rmwBucket.slotAt(s), p, 12);
                uint8_t lc = txn.rmwBucket.liveCount();
                if (lc > 0) txn.rmwBucket.setLiveCount(lc - 1);
                txn.rmwBucket.setTombstoneCount(txn.rmwBucket.tombstoneCount() + 1);
                startRmwWrite(slotIdx); return;
            }
        }
    }

    if (anyCorrupt) {
        txn.result.status = BackstoreStatus::Corrupt;
        txn.result.linePa = pa; txn.result.op = txn.op;
        completeSlot(slotIdx); return;
    }

    if (txn.emptySeen) {
        if (txn.op == BackstoreOp::Lookup) {
            txn.result.status = BackstoreStatus::Ok; txn.result.found = false;
            completeSlot(slotIdx); return;
        }
        if (txn.op == BackstoreOp::Erase) {
            txn.result.status = BackstoreStatus::AlreadyAbsent;
            completeSlot(slotIdx); return;
        }
        // Upsert: tombstone or empty
        size_t tb; int ts; bool reuse = txn.tombstoneSeen;
        if (reuse) { tb = txn.tombstoneBucket; ts = txn.tombstoneSlot; }
        else { tb = txn.emptyBucket; ts = txn.emptySlot; }

        size_t tFlat = flatBucketIdx(txn.groupIdx, tb);
        if (tb != bucketIdx) {
            txn.rmwReadDone = false; txn.rmwBucketIdx = tb; txn.rmwFlatIdx = tFlat;
            size_t tOff = tableBucketOffset(txn.groupIdx, tb);
            _metaRNF->readLine(tOff,
                [this, slotIdx, reuse, tb, ts](MetaRNFLineStatus st2, const uint8_t* d2) {
                auto& tx = _slots[slotIdx];
                if (st2 != MetaRNFLineStatus::Ok) {
                    tx.result.status = mapMetaRNFStatus(st2); completeSlot(slotIdx); return;
                }
                std::memcpy(&tx.rmwBucket, d2, 64);
                tx.rmwBucketIdx = tb; tx.rmwReadDone = true;
                uint8_t p[12]; H64Codec::pack(tx.upsertEntry, p);
                std::memcpy(tx.rmwBucket.slotAt(ts), p, 12);
                if (reuse) { uint8_t tc = tx.rmwBucket.tombstoneCount(); if (tc) tx.rmwBucket.setTombstoneCount(tc-1); }
                tx.rmwBucket.setLiveCount(tx.rmwBucket.liveCount() + 1);
                startRmwWrite(slotIdx);
            });
            return;
        }
        txn.rmwBucket = bucket; txn.rmwBucketIdx = tb; txn.rmwFlatIdx = tFlat; txn.rmwReadDone = true;
        uint8_t p[12]; H64Codec::pack(txn.upsertEntry, p);
        std::memcpy(txn.rmwBucket.slotAt(ts), p, 12);
        if (reuse) { uint8_t tc = txn.rmwBucket.tombstoneCount(); if (tc) txn.rmwBucket.setTombstoneCount(tc-1); }
        txn.rmwBucket.setLiveCount(txn.rmwBucket.liveCount() + 1);
        startRmwWrite(slotIdx); return;
    }

    // Continue probe
    txn.probeIdx++;
    if (txn.probeIdx >= txn.activeBuckets) {
        if (txn.op == BackstoreOp::Lookup) {
            txn.result.status = BackstoreStatus::CapacityExhausted; txn.result.found = false;
        } else txn.result.status = BackstoreStatus::CapacityExhausted;
        completeSlot(slotIdx); return;
    }

    size_t nextIdx = (txn.homeBucket + txn.probeIdx) % txn.activeBuckets;
    size_t nextOff = tableBucketOffset(txn.groupIdx, nextIdx);
    if (!h64BucketOffsetInRange(nextOff, _cfg.metadata_socket_lines * 64ULL)) {
        txn.result.status = BackstoreStatus::IoError; completeSlot(slotIdx); return;
    }
    _metaRNF->readLine(nextOff, [this, slotIdx](MetaRNFLineStatus st3, const uint8_t* d3) {
        onProbeBucketRead(slotIdx, st3, d3);
    });
}

// ============================================================
// RMW Write with per-bucket waiter serialization
// ============================================================

void BackstoreHostH64::startRmwWrite(int slotIdx) {
    auto& txn = _slots[slotIdx];
    if (!txn.rmwReadDone) return;

    size_t flatIdx = txn.rmwFlatIdx;
    if (activeRmwCount() >= _cfg.max_active_rmw) {
        enqueueRmwCredit(slotIdx);
        return;
    }

    BucketState* bs = acquireBucketState(flatIdx);
    if (!bs) {
        txn.result.status = BackstoreStatus::RetryableBusy; completeSlot(slotIdx); return;
    }

    if (bs->locked) {
        if (bs->waiterCount >= _cfg.max_waiters_per_bucket) {
            txn.result.status = BackstoreStatus::RetryableBusy; completeSlot(slotIdx); return;
        }
        int wi = (bs->waiterHead + bs->waiterCount) % kMaxWaitersPerBucket;
        bs->waiters[wi].slotIdx = slotIdx;
        bs->waiters[wi].arrivalSeq = _arrivalSeq++;
        bs->waiterCount++;
        txn.state = SlotState::RmwPending;
        return;
    }

    bs->locked = true;
    txn.state = SlotState::RmwPending;

    size_t off = tableBucketOffset(txn.groupIdx, txn.rmwBucketIdx);
    uint8_t buf[64];
    std::memcpy(buf, &txn.rmwBucket, 64);
    _metaRNF->writeLine(off, buf,
        [this, slotIdx, flatIdx](MetaRNFLineStatus st) {
        auto& tx = _slots[slotIdx];
        BucketState* bst = findBucketState(flatIdx);
        if (!bst) {
            tx.result.status = BackstoreStatus::IoError;
            completeSlot(slotIdx);
            return;
        }
        bst->locked = false;
        if (st != MetaRNFLineStatus::Ok) {
            tx.result.status = mapMetaRNFStatus(st);
            completeSlot(slotIdx);
        } else {
            switch (tx.op) {
                case BackstoreOp::Upsert: tx.result.status = BackstoreStatus::Ok; break;
                case BackstoreOp::Erase:  tx.result.status = BackstoreStatus::Ok; tx.result.existed = true; break;
                default: break;
            }
            completeSlot(slotIdx);
        }
        resumeBucketWaiter(flatIdx);
        resumeRmwCredits();
    });
}

void BackstoreHostH64::resumeBucketWaiter(size_t flatIdx) {
    BucketState* bs = findBucketState(flatIdx);
    if (!bs) return;
    if (bs->waiterCount == 0) {
        releaseBucketStateIfIdle(flatIdx);
        return;
    }

    // Select oldest by arrivalSeq (linear scan, acceptable for <=8)
    int oldestSlot = -1; int oldestPos = -1; uint64_t oldestSeq = ~0ULL;
    for (int i = 0; i < bs->waiterCount; ++i) {
        int wi = (bs->waiterHead + i) % kMaxWaitersPerBucket;
        if (bs->waiters[wi].slotIdx >= 0 && bs->waiters[wi].arrivalSeq < oldestSeq) {
            oldestSeq = bs->waiters[wi].arrivalSeq; oldestSlot = bs->waiters[wi].slotIdx; oldestPos = wi;
        }
    }
    if (oldestSlot < 0) return;

    // Remove from queue
    bs->waiters[oldestPos].slotIdx = -1;
    bs->waiterCount--;
    // Compact if needed: shift entries left to fill gap
    int shift = 0;
    for (int i = 0; i < bs->waiterCount + 1; ++i) {
        int wi = (bs->waiterHead + i) % kMaxWaitersPerBucket;
        if (bs->waiters[wi].slotIdx < 0) shift++;
        else if (shift > 0) {
            int tw = (bs->waiterHead + i - shift + kMaxWaitersPerBucket) % kMaxWaitersPerBucket;
            bs->waiters[tw] = bs->waiters[wi]; bs->waiters[wi].slotIdx = -1;
        }
    }

    rereadForRmw(oldestSlot, flatIdx);
}

void BackstoreHostH64::rereadForRmw(int slotIdx, size_t flatIdx) {
    BucketState* bs = findBucketState(flatIdx);
    if (!bs) {
        _slots[slotIdx].result.status = BackstoreStatus::IoError;
        completeSlot(slotIdx);
        return;
    }
    bs->locked = true;

    auto& txn = _slots[slotIdx];
    size_t off = tableBucketOffset(txn.groupIdx, txn.rmwBucketIdx);

    _metaRNF->readLine(off, [this, slotIdx, flatIdx](MetaRNFLineStatus st, const uint8_t* data64) {
        auto& tx = _slots[slotIdx];
        BucketState* bst = findBucketState(flatIdx);
        if (!bst) {
            tx.result.status = BackstoreStatus::IoError;
            completeSlot(slotIdx);
            return;
        }
        if (st != MetaRNFLineStatus::Ok) {
            bst->locked = false;
            tx.result.status = mapMetaRNFStatus(st); completeSlot(slotIdx);
            resumeBucketWaiter(flatIdx);
            resumeRmwCredits();
            return;
        }

        H64BucketLine fresh;
        if (data64) std::memcpy(&fresh, data64, 64);
        tx.rmwBucket = fresh; tx.rmwReadDone = true;

        uint64_t pa = tx.linePa;
        bool found = false; int foundSlot = -1;

        for (int s = 0; s < (int)kSlotsPerBucket; ++s) {
            H64SlotEntry slot;
            H64Codec::unpack(fresh.slotAt(s), slot);
            if (slot.state == H64SlotState::LIVE && slot.pa == pa) {
                found = true; foundSlot = s;
                if (tx.op == BackstoreOp::Upsert) {
                    if (tx.snapshotEpoch < slot.epoch) {
                        bst->locked = false; tx.result.status = BackstoreStatus::StaleEpoch;
                        completeSlot(slotIdx); resumeBucketWaiter(flatIdx); return;
                    }
                    uint8_t p[12]; H64Codec::pack(tx.upsertEntry, p);
                    std::memcpy(tx.rmwBucket.slotAt(s), p, 12);
                } else if (tx.op == BackstoreOp::Erase) {
                    if (tx.snapshotEpoch < slot.epoch) {
                        bst->locked = false; tx.result.status = BackstoreStatus::StaleEpoch;
                        completeSlot(slotIdx); resumeBucketWaiter(flatIdx); return;
                    }
                    slot.state = H64SlotState::HASH_TOMBSTONE;
                    slot.integrity = H64Codec::computeIntegrity(
                        slot.pa, static_cast<uint8_t>(slot.mesi),
                        static_cast<uint8_t>(H64SlotState::HASH_TOMBSTONE),
                        slot.sharers, slot.epoch);
                    uint8_t p[12]; H64Codec::pack(slot, p);
                    std::memcpy(tx.rmwBucket.slotAt(s), p, 12);
                    uint8_t lc = tx.rmwBucket.liveCount();
                    if (lc > 0) tx.rmwBucket.setLiveCount(lc - 1);
                    tx.rmwBucket.setTombstoneCount(tx.rmwBucket.tombstoneCount() + 1);
                }
                break;
            }
        }

        if (!found && tx.op == BackstoreOp::Upsert) {
            int reuseSlot = -1; bool reuseTomb = false;
            for (int s = 0; s < (int)kSlotsPerBucket; ++s) {
                H64SlotEntry sl;
                H64Codec::unpack(fresh.slotAt(s), sl);
                if (sl.state == H64SlotState::HASH_TOMBSTONE && reuseSlot < 0) { reuseSlot = s; reuseTomb = true; }
                if (sl.state == H64SlotState::EMPTY && reuseSlot < 0) { reuseSlot = s; }
            }
            if (reuseSlot >= 0) {
                uint8_t p[12]; H64Codec::pack(tx.upsertEntry, p);
                std::memcpy(tx.rmwBucket.slotAt(reuseSlot), p, 12);
                if (reuseTomb) { uint8_t tc = tx.rmwBucket.tombstoneCount(); if (tc) tx.rmwBucket.setTombstoneCount(tc-1); }
                tx.rmwBucket.setLiveCount(tx.rmwBucket.liveCount() + 1);
            } else {
                bst->locked = false; tx.result.status = BackstoreStatus::CapacityExhausted;
                completeSlot(slotIdx); resumeBucketWaiter(flatIdx); return;
            }
        } else if (!found && tx.op == BackstoreOp::Erase) {
            bst->locked = false; tx.result.status = BackstoreStatus::AlreadyAbsent;
            completeSlot(slotIdx); resumeBucketWaiter(flatIdx); return;
        }

        size_t wOff = tableBucketOffset(tx.groupIdx, tx.rmwBucketIdx);

        uint8_t buf[64]; std::memcpy(buf, &tx.rmwBucket, 64);
        _metaRNF->writeLine(wOff, buf, [this, slotIdx, flatIdx](MetaRNFLineStatus st2) {
            auto& tx2 = _slots[slotIdx];
            BucketState* bst2 = findBucketState(flatIdx);
            if (!bst2) {
                tx2.result.status = BackstoreStatus::IoError;
                completeSlot(slotIdx);
                return;
            }
            bst2->locked = false;
            if (st2 != MetaRNFLineStatus::Ok) {
                tx2.result.status = mapMetaRNFStatus(st2); completeSlot(slotIdx);
            } else {
                switch (tx2.op) {
                    case BackstoreOp::Upsert: tx2.result.status = BackstoreStatus::Ok; break;
                    case BackstoreOp::Erase:  tx2.result.status = BackstoreStatus::Ok; tx2.result.existed = true; break;
                    default: break;
                }
                completeSlot(slotIdx);
            }
            resumeBucketWaiter(flatIdx);
            resumeRmwCredits();
        });
    });
}

// ============================================================
// Completion
// ============================================================

void BackstoreHostH64::completeSlot(int slotIdx) {
    auto& txn = _slots[slotIdx];
    txn.result.linePa = txn.linePa; txn.result.op = txn.op;
    BackstoreCompletion result = txn.result;
    if (_debugEnabled) std::fprintf(stderr, "[DEBUG-H64-SLOT-COMPLETE] slot=%d pa=0x%lx op=%s status=%s found=%d\n",
                 slotIdx, result.linePa,
                 backstoreOpName(result.op),
                 backstoreStatusName(result.status),
                 result.found ? 1 : 0);
    auto cb = std::move(txn.cb);
    freeSlot(slotIdx);
    if (cb) cb(result);
}

} }
