#include "modules/ubiomodule/UBCCController.hh"

#include <cassert>
#include <cstdio>
#include <vector>

using namespace cc::glob;

class ScanHost final : public UBCCHostIf
{
  public:
    uint64_t tick = 0;
    bool fail = false;
    std::vector<uint64_t> live;

    uint64_t hostCurrentTick() const override { return tick; }
    void hostIssueBackstoreRead(uint64_t) override {}
    void hostIssueBackstoreWrite(uint64_t) override {}
    void hostIssueBackstoreDelete(uint64_t) override {}
    void readDsmData(uint64_t, std::function<void(const uint8_t*)> cb) override
    {
        if (cb) cb(nullptr);
    }
    void writeDsmData(uint64_t, const uint8_t*) override {}
    void hostScanH64BloomSlice(int slice, std::function<void(uint64_t)> onLive,
                               std::function<void(bool)> completion) override
    {
        if (fail) {
            completion(false);
            return;
        }
        for (uint64_t pa : live) {
            if (h64BloomSliceForPa(pa, 256, 0x9e3779b97f4a7c15ULL) ==
                static_cast<size_t>(slice)) {
                onLive(pa);
            }
        }
        completion(true);
    }
};

static uint64_t
paForSlice(int slice, uint64_t start)
{
    for (uint64_t pa = start; ; pa += 64) {
        if (h64BloomSliceForPa(pa, 256, 0x9e3779b97f4a7c15ULL) ==
            static_cast<size_t>(slice)) {
            return pa;
        }
    }
}

int
main()
{
    ResidentDirConfig cfg;
    cfg.sram_bytes = 4352;
    cfg.bloom_bytes = 128;
    cfg.ways = 1;
    cfg.set_bits = 1;
    UBCCController ubcc(0, 0, nullptr, 64, cfg.bloom_bytes, 0, 1, 3, &cfg);
    ScanHost host;
    ubcc.setHost(&host);
    ubcc.setH64BloomAllMisses(true);

    const int slice = 0;
    const uint64_t residentOnly = paForSlice(slice, 0x10000000ULL);
    const uint64_t h64Only = paForSlice(slice, residentOnly + 64);
    const uint64_t duplicate = paForSlice(slice, h64Only + 64);
    UBCCDirEntry entry;
    entry.lineAddr = residentOnly;
    entry.state = UBCCMESIState::G_S;
    entry.sharersMask = 1;
    assert(ubcc.directory().insert(residentOnly, entry));
    entry.lineAddr = duplicate;
    assert(ubcc.directory().insert(duplicate, entry));
    host.live = {h64Only, duplicate};

    // Four resident slots are scanned in one wakeup for this tiny config.
    ubcc.wakeup();
    assert(ubcc.directory().bloomSliceControl(slice).state ==
           ResidentDir::BloomSliceState::Valid);
    assert(ubcc.directory().bloomMayContain(residentOnly));
    assert(ubcc.directory().bloomMayContain(h64Only));
    assert(ubcc.directory().bloomMayContain(duplicate));

    const int failSlice = 1;
    host.fail = true;
    for (int i = 0; i < 2; ++i)
        ubcc.wakeup();
    assert(ubcc.directory().bloomSliceControl(failSlice).state ==
           ResidentDir::BloomSliceState::Invalid);
    std::fprintf(stderr, "joint H64 Bloom rebuild regression passed\n");
    return 0;
}
