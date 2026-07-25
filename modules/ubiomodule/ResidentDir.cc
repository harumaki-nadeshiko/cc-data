#include "ResidentDir.hh"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "modules/ubiomodule/ubio_base.hh"

namespace cc
{

namespace glob
{

// ========================================================================
// UBCCDirEntry static helpers (unchanged)
// ========================================================================

int
UBCCDirEntry::ownerFromSharers(const UBCCDirEntry &e)
{
    if (!e.isExclusive()) return -1;
    if (__builtin_popcountll(e.sharersMask) != 1) return -1;
    return __builtin_ctzll(e.sharersMask);
}

bool UBCCDirEntry::protoDirty(const UBCCDirEntry &e)
{ return e.state == UBCCMESIState::G_M; }

bool UBCCDirEntry::canonicalOneHotRequired(const UBCCDirEntry &e)
{ return e.state == UBCCMESIState::G_E || e.state == UBCCMESIState::G_M; }

// ========================================================================
// Layout search
// ========================================================================

int ResidentDir::nextPow2(int v)
{
    int p = 1;
    while (p < v) p <<= 1;
    return p;
}

ResidentDirLayout
ResidentDir::searchOptimalLayout(const ResidentDirConfig &cfg)
{
    // avail_bytes for _dirBits = sram - bloom - groupIndex - blc - desc
    // GroupIndex[16] is an in-object member (not in _dirBits), so we
    // subtract its budget to prevent _dirBits from consuming it.
    // BLC and desc_scratch are reserved future budgets.
    // For tiny test configs (sram < 64 KiB), skip blc/desc reservation
    // to avoid negative avail_bytes (e.g. evict_test.cc with sram=1024).
    const size_t group_idx_budget = cfg.effectiveGroupIndexBytes();
    const bool full_budget = (cfg.sram_bytes >= 64 * 1024);
    const size_t blc_effective = full_budget ? cfg.blc_bytes : 0;
    const size_t desc_effective = full_budget ? cfg.desc_scratch_bytes : 0;
    // For tiny test configs (sram < 64 KiB), skip group_index_budget as well
    // to prevent unsigned underflow (group_index_budget = 4096 > sram_bytes).
    const size_t group_idx_effective = full_budget ? group_idx_budget : 0;
    const size_t avail_bytes = cfg.sram_bytes
                               - cfg.bloom_bytes
                               - group_idx_effective
                               - blc_effective
                               - desc_effective;
    const uint64_t avail_bits = (uint64_t)avail_bytes * 8;
    const int cl_addr_bits = cfg.pa_bits - 6; // cacheline address bits

    // Fixed bits per entry (excluding tag)
    // valid(1) + mesi(2) + dirty(1) + ctrl(3) + sharers + epoch
    const int entry_fixed = 1 + 2 + 1 + 3 + cfg.sharers_bits + cfg.epoch_bits;

    ResidentDirLayout best{};
    best.capacity = 0;

    // Search range for ways: 2..32 (any integer, not just powers of 2)
    int way_lo = 2, way_hi = 32;
    if (cfg.ways > 0) { way_lo = cfg.ways; way_hi = cfg.ways; }

    for (int w = way_lo; w <= way_hi; w++) {
        int plru_padded = nextPow2(w);
        int plru_bits = plru_padded - 1;

        // Search set_bits from high to low; first fit is maximum sets for this w.
        // Limit sb to 30 to avoid int overflow in (1 << sb).
        int sb_lo = 1, sb_hi = std::min(cl_addr_bits - 1, 30);
        if (cfg.set_bits > 0) { sb_lo = cfg.set_bits; sb_hi = cfg.set_bits; }

        for (int sb = sb_hi; sb >= sb_lo; sb--) {
            int tag_bits = cl_addr_bits - sb;
            if (tag_bits < 1) continue;
            int entry_bits = entry_fixed + tag_bits;
            int sets = 1 << sb;
            uint64_t set_bits_total = (uint64_t)w * entry_bits + plru_bits;
            uint64_t total_bits = (uint64_t)sets * set_bits_total;
            if (total_bits > avail_bits) continue;

            // First fit from top: this is the max sets for this w.
            size_t cap = (size_t)sets * w;
            if (cap > best.capacity) {
                best.ways = w;
                best.set_bits = sb;
                best.num_sets = sets;
                best.tag_bits = tag_bits;
                best.entry_bits = entry_bits;
                best.plru_padded_ways = plru_padded;
                best.plru_bits = plru_bits;
                best.set_total_bits = (int)set_bits_total;
                best.set_bytes = ((int)set_bits_total + 7) / 8;
                best.capacity = cap;
                best.dir_bytes = (size_t)sets * best.set_bytes;
                int off = 0;
                best.off_valid   = off; off += 1;
                best.off_mesi    = off; off += 2;
                best.off_dirty   = off; off += 1;
                best.off_ctrl    = off; off += 3;
                best.off_sharers = off; off += cfg.sharers_bits;
                best.off_epoch   = off; off += cfg.epoch_bits;
                best.off_tag     = off;
                best.sharers_bits = cfg.sharers_bits;
                best.epoch_bits   = cfg.epoch_bits;
                best.pa_bits      = cfg.pa_bits;
            }
            break; // for fixed w, larger sb = more sets = more capacity; first fit is best
        }
    }

    if (best.capacity == 0) {
        // Fallback: minimal config
        best.ways = 2; best.set_bits = 1; best.num_sets = 2;
        best.tag_bits = cl_addr_bits - 1;
        best.entry_bits = entry_fixed + best.tag_bits;
        best.plru_padded_ways = 2; best.plru_bits = 1;
        best.set_total_bits = 2 * best.entry_bits + 1;
        best.set_bytes = (best.set_total_bits + 7) / 8;
        best.capacity = 4;
        best.dir_bytes = 2 * best.set_bytes;
        int off = 0;
        best.off_valid = off; off += 1;
        best.off_mesi = off; off += 2;
        best.off_dirty = off; off += 1;
        best.off_ctrl = off; off += 3;
        best.off_sharers = off; off += cfg.sharers_bits;
        best.off_epoch = off; off += cfg.epoch_bits;
        best.off_tag = off;
        best.sharers_bits = cfg.sharers_bits;
        best.epoch_bits = cfg.epoch_bits;
        best.pa_bits = cfg.pa_bits;
    }

    return best;
}

// ========================================================================
// Construction
// ========================================================================

ResidentDir::ResidentDir(size_t bf_bytes, size_t force_entries)
    : _count(0), _bloomBytes(0), _bloomBitCount(0)
{
    ResidentDirConfig cfg;
    cfg.bloom_bytes = bf_bytes;
    if (force_entries > 0) {
        // Solve for ways/sets that yield <= force_entries
        // Simple: use 8-way and compute set_bits
        cfg.ways = 8;
        int sb = 0;
        while ((1 << (sb + 1)) * 8 <= (int)force_entries && sb < 20) sb++;
        cfg.set_bits = sb;
    }
    init(cfg);
}

ResidentDir::ResidentDir(const ResidentDirConfig &cfg)
    : _count(0), _bloomBytes(0), _bloomBitCount(0)
{
    init(cfg);
}

void
ResidentDir::init(const ResidentDirConfig &cfg)
{
    _layout = searchOptimalLayout(cfg);
    _count = 0;

    // Allocate bit-packed directory storage
    // Total bits = num_sets * set_total_bits; we store in byte-granularity
    size_t total_dir_bits = (size_t)_layout.num_sets * _layout.set_total_bits;
    size_t total_dir_bytes = (total_dir_bits + 7) / 8;
    _dirBits.assign(total_dir_bytes, 0);

    // Override layout.dir_bytes with the actual allocation (which may be
    // slightly smaller than set_bytes*num_sets due to bit-packing precision).
    _layout.dir_bytes = total_dir_bytes;

    // Bloom filter
    _bloomBytes = cfg.bloom_bytes;
    _bloomBitCount = _bloomBytes * 8;
    _bloomBits.assign(_bloomBytes, 0);
    for (int g = 0; g < BloomGroups; g++)
        _groupIndex[g] = GroupIndex();

    // In-object GroupIndex storage: BloomGroups * sizeof(GroupIndex)
    constexpr size_t groupIndexStorage = BloomGroups * sizeof(GroupIndex);
    static_assert(groupIndexStorage == 16 * 256,
                  "GroupIndex[16] must be exactly 4096 bytes");

    // Issue 4: validate group_index_bytes matches physical reality.
    // Tiny test configs (sram < 64 KiB) are exempt.
    if (cfg.sram_bytes >= 64 * 1024) {
        size_t gb = cfg.effectiveGroupIndexBytes();
        if (gb != groupIndexStorage) {
            std::fprintf(stderr,
                "[ResidentDir-FATAL] group_index_bytes=%zu != %zu "
                "(sizeof(GroupIndex)*BloomGroups). "
                "Fix config or use sram < 65536 for tiny test mode.\n",
                gb, groupIndexStorage);
            std::abort();
        }
    }

    // Phase 0: on-chip budget accounting (design §4.1)
    const bool full_budget = (cfg.sram_bytes >= 64 * 1024);
    const size_t blc_reserved = full_budget ? cfg.blc_bytes : 0;
    const size_t desc_reserved = full_budget ? cfg.desc_scratch_bytes : 0;
    const size_t total_on_chip = total_dir_bytes
                                 + _bloomBytes
                                 + groupIndexStorage
                                 + blc_reserved
                                 + desc_reserved;

    std::fprintf(stderr,
        "[ResidentDir] layout: %d sets x %d ways = %zu entries, "
        "tag=%d entry=%d bits, set_total=%d bits, dir=%zuKB, "
        "bloom=%zuKB, sharers=%d, epoch=%d, pa=%d\n",
        _layout.num_sets, _layout.ways, _layout.capacity,
        _layout.tag_bits, _layout.entry_bits, _layout.set_total_bits,
        total_dir_bytes / 1024, _bloomBytes / 1024,
        _layout.sharers_bits, _layout.epoch_bits, _layout.pa_bits);

    // Budget assertion: total on-chip ≤ sram_bytes ≤ 512 KiB.
    // Only enforced for production-scale configs (sram ≥ 64 KiB).
    // Tiny test configs (evict_test, resident_dir_bench small modes) are
    // exempt to avoid aborting on GroupIndex storage that the test
    // doesn't use but must still allocate as an in-object member.
    std::fprintf(stderr,
        "[ResidentDir-BUDGET] total_on_chip=%zu KiB  breakdown: "
        "dir=%zu KiB  bloom=%zu KiB  groupIndex[16]=%zu KiB  "
        "blc_reserved=%zu KiB  desc_reserved=%zu KiB  "
        "sram_budget=%zu KiB  limit=512 KiB %s\n",
        total_on_chip / 1024,
        total_dir_bytes / 1024,
        _bloomBytes / 1024,
        groupIndexStorage / 1024,
        blc_reserved / 1024,
        desc_reserved / 1024,
        cfg.sram_bytes / 1024,
        cfg.sram_bytes < 64 * 1024 ? "(tiny-test: assertion skipped)" : "");

    if (cfg.sram_bytes >= 64 * 1024) {
        if (cfg.sram_bytes > 512 * 1024) {
            std::fprintf(stderr, "[ResidentDir-BUDGET] ERROR: sram_bytes=%zu exceeds 512 KiB hard limit\n",
                         cfg.sram_bytes);
            std::abort();
        }
        if (total_on_chip > cfg.sram_bytes) {
            std::fprintf(stderr,
                "[ResidentDir-BUDGET] ERROR: total_on_chip=%zu > sram_budget=%zu — "
                "reduce bloom, group_index, blc, or desc_scratch\n",
                total_on_chip, cfg.sram_bytes);
            std::abort();
        }
        if (total_on_chip > 512 * 1024) {
            std::fprintf(stderr,
                "[ResidentDir-BUDGET] ERROR: total_on_chip=%zu > 512 KiB hard limit\n",
                total_on_chip);
            std::abort();
        }
    } else {
        // Tiny test: just report the numbers, don't enforce.
        std::fprintf(stderr,
            "[ResidentDir-BUDGET] tiny-test mode: assertion skipped "
            "(sram=%zu < 64KiB)\n", cfg.sram_bytes);
    }

    std::fflush(stderr);
}

// ========================================================================
// Bit-packed read/write primitives
// ========================================================================

void
ResidentDir::writeBits(size_t bitOffset, int numBits, uint64_t value)
{
    // Write numBits (1..64) starting at bitOffset into _dirBits[]
    for (int i = 0; i < numBits; i++) {
        size_t byteIdx = (bitOffset + i) / 8;
        int    bitIdx  = (bitOffset + i) % 8;
        if (value & (1ULL << i))
            _dirBits[byteIdx] |= (1u << bitIdx);
        else
            _dirBits[byteIdx] &= ~(1u << bitIdx);
    }
}

uint64_t
ResidentDir::readBits(size_t bitOffset, int numBits) const
{
    uint64_t val = 0;
    for (int i = 0; i < numBits; i++) {
        size_t byteIdx = (bitOffset + i) / 8;
        int    bitIdx  = (bitOffset + i) % 8;
        if (_dirBits[byteIdx] & (1u << bitIdx))
            val |= (1ULL << i);
    }
    return val;
}

// ========================================================================
// Set/way addressing
// ========================================================================

int
ResidentDir::setIndex(uint64_t pa) const
{
    return (int)((pa >> 6) & ((1ULL << _layout.set_bits) - 1));
}

uint64_t
ResidentDir::tagOf(uint64_t pa) const
{
    return (pa >> (6 + _layout.set_bits)) & ((1ULL << _layout.tag_bits) - 1);
}

uint64_t
ResidentDir::rebuildPA(int set, int way) const
{
    // Reverse of setIndex+tagOf: pa = (tag << (6+set_bits)) | (set << 6)
    uint64_t tag = getTag(set, way);
    return (tag << (6 + _layout.set_bits)) | ((uint64_t)set << 6);
}

size_t
ResidentDir::entryBitOffset(int set, int way) const
{
    // Each set occupies set_total_bits contiguous bits.
    // Within a set: [way0_entry | way1_entry | ... | wayN_entry | plru_tree]
    return (size_t)set * _layout.set_total_bits + (size_t)way * _layout.entry_bits;
}

size_t
ResidentDir::plruBitOffset(int set) const
{
    // PLRU tree is after all entries in the set
    return (size_t)set * _layout.set_total_bits +
           (size_t)_layout.ways * _layout.entry_bits;
}

// ========================================================================
// Per-entry field accessors
// ========================================================================

bool ResidentDir::getValid(int set, int way) const
{ return readBits(entryBitOffset(set, way) + _layout.off_valid, 1) != 0; }

void ResidentDir::setValid(int set, int way, bool v)
{ writeBits(entryBitOffset(set, way) + _layout.off_valid, 1, v ? 1 : 0); }

uint64_t ResidentDir::getTag(int set, int way) const
{ return readBits(entryBitOffset(set, way) + _layout.off_tag, _layout.tag_bits); }

void ResidentDir::setTag(int set, int way, uint64_t tag)
{ writeBits(entryBitOffset(set, way) + _layout.off_tag, _layout.tag_bits, tag); }

uint8_t ResidentDir::getMesi(int set, int way) const
{ return (uint8_t)readBits(entryBitOffset(set, way) + _layout.off_mesi, 2); }

void ResidentDir::setMesi(int set, int way, uint8_t mesi)
{ writeBits(entryBitOffset(set, way) + _layout.off_mesi, 2, mesi); }

bool ResidentDir::getDirty(int set, int way) const
{ return readBits(entryBitOffset(set, way) + _layout.off_dirty, 1) != 0; }

void ResidentDir::setDirty(int set, int way, bool v)
{ writeBits(entryBitOffset(set, way) + _layout.off_dirty, 1, v ? 1 : 0); }

uint8_t ResidentDir::getCtrl(int set, int way) const
{ return (uint8_t)readBits(entryBitOffset(set, way) + _layout.off_ctrl, 3); }

void ResidentDir::setCtrl(int set, int way, uint8_t ctrl)
{ writeBits(entryBitOffset(set, way) + _layout.off_ctrl, 3, ctrl & 0x7); }

uint64_t ResidentDir::getSharers(int set, int way) const
{ return readBits(entryBitOffset(set, way) + _layout.off_sharers, _layout.sharers_bits); }

void ResidentDir::setSharers(int set, int way, uint64_t sh)
{ writeBits(entryBitOffset(set, way) + _layout.off_sharers, _layout.sharers_bits, sh); }

uint64_t ResidentDir::getEpoch(int set, int way) const
{ return readBits(entryBitOffset(set, way) + _layout.off_epoch, _layout.epoch_bits); }

void ResidentDir::setEpoch(int set, int way, uint64_t ep)
{ writeBits(entryBitOffset(set, way) + _layout.off_epoch, _layout.epoch_bits, ep); }

// ========================================================================
// Encode/decode full entry
// ========================================================================

void
ResidentDir::encodeEntry(int set, int way, uint64_t pa, const UBCCDirEntry &in)
{
    // 4.1 CompactCodec guard: backstore CompactCodec uses 10-bit sharers mask
    // (kMask10 in BackstoreTypes.hh). SRAM default is 8-bit — verified safe for
    // 8 nodes (no overflow). For 16-node expansion, --sharers-bits=10 MUST be
    // passed or this assert fires and backstore data loses high sharer bits.
    assert(_layout.sharers_bits <= 10 &&
           "sharers_bits exceeds CompactCodec kMask10 capacity");
    validateCanonical(in, pa);
    setValid(set, way, true);
    setTag(set, way, tagOf(pa));
    setMesi(set, way, (uint8_t)in.state);
    setDirty(set, way, in.residentDirty);
    setSharers(set, way, in.sharersMask);
    setEpoch(set, way, in.epoch);
    // ctrl is preserved (not overwritten by encode)
}

void
ResidentDir::decodeEntry(int set, int way, UBCCDirEntry &out) const
{
    uint64_t tag = getTag(set, way);
    // Reconstruct PA from set index + tag
    out.lineAddr = ((tag << _layout.set_bits) | (uint64_t)set) << 6;
    out.state = (UBCCMESIState)getMesi(set, way);
    out.residentDirty = getDirty(set, way);
    out.sharersMask = getSharers(set, way);
    out.epoch = getEpoch(set, way);
}

// ========================================================================
// Pseudo-LRU (tree-based, padded to power-of-2)
// ========================================================================

uint32_t
ResidentDir::getPlruTree(int set) const
{
    return (uint32_t)readBits(plruBitOffset(set), _layout.plru_bits);
}

void
ResidentDir::setPlruTree(int set, uint32_t tree)
{
    writeBits(plruBitOffset(set), _layout.plru_bits, tree);
}

int
ResidentDir::plruVictimWay(int set) const
{
    // Walk the binary tree from root to leaf.
    // Tree node i has children 2i+1 (left) and 2i+2 (right).
    // Bit i=0 means go left (choose left subtree for eviction).
    uint32_t tree = getPlruTree(set);
    int node = 0;
    int pw = _layout.plru_padded_ways;
    while (node < pw - 1) { // internal nodes: 0 .. pw-2
        int left  = 2 * node + 1;
        int right = 2 * node + 2;
        if (tree & (1u << node))
            node = right; // bit set → go right (left was recently used)
        else
            node = left;  // bit clear → go left
    }
    // leaf index = node - (pw - 1)
    int way = node - (pw - 1);
    // Clamp to actual ways (for non-power-of-2 ways, retry if invalid)
    if (way >= _layout.ways) {
        // Shouldn't happen often; fall back to first invalid way or linear scan
        for (int w = 0; w < _layout.ways; w++) {
            if (!getValid(set, w)) return w;
        }
        // All valid, return way 0 as fallback
        return 0;
    }
    return way;
}

void
ResidentDir::plruTouch(int set, int way)
{
    // Walk from leaf to root, setting bits to point AWAY from this way.
    uint32_t tree = getPlruTree(set);
    int pw = _layout.plru_padded_ways;
    int node = (pw - 1) + way; // leaf node index
    while (node > 0) {
        int parent = (node - 1) / 2;
        if (node == 2 * parent + 1) {
            // This is the left child → set parent bit (point right, away from us)
            tree |= (1u << parent);
        } else {
            // This is the right child → clear parent bit (point left, away from us)
            tree &= ~(1u << parent);
        }
        node = parent;
    }
    setPlruTree(set, tree);
}

// ========================================================================
// findWay: search for PA in a set
// ========================================================================

int
ResidentDir::findWay(uint64_t pa) const
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag)
            return w;
    }
    return -1;
}

// ========================================================================
// Public data interface
// ========================================================================

bool
ResidentDir::lookup(uint64_t pa, UBCCDirEntry &out) const
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag) {
            decodeEntry(set, w, out);
            const_cast<ResidentDir*>(this)->_dirHits++;
            return true;
        }
    }
    const_cast<ResidentDir*>(this)->_dirMisses++;
    return false;
}

bool
ResidentDir::lookupWithSlot(uint64_t pa, UBCCDirEntry &out, size_t &slot) const
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag) {
            decodeEntry(set, w, out);
            slot = globalSlot(set, w);
            const_cast<ResidentDir*>(this)->_dirHits++;
            return true;
        }
    }
    const_cast<ResidentDir*>(this)->_dirMisses++;
    return false;
}

bool
ResidentDir::insert(uint64_t pa, const UBCCDirEntry &in)
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);

    // Already exists?
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag)
            return false; // duplicate
    }

    // Find free way
    for (int w = 0; w < _layout.ways; w++) {
        if (!getValid(set, w)) {
            encodeEntry(set, w, pa, in);
            setCtrl(set, w, 0); // clear ctrl on fresh insert
            plruTouch(set, w);
            _count++;
            return true;
        }
    }

    return false; // set full, caller must evict first
}

void
ResidentDir::update(uint64_t pa, const UBCCDirEntry &in)
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag) {
            // Preserve ctrl, update payload
            uint8_t ctrl = getCtrl(set, w);
            encodeEntry(set, w, pa, in);
            setCtrl(set, w, ctrl);
            return;
        }
    }
    // Not found — insert
    insert(pa, in);
}

bool
ResidentDir::remove(uint64_t pa)
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag) {
            setValid(set, w, false);
            // Zero out the entry bits for cleanliness
            size_t base = entryBitOffset(set, w);
            for (int b = 0; b < _layout.entry_bits; b++)
                writeBits(base + b, 1, 0);
            _count--;
            _dirEvictions++;  // 3.4 counter
            return true;
        }
    }
    return false;
}

bool
ResidentDir::forceRemove(uint64_t pa)
{
    return remove(pa);
}

void
ResidentDir::clear()
{
    std::fill(_dirBits.begin(), _dirBits.end(), 0);
    _count = 0;
    bloomClear();
}

// ========================================================================
// Control flags
// ========================================================================

void ResidentDir::setFillPending(uint64_t pa, bool v)
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag) {
            uint8_t c = getCtrl(set, w);
            if (v) c |= kCtrlFillPending; else c &= ~kCtrlFillPending;
            setCtrl(set, w, c);
            return;
        }
    }
}

void ResidentDir::setWbPending(uint64_t pa, bool v)
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag) {
            uint8_t c = getCtrl(set, w);
            if (v) c |= kCtrlWbPending; else c &= ~kCtrlWbPending;
            setCtrl(set, w, c);
            return;
        }
    }
}

void ResidentDir::setPinned(uint64_t pa, bool v)
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag) {
            uint8_t c = getCtrl(set, w);
            if (v) c |= kCtrlPinned; else c &= ~kCtrlPinned;
            setCtrl(set, w, c);
            return;
        }
    }
}

bool ResidentDir::fillPending(uint64_t pa) const
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag)
            return (getCtrl(set, w) & kCtrlFillPending) != 0;
    }
    return false;
}

bool ResidentDir::wbPending(uint64_t pa) const
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag)
            return (getCtrl(set, w) & kCtrlWbPending) != 0;
    }
    return false;
}

bool ResidentDir::pinned(uint64_t pa) const
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag)
            return (getCtrl(set, w) & kCtrlPinned) != 0;
    }
    return false;
}

void ResidentDir::touch(uint64_t pa)
{
    int set = setIndex(pa);
    uint64_t tag = tagOf(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (getValid(set, w) && getTag(set, w) == tag) {
            plruTouch(set, w);
            return;
        }
    }
}

// ========================================================================
// Capacity / eviction
// ========================================================================

bool
ResidentDir::hasFreeSlot() const
{
    return _count < _layout.capacity;
}

bool
ResidentDir::hasFreeSlotForPa(uint64_t pa) const
{
    int set = setIndex(pa);
    for (int w = 0; w < _layout.ways; w++) {
        if (!getValid(set, w)) return true;
    }
    return false;
}

bool
ResidentDir::sameSet(uint64_t lhs, uint64_t rhs) const
{
    return setIndex(lhs) == setIndex(rhs);
}

bool
ResidentDir::pickVictim(uint64_t avoidPa, uint64_t &victimPa, UBCCDirEntry &victim) const
{
    // Pick victim from the SAME set as avoidPa (set-associative eviction).
    int set = setIndex(avoidPa);

    // Use PLRU to find victim way
    int victimWay = plruVictimWay(set);

    // If PLRU victim is pinned or is avoidPa, scan for alternative
    for (int attempt = 0; attempt < _layout.ways; attempt++) {
        int w = (victimWay + attempt) % _layout.ways;
        if (!getValid(set, w)) continue; // skip empty (shouldn't need eviction)
        uint8_t ctrl = getCtrl(set, w);
        if (ctrl & kCtrlPinned) continue;

        UBCCDirEntry e;
        decodeEntry(set, w, e);
        if (e.lineAddr == avoidPa) continue;

        victimPa = e.lineAddr;
        victim = e;
        return true;
    }
    return false; // all ways pinned or match avoidPa
}

// ========================================================================
// Bloom Filter (same logic as before, just using _bloomBits vector)
// ========================================================================

uint64_t
ResidentDir::splitmix64(uint64_t x)
{
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

int ResidentDir::bloomGroup(uint64_t pa) const
{ return (int)(splitmix64(pa >> 6) % BloomGroups); }

int ResidentDir::groupForPa(uint64_t pa) const
{ return bloomGroup(pa); }

size_t
ResidentDir::bloomByteOffset(uint64_t pa, int hash_idx, int group) const
{
    static constexpr uint64_t seeds[4] = {
        0x243f6a8885a308d3ULL, 0x13198a2e03707344ULL,
        0xa4093822299f31d0ULL, 0xc6e00bf33da88fc2ULL,
    };
    size_t groupBytes = bloomGroupBytes();
    if (groupBytes == 0) return 0;
    uint64_t h = splitmix64(pa ^ seeds[hash_idx % BloomHashes]);
    size_t off = (h % (groupBytes * 8)) / 8;
    return (size_t)(group * groupBytes) + off;
}

bool
ResidentDir::bloomBitTest(uint64_t pa, int hash_idx) const
{
    if (_bloomBitCount == 0) return false;
    int group = bloomGroup(pa);
    size_t byteOff = bloomByteOffset(pa, hash_idx, group);
    if (byteOff >= _bloomBits.size()) return false;
    uint64_t h = splitmix64(pa ^ (uint64_t)(hash_idx * 0x9e3779b9ULL));
    int bitSub = (int)(h % 8);
    size_t bitIdx = byteOff * 8 + bitSub;
    if (bitIdx >= _bloomBitCount) return false;
    return (_bloomBits[bitIdx / 8] >> (bitIdx % 8)) & 1u;
}

void
ResidentDir::bloomBitSet(uint64_t pa, int hash_idx)
{
    if (_bloomBitCount == 0) return;
    int group = bloomGroup(pa);
    size_t byteOff = bloomByteOffset(pa, hash_idx, group);
    if (byteOff >= _bloomBits.size()) return;
    uint64_t h = splitmix64(pa ^ (uint64_t)(hash_idx * 0x9e3779b9ULL));
    int bitSub = (int)(h % 8);
    size_t bitIdx = byteOff * 8 + bitSub;
    if (bitIdx >= _bloomBitCount) return;
    _bloomBits[bitIdx / 8] |= (uint8_t)(1u << (bitIdx % 8));
}

bool
ResidentDir::bloomMayContain(uint64_t pa) const
{
    if (_bloomBitCount == 0) return false;
    for (int i = 0; i < BloomHashes; i++) {
        if (!bloomBitTest(pa, i)) return false;
    }
    return true;
}

void
ResidentDir::bloomInsert(uint64_t pa)
{
    if (_bloomBitCount == 0) return;
    for (int i = 0; i < BloomHashes; i++)
        bloomBitSet(pa, i);
    int g = bloomGroup(pa);
    _groupIndex[g].insert_count++;
}

void
ResidentDir::bloomRemove(uint64_t pa)
{
    int g = bloomGroup(pa);
    _groupIndex[g].stale_delete_count++;
}

void
ResidentDir::bloomClear()
{
    std::fill(_bloomBits.begin(), _bloomBits.end(), 0);
    for (int g = 0; g < BloomGroups; g++)
        _groupIndex[g] = GroupIndex();
}

// ========================================================================
// Reconstruction
// ========================================================================

bool
ResidentDir::shouldReconstructGroup(int g) const
{
    if (g < 0 || g >= BloomGroups) return false;
    const GroupIndex &gi = _groupIndex[g];
    if (gi.insert_count > 0 && gi.insert_count % kReconstructPeriod == 0)
        return true;
    if (gi.live_count > 0 &&
        (double)gi.stale_delete_count / gi.live_count > kReconstructStaleThreshold)
        return true;
    return false;
}

void
ResidentDir::scanResidentForGroup(int g, std::vector<uint8_t> &shadowBF) const
{
    size_t groupBytes = bloomGroupBytes();
    for (int s = 0; s < _layout.num_sets; s++) {
        for (int w = 0; w < _layout.ways; w++) {
            if (!getValid(s, w)) continue;
            UBCCDirEntry e;
            decodeEntry(s, w, e);
            if (bloomGroup(e.lineAddr) != g) continue;
            if (e.state == UBCCMESIState::G_I && !e.residentDirty) continue;

            for (int h = 0; h < BloomHashes; h++) {
                size_t byteOff = bloomByteOffset(e.lineAddr, h, g);
                if (byteOff >= groupBytes) continue;
                uint64_t hval = splitmix64(
                    e.lineAddr ^ (uint64_t)(h * 0x9e3779b9ULL));
                int bitSub = (int)(hval % 8);
                size_t bitIdx = byteOff * 8 + bitSub;
                if (bitIdx / 8 >= shadowBF.size()) continue;
                shadowBF[bitIdx / 8] |= (uint8_t)(1u << (bitIdx % 8));
            }
        }
    }
}

void
ResidentDir::reconstructGroup(int g)
{
    if (g < 0 || g >= BloomGroups) return;
    size_t groupBytes = bloomGroupBytes();
    if (groupBytes == 0) return;

    std::vector<uint8_t> shadowBF(groupBytes, 0);
    scanResidentForGroup(g, shadowBF);

    size_t groupStart = (size_t)g * groupBytes;
    std::memcpy(&_bloomBits[groupStart], shadowBF.data(), groupBytes);
    _groupIndex[g].stale_delete_count = 0;
}

// ========================================================================
// Diagnostics
// ========================================================================

double
ResidentDir::estimateFPR(int group) const
{
    size_t n = 0;
    if (group < 0 || group >= BloomGroups) {
        for (int g = 0; g < BloomGroups; g++)
            n += _groupIndex[g].live_count;
    } else {
        n = _groupIndex[group].live_count;
    }
    if (n == 0) return 0.0;

    size_t m;
    if (group < 0 || group >= BloomGroups)
        m = _bloomBitCount;
    else
        m = bloomGroupBytes() * 8;

    int k = BloomHashes;
    double nm = (double)n / (double)m;
    return std::pow(1.0 - std::exp(-(double)k * nm), (double)k);
}

void
ResidentDir::validateCanonical(const UBCCDirEntry &in, uint64_t pa) const
{
    if (UBCCDirEntry::canonicalOneHotRequired(in)) {
        panic_if(__builtin_popcountll(in.sharersMask) != 1,
                 "ResidentDir invalid exclusive entry PA=0x%lx sharers=0x%lx",
                 pa, in.sharersMask);
    }
    if (in.state == UBCCMESIState::G_S) {
        panic_if(in.sharersMask == 0,
                 "ResidentDir invalid shared entry PA=0x%lx sharers=0", pa);
    }
    if (in.state == UBCCMESIState::G_I) {
        panic_if(in.sharersMask != 0,
                 "ResidentDir invalid G_I entry PA=0x%lx sharers=0x%lx",
                 pa, in.sharersMask);
    }
}

void
ResidentDir::dumpStatsJson() const
{
    std::fprintf(stderr,
        "[ResidentDirStats] {\"dir_hits\":%lu,\"dir_misses\":%lu,"
        "\"dir_evictions\":%lu,\"bloom_fp_count\":%lu,"
        "\"capacity\":%zu,\"count\":%zu}\n",
        _dirHits, _dirMisses, _dirEvictions, _bloomFpCount,
        _layout.capacity, _count);
}

} // namespace glob
} // namespace cc
