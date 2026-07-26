#include "modules/ubiomodule/BackstoreSchemaH64.hh"
#include "modules/ubiomodule/ResidentDir.hh"

#include <cassert>
#include <cstdio>
#include <vector>

using namespace cc::glob;

int
main()
{
    ResidentDirConfig cfg;
    cfg.sram_bytes = 128 * 1024;
    cfg.bloom_bytes = 16 * 1024;
    ResidentDir dir(cfg);

    constexpr uint64_t seed = 0x9e3779b97f4a7c15ULL;
    constexpr uint64_t pa = 0x10000040ULL;
    const size_t group = BackstoreSchemaH64::groupForPaStatic(pa, 256, seed);
    const int slice = static_cast<int>(h64BloomSliceForGroup(group));
    assert(dir.groupForPa(pa) == slice);
    assert(!dir.bloomNegativeAuthoritative(pa));

    const size_t sliceBytes = cfg.bloom_bytes / ResidentDir::BloomGroups;
    std::vector<uint8_t> scratch(sliceBytes, 0);
    dir.setBloomSliceRebuilding(slice, 16);
    assert(dir.bloomSliceControl(slice).state ==
           ResidentDir::BloomSliceState::Rebuilding);
    assert(!dir.bloomNegativeAuthoritative(pa));

    dir.publishBloomSlice(slice, scratch.data(), scratch.size());
    assert(dir.bloomSliceControl(slice).state ==
           ResidentDir::BloomSliceState::Valid);
    assert(dir.bloomNegativeAuthoritative(pa));

    dir.bloomInsert(pa);
    assert(!dir.bloomNegativeAuthoritative(pa));
    dir.invalidateBloomSlice(slice);
    assert(!dir.bloomNegativeAuthoritative(pa));

    std::fprintf(stderr, "h64 bloom slice mapping regression passed\n");
    return 0;
}
