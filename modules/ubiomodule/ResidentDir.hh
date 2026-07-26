#ifndef __MEM_RUBY_PROTOCOL_CHI_EP_RESIDENTDIR_HH__
#define __MEM_RUBY_PROTOCOL_CHI_EP_RESIDENTDIR_HH__

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

#include "BackstoreTypes.hh"
#include "BackstoreSchemaH64.hh"

namespace cc
{

namespace glob
{

// ---- Directory Entry (unpacked, used at API boundary) ----

struct UBCCDirEntry {
    uint64_t lineAddr;
    UBCCMESIState state;
    uint64_t sharersMask;
    uint64_t epoch;
    bool residentDirty;

    UBCCDirEntry()
        : lineAddr(0), state(UBCCMESIState::G_I), sharersMask(0),
          epoch(0), residentDirty(false)
    {}

    bool isEmpty() const { return state == UBCCMESIState::G_I && !residentDirty; }
    bool isTombstone() const { return state == UBCCMESIState::G_I && residentDirty; }
    bool isExclusive() const
    {
        return state == UBCCMESIState::G_E || state == UBCCMESIState::G_M;
    }

    static int ownerFromSharers(const UBCCDirEntry &e);
    static bool protoDirty(const UBCCDirEntry &e);
    static bool canonicalOneHotRequired(const UBCCDirEntry &e);
};

// ---- Runtime layout configuration ----

struct ResidentDirConfig {
    // Total on-chip SRAM budget for all long-lived structures.
    // The ResidentDir constructor asserts dir_bytes + bloom + groupIndex +
    // blc_reserved + desc_scratch ≤ sram_bytes ≤ 512 KiB.
    size_t sram_bytes     = 512 * 1024;   // total SRAM budget

    // Bloom filter: grouped advisory negative filter.
    // Legacy Phase 0 default: 60 KiB (preserves TC132/133/134 capacity).
    // Future H64 target profile: 40 KiB (see docs/design/...plan.md §4.1).
    size_t bloom_bytes    = 60 * 1024;    // legacy default: 60 KiB

    // group_index_bytes: MUST equal sizeof(GroupIndex) * BloomGroups
    // (= 4096).  This is the in-object storage for _groupIndex[16],
    // NOT part of _dirBits.  It is subtracted from the dir-bit budget
    // so _dirBits does not consume this reservation.
    // Phase 0: this field is intentionally not a free parameter.
    // Any deviation from 4096 is rejected at startup (except for
    // tiny test configs with sram < 64 KiB).
    size_t group_index_bytes = 4096;

    // DEPRECATED: backward-compat alias for group_index_bytes.
    // Kept for tools (evict_test, resident_dir_bench) that write
    // cfg.index_bytes.  Sync'd to group_index_bytes at init time.
    size_t index_bytes    = 4096;

    // BLC (Backstore Location Cache): 2 KiB reserved in future H64 profile.
    // Phase 0 legacy default: 0 — BLC is not implemented and not a legacy
    // capability.  The H64 target profile reserves 2 KiB.
    size_t blc_bytes      = 0;            // legacy default: 0

    // Group descriptors + Bloom scratch: 2 KiB reserved in future H64 profile.
    // Phase 0 legacy default: 0 — not implemented.
    size_t desc_scratch_bytes = 0;        // legacy default: 0

    int    pa_bits        = 40;           // effective PA bits (1TB = 40)
    int    sharers_bits   = 8;            // width of sharers field
    int    epoch_bits     = 24;           // width of epoch field
    int    ways           = 0;            // 0 = auto-search optimal
    int    set_bits       = 0;            // 0 = auto-search optimal

    // Effective group index budget (normalized from deprecated field or
    // explicit group_index_bytes).  For production configs this must
    // equal 4096.
    size_t effectiveGroupIndexBytes() const {
        // Explicit group_index_bytes takes priority if set.
        // Backup: use index_bytes (for legacy tools).
        if (group_index_bytes != 4096) return group_index_bytes;
        return index_bytes;
    }
};

// ---- Computed layout (derived from config at construction time) ----

struct ResidentDirLayout {
    int    ways;                // actual associativity chosen
    int    set_bits;            // log2(num_sets)
    int    num_sets;            // 1 << set_bits
    int    tag_bits;            // cacheline_addr_bits - set_bits
    int    entry_bits;          // total bits per entry (tag+valid+mesi+dirty+ctrl+sharers+epoch)
    int    plru_padded_ways;    // next_power_of_2(ways)
    int    plru_bits;           // plru_padded_ways - 1
    int    set_total_bits;      // ways * entry_bits + plru_bits
    int    set_bytes;           // ceil(set_total_bits / 8)
    size_t capacity;            // ways * num_sets
    size_t dir_bytes;           // num_sets * set_bytes (actual SRAM used for dir)

    // Per-entry field bit offsets within an entry (relative to entry start)
    int    off_valid;           // 0: 1 bit
    int    off_mesi;            // 1: 2 bits
    int    off_dirty;           // 3: 1 bit
    int    off_ctrl;            // 4: 3 bits (fill_pending, wb_pending, pinned)
    int    off_sharers;         // 7: sharers_bits
    int    off_epoch;           // 7+sharers_bits: epoch_bits
    int    off_tag;             // 7+sharers_bits+epoch_bits: tag_bits

    // Config echo
    int    sharers_bits;
    int    epoch_bits;
    int    pa_bits;
};

// ---- Set-associative Resident Directory with Pseudo-LRU ----

class ResidentDir
{
  public:
    static constexpr int    BloomHashes  = 4;
    static constexpr int    BloomGroups  = 16;
    static constexpr size_t DefaultBloomBytes = 60 * 1024;
    static constexpr size_t DefaultIndexBytes = 4 * 1024;

    enum class BloomSliceState : uint8_t { Invalid, Rebuilding, Valid };
    struct BloomSliceControl {
        BloomSliceState state = BloomSliceState::Invalid;
        uint32_t rebuildEpoch = 0;
        uint16_t pendingGroups = 0;
        bool retryRequired = false;
    };

    // Legacy constructor (backward compat)
    explicit ResidentDir(size_t bf_bytes = DefaultBloomBytes,
                         size_t force_entries = 0);

    // Full-config constructor
    explicit ResidentDir(const ResidentDirConfig &cfg);

    // ---- Data interface (same API as before) ----
    bool lookup(uint64_t pa, UBCCDirEntry& out) const;
    bool lookupWithSlot(uint64_t pa, UBCCDirEntry& out, size_t& slot) const;
    bool insert(uint64_t pa, const UBCCDirEntry& in);
    void update(uint64_t pa, const UBCCDirEntry& in);
    bool remove(uint64_t pa);
    bool forceRemove(uint64_t pa);
    void clear();

    // ---- Control flags ----
    void setFillPending(uint64_t pa, bool v);
    void setWbPending(uint64_t pa, bool v);
    void setPinned(uint64_t pa, bool v);
    bool fillPending(uint64_t pa) const;
    bool wbPending(uint64_t pa) const;
    bool pinned(uint64_t pa) const;
    void touch(uint64_t pa);

    // ---- Capacity / eviction ----
    bool hasFreeSlot() const;
    bool hasFreeSlotForPa(uint64_t pa) const;
    bool sameSet(uint64_t lhs, uint64_t rhs) const;
    bool pickVictim(uint64_t avoidPa, uint64_t &victimPa, UBCCDirEntry &victim) const;
    size_t capacity() const { return _layout.capacity; }
    size_t count() const { return _count; }
    const ResidentDirLayout& layout() const { return _layout; }
    int numSets() const { return _layout.num_sets; }
    int numWays() const { return _layout.ways; }

    // ---- Low-level entry field access (for async writeback scan) ----
    bool     getValid(int set, int way) const;
    bool     getDirty(int set, int way) const;
    uint64_t getTag(int set, int way) const;
    uint64_t getEpoch(int set, int way) const;
    void     setDirty(int set, int way, bool v);

    // Reconstruct full PA from set/way (requires valid entry)
    uint64_t rebuildPA(int set, int way) const;

    // ---- Bloom Filter (grouped) ----
    bool bloomMayContain(uint64_t pa) const;
    void bloomInsert(uint64_t pa);
    void bloomRemove(uint64_t pa);
    void bloomClear();

    // ---- Group Index ----
    const GroupIndex& groupIndex(int g) const { return _groupIndex[g]; }
    GroupIndex& groupIndex(int g) { return _groupIndex[g]; }
    int groupForPa(uint64_t pa) const;
    BloomSliceControl bloomSliceControl(int slice) const;
    void setBloomSliceRebuilding(int slice, uint16_t pendingGroups);
    void publishBloomSlice(int slice, const uint8_t *bytes, size_t byteCount);
    void invalidateBloomSlice(int slice, bool retryRequired = true);
    bool bloomNegativeAuthoritative(uint64_t pa) const;

    // ---- Reconstruction ----
    bool shouldReconstructGroup(int g) const;
    void reconstructGroup(int g);

    // ---- Diagnostics ----
    double estimateFPR(int group = -1) const;

    // 3.4: Performance counters
    uint64_t _dirHits = 0;
    uint64_t _dirMisses = 0;
    uint64_t _dirEvictions = 0;
    uint64_t _bloomFpCount = 0;

    void incrementBloomFp() { _bloomFpCount++; }
    void dumpStatsJson() const;

    // Legacy compat: `control` (slot-based) no longer meaningful; return 0
    uint8_t control(size_t) const { return 0; }

  private:
    void init(const ResidentDirConfig &cfg);

    // ---- Layout search ----
    static ResidentDirLayout searchOptimalLayout(const ResidentDirConfig &cfg);
    static int nextPow2(int v);

    // ---- Set/way addressing ----
    int  setIndex(uint64_t pa) const;
    uint64_t tagOf(uint64_t pa) const;
    size_t globalSlot(int set, int way) const { return (size_t)set * _layout.ways + way; }

    // ---- Bit-packed entry access ----
    // All bit operations address into _dirBits[] which is the SRAM region
    // for directory entries (separate from bloom).
    void   writeBits(size_t bitOffset, int numBits, uint64_t value);
    uint64_t readBits(size_t bitOffset, int numBits) const;

    // Per-entry accessors (set, way) → absolute bit offset
    size_t entryBitOffset(int set, int way) const;
    size_t plruBitOffset(int set) const;

    // Entry field read/write (remaining private)
    void     setValid(int set, int way, bool v);
    void     setTag(int set, int way, uint64_t tag);
    uint8_t  getMesi(int set, int way) const;
    void     setMesi(int set, int way, uint8_t mesi);
    uint8_t  getCtrl(int set, int way) const;
    void     setCtrl(int set, int way, uint8_t ctrl);
    uint64_t getSharers(int set, int way) const;
    void     setSharers(int set, int way, uint64_t sh);
    void     setEpoch(int set, int way, uint64_t ep);

    void encodeEntry(int set, int way, uint64_t pa, const UBCCDirEntry &in);
    void decodeEntry(int set, int way, UBCCDirEntry &out) const;

    // ---- Pseudo-LRU (tree-based) ----
    uint32_t getPlruTree(int set) const;
    void     setPlruTree(int set, uint32_t tree);
    int      plruVictimWay(int set) const;
    void     plruTouch(int set, int way);

    // Find way in set matching pa; returns -1 if miss
    int findWay(uint64_t pa) const;

    // ---- Bloom Filter helpers ----
    static uint64_t splitmix64(uint64_t x);
    int    bloomGroup(uint64_t pa) const;
    size_t bloomByteOffset(uint64_t pa, int hash_idx, int group) const;
    bool   bloomBitTest(uint64_t pa, int hash_idx) const;
    void   bloomBitSet(uint64_t pa, int hash_idx);
    size_t bloomGroupBytes() const { return _bloomBytes / BloomGroups; }

    void validateCanonical(const UBCCDirEntry& in, uint64_t pa) const;
    void scanResidentForGroup(int g, std::vector<uint8_t>& shadowBF) const;

  private:
    ResidentDirLayout _layout;
    size_t _count;

    // SRAM storage: all bit-packed into a single flat buffer
    std::vector<uint8_t> _dirBits;   // directory entries (set-associative)
    std::vector<uint8_t> _bloomBits; // bloom filter

    size_t _bloomBytes;
    size_t _bloomBitCount;

    GroupIndex _groupIndex[BloomGroups];
    BloomSliceControl _sliceControl[BloomGroups];

    static constexpr uint32_t kReconstructPeriod = 1024;
    static constexpr double kReconstructStaleThreshold = 0.25;

    // ctrl flag bit definitions within the 3-bit ctrl field
    static constexpr uint8_t kCtrlFillPending = 1u << 0;
    static constexpr uint8_t kCtrlWbPending   = 1u << 1;
    static constexpr uint8_t kCtrlPinned      = 1u << 2;
};

} // namespace glob
} // namespace cc

#endif // __MEM_RUBY_PROTOCOL_CHI_EP_RESIDENTDIR_HH__
