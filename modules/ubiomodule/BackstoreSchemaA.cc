#include "BackstoreSchemaA.hh"

#include <cstring>

namespace cc
{

namespace glob
{

namespace
{

uint64_t splitmix64(uint64_t x)
{
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

} // anonymous namespace

uint32_t
BackstoreSchemaA::groupForPa(uint64_t pa) const
{
    return static_cast<uint32_t>(splitmix64(pa >> 6) % BackstoreNumGroups);
}

std::vector<uint64_t>
BackstoreSchemaA::candidatePagesForLookup(uint64_t pa,
                                          const GroupIndex& idx) const
{
    std::vector<uint64_t> pages;
    // Phase D1: return ALL pages in the chain (head through tail via
    // page_directory slots 0-3), so lookup can find entries that
    // overflowed onto chained pages.  Previously only returned head.
    for (int i = 0; i < 4; ++i) {
        if (idx.page_directory[i] != 0)
            pages.push_back(idx.page_directory[i]);
    }
    return pages;
}

bool
BackstoreSchemaA::lookupInPage(uint64_t pa, const BackstorePage& page,
                               BackstoreEntry& out) const
{
    int off = findEntryOffset(page, pa);
    if (off < 0)
        return false;

    const uint8_t* raw = page.entryAt(off);
    if (!raw)
        return false;

    CompactCodec::unpack(pa, raw, out);
    if (out.deleted)
        return false;
    return true;
}

BackstoreOrganization::UpdatePlan
BackstoreSchemaA::planUpsert(uint64_t pa, const BackstoreEntry& entry,
                             const GroupIndex& idx) const
{
    UpdatePlan plan;

    if (idx.page_directory[kTailIdx] != 0) {
        plan.target_page_pa = idx.page_directory[kTailIdx];
        plan.needs_read_before = true;
        plan.needs_new_page = false;
        plan.entry_slot_offset = -1;
    } else {
        plan.needs_new_page = true;
        plan.needs_read_before = false;
        plan.target_page_pa = 0;
        plan.entry_slot_offset = 0;
    }

    return plan;
}

BackstoreOrganization::UpdatePlan
BackstoreSchemaA::planDelete(uint64_t pa,
                             const GroupIndex& idx) const
{
    UpdatePlan plan;
    plan.is_tombstone = true;

    if (idx.page_directory[kHeadIdx] == 0) {
        plan.target_page_pa = 0;
        plan.entry_slot_offset = -1;
        return plan;
    }

    plan.target_page_pa = idx.page_directory[kHeadIdx];
    plan.needs_read_before = true;
    plan.needs_new_page = false;
    plan.entry_slot_offset = -1;

    return plan;
}

std::vector<uint64_t>
BackstoreSchemaA::scanGroupPages(const GroupIndex& idx) const
{
    std::vector<uint64_t> pages;
    // Phase D1: return all pages in directory slots 0-3
    for (int i = 0; i < 4; ++i) {
        if (idx.page_directory[i] != 0)
            pages.push_back(idx.page_directory[i]);
    }
    return pages;
}

void
BackstoreSchemaA::applyUpsert(BackstorePage& page, uint64_t pa,
                              const BackstoreEntry& entry,
                              const UpdatePlan& plan) const
{
    if (plan.needs_new_page) {
        page.clear();
        page.hdr.page_id = plan.target_page_pa;
    }

    int off = findEntryOffset(page, pa);
    if (off >= 0) {
        CompactCodec::pack(entry, page.entryAt(off));
        return;
    }

    if (page.isFull()) {
        return;
    }

    if (plan.entry_slot_offset >= 0 &&
        plan.entry_slot_offset + CompactEntryBytes <= (int)BackstorePageEntryArea) {
        off = plan.entry_slot_offset;
    } else {
        off = findFreeSlot(page);
        if (off < 0)
            return;
    }

    CompactCodec::pack(entry, page.entryAt(off));
    page.hdr.entry_count++;
}

bool
BackstoreSchemaA::applyDelete(BackstorePage& page, uint64_t pa,
                              const UpdatePlan& plan) const
{
    int off = (plan.entry_slot_offset >= 0)
              ? plan.entry_slot_offset
              : findEntryOffset(page, pa);

    if (off < 0)
        return false;

    uint8_t* raw = page.entryAt(off);
    if (!raw)
        return false;

    BackstoreEntry cur;
    CompactCodec::unpack(pa, raw, cur);
    cur.deleted = true;
    CompactCodec::pack(cur, raw);
    return true;
}

void
BackstoreSchemaA::updateIndexAfterWrite(GroupIndex& idx,
                                        const UpdatePlan& plan,
                                        uint64_t new_page_pa) const
{
    if (plan.needs_new_page && new_page_pa != 0) {
        if (idx.page_directory[kHeadIdx] == 0) {
            // First page in this group
            idx.page_directory[kHeadIdx] = new_page_pa;
            idx.page_directory[kTailIdx] = new_page_pa;
        } else {
            // Phase D1: allocate into the next free directory slot (0-3).
            // Slots 2 and 3 are reserved for overflow chaining.
            int slot = idx.firstFreeDirSlot();
            if (slot >= 0) {
                idx.page_directory[slot] = new_page_pa;
                idx.page_directory[kTailIdx] = new_page_pa;
            }
            // If no free slot (all 4 used), overwrite the tail slot to
            // keep the chain bounded.  The old tail page remains reachable
            // via the scan that returns all non-zero directory entries.
        }
    }
}

int
BackstoreSchemaA::findFreeSlot(const BackstorePage& page) const
{
    for (int off = 0;
         off + CompactEntryBytes <= (int)BackstorePageEntryArea;
         off += CompactEntryBytes)
    {
        const uint8_t* raw = page.entryAt(off);
        if (!raw) break;
        bool zero = true;
        for (size_t i = 0; i < CompactEntryBytes; ++i)
            if (raw[i] != 0) { zero = false; break; }
        if (zero) return off;
    }
    // Phase D1: if all slots occupied or page is full, return -1
    // to signal caller that an overflow page is needed.
    // Never return an OOB offset (entry_count * entry_bytes could
    // exceed BackstorePageEntryArea when full).
    return -1;
}

int
BackstoreSchemaA::findEntryOffset(const BackstorePage& page, uint64_t pa) const
{
    for (int off = 0;
         off + CompactEntryBytes <= (int)BackstorePageEntryArea;
         off += CompactEntryBytes)
    {
        const uint8_t* raw = page.entryAt(off);
        if (!raw) break;

        bool zero = true;
        for (size_t i = 0; i < CompactEntryBytes; ++i)
            if (raw[i] != 0) { zero = false; break; }
        if (zero) continue;

        BackstoreEntry tmp;
        CompactCodec::unpack(pa, raw, tmp);
        (void)tmp;
        // Compare PA from the packed format:
        uint32_t w0, w1;
        std::memcpy(&w0, raw, 4);
        std::memcpy(&w1, raw + 4, 4);
        uint64_t paLo = (w0 & 0xFFFFFFFFULL);
        uint64_t paHi = ((w1 >> 20) & 0xFFFULL);
        uint64_t storedPa = paLo | (paHi << 32);

        if (storedPa == (pa & ((1ULL << 44) - 1)))
            return off;
    }
    return -1;
}

} // namespace glob
} // namespace cc
