#ifndef __MEM_RUBY_PROTOCOL_CHI_EP_BACKSTORESCHEMAH64_HH__
#define __MEM_RUBY_PROTOCOL_CHI_EP_BACKSTORESCHEMAH64_HH__

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <vector>

#include "BackstoreTypes.hh"

namespace cc
{

namespace glob
{

// ============================================================
// H64 Slot State
// ============================================================
enum class H64SlotState : uint8_t {
    EMPTY          = 0,
    LIVE           = 1,
    HASH_TOMBSTONE = 2,
    RESERVED       = 3,
};

static constexpr const char* h64SlotStateName(H64SlotState s)
{
    switch (s) {
        case H64SlotState::EMPTY:          return "EMPTY";
        case H64SlotState::LIVE:           return "LIVE";
        case H64SlotState::HASH_TOMBSTONE: return "HASH_TOMBSTONE";
        case H64SlotState::RESERVED:       return "RESERVED";
    }
    return "UNKNOWN";
}

// ============================================================
// Unpacked representation of a directory entry in an H64 slot
// ============================================================
struct H64SlotEntry {
    uint64_t      pa;
    UBCCMESIState mesi;
    uint16_t      sharers;
    uint32_t      epoch;
    H64SlotState  state;
    uint8_t       integrity;

    H64SlotEntry()
        : pa(0), mesi(UBCCMESIState::G_I), sharers(0),
          epoch(0), state(H64SlotState::EMPTY), integrity(0) {}

    bool operator==(const H64SlotEntry& o) const;
    bool operator!=(const H64SlotEntry& o) const { return !(*this == o); }

    /** True if sharers has exactly one bit set (one-hot). */
    bool hasOneHotSharers() const {
        return sharers != 0 && (sharers & (sharers - 1)) == 0;
    }
};

// ============================================================
// Lookup/Operation result codes
// ============================================================
enum class H64Status : uint8_t {
    Found             = 0,
    NotFound          = 1,
    AlreadyAbsent     = 2,
    StaleEpoch        = 3,
    CapacityExhausted = 4,
    Corrupt           = 5,
    RetryableBusy     = 6,
    InvalidArgument   = 7,
};

static constexpr const char* h64StatusName(H64Status s)
{
    switch (s) {
        case H64Status::Found:             return "Found";
        case H64Status::NotFound:          return "NotFound";
        case H64Status::AlreadyAbsent:     return "AlreadyAbsent";
        case H64Status::StaleEpoch:        return "StaleEpoch";
        case H64Status::CapacityExhausted: return "CapacityExhausted";
        case H64Status::Corrupt:           return "Corrupt";
        case H64Status::RetryableBusy:     return "RetryableBusy";
        case H64Status::InvalidArgument:   return "InvalidArgument";
    }
    return "UNKNOWN";
}

// ============================================================
// H64 Codec: pack/unpack a 12-byte slot
//
// Slot layout (12 bytes, 96 bits, 3 × uint32_t LE):
//   w0[31:0]  = PA[31:0]                                                     (32b)
//   w1[31:0]  = PA[43:32](12b) | MESI(2b) | SlotState(2b) | Sharers(16b)    (32b)
//   w2[31:0]  = Epoch[23:0](24b) | Integrity(8b)                             (32b)
//
// Bit positions within w1:
//   Bits 31:20 = PA[43:32]    (12 bits)
//   Bits 19:18 = MESI state   (2 bits)
//   Bits 17:16 = Slot state   (2 bits)
//   Bits 15:0  = Sharers mask (16 bits)
//
// Bit positions within w2:
//   Bits 31:8  = Epoch        (24 bits)
//   Bits  7:0  = Integrity    (8 bits)
//
// Integrity: 8-bit deterministic checksum over the other fields.
//   EMPTY slots: integrity must be 0x00 (zero encoding).
//   LIVE/TOMBSTONE/RESERVED: XOR of PA bytes, MESI, SlotState,
//   Sharers bytes, and Epoch bytes.
// ============================================================
namespace H64Codec
{

static constexpr size_t kSlotBytes      = 12;
static constexpr uint64_t kPAMask44     = (1ULL << 44) - 1;
static constexpr uint32_t kMesiMask     = (1U << 2) - 1;
static constexpr uint32_t kSlotStateMask = (1U << 2) - 1;
static constexpr uint32_t kSharersMask  = (1U << 16) - 1;
static constexpr uint32_t kEpochMask    = (1U << 24) - 1;

/**
 * Compute the 8-bit integrity checksum for the given fields.
 * EMPTY slots produce 0x00 because all fields are zero.
 */
uint8_t computeIntegrity(uint64_t pa, uint8_t mesi,
                         uint8_t slotState, uint16_t sharers,
                         uint32_t epoch);

/**
 * Pack an H64SlotEntry into a 12-byte wire-format buffer.
 * Computes and stores integrity automatically.
 * The caller provides out[12].
 */
void pack(const H64SlotEntry& in, uint8_t out[kSlotBytes]);

/**
 * Unpack a 12-byte wire-format buffer into an H64SlotEntry.
 * Returns the stored integrity as-is (does NOT validate).
 */
void unpack(const uint8_t in[kSlotBytes], H64SlotEntry& out);

/**
 * Verify that the integrity byte in a packed 12-byte slot matches
 * a fresh computation from the other fields.
 * Returns true if valid.
 */
bool checkSlotIntegrity(const uint8_t in[kSlotBytes]);

} // namespace H64Codec

// ============================================================
// Slot-level integrity check (on unpacked entry)
// ============================================================
inline bool checkIntegrity(const H64SlotEntry& e)
{
    uint8_t expected = H64Codec::computeIntegrity(
        e.pa, static_cast<uint8_t>(e.mesi),
        static_cast<uint8_t>(e.state), e.sharers, e.epoch);
    return e.integrity == expected;
}

// ============================================================
// Input validation helpers
// ============================================================

/** True if pa is 64B-aligned and within 44-bit range. */
inline bool isValidPA(uint64_t pa)
{
    return (pa & 0x3FULL) == 0 && pa <= ((1ULL << 44) - 1);
}

/** Validate entry fields for upsert. Returns InvalidArgument reason or nullptr. */
const char* validateUpsertEntry(uint64_t callerPa, const H64SlotEntry& entry);

// ============================================================
// Bucket header: packed 32-bit word (4 bytes)
//
// Bit layout (MSB → LSB):
//   Bits 31:24 = format_version (8b)
//   Bits 23:16 = generation (8b)
//   Bits 15:12 = live_count (4b)
//   Bits 11:8  = tombstone_count (4b)
//   Bits  7:0  = reserved (8b)
// ============================================================
static constexpr size_t kBucketHeaderSize = 4;

namespace H64BucketHeader
{

// Pack field values into a uint32_t.
inline uint32_t pack(uint8_t format_version, uint8_t generation,
                     uint8_t live_count, uint8_t tombstone_count,
                     uint8_t reserved)
{
    return (static_cast<uint32_t>(format_version)  << 24)
         | (static_cast<uint32_t>(generation)      << 16)
         | (static_cast<uint32_t>(live_count)      << 12)
         | (static_cast<uint32_t>(tombstone_count) << 8)
         | (static_cast<uint32_t>(reserved)        << 0);
}

// Unpack a uint32_t into individual fields.
inline void unpack(uint32_t raw,
                   uint8_t& format_version, uint8_t& generation,
                   uint8_t& live_count, uint8_t& tombstone_count,
                   uint8_t& reserved)
{
    format_version  = static_cast<uint8_t>((raw >> 24) & 0xFF);
    generation      = static_cast<uint8_t>((raw >> 16) & 0xFF);
    live_count      = static_cast<uint8_t>((raw >> 12) & 0xF);
    tombstone_count = static_cast<uint8_t>((raw >> 8)  & 0xF);
    reserved        = static_cast<uint8_t>((raw >> 0)  & 0xFF);
}

// Default header value (format_version=1, all others=0).
inline uint32_t defaultRaw()
{
    return pack(1, 0, 0, 0, 0);
}

// Validate a raw header.
inline bool valid(uint32_t raw)
{
    uint8_t fmt, gen, live, tomb, rsv;
    unpack(raw, fmt, gen, live, tomb, rsv);
    return fmt == 1 && live <= 5 && tomb <= 5 && (live + tomb) <= 5;
}

// Individual field getters (from raw).
inline uint8_t formatVersion(uint32_t raw)  { return static_cast<uint8_t>((raw >> 24) & 0xFF); }
inline uint8_t generation(uint32_t raw)     { return static_cast<uint8_t>((raw >> 16) & 0xFF); }
inline uint8_t liveCount(uint32_t raw)      { return static_cast<uint8_t>((raw >> 12) & 0xF); }
inline uint8_t tombstoneCount(uint32_t raw) { return static_cast<uint8_t>((raw >> 8)  & 0xF); }
inline uint8_t reserved(uint32_t raw)       { return static_cast<uint8_t>((raw >> 0)  & 0xFF); }

} // namespace H64BucketHeader

// ============================================================
// 64-byte BucketLine: raw header (4B) + 5 slots (5×12B = 60B) = 64B
// ============================================================
static constexpr size_t kSlotsPerBucket = 5;
static constexpr size_t kBucketLineSize = 64;
static constexpr size_t kSlotAreaSize   = kSlotsPerBucket * H64Codec::kSlotBytes; // 60

struct H64BucketLine {
    uint32_t hdr_raw;              // 4B packed header (H64BucketHeader format)
    uint8_t  slots[kSlotAreaSize]; // 60B

    H64BucketLine()
        : hdr_raw(H64BucketHeader::defaultRaw())
    {
        std::memset(slots, 0, kSlotAreaSize);
    }

    const uint8_t* slotAt(int idx) const {
        return &slots[idx * H64Codec::kSlotBytes];
    }
    uint8_t* slotAt(int idx) {
        return &slots[idx * H64Codec::kSlotBytes];
    }

    void clear() {
        hdr_raw = H64BucketHeader::defaultRaw();
        std::memset(slots, 0, kSlotAreaSize);
    }

    // Convenience field accessors (from hdr_raw)
    uint8_t fmtVersion() const  { return H64BucketHeader::formatVersion(hdr_raw); }
    uint8_t generation() const  { return H64BucketHeader::generation(hdr_raw); }
    uint8_t liveCount() const   { return H64BucketHeader::liveCount(hdr_raw); }
    uint8_t tombstoneCount() const { return H64BucketHeader::tombstoneCount(hdr_raw); }
    uint8_t rsvd() const        { return H64BucketHeader::reserved(hdr_raw); }
    bool    hdrValid() const    { return H64BucketHeader::valid(hdr_raw); }

    void setGeneration(uint8_t g) {
        uint8_t fmt, gen, live, tomb, rsv;
        H64BucketHeader::unpack(hdr_raw, fmt, gen, live, tomb, rsv);
        hdr_raw = H64BucketHeader::pack(fmt, g, live, tomb, rsv);
    }
    void setLiveCount(uint8_t c) {
        uint8_t fmt, gen, live, tomb, rsv;
        H64BucketHeader::unpack(hdr_raw, fmt, gen, live, tomb, rsv);
        hdr_raw = H64BucketHeader::pack(fmt, gen, c, tomb, rsv);
    }
    void setTombstoneCount(uint8_t c) {
        uint8_t fmt, gen, live, tomb, rsv;
        H64BucketHeader::unpack(hdr_raw, fmt, gen, live, tomb, rsv);
        hdr_raw = H64BucketHeader::pack(fmt, gen, live, c, rsv);
    }
};

static_assert(sizeof(H64BucketLine) == kBucketLineSize,
              "H64BucketLine must be exactly 64B");

// ============================================================
// Hash Table Configuration
// ============================================================
struct H64Config {
    size_t   num_groups;            // default 256; use small values for collision tests
    size_t   buckets_per_group;     // buckets within each group
    uint64_t hash_seed;             // splitmix seed for first hash level

    H64Config()
        : num_groups(256),
          buckets_per_group(1024),
          hash_seed(0x9e3779b97f4a7c15ULL) {}

    /** Total live slot capacity across all groups. */
    size_t totalSlots() const {
        return num_groups * buckets_per_group * kSlotsPerBucket;
    }

    /** Total DRAM bytes consumed by the bucket table. */
    size_t totalBytes() const {
        return num_groups * buckets_per_group * kBucketLineSize;
    }

    /** Total slots in one group. */
    size_t groupSlots() const {
        return buckets_per_group * kSlotsPerBucket;
    }
};

// ============================================================
// Schema H64: Fixed 64B Bucket Open-Addressing Hash Table
//
// Addressing: PA -> group -> home bucket -> bounded linear probe.
// Each bucket holds exactly 5 slots.
//
// Probe sequence:
//   home = hash(pa) % buckets_per_group
//   probe bucket home, home+1, ... (wrapping) until:
//     - matching LIVE slot => Found
//     - EMPTY slot         => NotFound (terminal)
//   HASH_TOMBSTONE does not terminate the probe.
//
// Corruption: if any bucket in the probe path has an invalid header,
// a RESERVED slot, or a slot with bad integrity, the entire operation
// returns Corrupt.  No Corrupt condition is downgraded to NotFound,
// AlreadyAbsent, or CapacityExhausted.
// ============================================================
class BackstoreSchemaH64
{
  public:
    explicit BackstoreSchemaH64(const H64Config& cfg);
    ~BackstoreSchemaH64() = default;

    // ---- Core API ----

    /**
     * Lookup a PA in the hash table.
     * On Found: copies the slot entry into `out`.
     * Never returns NotFound unless a valid EMPTY slot terminates the probe
     * and no corruption was encountered.
     */
    H64Status lookup(uint64_t pa, H64SlotEntry& out) const;

    /**
     * Upsert (insert or update) an entry.
     * - Matching LIVE slot with higher epoch => StaleEpoch (non-overwrite).
     * - Matching LIVE slot with equal or lower epoch => overwrite.
     * - No matching LIVE => insert at first reusable HASH_TOMBSTONE,
     *   otherwise first EMPTY.
     * - No usable slot => CapacityExhausted.
     * - Invalid input => InvalidArgument.
     * - Corrupt bucket/slot in probe path => Corrupt.
     */
    H64Status upsert(uint64_t pa, const H64SlotEntry& entry);

    /**
     * Erase a PA by marking its slot as HASH_TOMBSTONE.
     * - Not found after complete probe => AlreadyAbsent.
     * - Found but deleteEpoch < storedEpoch => StaleEpoch.
     * - Otherwise => HASH_TOMBSTONE.
     * - Corrupt bucket/slot in probe path => Corrupt.
     */
    H64Status erase(uint64_t pa, uint32_t deleteEpoch);

    /**
     * Rebuild a single group:
     *   - Pre-validate LIVE count ≤ group capacity.
     *   - Record old generation.
     *   - Clear every bucket in the group.
     *   - Set uniform generation = oldGen + 1 on all buckets.
     *   - Re-insert LIVE entries.
     *   - If pre-validation fails or re-insert fails: return Corrupt
     *     (does NOT modify the group — safe abort).
     */
    H64Status rebuildGroup(size_t groupIdx);

    // ---- Introspection (testing only) ----

    const H64Config& config() const { return _cfg; }

    /** Iterate every LIVE entry across all groups. */
    size_t scanLiveEntries(std::function<void(const H64SlotEntry&)> cb) const;

    /** Read the generation stored in the first bucket's header of a group. */
    uint8_t groupGeneration(size_t groupIdx) const;

    /** Direct const bucket access for test checking. */
    const H64BucketLine& bucket(size_t groupIdx, size_t bucketIdx) const;

    /** Direct mutable bucket access (use with care). */
    H64BucketLine& bucket(size_t groupIdx, size_t bucketIdx);

    /**
     * Compute the probe distance (in buckets) for a given PA.
     * If the probe encounters corruption, returns 0 (no valid distance).
     */
    size_t probeDistance(uint64_t pa) const;

    // ---- Hash helpers (public: shared with BackstoreHostH64) ----
    /** splitmix64-based hash: single source of truth for H64 routing. */
    static uint64_t mix64(uint64_t x, uint64_t seed);

    /** Compute group index from linePa >> 6. */
    static size_t groupForPaStatic(uint64_t linePa, size_t numGroups, uint64_t seed);

    /** Compute home bucket from linePa >> 6 within a group. */
    static size_t homeBucketForPaStatic(uint64_t linePa, size_t bucketsPerGroup, uint64_t seed);

  private:
    // ---- Instance hash helpers (delegate to static) ----
    size_t groupForPa(uint64_t pa) const;
    size_t homeBucketForPa(uint64_t pa, size_t groupIdx) const;

    // ---- Probe result ----
    struct ProbeResult {
        bool   matched;              // true if a LIVE slot with matching PA was found
        size_t groupIdx;
        size_t bucketIdx;
        int    slotIdx;              // -1 if no match
        int    firstTombstoneSlot;   // -1 if no tombstone seen
        int    firstEmptySlot;       // -1 if no empty seen (full probe)
        size_t probeCount;           // how many buckets were examined
        bool   corrupt;              // true if ANY bucket/slot in probe path is corrupt
        bool   exhausted;            // true if all slots probed without an EMPTY

        ProbeResult()
            : matched(false), groupIdx(0), bucketIdx(0),
              slotIdx(-1), firstTombstoneSlot(-1), firstEmptySlot(-1),
              probeCount(0), corrupt(false), exhausted(false) {}
    };

    ProbeResult probe(uint64_t pa) const;

    // ---- Slot I/O within a bucket ----
    void readSlot(const H64BucketLine& bucket, int idx, H64SlotEntry& out) const;
    void writeSlot(H64BucketLine& bucket, int idx, const H64SlotEntry& in);

    // Check if a slot at a given index in a bucket is corrupt
    // (RESERVED state, or non-EMPTY with bad integrity).
    bool slotCorrupt(const H64BucketLine& bucket, int idx) const;

    // ---- Index arithmetic ----
    size_t flatBucketIdx(size_t groupIdx, size_t bucketIdx) const {
        return groupIdx * _cfg.buckets_per_group + bucketIdx;
    }

    H64Config _cfg;
    std::vector<H64BucketLine> _buckets;
};

} // namespace glob
} // namespace cc

#endif // __MEM_RUBY_PROTOCOL_CHI_EP_BACKSTORESCHEMAH64_HH__
