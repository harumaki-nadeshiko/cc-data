// Diagnostic counterexamples, not an end-to-end TC143 regression test.
#include "modules/ubiomodule/UBCCController.hh"
#include <cassert>
#include <cstdio>

using namespace cc::glob;

class BusyLookupHost final : public UBCCHostIf {
  public:
    UBCCController *controller = nullptr;
    unsigned reads = 0;
    uint64_t hostCurrentTick() const override { return 1; }
    void hostIssueBackstoreRead(uint64_t pa) override {
        ++reads;
        BackstoreCompletion completion;
        completion.linePa = pa;
        completion.op = BackstoreOp::Lookup;
        completion.status = BackstoreStatus::RetryableBusy;
        controller->onBackstoreH64Complete(completion);
    }
    void hostIssueBackstoreWrite(uint64_t) override {}
    void hostIssueBackstoreDelete(uint64_t) override {}
    void readDsmData(uint64_t, std::function<void(const uint8_t*)> cb) override {
        cb(nullptr);
    }
    void writeDsmData(uint64_t, const uint8_t*) override {}
};

int main() {
    ResidentDirConfig cfg;
    cfg.ways = 2;
    cfg.set_bits = 1;
    UBCCController controller(0, 0, nullptr, 64, cfg.bloom_bytes, 0, 1, 3, &cfg);
    BusyLookupHost host;
    host.controller = &controller;
    controller.setHost(&host);
    controller.setResidentOverflowPolicy(ResidentOverflowPolicy::Spill);
    controller.setH64BloomAllMisses(true);
    constexpr uint64_t pa = 0x10000000;
    assert(controller.debugSeedResidentForTest(
        pa, static_cast<int>(MESIState::G_M), 2, 17, false));
    assert(controller.processStoreCommit(pa, 1, 17));
    controller.publishBloomLive(pa);
    controller.directory().forceRemove(pa); // completed metadata spill
    assert(!controller.processStoreCommit(pa, 1, 17));
    assert(!controller.storeCommitTransient(pa));
    std::puts("NONRESIDENT: exact=false transient=false (no epoch mutation)");

    assert(!controller.prepareWritebackPersistence(pa, 1, 17));
    assert(host.reads == 1);
    assert(controller.directory().fillPending(pa));
    assert(controller.debugClearResidentWaitersForTest(pa));
    controller.wakeup();
    DirEntry entry;
    const bool present = controller.directory().lookup(pa, entry);
    const bool ready = controller.prepareWritebackPersistence(pa, 1, 17);
    std::printf("ORPHAN: present=%d ready=%d reads=%u state=%d epoch=%llu\n",
                present, ready, host.reads, present ? int(entry.state) : -1,
                static_cast<unsigned long long>(present ? entry.epoch : 0));
    // The WIP exposes an unverified placeholder; restoring orphan removal
    // instead starts a fresh lookup and cannot return Ready here.
    assert(!ready);
    return 0;
}
