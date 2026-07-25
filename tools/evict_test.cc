#include "ResidentDir.hh"
#include <cstdio>
#include <chrono>
using namespace cc::glob;
using namespace std::chrono;
int main(int argc, char**argv) {
    ResidentDirConfig cfg;
    cfg.sram_bytes = 1024; cfg.bloom_bytes = 0;
    cfg.group_index_bytes = 64; cfg.index_bytes = 64;
    cfg.blc_bytes = 0; cfg.desc_scratch_bytes = 0;
    cfg.ways = 1; cfg.sharers_bits = 8; cfg.epoch_bits = 24; cfg.pa_bits = 40;
    ResidentDir dir(cfg);
    printf("capacity=%zu\n", dir.capacity());
    UBCCDirEntry e; e.state = UBCCMESIState::G_E; e.sharersMask = 1; e.epoch = 1;
    e.residentDirty = true;
    int inserts=0, evictions=0, N=200;
    auto t0=high_resolution_clock::now();
    for(uint64_t pa=0x1000; pa<0x1000+N*64; pa+=64) {
        e.lineAddr=pa;
        if(!dir.insert(pa,e)) {
            uint64_t vpa; UBCCDirEntry ve;
            if(dir.pickVictim(pa,vpa,ve)) { dir.forceRemove(vpa); evictions++; dir.insert(pa,e); }
        }
        inserts++;
    }
    auto t1=high_resolution_clock::now();
    printf("inserts=%d evictions=%d (%.1f%%) avg_ns=%ld\n",
           inserts, evictions, evictions*100.0/inserts,
           duration_cast<nanoseconds>(t1-t0).count()/inserts);
    UBCCDirEntry out; int hits=0;
    t0=high_resolution_clock::now();
    for(uint64_t pa=0x1000; pa<0x1000+inserts*64; pa+=64) { if(dir.lookup(pa,out)) hits++; }
    t1=high_resolution_clock::now();
    printf("lookup: hits=%d/%d avg_ns=%ld\n", hits, inserts,
           duration_cast<nanoseconds>(t1-t0).count()/inserts);
    // Weighted model
    double ev_rate = evictions * 1.0 / inserts;
    double T_direct = 238.0; // ns from capacity-fit run (0 evictions)
    double T_dram   = 68.0;  // ns: UBIO_DRAM_DELAY_PS
    double T_weighted = (1-ev_rate) * T_direct + ev_rate * (T_direct + T_dram);
    printf("\n加权模型: evict_rate=%.1f%% T_direct=%0.fns T_dram=%.0fns\n", ev_rate*100, T_direct, T_dram);
    printf("T_weighted=%.0fns vs T_direct=%.0fns (+%.0f%%) [限制≤50%%]\n",
           T_weighted, T_direct, (T_weighted/T_direct-1)*100);
    return 0;
}
