// Regression for TC132: an unrelated writeback completion must not let a
// capacity waiter replay itself forever while its target set remains full.

#include "modules/ubiomodule/UBCCController.hh"

#include <cassert>
#include <cstdio>
#include <cstring>

using namespace cc::glob;

class HoldBackstoreHost final : public UBCCHostIf
{
  public:
    uint64_t hostCurrentTick() const override { return 1; }
    void hostIssueBackstoreRead(uint64_t) override {}
    void hostIssueBackstoreWrite(uint64_t) override {}
    void hostIssueBackstoreDelete(uint64_t) override {}
    void readDsmData(uint64_t, std::function<void(const uint8_t*)> cb) override
    {
        if (cb) cb(nullptr);
    }
    void writeDsmData(uint64_t, const uint8_t*) override {}
};

int
main()
{
    // Two one-way sets make same-set capacity and unrelated-set completion
    // deterministic without adding any directory state.
    ResidentDirConfig cfg;
    cfg.sram_bytes = 4352;
    cfg.bloom_bytes = 128;
    cfg.ways = 1;
    cfg.set_bits = 1;

    UBCCController ubcc(0, 0, nullptr, 64, cfg.bloom_bytes, 0, 1, 3, &cfg);
    HoldBackstoreHost host;
    ubcc.setHost(&host);
    ubcc.setResidentOverflowPolicy(ResidentOverflowPolicy::Spill);

    constexpr uint64_t victim = 0x10000000; // home-0 DSM, set 0
    constexpr uint64_t target = 0x10000080; // same set as victim
    constexpr uint64_t unrelated = 0x10000040; // set 1

    assert(ubcc.debugSeedResidentForTest(
        victim, static_cast<int>(MESIState::G_M), 1, 1, true));
    assert(ubcc.debugSeedResidentForTest(
        unrelated, static_cast<int>(MESIState::G_E), 1, 1, false));

    // Start a held eviction, keeping victim pinned so target cannot enter.
    assert(ubcc.debugForceResidentEvictForTest(victim));
    const auto busy = ubcc.processOuterRequest(
        target, UBCC_OuterReqType::GlobalReadUnique, true, 0, 0, 1, 42);
    assert(static_cast<int>(busy) == -1);

    // This completion frees no target-set slot. Before P0, its global replay
    // pop/re-enqueued target forever at the same tick.
    ubcc.onBackstoreWriteAck(unrelated);

    const std::string state = ubcc.inspectOffloadLineForTest(target);
    assert(state.find("\"resident_present\":false") != std::string::npos);
    assert(state.find("\"resident_waiter_depth\":1") != std::string::npos);

    // The held victim is in target's set. Its durable completion removes the
    // victim and must wake target exactly through the matching set-local path.
    ubcc.onBackstoreWriteAck(victim);
    const std::string progressed = ubcc.inspectOffloadLineForTest(target);
    assert(progressed.find("\"resident_present\":true") != std::string::npos);
    assert(progressed.find("\"resident_waiter_depth\":0") != std::string::npos);

    std::fprintf(stderr, "capacity waiter liveness regression passed\n");
    return 0;
}
