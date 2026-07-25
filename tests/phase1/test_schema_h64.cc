/**
 * test_schema_h64.cc – Phase 1 standalone validation for Schema H64.
 *
 * Compile (from project root):
 *   g++ -std=c++17 -O2 -Wall \
 *       -I modules/ubiomodule -I modules/ubiomodule/mem/ruby -I . \
 *       tests/phase1/test_schema_h64.cc modules/ubiomodule/BackstoreSchemaH64.cc \
 *       -o /tmp/test_schema_h64
 *
 * Run:
 *   /tmp/test_schema_h64 [--ops=N] [--verbose]
 */

#include "BackstoreSchemaH64.hh"

#include <algorithm>
#include <cassert>
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <random>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using namespace cc::glob;

// ============================================================
// Test framework helpers
// ============================================================

static int g_pass = 0;
static int g_fail = 0;
static bool g_verbose = false;

#define TEST(name)                                                 \
    do {                                                           \
        if (g_verbose) std::printf("  TEST %s ... ",               \
                                   std::string(name).c_str());     \
    } while (0)

#define OK()                                                       \
    do {                                                           \
        if (g_verbose) std::printf("OK\n");                        \
        ++g_pass;                                                  \
    } while (0)

#define FAIL(fmt, ...)                                             \
    do {                                                           \
        std::printf("FAIL: %s:%d: " fmt "\n", __FILE__, __LINE__,  \
                    ##__VA_ARGS__);                                \
        ++g_fail;                                                  \
    } while (0)

#define CHECK(cond, fmt, ...)                                      \
    do {                                                           \
        if (!(cond)) {                                             \
            FAIL(fmt, ##__VA_ARGS__);                              \
            return;                                                \
        }                                                          \
    } while (0)

// ============================================================
// Utility helpers
// ============================================================

static std::string h64StatusStr(H64Status s)
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
    return "?";
}

static uint64_t lcg(uint64_t& state)
{
    state = state * 6364136223846793005ULL + 1442695040888963407ULL;
    return state;
}

// ============================================================
// Test 1: Codec round-trip (updated: verify integrity computation)
// ============================================================
static void test_codec_roundtrip()
{
    TEST("codec round-trip + integrity");

    CHECK(H64Codec::kSlotBytes == 12,
          "kSlotBytes=%zu expected 12", H64Codec::kSlotBytes);

    // All-zeros → EMPTY with zero integrity
    {
        uint8_t buf[12] = {};
        H64SlotEntry e;
        H64Codec::unpack(buf, e);
        CHECK(e.pa  == 0, "zero slot pa=%" PRIu64, e.pa);
        CHECK(e.mesi == UBCCMESIState::G_I, "zero mesi");
        CHECK(e.state == H64SlotState::EMPTY, "zero state");
        CHECK(e.sharers == 0, "zero sharers");
        CHECK(e.epoch == 0, "zero epoch");
        CHECK(e.integrity == 0, "zero integrity=%u", (unsigned)e.integrity);
        CHECK(checkIntegrity(e), "zero integrity check failed");
        CHECK(H64Codec::checkSlotIntegrity(buf), "zero slot integrity check failed");

        // Pack the EMPTY entry and verify
        uint8_t buf2[12] = {};
        H64Codec::pack(e, buf2);
        CHECK(std::memcmp(buf, buf2, 12) == 0,
              "zero pack->unpack->pack mismatch");
    }

    // Max-values round-trip with integrity
    {
        H64SlotEntry e;
        e.pa        = (1ULL << 44) - 1;
        e.mesi      = UBCCMESIState::G_M;
        e.state     = H64SlotState::LIVE;
        e.sharers   = 0xFFFF;
        e.epoch     = (1U << 24) - 1;

        uint8_t buf[12];
        H64Codec::pack(e, buf);
        H64SlotEntry e2;
        H64Codec::unpack(buf, e2);
        // Compare data fields (integrity is auto-computed by pack)
        CHECK(e.pa == e2.pa && e.mesi == e2.mesi && e.state == e2.state &&
              e.sharers == e2.sharers && e.epoch == e2.epoch,
              "max-values round-trip failed: pa=%" PRIu64 "->%" PRIu64,
              e.pa, e2.pa);
        CHECK(checkIntegrity(e2),
              "max-values integrity check failed");
        CHECK(H64Codec::checkSlotIntegrity(buf),
              "max-values slot integrity check failed");
    }

    // Single-bit corruption → integrity failure
    {
        H64SlotEntry e;
        e.pa = 0x1000; e.mesi = UBCCMESIState::G_E;
        e.state = H64SlotState::LIVE; e.sharers = 1; e.epoch = 42;

        uint8_t buf[12];
        H64Codec::pack(e, buf);
        CHECK(H64Codec::checkSlotIntegrity(buf),
              "slot integrity should be valid before corruption");

        // Corrupt one bit in PA area
        buf[0] ^= 0x01;
        CHECK(!H64Codec::checkSlotIntegrity(buf),
              "single-bit corruption should fail integrity check");

        // Restore
        buf[0] ^= 0x01;
        CHECK(H64Codec::checkSlotIntegrity(buf),
              "restored integrity should pass");

        // Corrupt epoch area
        buf[9] ^= 0x80;
        CHECK(!H64Codec::checkSlotIntegrity(buf),
              "epoch-bit corruption should fail integrity check");
    }

    // EMPTY with non-zero data → corrupt
    {
        uint8_t buf[12] = {};
        buf[0] = 0x01; // non-zero PA byte
        CHECK(!H64Codec::checkSlotIntegrity(buf),
              "all-zeros EMPTY with corrupted pa-byte should fail");
    }

    // Random round-trips (10K)
    {
        uint64_t rng = 0xDEADBEEFCAFE1234ULL;
        for (int i = 0; i < 10000; ++i) {
            H64SlotEntry e;
            e.pa        = lcg(rng) & ((1ULL << 44) - 1);
            e.mesi      = static_cast<UBCCMESIState>(lcg(rng) & 0x3);
            e.state     = static_cast<H64SlotState>(lcg(rng) & 0x3);
            e.sharers   = static_cast<uint16_t>(lcg(rng) & 0xFFFF);
            e.epoch     = static_cast<uint32_t>(lcg(rng) & ((1U << 24) - 1));

            uint8_t buf[12];
            H64Codec::pack(e, buf);
            H64SlotEntry e2;
            H64Codec::unpack(buf, e2);

            // Compare data fields (integrity is auto-computed by pack)
            CHECK(e.pa == e2.pa &&
                  e.mesi == e2.mesi &&
                  e.state == e2.state &&
                  e.sharers == e2.sharers &&
                  e.epoch == e2.epoch,
                  "codec roundtrip #%d failed: pa=%" PRIu64 "->%" PRIu64,
                  i, e.pa, e2.pa);

            CHECK(checkIntegrity(e2),
                  "codec roundtrip #%d integrity check failed", i);
        }
    }

    OK();
}

// ============================================================
// Test 2: Bucket layout static assertions
// ============================================================
static void test_bucket_layout()
{
    TEST("bucket layout static assertions");

    CHECK(sizeof(H64BucketLine) == 64,
          "H64BucketLine size=%zu expected 64", sizeof(H64BucketLine));
    CHECK(kSlotsPerBucket == 5,
          "kSlotsPerBucket=%zu expected 5", kSlotsPerBucket);
    CHECK(kSlotAreaSize == 60,
          "kSlotAreaSize=%zu expected 60", kSlotAreaSize);
    CHECK(kBucketHeaderSize == 4,
          "kBucketHeaderSize=%zu expected 4", kBucketHeaderSize);

    // Verify header pack/unpack round-trip
    uint32_t packed = H64BucketHeader::pack(1, 42, 3, 1, 0xAB);
    uint8_t fmt, gen, live, tomb, rsv;
    H64BucketHeader::unpack(packed, fmt, gen, live, tomb, rsv);
    CHECK(fmt  == 1,   "hdr fmt %u != 1", fmt);
    CHECK(gen  == 42,  "hdr gen %u != 42", gen);
    CHECK(live == 3,   "hdr live %u != 3", live);
    CHECK(tomb == 1,   "hdr tomb %u != 1", tomb);
    CHECK(rsv  == 0xAB,"hdr rsv %u != 0xAB", rsv);

    H64BucketLine bl;
    CHECK(bl.fmtVersion() == 1,
          "default fmtVersion=%u expected 1", bl.fmtVersion());
    CHECK(bl.liveCount() == 0,
          "default liveCount=%u expected 0", bl.liveCount());

    bl.clear();
    CHECK(bl.fmtVersion() == 1,
          "after clear fmtVersion=%u expected 1", bl.fmtVersion());

    // Validate header valid() function
    CHECK(H64BucketHeader::valid(H64BucketHeader::defaultRaw()),
          "default header should be valid");
    CHECK(!H64BucketHeader::valid(H64BucketHeader::pack(99, 0, 0, 0, 0)),
          "wrong format version should be invalid");
    CHECK(!H64BucketHeader::valid(H64BucketHeader::pack(1, 0, 6, 0, 0)),
          "live_count > 5 should be invalid");

    OK();
}

// ============================================================
// Test 3: Basic CRUD
// ============================================================
static void test_basic_crud()
{
    TEST("basic CRUD");
    H64Config cfg;
    cfg.num_groups = 16;
    cfg.buckets_per_group = 64;
    BackstoreSchemaH64 tbl(cfg);

    uint64_t pa = 0x1000;
    H64SlotEntry e;
    e.pa      = pa;
    e.mesi    = UBCCMESIState::G_E;
    e.sharers = 0x0001;
    e.epoch   = 1;
    e.state   = H64SlotState::LIVE;

    // Initial lookup: NotFound
    {
        H64SlotEntry out;
        H64Status st = tbl.lookup(pa, out);
        CHECK(st == H64Status::NotFound,
              "initial lookup expected NotFound, got %s", h64StatusStr(st).c_str());
    }

    // Insert
    CHECK(tbl.upsert(pa, e) == H64Status::Found, "insert failed");

    // Lookup after insert: Found
    {
        H64SlotEntry out;
        H64Status st = tbl.lookup(pa, out);
        CHECK(st == H64Status::Found,
              "lookup after insert expected Found, got %s", h64StatusStr(st).c_str());
        CHECK(out.pa == pa, "pa mismatch");
        CHECK(out.mesi == UBCCMESIState::G_E, "mesi mismatch");
        CHECK(out.epoch == 1, "epoch mismatch");
        CHECK(checkIntegrity(out), "live entry integrity check failed");
    }

    // Update
    {
        e.sharers = 0x0002; // one-hot sharers for G_E
        e.epoch   = 2;
        CHECK(tbl.upsert(pa, e) == H64Status::Found, "update failed");

        H64SlotEntry out;
        tbl.lookup(pa, out);
        CHECK(out.sharers == 0x0002, "update sharers mismatch");
        CHECK(out.epoch == 2, "update epoch mismatch");
    }

    // Delete
    CHECK(tbl.erase(pa, 2) == H64Status::Found, "delete failed");

    // Lookup after delete: NotFound
    {
        H64SlotEntry out;
        H64Status st = tbl.lookup(pa, out);
        CHECK(st == H64Status::NotFound,
              "lookup after delete expected NotFound, got %s", h64StatusStr(st).c_str());
    }

    // Delete already-absent
    CHECK(tbl.erase(pa, 2) == H64Status::AlreadyAbsent,
          "double-delete expected AlreadyAbsent");

    // Re-insert after delete
    {
        e.epoch = 3;
        CHECK(tbl.upsert(pa, e) == H64Status::Found, "re-insert failed");

        H64SlotEntry out;
        CHECK(tbl.lookup(pa, out) == H64Status::Found, "lookup after re-insert failed");
        CHECK(out.epoch == 3, "re-insert epoch mismatch");
    }

    OK();
}

// ============================================================
// Test 4: Same-PA update / idempotent
// ============================================================
static void test_same_pa_update()
{
    TEST("same-PA update / idempotent");
    H64Config cfg;
    cfg.num_groups = 4;
    cfg.buckets_per_group = 32;
    BackstoreSchemaH64 tbl(cfg);

    uint64_t pa = 0xABCD0000;
    H64SlotEntry e;
    e.pa = pa; e.mesi = UBCCMESIState::G_S;
    e.sharers = 0x00FF; e.epoch = 5; e.state = H64SlotState::LIVE;

    CHECK(tbl.upsert(pa, e) == H64Status::Found, "initial insert failed");

    // Same epoch, idempotent
    e.sharers = 0x00AA;
    CHECK(tbl.upsert(pa, e) == H64Status::Found, "same-epoch upsert failed");

    H64SlotEntry out;
    tbl.lookup(pa, out);
    CHECK(out.sharers == 0x00AA, "same-epoch update sharers mismatch");
    CHECK(out.epoch == 5, "same-epoch epoch changed");

    // Higher epoch update
    e.epoch = 6; e.sharers = 0x0055;
    CHECK(tbl.upsert(pa, e) == H64Status::Found, "higher-epoch upsert failed");
    tbl.lookup(pa, out);
    CHECK(out.epoch == 6, "higher-epoch epoch mismatch");

    // Lower epoch update: StaleEpoch
    e.epoch = 4; e.sharers = 0x0001;
    CHECK(tbl.upsert(pa, e) == H64Status::StaleEpoch,
          "lower-epoch upsert expected StaleEpoch");
    tbl.lookup(pa, out);
    CHECK(out.epoch == 6, "epoch changed after stale upsert");
    CHECK(out.sharers == 0x0055, "sharers changed after stale upsert");

    OK();
}

// ============================================================
// Test 5: Stale update / delete
// ============================================================
static void test_stale_operations()
{
    TEST("stale update / delete rejection");
    H64Config cfg;
    cfg.num_groups = 4;
    cfg.buckets_per_group = 32;
    BackstoreSchemaH64 tbl(cfg);

    uint64_t pa = 0xDEAD0000;
    H64SlotEntry e;
    e.pa = pa; e.mesi = UBCCMESIState::G_M;
    e.sharers = 0x0001; e.epoch = 10; e.state = H64SlotState::LIVE;

    CHECK(tbl.upsert(pa, e) == H64Status::Found, "insert failed");

    // Stale delete
    CHECK(tbl.erase(pa, 5) == H64Status::StaleEpoch, "stale delete expected StaleEpoch");

    H64SlotEntry out;
    CHECK(tbl.lookup(pa, out) == H64Status::Found, "entry lost after stale delete");

    // Valid delete
    CHECK(tbl.erase(pa, 10) == H64Status::Found, "valid delete failed");
    CHECK(tbl.lookup(pa, out) == H64Status::NotFound, "entry still present after delete");

    OK();
}

// ============================================================
// Test 6: Tombstone cluster lookup
// ============================================================
static void test_tombstone_cluster()
{
    TEST("tombstone cluster lookup");
    H64Config cfg;
    cfg.num_groups = 1;
    cfg.buckets_per_group = 16;
    BackstoreSchemaH64 tbl(cfg);

    const int N = 20;
    std::vector<uint64_t> pas;
    uint64_t base = 0x100000;

    for (int i = 0; i < N; ++i) {
        H64SlotEntry e;
        e.pa = base + i * 64;
        e.mesi = UBCCMESIState::G_S;
        e.sharers = static_cast<uint16_t>(i);
        e.epoch = 1;
        e.state = H64SlotState::LIVE;

        H64Status st = tbl.upsert(e.pa, e);
        CHECK(st == H64Status::Found || st == H64Status::CapacityExhausted,
              "insert #%d: %s", i, h64StatusStr(st).c_str());
        if (st == H64Status::Found) pas.push_back(e.pa);
    }

    // Delete even-indexed
    std::unordered_set<uint64_t> deleted;
    for (size_t i = 0; i < pas.size(); i += 2) {
        tbl.erase(pas[i], 1);
        deleted.insert(pas[i]);
    }

    // Verify
    for (size_t i = 0; i < pas.size(); ++i) {
        H64SlotEntry out;
        H64Status st = tbl.lookup(pas[i], out);
        if (deleted.count(pas[i])) {
            CHECK(st == H64Status::NotFound,
                  "deleted PA 0x%" PRIx64 " expected NotFound, got %s",
                  pas[i], h64StatusStr(st).c_str());
        } else {
            CHECK(st == H64Status::Found,
                  "live PA 0x%" PRIx64 " expected Found, got %s",
                  pas[i], h64StatusStr(st).c_str());
        }
    }

    // Never-inserted PA
    {
        H64SlotEntry out;
        H64Status st = tbl.lookup(0xFFFF0000, out);
        CHECK(st == H64Status::NotFound || st == H64Status::CapacityExhausted,
              "never-inserted PA got %s", h64StatusStr(st).c_str());
    }

    OK();
}

// ============================================================
// Test 7: Delete / reinsert (tombstone reuse)
// ============================================================
static void test_delete_reinsert()
{
    TEST("delete / reinsert (tombstone reuse)");
    H64Config cfg;
    cfg.num_groups = 1;
    cfg.buckets_per_group = 4;
    BackstoreSchemaH64 tbl(cfg);

    uint64_t pa = 0x5000;
    H64SlotEntry e;
    e.pa = pa; e.mesi = UBCCMESIState::G_E;
    e.sharers = 1; e.epoch = 1; e.state = H64SlotState::LIVE;

    size_t before = 0;
    tbl.scanLiveEntries([&](const H64SlotEntry&) { ++before; });

    CHECK(tbl.upsert(pa, e) == H64Status::Found, "insert failed");

    size_t afterInsert = 0;
    tbl.scanLiveEntries([&](const H64SlotEntry&) { ++afterInsert; });
    CHECK(afterInsert == before + 1, "live count after insert: %zu != %zu", afterInsert, before + 1);

    // Delete → tombstone
    CHECK(tbl.erase(pa, 1) == H64Status::Found, "delete failed");

    size_t afterDelete = 0;
    tbl.scanLiveEntries([&](const H64SlotEntry&) { ++afterDelete; });
    CHECK(afterDelete == before, "live count after delete: %zu != %zu", afterDelete, before);

    // Re-insert reuses tombstone, live count +1, tomb count -1
    e.epoch = 2;
    CHECK(tbl.upsert(pa, e) == H64Status::Found, "re-insert failed");

    size_t afterReinsert = 0;
    tbl.scanLiveEntries([&](const H64SlotEntry&) { ++afterReinsert; });
    CHECK(afterReinsert == before + 1, "live count after re-insert: %zu != %zu", afterReinsert, before + 1);

    H64SlotEntry out;
    CHECK(tbl.lookup(pa, out) == H64Status::Found, "re-inserted PA not found");
    CHECK(out.epoch == 2, "re-insert epoch mismatch");

    OK();
}

// ============================================================
// Test 8: Tombstone priority over EMPTY (FIX #1)
// ============================================================
static void test_tombstone_priority()
{
    TEST("tombstone priority over EMPTY");

    // Use 1 bucket so all PAs share the same probe chain
    H64Config cfg;
    cfg.num_groups = 1;
    cfg.buckets_per_group = 1; // single bucket, 5 slots
    BackstoreSchemaH64 tbl(cfg);

    // Insert PA_A → first available slot
    uint64_t paA = 0x1000;
    H64SlotEntry eA;
    eA.pa = paA; eA.mesi = UBCCMESIState::G_S;
    eA.sharers = 1; eA.epoch = 1; eA.state = H64SlotState::LIVE;
    CHECK(tbl.upsert(paA, eA) == H64Status::Found, "insert A failed");

    // Insert PA_B → next available slot (after A in probe chain)
    uint64_t paB = 0x2000;
    H64SlotEntry eB;
    eB.pa = paB; eB.mesi = UBCCMESIState::G_S;
    eB.sharers = 2; eB.epoch = 1; eB.state = H64SlotState::LIVE;
    CHECK(tbl.upsert(paB, eB) == H64Status::Found, "insert B failed");

    // Verify header counts
    const auto& bkt0 = tbl.bucket(0, 0);
    CHECK(bkt0.liveCount() == 2,
          "live count=%u expected 2 after inserts", bkt0.liveCount());
    CHECK(bkt0.tombstoneCount() == 0,
          "tombstone count=%u expected 0", bkt0.tombstoneCount());

    // Delete PA_B → tombstone
    CHECK(tbl.erase(paB, 1) == H64Status::Found, "delete B failed");
    const auto& bkt1 = tbl.bucket(0, 0);
    CHECK(bkt1.tombstoneCount() == 1,
          "tombstone count=%u expected 1 after delete", bkt1.tombstoneCount());
    CHECK(bkt1.liveCount() == 1,
          "live count=%u expected 1 after delete", bkt1.liveCount());

    // Now insert PA_C. Must reuse tombstone, NOT a new empty slot
    uint64_t paC = 0x3000;
    H64SlotEntry eC;
    eC.pa = paC; eC.mesi = UBCCMESIState::G_E;
    eC.sharers = 1; eC.epoch = 5; eC.state = H64SlotState::LIVE;
    CHECK(tbl.upsert(paC, eC) == H64Status::Found, "insert C failed");

    // Check: tombstone count should be back to 0, live count = 2
    const auto& bkt2 = tbl.bucket(0, 0);
    CHECK(bkt2.tombstoneCount() == 0,
          "tombstone count=%u expected 0 after reuse", bkt2.tombstoneCount());
    CHECK(bkt2.liveCount() == 2,
          "live count=%u expected 2 (A+C)", bkt2.liveCount());

    // Lookup PA_C: Found
    H64SlotEntry out;
    CHECK(tbl.lookup(paC, out) == H64Status::Found,
          "PA_C not found after tombstone-reuse insert");
    CHECK(out.epoch == 5, "PA_C epoch mismatch");

    // Lookup PA_A: still Found (untouched)
    CHECK(tbl.lookup(paA, out) == H64Status::Found,
          "PA_A lost after tombstone-reuse");
    CHECK(out.epoch == 1, "PA_A epoch changed");

    // Now fill up with tombstones first, then verify capacity doesn't exhaust early
    // Delete PA_A too → another tombstone (B's was already reused by C)
    CHECK(tbl.erase(paA, 1) == H64Status::Found, "delete A failed");
    // Only A's slot is a tombstone now (one tombstone total)
    const auto& bkt3 = tbl.bucket(0, 0);
    CHECK(bkt3.tombstoneCount() == 1,
          "tombstone count=%u expected 1 (only A was deleted)", bkt3.tombstoneCount());
    CHECK(bkt3.liveCount() == 1,
          "live count=%u expected 1 (only C live)", bkt3.liveCount());

    // Re-insert PA_D → should reuse first tombstone (slot 0)
    uint64_t paD = 0x4000;
    H64SlotEntry eD;
    eD.pa = paD; eD.mesi = UBCCMESIState::G_S;
    eD.sharers = 0xF; eD.epoch = 10; eD.state = H64SlotState::LIVE;
    CHECK(tbl.upsert(paD, eD) == H64Status::Found, "insert D failed");
    CHECK(tbl.lookup(paD, out) == H64Status::Found, "PA_D not found");
    const auto& bkt4 = tbl.bucket(0, 0);
    CHECK(bkt4.tombstoneCount() == 0,
          "tombstone count=%u expected 0 after D reuses A's tombstone", bkt4.tombstoneCount());
    CHECK(bkt4.liveCount() == 2,
          "live count=%u expected 2 (C+D)", bkt4.liveCount());

    // ============================================================
    // Negative: complex tombstone accumulation → no early capacity exhaustion
    // ============================================================
    H64Config cfg2;
    cfg2.num_groups = 1;
    cfg2.buckets_per_group = 2; // 10 slots total
    BackstoreSchemaH64 tbl2(cfg2);

    // Insert 10 entries to fill all slots
    std::vector<uint64_t> fillPAs;
    for (int i = 0; i < 10; ++i) {
        uint64_t pa = 0xA000 + i * 64;
        H64SlotEntry e;
        e.pa = pa; e.mesi = UBCCMESIState::G_S;
        e.sharers = static_cast<uint16_t>(i + 1); e.epoch = 1;
        e.state = H64SlotState::LIVE;
        H64Status st = tbl2.upsert(pa, e);
        if (st == H64Status::CapacityExhausted) break;
        CHECK(st == H64Status::Found, "fill insert %d failed: %s",
              i, h64StatusStr(st).c_str());
        fillPAs.push_back(pa);
    }

    // Delete all entries → all become tombstones
    size_t deleted = 0;
    for (auto pa : fillPAs) {
        if (tbl2.erase(pa, 1) == H64Status::Found) ++deleted;
    }
    CHECK(deleted == fillPAs.size(),
          "deleted %zu/%zu entries", deleted, fillPAs.size());

    // Re-insert all → should reuse tombstones, not hit CapacityExhausted
    size_t reinserted = 0;
    for (auto pa : fillPAs) {
        H64SlotEntry e;
        e.pa = pa; e.mesi = UBCCMESIState::G_S;
        e.sharers = 1; e.epoch = 2;
        e.state = H64SlotState::LIVE;
        if (tbl2.upsert(pa, e) == H64Status::Found) ++reinserted;
    }
    CHECK(reinserted == fillPAs.size(),
          "reinserted %zu/%zu after full delete; expected no CapacityExhausted",
          reinserted, fillPAs.size());

    // All entries reachable
    for (auto pa : fillPAs) {
        H64SlotEntry out2;
        CHECK(tbl2.lookup(pa, out2) == H64Status::Found,
              "PA=0x%" PRIx64 " not found after re-insertion", pa);
    }

    OK();
}

// ============================================================
// Test 9: Collision + capacity exhaustion
// ============================================================
static void test_capacity_exhaustion()
{
    TEST("collision + capacity exhaustion");
    H64Config cfg;
    cfg.num_groups = 1;
    cfg.buckets_per_group = 2;
    BackstoreSchemaH64 tbl(cfg);

    int inserted = 0;
    for (int i = 0; i < 20; ++i) {
        uint64_t pa = 0x100000 + i * 64;
        H64SlotEntry e;
        e.pa = pa; e.mesi = UBCCMESIState::G_S;
        e.sharers = static_cast<uint16_t>(i); e.epoch = 1; e.state = H64SlotState::LIVE;

        H64Status st = tbl.upsert(pa, e);
        if (st == H64Status::Found) {
            ++inserted;
        } else if (st == H64Status::CapacityExhausted) {
            break;
        } else {
            CHECK(false, "unexpected insert status: %s", h64StatusStr(st).c_str());
        }
    }

    CHECK(inserted <= 10, "inserted %d > capacity 10", inserted);
    CHECK(inserted >= 1, "inserted %d, expected at least 1", inserted);

    if (g_verbose) std::printf("(inserted %d/10) ", inserted);

    // Verify all inserted entries
    size_t liveCount = 0;
    tbl.scanLiveEntries([&](const H64SlotEntry& e) {
        ++liveCount;
        H64SlotEntry out;
        CHECK(tbl.lookup(e.pa, out) == H64Status::Found,
              "live entry 0x%" PRIx64 " not found", e.pa);
    });
    CHECK(liveCount == static_cast<size_t>(inserted),
          "scanLive %zu != inserted %d", liveCount, inserted);

    // Post-full insert
    H64SlotEntry ee;
    ee.pa = 0xF0000000; ee.mesi = UBCCMESIState::G_I;
    ee.epoch = 1; ee.state = H64SlotState::LIVE;
    CHECK(tbl.upsert(ee.pa, ee) == H64Status::CapacityExhausted,
          "post-full insert expected CapacityExhausted");

    OK();
}

// ============================================================
// Test 10: Group rebuild (fixed: safe abort, generation)
// ============================================================
static void test_group_rebuild()
{
    TEST("per-group rebuild (safe abort + generation)");
    H64Config cfg;
    cfg.num_groups = 4;
    cfg.buckets_per_group = 8;
    BackstoreSchemaH64 tbl(cfg);

    uint64_t rng = 42;

    struct Rec {
        uint64_t pa;
        uint32_t epoch;
        uint16_t sharers;
        size_t   group;
    };
    std::vector<Rec> records;

    // Fill with valid random entries
    for (int i = 0; i < 50; ++i) {
        uint64_t pa = (lcg(rng) & 0xFFFFFFFFFFFULL) & ~0x3FULL;

        H64SlotEntry e;
        e.pa = pa;
        // Ensure valid MESI/SHARERS combo
        uint32_t rawMesi = static_cast<uint32_t>(lcg(rng) & 0x3);
        e.mesi = static_cast<UBCCMESIState>(rawMesi);
        e.state = H64SlotState::LIVE;

        if (e.mesi == UBCCMESIState::G_E || e.mesi == UBCCMESIState::G_M) {
            // One-hot sharers from a power-of-2 mask
            e.sharers = static_cast<uint16_t>(1u << (lcg(rng) & 0xF));
        } else if (e.mesi == UBCCMESIState::G_I) {
            e.sharers = 0;
        } else {
            e.sharers = static_cast<uint16_t>(lcg(rng) & 0xFFFF);
        }
        e.epoch = static_cast<uint32_t>(lcg(rng) & 0xFFFFFF);

        H64Status st = tbl.upsert(pa, e);
        if (st == H64Status::Found) {
            records.push_back({pa, e.epoch, e.sharers, 0});
        }
    }

    // Find group for each record
    for (auto& r : records) {
        bool found = false;
        for (size_t g = 0; g < cfg.num_groups && !found; ++g) {
            for (size_t b = 0; b < cfg.buckets_per_group && !found; ++b) {
                const auto& bucket = tbl.bucket(g, b);
                for (int s = 0; s < 5; ++s) {
                    H64SlotEntry slotE;
                    H64Codec::unpack(bucket.slotAt(s), slotE);
                    if (slotE.state == H64SlotState::LIVE && slotE.pa == r.pa) {
                        r.group = g;
                        found = true;
                        break;
                    }
                }
            }
        }
        CHECK(found, "inserted PA=0x%" PRIx64 " not found in any bucket", r.pa);
    }

    if (g_verbose) std::printf("(%zu entries) ", records.size());

    // Record generations before rebuild
    std::vector<uint8_t> genBefore(cfg.num_groups);
    for (size_t g = 0; g < cfg.num_groups; ++g) {
        genBefore[g] = tbl.groupGeneration(g);
    }

    // Rebuild groups that have entries
    std::unordered_set<size_t> rebuiltGroups;
    for (size_t g = 0; g < cfg.num_groups; ++g) {
        bool hasEntries = false;
        for (const auto& r : records) {
            if (r.group == g) { hasEntries = true; break; }
        }
        if (!hasEntries) continue;

        H64Status st = tbl.rebuildGroup(g);
        CHECK(st == H64Status::Found,
              "rebuildGroup(%zu) expected Found, got %s",
              g, h64StatusStr(st).c_str());
        rebuiltGroups.insert(g);
    }

    // Verify generations
    for (size_t g = 0; g < cfg.num_groups; ++g) {
        uint8_t genAfter = tbl.groupGeneration(g);
        if (rebuiltGroups.count(g)) {
            CHECK(genAfter == static_cast<uint8_t>(genBefore[g] + 1),
                  "rebuilt group %zu gen=%u expected %u",
                  g, (unsigned)genAfter, (unsigned)genBefore[g] + 1);
        } else {
            CHECK(genAfter == genBefore[g],
                  "unrebuilt group %zu gen changed: %u -> %u",
                  g, (unsigned)genBefore[g], (unsigned)genAfter);
        }
    }

    // Verify all generations are uniform within each rebuilt group
    for (size_t g : rebuiltGroups) {
        uint8_t firstGen = tbl.bucket(g, 0).generation();
        for (size_t b = 1; b < cfg.buckets_per_group; ++b) {
            CHECK(tbl.bucket(g, b).generation() == firstGen,
                  "group %zu bucket %zu gen=%u != first gen=%u",
                  g, b, tbl.bucket(g, b).generation(), firstGen);
        }
    }

    // All entries still reachable
    for (const auto& r : records) {
        H64SlotEntry out;
        H64Status st = tbl.lookup(r.pa, out);
        CHECK(st == H64Status::Found,
              "after rebuild: PA=0x%" PRIx64 " (group %zu) not found, status=%s",
              r.pa, r.group, h64StatusStr(st).c_str());
        if (st == H64Status::Found) {
            CHECK(out.epoch == r.epoch,
                  "after rebuild: PA=0x%" PRIx64 " epoch %u != %u",
                  r.pa, out.epoch, r.epoch);
            CHECK(checkIntegrity(out),
                  "after rebuild: PA=0x%" PRIx64 " integrity check failed", r.pa);
        }
    }

    size_t liveCount = 0;
    tbl.scanLiveEntries([&](const H64SlotEntry&) { ++liveCount; });
    CHECK(liveCount == records.size(),
          "scanLiveEntries %zu != records %zu", liveCount, records.size());

    // Rebuild invalid group → InvalidArgument
    CHECK(tbl.rebuildGroup(999) == H64Status::InvalidArgument,
          "invalid group rebuild expected InvalidArgument");

    OK();
}

// ============================================================
// Test 11: Corrupt propagation (FIX #2)
// ============================================================
static void test_corrupt_propagation()
{
    TEST("corrupt propagation (not downgraded)");

    // Use single bucket so ALL probe paths go through bucket 0.
    // This guarantees the corrupted header is always encountered.
    H64Config cfg;
    cfg.num_groups = 1;
    cfg.buckets_per_group = 1; // single bucket for guaranteed corruption coverage
    BackstoreSchemaH64 tbl(cfg);

    // Setup: insert PA_A into the table
    uint64_t paA = 0x3000;
    H64SlotEntry eA;
    eA.pa = paA; eA.mesi = UBCCMESIState::G_E;
    eA.sharers = 1; eA.epoch = 1; eA.state = H64SlotState::LIVE;
    CHECK(tbl.upsert(paA, eA) == H64Status::Found, "insert A failed");

    // Corrupt the only bucket's header
    H64BucketLine& bl = tbl.bucket(0, 0);
    uint8_t fmt, gen, live, tomb, rsv;
    H64BucketHeader::unpack(bl.hdr_raw, fmt, gen, live, tomb, rsv);
    bl.hdr_raw = H64BucketHeader::pack(99, gen, live, tomb, rsv); // invalid fmt

    // --- Subtest 1: lookup existing key → Corrupt (not Found) ---
    {
        H64SlotEntry out;
        H64Status st = tbl.lookup(paA, out);
        CHECK(st == H64Status::Corrupt,
              "lookup existing key in corrupt path expected Corrupt, got %s",
              h64StatusStr(st).c_str());
    }

    // --- Subtest 2: lookup non-existing key → Corrupt (not NotFound) ---
    {
        H64SlotEntry out;
        H64Status st = tbl.lookup(0x77770000, out);
        CHECK(st == H64Status::Corrupt,
              "lookup non-existing key in corrupt path expected Corrupt, got %s",
              h64StatusStr(st).c_str());
    }

    // --- Subtest 3: upsert in corrupt path → Corrupt ---
    {
        H64SlotEntry e;
        e.pa = 0x88880000; e.mesi = UBCCMESIState::G_S;
        e.sharers = 1; e.epoch = 1; e.state = H64SlotState::LIVE;
        H64Status st = tbl.upsert(0x88880000, e);
        CHECK(st == H64Status::Corrupt,
              "upsert in corrupt path expected Corrupt, got %s",
              h64StatusStr(st).c_str());
    }

    // --- Subtest 4: erase existing key → Corrupt ---
    {
        H64Status st = tbl.erase(paA, 1);
        CHECK(st == H64Status::Corrupt,
              "erase existing key in corrupt path expected Corrupt, got %s",
              h64StatusStr(st).c_str());
    }

    // --- Subtest 5: erase non-existing key → Corrupt ---
    {
        H64Status st = tbl.erase(0x99990000, 1);
        CHECK(st == H64Status::Corrupt,
              "erase non-existing key in corrupt path expected Corrupt, got %s",
              h64StatusStr(st).c_str());
    }

    // Restore header to clean state
    bl.hdr_raw = H64BucketHeader::pack(1, gen, live, tomb, rsv);

    // --- Subtest 6: RESERVED slot → Corrupt ---
    // Make bucket 0 slot 2 RESERVED (state=3)
    {
        uint8_t* slot2 = bl.slotAt(2);
        uint32_t w1 = 0;
        std::memcpy(&w1, slot2 + 4, 4);
        w1 = (w1 & ~(0x3u << 16)) | (3u << 16); // set state bits to 3 (RESERVED)
        std::memcpy(slot2 + 4, &w1, 4);
    }

    {
        H64SlotEntry out;
        H64Status st = tbl.lookup(paA, out);
        CHECK(st == H64Status::Corrupt,
              "lookup with RESERVED slot expected Corrupt, got %s",
              h64StatusStr(st).c_str());
    }

    // Restore slot 2
    {
        std::memset(bl.slotAt(2), 0, H64Codec::kSlotBytes);
    }

    // --- Subtest 7: slot with bad integrity → Corrupt ---
    {
        H64Config cfg2;
        cfg2.num_groups = 1;
        cfg2.buckets_per_group = 1; // single bucket for guaranteed coverage
        BackstoreSchemaH64 tbl2(cfg2);

        uint64_t paB = 0x44440000;
        H64SlotEntry eB;
        eB.pa = paB; eB.mesi = UBCCMESIState::G_S;
        eB.sharers = 0xF; eB.epoch = 10; eB.state = H64SlotState::LIVE;
        CHECK(tbl2.upsert(paB, eB) == H64Status::Found, "insert B failed");

        // Corrupt one byte in the stored entry — flip a bit in PA area
        bool corrupted = false;
        for (size_t b = 0; b < cfg2.buckets_per_group && !corrupted; ++b) {
            H64BucketLine& bucket = tbl2.bucket(0, b);
            for (int s = 0; s < 5; ++s) {
                H64SlotEntry slotE;
                H64Codec::unpack(bucket.slotAt(s), slotE);
                if (slotE.state == H64SlotState::LIVE && slotE.pa == paB) {
                    bucket.slotAt(s)[3] ^= 0x01; // flip one bit in PA
                    corrupted = true;
                    break;
                }
            }
        }
        CHECK(corrupted, "could not find PA_B to corrupt");

        // lookup with bad integrity → Corrupt, NOT NotFound
        {
            H64SlotEntry out;
            H64Status st = tbl2.lookup(paB, out);
            CHECK(st == H64Status::Corrupt,
                  "lookup with bad integrity expected Corrupt, got %s",
                  h64StatusStr(st).c_str());
        }

        // erase with bad integrity → Corrupt
        {
            H64Status st = tbl2.erase(paB, 10);
            CHECK(st == H64Status::Corrupt,
                  "erase with bad integrity expected Corrupt, got %s",
                  h64StatusStr(st).c_str());
        }

        // Non-existing key in corrupt bucket → Corrupt, not NotFound
        {
            H64SlotEntry out;
            H64Status st = tbl2.lookup(0xFFFF0000, out);
            CHECK(st == H64Status::Corrupt,
                  "lookup non-existing with bad integrity expected Corrupt, got %s",
                  h64StatusStr(st).c_str());
        }
    }

    OK();
}

// ============================================================
// Test 12: Integrity field validation (FIX #3)
// ============================================================
static void test_integrity_validation()
{
    TEST("integrity field validation");

    // Verify computeIntegrity is deterministic
    {
        uint8_t a = H64Codec::computeIntegrity(0x1000, 2, 1, 0xABCD, 42);
        uint8_t b = H64Codec::computeIntegrity(0x1000, 2, 1, 0xABCD, 42);
        CHECK(a == b, "integrity not deterministic: %u != %u", (unsigned)a, (unsigned)b);
    }

    // Different fields produce different integrity
    {
        uint8_t a = H64Codec::computeIntegrity(0x1000, 2, 1, 0xABCD, 42);
        uint8_t b = H64Codec::computeIntegrity(0x2000, 2, 1, 0xABCD, 42);
        CHECK(a != b, "different PA should produce different integrity");
    }

    // EMPTY has zero integrity
    {
        uint8_t e = H64Codec::computeIntegrity(0, 0, 0, 0, 0);
        CHECK(e == 0, "EMPTY integrity expected 0, got %u", (unsigned)e);
    }

    // Packed EMPTY slot has zero integrity byte
    {
        H64SlotEntry empty;
        uint8_t buf[12];
        H64Codec::pack(empty, buf);
        uint32_t w2;
        std::memcpy(&w2, buf + 8, 4);
        uint8_t integ = static_cast<uint8_t>(w2 & 0xFF);
        CHECK(integ == 0, "packed EMPTY integrity=%u expected 0", (unsigned)integ);
    }

    // Corrupted EMPTY (non-zero data) → slotCorrupt true → lookup returns Corrupt
    {
        H64Config cfg;
        cfg.num_groups = 1;
        cfg.buckets_per_group = 1; // single bucket, all probes hit it
        BackstoreSchemaH64 tbl(cfg);

        // Corrupt the table's actual bucket directly
        H64BucketLine& bl = tbl.bucket(0, 0);
        bl.slotAt(0)[0] = 0x01; // corrupt EMPTY slot with non-zero byte

        H64SlotEntry out;
        H64Status st = tbl.lookup(0x1000, out);
        CHECK(st == H64Status::Corrupt,
              "corrupt EMPTY slot lookup expected Corrupt, got %s",
              h64StatusStr(st).c_str());

        // Upsert with corrupt slot in path → Corrupt
        H64SlotEntry e;
        e.pa = 0x2000; e.mesi = UBCCMESIState::G_S;
        e.sharers = 1; e.epoch = 1; e.state = H64SlotState::LIVE;
        st = tbl.upsert(0x2000, e);
        CHECK(st == H64Status::Corrupt,
              "upsert with corrupt EMPTY slot expected Corrupt, got %s",
              h64StatusStr(st).c_str());

        // Erase with corrupt slot in path → Corrupt
        st = tbl.erase(0x3000, 1);
        CHECK(st == H64Status::Corrupt,
              "erase with corrupt EMPTY slot expected Corrupt, got %s",
              h64StatusStr(st).c_str());

        // Clean the corruption and verify operations resume normally
        bl.slotAt(0)[0] = 0x00; // restore
        CHECK(tbl.upsert(0x2000, e) == H64Status::Found,
              "upsert after restore failed");
    }

    // Upsert with valid data → integrity stored correctly
    {
        H64Config cfg;
        cfg.num_groups = 2;
        cfg.buckets_per_group = 4;
        BackstoreSchemaH64 tbl(cfg);

        uint64_t pa = 0x6000;
        H64SlotEntry e;
        e.pa = pa; e.mesi = UBCCMESIState::G_M;
        e.sharers = 1; e.epoch = 99; e.state = H64SlotState::LIVE;

        CHECK(tbl.upsert(pa, e) == H64Status::Found, "insert failed");

        // Find the slot and verify integrity
        for (size_t g = 0; g < cfg.num_groups; ++g) {
            for (size_t b = 0; b < cfg.buckets_per_group; ++b) {
                const auto& bucket = tbl.bucket(g, b);
                for (int s = 0; s < 5; ++s) {
                    H64SlotEntry slotE;
                    H64Codec::unpack(bucket.slotAt(s), slotE);
                    if (slotE.state == H64SlotState::LIVE && slotE.pa == pa) {
                        CHECK(checkIntegrity(slotE),
                              "live entry integrity check failed");
                        CHECK(H64Codec::checkSlotIntegrity(bucket.slotAt(s)),
                              "live entry slot integrity check failed");
                        goto verified;
                    }
                }
            }
        }
        verified:;
    }

    OK();
}

// ============================================================
// Test 13: Input validation (FIX #4)
// ============================================================
static void test_input_validation()
{
    TEST("input validation");

    H64Config cfg;
    cfg.num_groups = 4;
    cfg.buckets_per_group = 8;
    BackstoreSchemaH64 tbl(cfg);

    H64SlotEntry e;
    e.pa = 0x1000; e.mesi = UBCCMESIState::G_S;
    e.sharers = 0xF; e.epoch = 1; e.state = H64SlotState::LIVE;

    // --- Unaligned PA ---
    CHECK(tbl.upsert(0x1040, e) == H64Status::InvalidArgument,
          "entry.pa != callerPa upsert expected InvalidArgument");
    CHECK(tbl.lookup(0x1041, e) == H64Status::InvalidArgument,
          "unaligned PA lookup expected InvalidArgument");
    CHECK(tbl.erase(0x1041, 1) == H64Status::InvalidArgument,
          "unaligned PA erase expected InvalidArgument");

    // --- PA exceeds 44 bits ---
    {
        H64SlotEntry ee = e;
        ee.pa = (1ULL << 44) + 0x40;
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::InvalidArgument,
              ">44-bit PA upsert expected InvalidArgument");
    }

    // --- entry.pa != caller pa ---
    {
        H64SlotEntry ee = e;
        ee.pa = 0x2000;
        CHECK(tbl.upsert(0x3000, ee) == H64Status::InvalidArgument,
              "mismatched PA upsert expected InvalidArgument");
    }

    // --- Non-LIVE state for upsert ---
    {
        H64SlotEntry ee = e;
        ee.state = H64SlotState::HASH_TOMBSTONE;
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::InvalidArgument,
              "TOMBSTONE-state upsert expected InvalidArgument");
    }
    {
        H64SlotEntry ee = e;
        ee.state = H64SlotState::EMPTY;
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::InvalidArgument,
              "EMPTY-state upsert expected InvalidArgument");
    }

    // --- G_E/G_M with non-one-hot sharers ---
    {
        H64SlotEntry ee = e;
        ee.pa = 0x4000;
        ee.mesi = UBCCMESIState::G_E;
        ee.sharers = 0x0003; // two bits set
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::InvalidArgument,
              "G_E multi-bit sharers expected InvalidArgument");
    }
    {
        H64SlotEntry ee = e;
        ee.pa = 0x5000;
        ee.mesi = UBCCMESIState::G_M;
        ee.sharers = 0; // zero sharers
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::InvalidArgument,
              "G_M zero sharers expected InvalidArgument");
    }

    // --- G_E/G_M with one-hot sharers: VALID ---
    {
        H64SlotEntry ee = e;
        ee.pa = 0x6000;
        ee.mesi = UBCCMESIState::G_E;
        ee.sharers = 0x0001;
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::Found,
              "valid G_E one-hot upsert failed");
    }
    {
        H64SlotEntry ee = e;
        ee.pa = 0x7000;
        ee.mesi = UBCCMESIState::G_M;
        ee.sharers = 0x8000;
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::Found,
              "valid G_M one-hot upsert failed");
    }

    // --- G_I with non-zero sharers ---
    {
        H64SlotEntry ee = e;
        ee.pa = 0x8000;
        ee.mesi = UBCCMESIState::G_I;
        ee.sharers = 0x0001;
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::InvalidArgument,
              "G_I non-zero sharers expected InvalidArgument");
    }

    // --- G_S with zero sharers: VALID ---
    {
        H64SlotEntry ee = e;
        ee.pa = 0x9000;
        ee.mesi = UBCCMESIState::G_S;
        ee.sharers = 0;
        CHECK(tbl.upsert(ee.pa, ee) == H64Status::Found,
              "valid G_S zero-sharers upsert failed");
    }

    // --- Invalid MESI value (>3) ---
    // Can't construct directly through enum, but raw byte would be caught
    // The validateUpsertEntry function already checks `static_cast<uint8_t>(entry.mesi) > 3`

    OK();
}

// ============================================================
// Test 14: Corrupt / rebuild safety (FIX #5)
// ============================================================
static void test_corrupt_and_rebuild_safety()
{
    TEST("corrupt + rebuild safety guarantees");

    // Rebuild with too many entries (shouldn't happen, but code handles it)
    {
        H64Config cfg;
        cfg.num_groups = 1;
        cfg.buckets_per_group = 1; // 5 slots
        BackstoreSchemaH64 tbl(cfg);

        // Fill all 5 slots
        for (int i = 0; i < 5; ++i) {
            H64SlotEntry e;
            e.pa = 0x1000 + i * 0x40;
            e.mesi = UBCCMESIState::G_S;
            e.sharers = static_cast<uint16_t>(i + 1);
            e.epoch = 1;
            e.state = H64SlotState::LIVE;
            CHECK(tbl.upsert(e.pa, e) == H64Status::Found,
                  "fill slot %d failed", i);
        }

        // Rebuild should succeed (5 entries ≤ 5 slots)
        uint8_t genBefore = tbl.groupGeneration(0);
        CHECK(tbl.rebuildGroup(0) == H64Status::Found, "rebuild full group failed");
        uint8_t genAfter = tbl.groupGeneration(0);
        CHECK(genAfter == static_cast<uint8_t>(genBefore + 1),
              "generation not incremented: %u -> %u", genBefore, genAfter);

        // All entries still present
        for (int i = 0; i < 5; ++i) {
            H64SlotEntry out;
            CHECK(tbl.lookup(0x1000 + i * 0x40, out) == H64Status::Found,
                  "entry %d lost after rebuild", i);
        }
    }

    // Verify generation is uniform after rebuild
    {
        H64Config cfg;
        cfg.num_groups = 2;
        cfg.buckets_per_group = 4;
        BackstoreSchemaH64 tbl(cfg);

        // Insert a few entries
        for (int i = 0; i < 3; ++i) {
            H64SlotEntry e;
            e.pa = 0x10000 + i * 0x40;
            e.mesi = UBCCMESIState::G_S;
            e.sharers = static_cast<uint16_t>(i + 1);
            e.epoch = 1;
            e.state = H64SlotState::LIVE;
            tbl.upsert(e.pa, e);
        }

        tbl.rebuildGroup(0);

        // All buckets in group 0 have same generation
        uint8_t firstGen = tbl.bucket(0, 0).generation();
        for (size_t b = 1; b < cfg.buckets_per_group; ++b) {
            CHECK(tbl.bucket(0, b).generation() == firstGen,
                  "non-uniform generation in group 0: bucket %zu gen=%u != %u",
                  b, tbl.bucket(0, b).generation(), firstGen);
        }

        // Group 1 unchanged
        CHECK(tbl.groupGeneration(1) == 0,
              "unrebuilt group generation changed");
    }

    // Verify EMPTY slots have zero-integrity after rebuild
    {
        H64Config cfg;
        cfg.num_groups = 1;
        cfg.buckets_per_group = 2;
        BackstoreSchemaH64 tbl(cfg);

        H64SlotEntry e;
        e.pa = 0x20000; e.mesi = UBCCMESIState::G_E;
        e.sharers = 1; e.epoch = 1; e.state = H64SlotState::LIVE;
        tbl.upsert(e.pa, e);

        tbl.rebuildGroup(0);

        // All empty slots should pass integrity check
        for (size_t b = 0; b < cfg.buckets_per_group; ++b) {
            const auto& bucket = tbl.bucket(0, b);
            for (int s = 0; s < 5; ++s) {
                H64SlotEntry slotE;
                H64Codec::unpack(bucket.slotAt(s), slotE);
                if (slotE.state == H64SlotState::EMPTY) {
                    CHECK(checkIntegrity(slotE),
                          "EMPTY slot has bad integrity after rebuild: b=%zu s=%d", b, s);
                    // Check raw bytes are all zero
                    const uint8_t* raw = bucket.slotAt(s);
                    bool allZero = true;
                    for (size_t i = 0; i < 12; ++i) {
                        if (raw[i] != 0) { allZero = false; break; }
                    }
                    CHECK(allZero,
                          "EMPTY slot has non-zero bytes after rebuild: b=%zu s=%d", b, s);
                }
            }
        }
    }

    OK();
}

// ============================================================
// Test 15: Failure status propagation
// ============================================================
static void test_failure_status_propagation()
{
    TEST("failure status propagation");

    H64Config cfg;
    cfg.num_groups = 4;
    cfg.buckets_per_group = 8;
    BackstoreSchemaH64 tbl(cfg);

    // StaleEpoch from upsert
    {
        uint64_t pa = 0x7000;
        H64SlotEntry e;
        e.pa = pa; e.mesi = UBCCMESIState::G_E;
        e.sharers = 1; e.epoch = 100; e.state = H64SlotState::LIVE;
        CHECK(tbl.upsert(pa, e) == H64Status::Found, "setup failed");

        e.epoch = 50;
        CHECK(tbl.upsert(pa, e) == H64Status::StaleEpoch, "StaleEpoch upsert expected");
    }

    // StaleEpoch from erase
    {
        uint64_t pa = 0x8000;
        H64SlotEntry e;
        e.pa = pa; e.mesi = UBCCMESIState::G_M;
        e.sharers = 1; e.epoch = 200; e.state = H64SlotState::LIVE;
        CHECK(tbl.upsert(pa, e) == H64Status::Found, "setup failed");

        CHECK(tbl.erase(pa, 100) == H64Status::StaleEpoch, "StaleEpoch erase expected");

        H64SlotEntry out;
        CHECK(tbl.lookup(pa, out) == H64Status::Found, "entry lost after StaleEpoch erase");
        CHECK(out.epoch == 200, "epoch changed after StaleEpoch erase");
    }

    // AlreadyAbsent
    {
        CHECK(tbl.erase(0x9000, 1) == H64Status::AlreadyAbsent, "AlreadyAbsent expected");
    }

    // CapacityExhausted
    {
        H64Config tinyCfg;
        tinyCfg.num_groups = 1;
        tinyCfg.buckets_per_group = 1;
        BackstoreSchemaH64 tiny(tinyCfg);

        for (int i = 0; i < 10; ++i) {
            H64SlotEntry e;
            e.pa = 0xA000 + i * 64;
            e.mesi = UBCCMESIState::G_S;
            e.epoch = 1;
            e.state = H64SlotState::LIVE;
            H64Status st = tiny.upsert(e.pa, e);
            if (i >= 5) {
                CHECK(st == H64Status::CapacityExhausted,
                      "slot %d expected CapacityExhausted, got %s",
                      i, h64StatusStr(st).c_str());
            }
        }
    }

    // None of Corrupt/InvalidArgument should map to Found/NotFound
    // (tested in test_corrupt_propagation and test_input_validation)

    OK();
}

// ============================================================
// Test 16: Rebuild corruption detection and rollback
// ============================================================
static void test_rebuild_corrupt_detection()
{
    TEST("rebuild corruption detection + rollback");

    // --- Subtest 1: Header corruption → Corrupt, no mutation ---
    {
        H64Config cfg;
        cfg.num_groups = 1;
        cfg.buckets_per_group = 4;
        BackstoreSchemaH64 tbl(cfg);

        // Insert valid entries
        for (int i = 0; i < 3; ++i) {
            H64SlotEntry e;
            e.pa = 0x1000 + i * 64;
            e.mesi = UBCCMESIState::G_S;
            e.sharers = static_cast<uint16_t>(i + 1);
            e.epoch = 1;
            e.state = H64SlotState::LIVE;
            CHECK(tbl.upsert(e.pa, e) == H64Status::Found,
                  "setup insert %d failed", i);
        }

        // Corrupt a header
        H64BucketLine& bl = tbl.bucket(0, 2);
        uint8_t fmt, gen, live, tomb, rsv;
        H64BucketHeader::unpack(bl.hdr_raw, fmt, gen, live, tomb, rsv);
        bl.hdr_raw = H64BucketHeader::pack(99, gen, live, tomb, rsv);

        uint8_t genBefore = tbl.groupGeneration(0);
        H64Status st = tbl.rebuildGroup(0);
        CHECK(st == H64Status::Corrupt,
              "rebuild with corrupt header expected Corrupt, got %s",
              h64StatusStr(st).c_str());

        // Generation must NOT have changed
        uint8_t genAfter = tbl.groupGeneration(0);
        CHECK(genAfter == genBefore,
              "generation changed after aborted rebuild: %u -> %u",
              (unsigned)genBefore, (unsigned)genAfter);

        // All entries still reachable (no data loss)
        for (int i = 0; i < 3; ++i) {
            H64SlotEntry out;
            CHECK(tbl.lookup(0x1000 + i * 64, out) == H64Status::Found,
                  "entry %d lost after aborted corrupt rebuild", i);
        }

        // Cleanse the corruption
        bl.hdr_raw = H64BucketHeader::pack(1, gen, live, tomb, rsv);

        // Now rebuild should succeed
        st = tbl.rebuildGroup(0);
        CHECK(st == H64Status::Found,
              "rebuild after cleanse expected Found, got %s",
              h64StatusStr(st).c_str());
    }

    // --- Subtest 2: Slot corruption → Corrupt, no mutation ---
    {
        H64Config cfg;
        cfg.num_groups = 1;
        cfg.buckets_per_group = 4;
        BackstoreSchemaH64 tbl(cfg);

        // Insert entries
        H64SlotEntry e;
        e.pa = 0x5000; e.mesi = UBCCMESIState::G_E;
        e.sharers = 1; e.epoch = 1; e.state = H64SlotState::LIVE;
        CHECK(tbl.upsert(e.pa, e) == H64Status::Found, "insert failed");

        // Corrupt a slot in the group
        H64BucketLine& bl = tbl.bucket(0, 1);
        // Set the slot state to RESERVED
        uint32_t w1;
        std::memcpy(&w1, bl.slotAt(0) + 4, 4);
        w1 = (w1 & ~(0x3u << 16)) | (3u << 16);
        std::memcpy(bl.slotAt(0) + 4, &w1, 4);

        uint8_t genBefore = tbl.groupGeneration(0);
        H64Status st = tbl.rebuildGroup(0);
        CHECK(st == H64Status::Corrupt,
              "rebuild with RESERVED slot expected Corrupt, got %s",
              h64StatusStr(st).c_str());

        // Generation unchanged, entry still reachable
        CHECK(tbl.groupGeneration(0) == genBefore,
              "generation changed after aborted rebuild");
        H64SlotEntry out;
        CHECK(tbl.lookup(0x5000, out) == H64Status::Found,
              "entry lost after aborted rebuild");
    }

    // --- Subtest 3: Rebuild with full group → success ---
    {
        H64Config cfg;
        cfg.num_groups = 1;
        cfg.buckets_per_group = 1; // 5 slots
        BackstoreSchemaH64 tbl(cfg);

        for (int i = 0; i < 5; ++i) {
            H64SlotEntry e;
            e.pa = 0xA000 + i * 64;
            e.mesi = UBCCMESIState::G_S;
            e.sharers = static_cast<uint16_t>(i + 1);
            e.epoch = 100;
            e.state = H64SlotState::LIVE;
            CHECK(tbl.upsert(e.pa, e) == H64Status::Found,
                  "fill slot %d failed", i);
        }

        uint8_t genBefore = tbl.groupGeneration(0);
        CHECK(tbl.rebuildGroup(0) == H64Status::Found,
              "rebuild full group failed");

        uint8_t genAfter = tbl.groupGeneration(0);
        CHECK(genAfter == static_cast<uint8_t>(genBefore + 1),
              "generation not incremented: %u -> %u", genBefore, genAfter);

        // All entries still present with correct epochs
        for (int i = 0; i < 5; ++i) {
            H64SlotEntry out;
            CHECK(tbl.lookup(0xA000 + i * 64, out) == H64Status::Found,
                  "entry %d lost after rebuild", i);
            CHECK(out.epoch == 100,
                  "entry %d epoch %u != 100 after rebuild", i, out.epoch);
            CHECK(checkIntegrity(out),
                  "entry %d integrity check failed after rebuild", i);
        }
    }

    // --- Subtest 4: Rebuild empty group → success, gen incremented ---
    {
        H64Config cfg;
        cfg.num_groups = 2;
        cfg.buckets_per_group = 2;
        BackstoreSchemaH64 tbl(cfg);

        uint8_t genBefore = tbl.groupGeneration(1);
        CHECK(tbl.rebuildGroup(1) == H64Status::Found,
              "rebuild empty group failed");
        uint8_t genAfter = tbl.groupGeneration(1);
        CHECK(genAfter == static_cast<uint8_t>(genBefore + 1),
              "empty group gen not incremented: %u -> %u", genBefore, genAfter);

        // All slots in group 1 should be all-zero (valid EMPTY)
        for (size_t b = 0; b < cfg.buckets_per_group; ++b) {
            const auto& bucket = tbl.bucket(1, b);
            for (int s = 0; s < 5; ++s) {
                H64SlotEntry slotE;
                H64Codec::unpack(bucket.slotAt(s), slotE);
                CHECK(slotE.state == H64SlotState::EMPTY,
                      "non-EMPTY slot in rebuilt empty group: b=%zu s=%d", b, s);
                CHECK(checkIntegrity(slotE),
                      "bad integrity in empty group: b=%zu s=%d", b, s);
            }
        }
    }

    OK();
}
// ============================================================
static void test_epoch_edge_cases()
{
    TEST("epoch edge cases");
    H64Config cfg;
    cfg.num_groups = 4;
    cfg.buckets_per_group = 8;
    BackstoreSchemaH64 tbl(cfg);

    // Epoch 0
    uint64_t pa = 0xC000;
    H64SlotEntry e;
    e.pa = pa; e.mesi = UBCCMESIState::G_S;
    e.sharers = 0; e.epoch = 0; e.state = H64SlotState::LIVE;
    CHECK(tbl.upsert(pa, e) == H64Status::Found, "epoch-0 insert failed");

    e.sharers = 0x42;
    CHECK(tbl.upsert(pa, e) == H64Status::Found, "epoch-0 idempotent update failed");

    H64SlotEntry out;
    tbl.lookup(pa, out);
    CHECK(out.sharers == 0x42, "epoch-0 update sharers mismatch");
    CHECK(checkIntegrity(out), "epoch-0 integrity check failed");

    CHECK(tbl.erase(pa, 0) == H64Status::Found, "epoch-0 delete failed");

    // Max epoch
    pa = 0xD000;
    e.pa = pa; e.epoch = (1U << 24) - 1;
    e.sharers = 0xFFFF; e.state = H64SlotState::LIVE;
    CHECK(tbl.upsert(pa, e) == H64Status::Found, "max-epoch insert failed");

    tbl.lookup(pa, out);
    CHECK(out.epoch == (1U << 24) - 1, "max-epoch lookup mismatch");
    CHECK(checkIntegrity(out), "max-epoch integrity check failed");

    CHECK(tbl.erase(pa, (1U << 24) - 1) == H64Status::Found, "max-epoch delete failed");

    OK();
}

// ============================================================
// Test 17: Hash seed sensitivity
// ============================================================
static void test_hash_seed_sensitivity()
{
    TEST("hash seed sensitivity");

    const size_t N = 200;
    uint64_t rng = 8888;
    std::vector<uint64_t> pas;
    for (size_t i = 0; i < N; ++i) {
        pas.push_back((lcg(rng) & 0xFFFFFFFFFFFULL) & ~0x3FULL);
    }

    auto collectDist = [](const std::vector<uint64_t>& pas, uint64_t seed) {
        H64Config cfg;
        cfg.num_groups = 16;
        cfg.buckets_per_group = 64;
        cfg.hash_seed = seed;
        BackstoreSchemaH64 tbl(cfg);

        std::vector<size_t> dist(16, 0);
        for (auto pa : pas) {
            H64SlotEntry e;
            e.pa = pa; e.mesi = UBCCMESIState::G_S;
            e.sharers = 0; e.epoch = 0; e.state = H64SlotState::LIVE;
            tbl.upsert(pa, e);
            for (size_t g = 0; g < 16; ++g) {
                for (size_t b = 0; b < 64; ++b) {
                    const auto& bucket = tbl.bucket(g, b);
                    for (int s = 0; s < 5; ++s) {
                        H64SlotEntry slot;
                        H64Codec::unpack(bucket.slotAt(s), slot);
                        if (slot.state == H64SlotState::LIVE && slot.pa == pa) {
                            dist[g]++;
                            goto next_pa;
                        }
                    }
                }
                next_pa:;
            }
        }
        return dist;
    };

    auto distA = collectDist(pas, 0xAAAA);
    auto distB = collectDist(pas, 0xBBBB);

    bool differ = false;
    for (size_t g = 0; g < 16; ++g) {
        if (distA[g] != distB[g]) { differ = true; break; }
    }
    CHECK(differ, "distributions identical across different seeds");

    if (g_verbose) std::printf("(differ=%s) ", differ ? "yes" : "no");

    OK();
}

// ============================================================
// Test 18: Probe measurement
// ============================================================
static void test_probe_measurement()
{
    TEST("probe measurement");

    H64Config cfg;
    cfg.num_groups = 16;
    cfg.buckets_per_group = 32;
    BackstoreSchemaH64 tbl(cfg);

    const int N = 1500;
    std::vector<uint64_t> pas;
    uint64_t rng = 0xBEEF;
    int capacityExhausted = 0;

    for (int i = 0; i < N; ++i) {
        uint64_t pa = (lcg(rng) & 0xFFFFFFFFFFFULL) & ~0x3FULL;
        H64SlotEntry e;
        e.pa = pa; e.mesi = UBCCMESIState::G_S;
        e.sharers = static_cast<uint16_t>(i & 0xFFFF);
        e.epoch = static_cast<uint32_t>(i + 1);
        e.state = H64SlotState::LIVE;

        H64Status st = tbl.upsert(pa, e);
        if (st == H64Status::Found) {
            pas.push_back(pa);
        } else if (st == H64Status::CapacityExhausted) {
            ++capacityExhausted;
        }
    }

    size_t inserted = pas.size();

    size_t verified = 0;
    for (auto pa : pas) {
        H64SlotEntry out;
        if (tbl.lookup(pa, out) == H64Status::Found) ++verified;
    }
    CHECK(verified == inserted,
          "probe measurement: only %zu/%zu entries found", verified, inserted);

    size_t maxProbe = 0;
    double totalProbe = 0.0;
    for (auto pa : pas) {
        size_t d = tbl.probeDistance(pa);
        if (d > maxProbe) maxProbe = d;
        totalProbe += static_cast<double>(d);
    }
    double avgProbe = (inserted > 0) ? totalProbe / inserted : 0.0;

    if (g_verbose) {
        std::printf("(inserted=%zu/%d, cap_exhausted=%d, load=%.1f%%, "
                    "maxProbe=%zu, avgProbe=%.2f) ",
                    inserted, N, capacityExhausted,
                    100.0 * inserted / cfg.totalSlots(),
                    maxProbe, avgProbe);
    }

    CHECK(maxProbe <= static_cast<size_t>(cfg.buckets_per_group),
          "maxProbe=%zu exceeds buckets_per_group=%zu",
          maxProbe, cfg.buckets_per_group);

    OK();
}

// ============================================================
// Test 19: Randomized reference-model comparison
// ============================================================
struct RefModel {
    struct Entry {
        UBCCMESIState mesi;
        uint16_t sharers;
        uint32_t epoch;
    };
    std::unordered_map<uint64_t, Entry> map;

    void upsertStrict(uint64_t pa, UBCCMESIState mesi, uint16_t sh, uint32_t ep)
    {
        auto it = map.find(pa);
        if (it != map.end() && ep < it->second.epoch) {
            return;
        }
        map[pa] = {mesi, sh, ep};
    }
    void remove(uint64_t pa, uint32_t deleteEpoch)
    {
        auto it = map.find(pa);
        if (it == map.end()) return;
        if (deleteEpoch < it->second.epoch) return;
        map.erase(it);
    }
    bool contains(uint64_t pa) const { return map.count(pa) > 0; }
    uint32_t getEpoch(uint64_t pa) const {
        auto it = map.find(pa);
        return (it != map.end()) ? it->second.epoch : 0;
    }
};

static void test_randomized_1m(size_t numOps, uint64_t seed,
                                const H64Config& cfg,
                                size_t& outTotalOps,
                                size_t& outMaxProbe,
                                double& outAvgProbe,
                                size_t& outCorruptCount)
{
    BackstoreSchemaH64 tbl(cfg);
    RefModel ref;

    std::mt19937_64 rng(seed);
    uint64_t rngState = seed;

    const size_t poolSize = std::min<size_t>(numOps * 2, 100000);
    std::vector<uint64_t> paPool;
    for (size_t i = 0; i < poolSize; ++i) {
        paPool.push_back((lcg(rngState) & 0xFFFFFFFFFFFULL) & ~0x3FULL);
    }

    size_t totalProbe = 0;
    size_t probeSamples = 0;
    size_t maxProbe = 0;
    size_t totalOps = 0;
    size_t corruptCount = 0;

    for (size_t op = 0; op < numOps; ++op) {
        uint64_t pa = paPool[rng() % paPool.size()];
        int action = static_cast<int>(rng() % 100);

        if (action < 50) {
            // Upsert (50%)
            uint32_t epoch = static_cast<uint32_t>(rng() & 0xFFFFFF);
            uint16_t sharers = static_cast<uint16_t>(rng() & 0xFFFF);
            UBCCMESIState mesi = static_cast<UBCCMESIState>(rng() & 0x3);

            // Ensure valid sharers
            if (mesi == UBCCMESIState::G_E || mesi == UBCCMESIState::G_M) {
                if (sharers == 0) sharers = 1;
                sharers = 1u << (rng() & 0xF);
            } else if (mesi == UBCCMESIState::G_I) {
                sharers = 0;
            }

            H64SlotEntry e;
            e.pa = pa; e.mesi = mesi;
            e.sharers = sharers; e.epoch = epoch;
            e.state = H64SlotState::LIVE;

            H64Status st = tbl.upsert(pa, e);
            ref.upsertStrict(pa, mesi, sharers, epoch);

            if (st == H64Status::Corrupt) {
                ++corruptCount;
                ++totalOps;
                continue;
            }

            if (st == H64Status::CapacityExhausted) {
                ++totalOps;
                continue;
            }

            if (st != H64Status::Found && st != H64Status::StaleEpoch) {
                FAIL("op %zu: upsert unexpected status %s", op, h64StatusStr(st).c_str());
                return;
            }

            // Verify
            H64SlotEntry out;
            H64Status lookupSt = tbl.lookup(pa, out);
            if (ref.contains(pa)) {
                if (lookupSt != H64Status::Found) {
                    FAIL("op %zu: upsert PA=0x%" PRIx64 ": ref has it, table says %s",
                         op, pa, h64StatusStr(lookupSt).c_str());
                    return;
                }
                if (out.epoch != ref.getEpoch(pa)) {
                    FAIL("op %zu: PA=0x%" PRIx64 " epoch %u != ref %u",
                         op, pa, out.epoch, ref.getEpoch(pa));
                    return;
                }
            }

            // Probe measurement
            size_t d = tbl.probeDistance(pa);
            if (d > 0) {
                totalProbe += d;
                ++probeSamples;
                if (d > maxProbe) maxProbe = d;
            }
        } else if (action < 80) {
            // Lookup (30%)
            H64SlotEntry out;
            H64Status st = tbl.lookup(pa, out);

            if (st == H64Status::Corrupt) {
                ++corruptCount;
                ++totalOps;
                continue;
            }

            if (ref.contains(pa)) {
                if (st != H64Status::Found) {
                    FAIL("op %zu: lookup PA=0x%" PRIx64 ": ref has it, got %s",
                         op, pa, h64StatusStr(st).c_str());
                    return;
                }
                if (out.epoch != ref.getEpoch(pa)) {
                    FAIL("op %zu: lookup PA=0x%" PRIx64 " epoch %u != ref %u",
                         op, pa, out.epoch, ref.getEpoch(pa));
                    return;
                }
            }

            // Probe measurement
            size_t d = tbl.probeDistance(pa);
            if (d > 0) {
                totalProbe += d;
                ++probeSamples;
                if (d > maxProbe) maxProbe = d;
            }
        } else if (action < 95) {
            // Erase (15%)
            uint32_t deleteEpoch = static_cast<uint32_t>(rng() & 0xFFFFFF);
            H64Status st = tbl.erase(pa, deleteEpoch);
            ref.remove(pa, deleteEpoch);

            if (st == H64Status::Corrupt) {
                ++corruptCount;
                ++totalOps;
                continue;
            }

            if (st == H64Status::Found || st == H64Status::StaleEpoch ||
                st == H64Status::AlreadyAbsent) {
                // ok
            } else if (st == H64Status::CapacityExhausted) {
                ++totalOps;
                continue;
            }

            // Probe measurement
            size_t d = tbl.probeDistance(pa);
            if (d > 0) {
                totalProbe += d;
                ++probeSamples;
                if (d > maxProbe) maxProbe = d;
            }
        } else {
            // Rebuild random group (5%) — measure operation but skip probe
            size_t g = rng() % cfg.num_groups;
            H64Status st = tbl.rebuildGroup(g);
            if (st == H64Status::Corrupt) {
                ++corruptCount;
            } else if (st != H64Status::Found) {
                FAIL("op %zu: rebuildGroup(%zu) unexpected %s",
                     op, g, h64StatusStr(st).c_str());
                return;
            }

            // Verify ref entries are still there
            size_t liveInTable = 0;
            tbl.scanLiveEntries([&](const H64SlotEntry& entry) {
                ++liveInTable;
                if (!ref.contains(entry.pa)) {
                    FAIL("op %zu: after rebuild, PA=0x%" PRIx64 " in table but not ref",
                         op, entry.pa);
                    return;
                }
                if (entry.epoch != ref.getEpoch(entry.pa)) {
                    FAIL("op %zu: after rebuild, PA=0x%" PRIx64 " epoch=%u != ref=%u",
                         op, entry.pa, entry.epoch, ref.getEpoch(entry.pa));
                    return;
                }
            });
        }

        ++totalOps;
        if (totalOps >= numOps) break;
    }

    // Final full verification
    size_t finalLiveCount = 0;
    tbl.scanLiveEntries([&](const H64SlotEntry& entry) {
        ++finalLiveCount;
        if (!ref.contains(entry.pa)) {
            FAIL("final: PA=0x%" PRIx64 " in table but not ref", entry.pa);
            return;
        }
        if (entry.epoch != ref.getEpoch(entry.pa)) {
            FAIL("final: PA=0x%" PRIx64 " epoch=%u != ref=%u",
                 entry.pa, entry.epoch, ref.getEpoch(entry.pa));
            return;
        }
    });

    outTotalOps = totalOps;
    outMaxProbe = maxProbe;
    outAvgProbe = (probeSamples > 0) ? (double)totalProbe / probeSamples : 0.0;
    outCorruptCount = corruptCount;
}

static void test_randomized_driver()
{
    const size_t totalOpsPerSeed = 250000;
    uint64_t seeds[] = {42, 12345, 0xDEADBEEF, 0xCAFEBABE};

    size_t grandTotalOps = 0;
    size_t grandCorrupt = 0;
    int nRuns = 0;

    for (size_t si = 0; si < sizeof(seeds)/sizeof(seeds[0]); ++si) {
        char buf[32];
        std::snprintf(buf, sizeof(buf), "random ops seed=0x%" PRIX64, seeds[si]);
        TEST(std::string(buf));

        H64Config cfg;
        cfg.num_groups = 64;
        cfg.buckets_per_group = 256;
        cfg.hash_seed = seeds[si];

        size_t totalOps = 0, maxProbe = 0, corruptCount = 0;
        double avgProbe = 0.0;
        test_randomized_1m(totalOpsPerSeed, seeds[si], cfg,
                           totalOps, maxProbe, avgProbe, corruptCount);

        if (g_verbose) {
            std::printf("(ops=%zu, maxProbe=%zu, avgProbe=%.2f, corrupt=%zu) ",
                        totalOps, maxProbe, avgProbe, corruptCount);
        }
        // Always print per-seed summary
        std::printf("\n  seed=0x%" PRIX64 ": ops=%zu maxProbe=%zu avgProbe=%.2f corrupt=%zu",
                    seeds[si], totalOps, maxProbe, avgProbe, corruptCount);

        grandTotalOps += totalOps;
        grandCorrupt += corruptCount;
        ++nRuns;

        OK();
    }

    if (g_verbose) {
        std::printf("\n  Randomized summary: total ops=%zu, runs=%d, total corrupt=%zu\n",
                    grandTotalOps, nRuns, grandCorrupt);
    }
    std::printf("\n");
}

// ============================================================
// Main
// ============================================================

static void usage(const char* prog)
{
    std::printf("Usage: %s [--ops=N] [--verbose]\n", prog);
    std::printf("  --ops=N     Operations per seed in random test (default: 250000)\n");
    std::printf("  --verbose   Print per-test details\n");
}

int main(int argc, char** argv)
{
    size_t randomOpsPerSeed = 250000;

    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        if (arg.find("--ops=") == 0) {
            randomOpsPerSeed = static_cast<size_t>(std::stoull(arg.substr(6)));
        } else if (arg == "--verbose") {
            g_verbose = true;
        } else if (arg == "--help" || arg == "-h") {
            usage(argv[0]);
            return 0;
        } else {
            std::fprintf(stderr, "Unknown option: %s\n", arg.c_str());
            usage(argv[0]);
            return 1;
        }
    }

    std::printf("=== Schema H64 Phase 1 Reference Validation ===\n");
    std::printf("C++17, no external test framework\n");
    std::printf("BucketLine: %zu bytes (%zu header + %d slots × %zu bytes)\n",
                kBucketLineSize, kBucketHeaderSize,
                (int)kSlotsPerBucket, H64Codec::kSlotBytes);
    std::printf("Random ops per seed: %zu\n", randomOpsPerSeed);
    std::printf("\n");

    // ---- Run tests ----
    test_codec_roundtrip();
    test_bucket_layout();
    test_basic_crud();
    test_same_pa_update();
    test_stale_operations();
    test_tombstone_cluster();
    test_delete_reinsert();
    test_tombstone_priority();
    test_capacity_exhaustion();
    test_group_rebuild();
    test_corrupt_propagation();
    test_integrity_validation();
    test_input_validation();
    test_corrupt_and_rebuild_safety();
    test_rebuild_corrupt_detection();
    test_failure_status_propagation();
    test_epoch_edge_cases();
    test_hash_seed_sensitivity();
    test_probe_measurement();
    test_randomized_driver();

    // ---- Summary ----
    std::printf("\n=== Results ===\n");
    std::printf("Passed: %d\n", g_pass);
    std::printf("Failed: %d\n", g_fail);

    if (g_fail > 0) {
        std::printf("*** SOME TESTS FAILED ***\n");
        return 1;
    }

    std::printf("*** ALL TESTS PASSED ***\n");
    return 0;
}
