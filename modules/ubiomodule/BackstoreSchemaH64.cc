#include "BackstoreSchemaH64.hh"

#include <cassert>
#include <cstdio>

namespace cc
{
namespace glob
{

// ============================================================
// H64SlotEntry
// ============================================================

bool H64SlotEntry::operator==(const H64SlotEntry& o) const
{
    return pa == o.pa &&
           mesi == o.mesi &&
           sharers == o.sharers &&
           epoch == o.epoch &&
           state == o.state &&
           integrity == o.integrity;
}

// ============================================================
// Input validation
// ============================================================

const char* validateUpsertEntry(uint64_t callerPa, const H64SlotEntry& entry)
{
    if (!isValidPA(callerPa))
        return "PA not 64B-aligned or exceeds 44 bits";
    if (entry.pa != callerPa)
        return "entry.pa != caller PA";
    if (!isValidPA(entry.pa))
        return "entry.pa not valid";
    if (entry.state != H64SlotState::LIVE)
        return "entry.state must be LIVE for upsert";
    if (static_cast<uint8_t>(entry.mesi) > 3)
        return "invalid MESI state";
    if (static_cast<uint8_t>(entry.state) > 3)
        return "invalid SlotState";

    // Sharers / MESI coherency rules
    switch (entry.mesi) {
        case UBCCMESIState::G_I:
            if (entry.sharers != 0)
                return "G_I must have zero sharers";
            break;
        case UBCCMESIState::G_S:
            // G_S: sharers may be zero or multi-bit (RFC: require ≥1)
            break;
        case UBCCMESIState::G_E:
        case UBCCMESIState::G_M:
            if (!entry.hasOneHotSharers())
                return "G_E/G_M sharers must be one-hot";
            break;
    }

    return nullptr; // OK
}

// ============================================================
// H64Codec
// ============================================================

namespace H64Codec
{

uint8_t computeIntegrity(uint64_t pa, uint8_t mesi,
                         uint8_t slotState, uint16_t sharers,
                         uint32_t epoch)
{
    // XOR-fold: all struct bytes contribute
    uint8_t c = 0;

    // PA bytes (6 relevant bytes: PA[43:0])
    c ^= static_cast<uint8_t>(pa & 0xFF);
    c ^= static_cast<uint8_t>((pa >> 8) & 0xFF);
    c ^= static_cast<uint8_t>((pa >> 16) & 0xFF);
    c ^= static_cast<uint8_t>((pa >> 24) & 0xFF);
    c ^= static_cast<uint8_t>((pa >> 32) & 0xFF);
    c ^= static_cast<uint8_t>((pa >> 40) & 0x0F); // only 4 bits of PA[43:40]

    // MESI + SlotState combined into one byte
    c ^= static_cast<uint8_t>((slotState & 0x3) | ((mesi & 0x3) << 2));

    // Sharers bytes
    c ^= static_cast<uint8_t>(sharers & 0xFF);
    c ^= static_cast<uint8_t>((sharers >> 8) & 0xFF);

    // Epoch bytes
    c ^= static_cast<uint8_t>(epoch & 0xFF);
    c ^= static_cast<uint8_t>((epoch >> 8) & 0xFF);
    c ^= static_cast<uint8_t>((epoch >> 16) & 0xFF);

    return c;
}

void pack(const H64SlotEntry& in, uint8_t out[kSlotBytes])
{
    uint64_t pa     = in.pa & kPAMask44;
    uint32_t mesi   = static_cast<uint32_t>(in.mesi) & kMesiMask;
    uint32_t slotSt = static_cast<uint32_t>(in.state) & kSlotStateMask;
    uint32_t sh     = static_cast<uint32_t>(in.sharers) & kSharersMask;
    uint32_t ep     = in.epoch & kEpochMask;

    // Compute integrity from the actual field values
    uint32_t integ = computeIntegrity(pa, static_cast<uint8_t>(mesi),
                                      static_cast<uint8_t>(slotSt),
                                      static_cast<uint16_t>(sh), ep);

    uint32_t w0 = static_cast<uint32_t>(pa & 0xFFFFFFFFULL);
    uint32_t w1 = static_cast<uint32_t>(((pa >> 32) & 0xFFF) << 20)
                | (mesi << 18)
                | (slotSt << 16)
                | (sh << 0);
    uint32_t w2 = (ep << 8)
                | (integ << 0);

    std::memcpy(out + 0, &w0, 4);
    std::memcpy(out + 4, &w1, 4);
    std::memcpy(out + 8, &w2, 4);
}

void unpack(const uint8_t in[kSlotBytes], H64SlotEntry& out)
{
    uint32_t w0 = 0, w1 = 0, w2 = 0;
    std::memcpy(&w0, in + 0, 4);
    std::memcpy(&w1, in + 4, 4);
    std::memcpy(&w2, in + 8, 4);

    uint64_t pa_lo = w0 & 0xFFFFFFFFULL;
    uint64_t pa_hi = (w1 >> 20) & 0xFFFULL;
    uint64_t pa    = pa_lo | (pa_hi << 32);

    out.pa        = pa & kPAMask44;
    out.mesi      = static_cast<UBCCMESIState>((w1 >> 18) & 0x3);
    out.state     = static_cast<H64SlotState>((w1 >> 16) & 0x3);
    out.sharers   = static_cast<uint16_t>(w1 & 0xFFFF);
    out.epoch     = (w2 >> 8) & 0xFFFFFF;
    out.integrity = static_cast<uint8_t>(w2 & 0xFF);
}

bool checkSlotIntegrity(const uint8_t in[kSlotBytes])
{
    uint32_t w0 = 0, w1 = 0, w2 = 0;
    std::memcpy(&w0, in + 0, 4);
    std::memcpy(&w1, in + 4, 4);
    std::memcpy(&w2, in + 8, 4);

    uint64_t pa_lo = w0 & 0xFFFFFFFFULL;
    uint64_t pa_hi = (w1 >> 20) & 0xFFFULL;
    uint64_t pa    = pa_lo | (pa_hi << 32);

    pa &= kPAMask44;

    uint8_t  mesi   = static_cast<uint8_t>((w1 >> 18) & 0x3);
    uint8_t  slotSt = static_cast<uint8_t>((w1 >> 16) & 0x3);
    uint16_t sh     = static_cast<uint16_t>(w1 & 0xFFFF);
    uint32_t ep     = (w2 >> 8) & 0xFFFFFF;
    uint8_t  stored = static_cast<uint8_t>(w2 & 0xFF);

    uint8_t expected = computeIntegrity(pa, mesi, slotSt, sh, ep);
    return stored == expected;
}

} // namespace H64Codec

// ============================================================
// BackstoreSchemaH64
// ============================================================

uint64_t BackstoreSchemaH64::mix64(uint64_t x, uint64_t seed)
{
    x += seed;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

BackstoreSchemaH64::BackstoreSchemaH64(const H64Config& cfg)
    : _cfg(cfg)
{
    assert(cfg.buckets_per_group > 0);
    assert(cfg.num_groups > 0);
    _buckets.resize(cfg.num_groups * cfg.buckets_per_group);
}

// ---- Public static hash helpers (shared with BackstoreHostH64) ----

size_t BackstoreSchemaH64::groupForPaStatic(uint64_t linePa, size_t numGroups, uint64_t seed)
{
    return static_cast<size_t>(mix64(linePa >> 6, seed) % numGroups);
}

size_t BackstoreSchemaH64::homeBucketForPaStatic(uint64_t linePa, size_t bucketsPerGroup, uint64_t seed)
{
    uint64_t h = mix64(linePa >> 6, seed ^ 0x123456789ABCDEFULL);
    return static_cast<size_t>(h % bucketsPerGroup);
}

// ---- Instance hash helpers (delegate to static) ----

size_t BackstoreSchemaH64::groupForPa(uint64_t pa) const
{
    return groupForPaStatic(pa, _cfg.num_groups, _cfg.hash_seed);
}

size_t BackstoreSchemaH64::homeBucketForPa(uint64_t pa, size_t) const
{
    return homeBucketForPaStatic(pa, _cfg.buckets_per_group, _cfg.hash_seed);
}

// ---- Slot I/O ----

void BackstoreSchemaH64::readSlot(const H64BucketLine& bucket, int idx,
                                   H64SlotEntry& out) const
{
    H64Codec::unpack(bucket.slotAt(idx), out);
}

void BackstoreSchemaH64::writeSlot(H64BucketLine& bucket, int idx,
                                    const H64SlotEntry& in)
{
    H64Codec::pack(in, bucket.slotAt(idx));
}

bool BackstoreSchemaH64::slotCorrupt(const H64BucketLine& bucket, int idx) const
{
    const uint8_t* raw = bucket.slotAt(idx);

    // Quick state peek without full unpack
    uint32_t w1 = 0;
    std::memcpy(&w1, raw + 4, 4);
    uint8_t slotSt = static_cast<uint8_t>((w1 >> 16) & 0x3);

    if (slotSt == static_cast<uint8_t>(H64SlotState::RESERVED)) {
        return true;
    }

    if (slotSt == static_cast<uint8_t>(H64SlotState::EMPTY)) {
        // EMPTY must have all-zero encoding → integrity must be 0x00
        // Full zero-check for EMPTY: all 12 bytes must be zero
        bool allZero = true;
        for (size_t i = 0; i < H64Codec::kSlotBytes; ++i) {
            if (raw[i] != 0) { allZero = false; break; }
        }
        if (!allZero) return true;
        return false; // valid EMPTY
    }

    // LIVE or HASH_TOMBSTONE: must have valid integrity
    return !H64Codec::checkSlotIntegrity(raw);
}

// ---- Probe ----

BackstoreSchemaH64::ProbeResult
BackstoreSchemaH64::probe(uint64_t pa) const
{
    ProbeResult r;
    r.groupIdx = groupForPa(pa);
    size_t home = homeBucketForPa(pa, r.groupIdx);
    size_t nBuckets = _cfg.buckets_per_group;

    r.firstTombstoneSlot = -1;
    r.firstEmptySlot     = -1;

    for (size_t offset = 0; offset < nBuckets; ++offset) {
        size_t bi = (home + offset) % nBuckets;
        r.probeCount = offset + 1;

        const H64BucketLine& bucket = _buckets[flatBucketIdx(r.groupIdx, bi)];

        // Header corruption → mark probe as corrupt
        if (!bucket.hdrValid()) {
            r.corrupt = true;
        }

        // ---- Phase A: scan ALL slots for corruption before matching ----
        // This ensures that a RESERVED slot or bad-integrity slot at a
        // higher index than the match is still detected. Corrupt is never
        // downgraded to Found just because the match happened to appear first.
        for (int si = 0; si < static_cast<int>(kSlotsPerBucket); ++si) {
            if (slotCorrupt(bucket, si)) {
                r.corrupt = true;
            }
        }

        // ---- Phase B: find match, EMPTY, or TOMBSTONE ----
        for (int si = 0; si < static_cast<int>(kSlotsPerBucket); ++si) {
            if (slotCorrupt(bucket, si)) {
                continue; // skip corrupt slots — data is unreliable
            }

            H64SlotEntry e;
            readSlot(bucket, si, e);

            if (e.state == H64SlotState::LIVE && e.pa == pa) {
                r.matched   = true;
                r.bucketIdx = bi;
                r.slotIdx   = si;
                return r; // safe: corrupt flag already set if any corruption was seen
            }

            if (e.state == H64SlotState::EMPTY && r.firstEmptySlot < 0) {
                r.firstEmptySlot = (static_cast<int>(bi) * kSlotsPerBucket) + si;
                r.bucketIdx = bi;
            }

            if (e.state == H64SlotState::HASH_TOMBSTONE && r.firstTombstoneSlot < 0) {
                r.firstTombstoneSlot = (static_cast<int>(bi) * kSlotsPerBucket) + si;
            }
        }

        // If we saw an EMPTY in this bucket, terminate the probe.
        if (r.firstEmptySlot >= 0) {
            int linearSlot = r.firstEmptySlot;
            r.bucketIdx = static_cast<size_t>(linearSlot) / kSlotsPerBucket;
            break;
        }
    }

    if (r.firstEmptySlot < 0) {
        r.exhausted = true;
    }
    return r;
}

// ---- Core API ----

H64Status BackstoreSchemaH64::lookup(uint64_t pa, H64SlotEntry& out) const
{
    if (!isValidPA(pa)) {
        return H64Status::InvalidArgument;
    }

    ProbeResult pr = probe(pa);

    // Corrupt in probe path → always Corrupt, never NotFound
    if (pr.corrupt) {
        return H64Status::Corrupt;
    }

    if (pr.matched) {
        const H64BucketLine& bucket =
            _buckets[flatBucketIdx(pr.groupIdx, pr.bucketIdx)];
        readSlot(bucket, pr.slotIdx, out);
        return H64Status::Found;
    }

    if (pr.firstEmptySlot >= 0) {
        return H64Status::NotFound;
    }

    if (pr.exhausted) {
        return H64Status::CapacityExhausted;
    }

    return H64Status::RetryableBusy;
}

H64Status BackstoreSchemaH64::upsert(uint64_t pa, const H64SlotEntry& entry)
{
    // Input validation
    const char* err = validateUpsertEntry(pa, entry);
    if (err) {
        return H64Status::InvalidArgument;
    }

    ProbeResult pr = probe(pa);

    // Corrupt in probe path → always Corrupt
    if (pr.corrupt) {
        return H64Status::Corrupt;
    }

    if (pr.matched) {
        // Existing LIVE entry — epoch guard
        H64BucketLine& bucket =
            _buckets[flatBucketIdx(pr.groupIdx, pr.bucketIdx)];
        H64SlotEntry existing;
        readSlot(bucket, pr.slotIdx, existing);

        if (entry.epoch < existing.epoch) {
            return H64Status::StaleEpoch;
        }

        // Overwrite (idempotent for same epoch, upgrade for higher)
        writeSlot(bucket, pr.slotIdx, entry);
        return H64Status::Found;
    }

    // No matching LIVE — insert.
    // Priority: first reusable HASH_TOMBSTONE > first EMPTY.
    int targetLinearSlot = -1;
    bool reusingTombstone = false;

    if (pr.firstTombstoneSlot >= 0) {
        targetLinearSlot = pr.firstTombstoneSlot;
        reusingTombstone = true;
    } else if (pr.firstEmptySlot >= 0) {
        targetLinearSlot = pr.firstEmptySlot;
    } else {
        return H64Status::CapacityExhausted;
    }

    size_t bi = static_cast<size_t>(targetLinearSlot) / kSlotsPerBucket;
    int    si = targetLinearSlot % static_cast<int>(kSlotsPerBucket);

    H64BucketLine& bucket =
        _buckets[flatBucketIdx(pr.groupIdx, bi)];
    writeSlot(bucket, si, entry);

    if (reusingTombstone) {
        uint8_t tc = bucket.tombstoneCount();
        bucket.setTombstoneCount(tc > 0 ? tc - 1 : 0);
    }
    bucket.setLiveCount(bucket.liveCount() + 1);
    return H64Status::Found;
}

H64Status BackstoreSchemaH64::erase(uint64_t pa, uint32_t deleteEpoch)
{
    if (!isValidPA(pa)) {
        return H64Status::InvalidArgument;
    }

    ProbeResult pr = probe(pa);

    // Corrupt in probe path → always Corrupt
    if (pr.corrupt) {
        return H64Status::Corrupt;
    }

    if (!pr.matched) {
        if (pr.firstEmptySlot >= 0) {
            return H64Status::AlreadyAbsent;
        }
        if (pr.exhausted) {
            return H64Status::CapacityExhausted;
        }
        return H64Status::AlreadyAbsent;
    }

    H64BucketLine& bucket =
        _buckets[flatBucketIdx(pr.groupIdx, pr.bucketIdx)];
    H64SlotEntry existing;
    readSlot(bucket, pr.slotIdx, existing);

    if (deleteEpoch < existing.epoch) {
        return H64Status::StaleEpoch;
    }

    // Convert LIVE → HASH_TOMBSTONE
    existing.state = H64SlotState::HASH_TOMBSTONE;
    writeSlot(bucket, pr.slotIdx, existing);
    uint8_t lc = bucket.liveCount();
    bucket.setLiveCount(lc > 0 ? lc - 1 : 0);
    bucket.setTombstoneCount(bucket.tombstoneCount() + 1);
    return H64Status::Found;
}

H64Status BackstoreSchemaH64::rebuildGroup(size_t groupIdx)
{
    if (groupIdx >= _cfg.num_groups) {
        return H64Status::InvalidArgument;
    }

    size_t base = groupIdx * _cfg.buckets_per_group;
    size_t maxSlots = _cfg.groupSlots();

    // ============================================================
    // Phase 1: Pre-scan — collect LIVE entries AND check for corruption.
    //           NO mutation occurs in this phase.
    //           If corruption is detected, return Corrupt immediately.
    // ============================================================
    std::vector<H64SlotEntry> liveEntries;
    liveEntries.reserve(maxSlots); // bounded reserve, never exceeds groupSlots()

    for (size_t i = 0; i < _cfg.buckets_per_group; ++i) {
        const H64BucketLine& bucket = _buckets[base + i];

        // Header corruption → abort
        if (!bucket.hdrValid()) {
            return H64Status::Corrupt;
        }

        for (int si = 0; si < static_cast<int>(kSlotsPerBucket); ++si) {
            // Slot-level corruption → abort
            if (slotCorrupt(bucket, si)) {
                return H64Status::Corrupt;
            }

            H64SlotEntry e;
            readSlot(bucket, si, e);
            if (e.state == H64SlotState::LIVE) {
                liveEntries.push_back(e);
            }
        }
    }

    // Pre-validate: group must have space for its own LIVE entries
    if (liveEntries.size() > maxSlots) {
        // Should never happen — physical limit. Indicates corruption.
        return H64Status::Corrupt;
    }

    // ============================================================
    // Phase 2: Save old group state for rollback guarantee.
    //           If re-insertion fails, the old state is restored
    //           atomically — no partial data loss.
    // ============================================================
    std::vector<H64BucketLine> oldGroup(
        _buckets.begin() + base,
        _buckets.begin() + base + _cfg.buckets_per_group);

    uint8_t oldGen = oldGroup[0].generation();
    uint8_t newGen = oldGen + 1;

    // ============================================================
    // Phase 3: Mutate — clear, set generation, re-insert.
    //           Guarantee: if pre-scan passed (no corruption,
    //           live ≤ capacity), then re-insertion cannot fail
    //           because the cleared group has enough empty slots.
    //           If it does fail, rollback ensures no data loss.
    // ============================================================

    // 3a. Clear all buckets in this group
    for (size_t i = 0; i < _cfg.buckets_per_group; ++i) {
        _buckets[base + i].clear();
    }

    // 3b. Set uniform new generation on every bucket
    for (size_t i = 0; i < _cfg.buckets_per_group; ++i) {
        _buckets[base + i].setGeneration(newGen);
    }

    // 3c. Re-insert all LIVE entries
    for (const auto& entry : liveEntries) {
        H64Status st = upsert(entry.pa, entry);
        if (st != H64Status::Found) {
            // Guarantee: restore old group state — no partial data loss
            std::copy(oldGroup.begin(), oldGroup.end(),
                      _buckets.begin() + base);
            return H64Status::Corrupt;
        }
    }

    return H64Status::Found;
}

// ---- Introspection ----

size_t BackstoreSchemaH64::scanLiveEntries(
    std::function<void(const H64SlotEntry&)> cb) const
{
    size_t count = 0;
    for (size_t i = 0; i < _buckets.size(); ++i) {
        const H64BucketLine& bucket = _buckets[i];
        for (int si = 0; si < static_cast<int>(kSlotsPerBucket); ++si) {
            H64SlotEntry e;
            readSlot(bucket, si, e);
            if (e.state == H64SlotState::LIVE) {
                cb(e);
                ++count;
            }
        }
    }
    return count;
}

uint8_t BackstoreSchemaH64::groupGeneration(size_t groupIdx) const
{
    if (groupIdx >= _cfg.num_groups) return 0;
    size_t base = groupIdx * _cfg.buckets_per_group;
    return _buckets[base].generation();
}

const H64BucketLine& BackstoreSchemaH64::bucket(
    size_t groupIdx, size_t bucketIdx) const
{
    return _buckets[flatBucketIdx(groupIdx, bucketIdx)];
}

H64BucketLine& BackstoreSchemaH64::bucket(
    size_t groupIdx, size_t bucketIdx)
{
    return _buckets[flatBucketIdx(groupIdx, bucketIdx)];
}

size_t BackstoreSchemaH64::probeDistance(uint64_t pa) const
{
    ProbeResult pr = probe(pa);
    if (pr.corrupt) return 0;
    return pr.probeCount;
}

} // namespace glob
} // namespace cc
