#include "modules/ubiomodule/ResidentDir.hh"

#include <cstdio>

using namespace cc::glob;

int main()
{
    ResidentDirConfig cfg;
    cfg.sram_bytes = 8192;
    cfg.bloom_bytes = 0;
    cfg.blc_bytes = 0;
    cfg.desc_scratch_bytes = 0;
    cfg.ways = 1;
    cfg.set_bits = 1;
    cfg.pa_bits = 44;
    cfg.sharers_bits = 16;

    ResidentDir dir(cfg);
    const uint64_t pa = (15ULL << 40) + 0x16000;
    UBCCDirEntry entry;
    entry.lineAddr = pa;
    entry.state = UBCCMESIState::G_S;
    entry.sharersMask = 0xFFFF;
    entry.epoch = 1;

    if (!dir.insert(pa, entry)) {
        std::fprintf(stderr, "insert failed\n");
        return 1;
    }

    UBCCDirEntry decoded;
    if (!dir.lookup(pa, decoded) || decoded.lineAddr != pa ||
        decoded.sharersMask != 0xFFFF) {
        std::fprintf(stderr,
                     "round trip failed pa=0x%llx sharers=0x%llx\n",
                     static_cast<unsigned long long>(decoded.lineAddr),
                     static_cast<unsigned long long>(decoded.sharersMask));
        return 1;
    }

    uint64_t victimPa = 0;
    UBCCDirEntry victim;
    if (!dir.pickVictim(pa + 128, victimPa, victim) || victimPa != pa ||
        victim.lineAddr != pa || victim.sharersMask != 0xFFFF) {
        std::fprintf(stderr,
                     "victim decode failed pa=0x%llx line=0x%llx sharers=0x%llx\n",
                     static_cast<unsigned long long>(victimPa),
                     static_cast<unsigned long long>(victim.lineAddr),
                     static_cast<unsigned long long>(victim.sharersMask));
        return 1;
    }

    std::printf("resident_dir_n16 PASS pa=0x%llx sharers=0x%llx\n",
                static_cast<unsigned long long>(decoded.lineAddr),
                static_cast<unsigned long long>(decoded.sharersMask));
    return 0;
}
