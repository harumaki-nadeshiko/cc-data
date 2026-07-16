// ResidentDir layout + latency benchmark
// Usage:
//   build/bin/resident_dir_bench [--bench-mode=layout]  (default: layout+FPR)
//   build/bin/resident_dir_bench --bench-mode=latency --bloom-bytes=61440
//
// Latency mode measures:
//   1. Pure Dir lookup (bloom_bytes=0)       -> T_direct
//   2. Bloom+Dir lookup (bloom_bytes=61440)  -> T_bloom
//   3. DRAM offload (evict + re-fetch model) -> T_dram

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <vector>

#include "ResidentDir.hh"

using namespace cc::glob;

static constexpr size_t kDefaultNumOps = 200000;   // operations for latency benchmark
static constexpr size_t kCacheLineSize = 64;

static void print_usage(const char *prog)
{
    std::fprintf(stderr,
        "Usage: %s [options]\n"
        "Options:\n"
        "  --bench-mode=layout|latency   Benchmark mode (default: layout)\n"
        "  --bloom-bytes=N               Bloom filter bytes (default: 61440)\n"
        "  --sram-bytes=N                Total SRAM bytes (default: 524288)\n"
        "  --index-bytes=N               Group index bytes (default: 4096)\n"
        "  --sharers-bits=N              Sharers field width (default: 8)\n"
        "  --epoch-bits=N                Epoch field width (default: 24)\n"
        "  --pa-bits=N                   PA bits (default: 40)\n"
        "  --ways=N                      Associativity, 0=auto (default: 0)\n"
        "  --set-bits=N                  log2(sets), 0=auto (default: 0)\n"
        "  --num-ops=N                   Operations for latency mode (default: 200000)\n"
        "  --dram-delay-ps=N             Simulated DRAM delay in ps (default: 68000)\n",
        prog);
}

// Standard Bloom filter FPR: (1 - e^{-k*n/m})^k
static double estimateFPR_static(size_t m_bits, int k, size_t n)
{
    if (n == 0) return 0.0;
    double nm = (double)n / (double)m_bits;
    return std::pow(1.0 - std::exp(-(double)k * nm), (double)k);
}

// High-resolution timer: returns nanoseconds as double
static inline double now_ns() {
    auto t = std::chrono::high_resolution_clock::now().time_since_epoch();
    return (double)std::chrono::duration_cast<std::chrono::nanoseconds>(t).count();
}

// Generate random PA list (cacheline-aligned)
static std::vector<uint64_t> genRandomPAs(size_t count, unsigned int seed = 42) {
    std::mt19937_64 rng(seed);
    // Generate PAs in 0..2^36 range, cacheline aligned
    std::vector<uint64_t> pas(count);
    for (size_t i = 0; i < count; ++i) {
        pas[i] = (rng() & 0x0FFFFFFFFFULL) & ~(kCacheLineSize - 1);
    }
    return pas;
}

// ── Latency benchmark ────────────────────────────────────────────────

struct LatencyResult {
    double avgLookupNs = 0;
    double avgInsertNs = 0;
    double avgLookupInsertNs = 0;  // combined lookup+insert (typical path)
    size_t hits = 0;
    size_t misses = 0;
    size_t bloomFp = 0;
    size_t evictions = 0;
    size_t capacity = 0;
    size_t finalCount = 0;
    double totalTimeNs = 0;
};

static LatencyResult benchLookupInsert(ResidentDir &dir,
                                        const std::vector<uint64_t> &pas,
                                        bool verbose = false)
{
    LatencyResult r;
    r.capacity = dir.capacity();

    double totalLookupNs = 0, totalInsertNs = 0, totalLookupInsertNs = 0;
    size_t n = pas.size();

    UBCCDirEntry entry;
    entry.state = UBCCMESIState::G_S;      // shared (no one-hot popcount requirement)
    entry.sharersMask = 1;                  // at least one sharer required for G_S
    entry.epoch = 1;
    entry.residentDirty = false;

    for (size_t i = 0; i < n; ++i) {
        uint64_t pa = pas[i];

        // Measure lookup
        double t0 = now_ns();
        UBCCDirEntry out;
        bool found = dir.lookup(pa, out);
        double t1 = now_ns();

        if (found) r.hits++; else r.misses++;

        // Measure insert
        entry.lineAddr = pa;
        double t2 = now_ns();
        dir.insert(pa, entry);
        double t3 = now_ns();

        totalLookupNs += (t1 - t0);
        totalInsertNs += (t3 - t2);
        totalLookupInsertNs += (t3 - t0);
    }

    r.finalCount = dir.count();
    r.bloomFp = dir._bloomFpCount;
    r.evictions = dir._dirEvictions;

    if (n > 0) {
        r.avgLookupNs = totalLookupNs / (double)n;
        r.avgInsertNs = totalInsertNs / (double)n;
        r.avgLookupInsertNs = totalLookupInsertNs / (double)n;
    }
    r.totalTimeNs = totalLookupInsertNs;

    return r;
}

// DRAM offload simulation: fill dir to capacity, then measure evict+re-insert cycle
// Phase 1: Insert capacity * 110% entries to force evictions
// Phase 2: Measure per-eviction cost and per-refill cost
struct DramOffloadResult {
    double avgEvictNs = 0;       // pickVictim + remove + bloomInsert
    double avgRefillNs = 0;      // bloom lookaside + insert after miss
    size_t evictionCount = 0;
    double dramDelayNs = 0;      // simulated DRAM delay (from --dram-delay-ps)
};

static DramOffloadResult benchDramOffload(ResidentDir &dir,
                                           const std::vector<uint64_t> &pas,
                                           double dramDelayNs,
                                           bool verbose = false)
{
    DramOffloadResult r;
    r.dramDelayNs = dramDelayNs;
    size_t cap = dir.capacity();
    size_t n = pas.size();

    double totalEvictNs = 0;
    double totalRefillNs = 0;
    size_t evictCnt = 0;
    size_t refillCnt = 0;

    UBCCDirEntry entry;
    entry.state = UBCCMESIState::G_S;      // shared (no one-hot popcount requirement)
    entry.sharersMask = 1;                  // at least one sharer required for G_S
    entry.epoch = 1;
    entry.residentDirty = false;

    // Phase 1: Fill the directory, measuring evictions as they occur
    for (size_t i = 0; i < n; ++i) {
        uint64_t pa = pas[i];
        entry.lineAddr = pa;

        // If at capacity, we'll evict; measure eviction cost
        bool needEvict = !dir.hasFreeSlotForPa(pa);
        if (needEvict) {
            uint64_t victimPa = 0;
            UBCCDirEntry victim;
            double tEv0 = now_ns();
            bool hasVictim = dir.pickVictim(pa, victimPa, victim);
            double tEv1 = now_ns();
            if (hasVictim) {
                dir.remove(victimPa);
                dir.bloomInsert(victimPa);
                double tEv2 = now_ns();
                totalEvictNs += (tEv2 - tEv0);
                evictCnt++;
            }
        }

        double tRef0 = now_ns();
        dir.insert(pa, entry);
        double tRef1 = now_ns();
        totalRefillNs += (tRef1 - tRef0);
        refillCnt++;
    }

    if (evictCnt > 0) {
        r.avgEvictNs = totalEvictNs / (double)evictCnt;
        r.evictionCount = evictCnt;
    }
    if (refillCnt > 0) {
        r.avgRefillNs = totalRefillNs / (double)refillCnt;
    }

    return r;
}

// ── Main ─────────────────────────────────────────────────────────────

int main(int argc, char **argv)
{
    ResidentDirConfig cfg;
    // defaults
    cfg.sram_bytes   = 524288;   // 512 KB
    cfg.bloom_bytes  = 61440;    // 60 KB
    cfg.index_bytes  = 4096;     // 4 KB
    cfg.sharers_bits = 8;
    cfg.epoch_bits   = 24;
    cfg.pa_bits      = 40;
    cfg.ways         = 0;
    cfg.set_bits     = 0;

    const char *benchMode = "layout";
    size_t numOps = kDefaultNumOps;
    uint64_t dramDelayPs = 68000;  // 68 ns from solve_latency_params.py

    for (int i = 1; i < argc; i++) {
        const char *arg = argv[i];
        if (std::strncmp(arg, "--bench-mode=", 13) == 0)
            benchMode = arg + 13;
        else if (std::strncmp(arg, "--bloom-bytes=", 14) == 0)
            cfg.bloom_bytes = (size_t)std::strtoull(arg + 14, nullptr, 10);
        else if (std::strncmp(arg, "--sram-bytes=", 13) == 0)
            cfg.sram_bytes = (size_t)std::strtoull(arg + 13, nullptr, 10);
        else if (std::strncmp(arg, "--index-bytes=", 14) == 0)
            cfg.index_bytes = (size_t)std::strtoull(arg + 14, nullptr, 10);
        else if (std::strncmp(arg, "--sharers-bits=", 15) == 0)
            cfg.sharers_bits = (int)std::strtol(arg + 15, nullptr, 10);
        else if (std::strncmp(arg, "--epoch-bits=", 13) == 0)
            cfg.epoch_bits = (int)std::strtol(arg + 13, nullptr, 10);
        else if (std::strncmp(arg, "--pa-bits=", 10) == 0)
            cfg.pa_bits = (int)std::strtol(arg + 10, nullptr, 10);
        else if (std::strncmp(arg, "--ways=", 7) == 0)
            cfg.ways = (int)std::strtol(arg + 7, nullptr, 10);
        else if (std::strncmp(arg, "--set-bits=", 11) == 0)
            cfg.set_bits = (int)std::strtol(arg + 11, nullptr, 10);
        else if (std::strncmp(arg, "--num-ops=", 10) == 0)
            numOps = (size_t)std::strtoull(arg + 10, nullptr, 10);
        else if (std::strncmp(arg, "--dram-delay-ps=", 16) == 0)
            dramDelayPs = (uint64_t)std::strtoull(arg + 16, nullptr, 10);
        else if (std::strcmp(arg, "--help") == 0 || std::strcmp(arg, "-h") == 0) {
            print_usage(argv[0]);
            return 0;
        } else {
            std::fprintf(stderr, "Unknown option: %s\n", arg);
            print_usage(argv[0]);
            return 1;
        }
    }

    // ── Layout Mode (existing) ──────────────────────────────────────
    if (std::strcmp(benchMode, "layout") == 0 || std::strcmp(benchMode, "both") == 0) {
        std::fprintf(stdout, "=== ResidentDir Layout Benchmark ===\n");
        std::fprintf(stdout, "Config:\n");
        std::fprintf(stdout, "  sram_bytes   = %zu\n", cfg.sram_bytes);
        std::fprintf(stdout, "  bloom_bytes  = %zu\n", cfg.bloom_bytes);
        std::fprintf(stdout, "  index_bytes  = %zu\n", cfg.index_bytes);
        std::fprintf(stdout, "  sharers_bits = %d\n", cfg.sharers_bits);
        std::fprintf(stdout, "  epoch_bits   = %d\n", cfg.epoch_bits);
        std::fprintf(stdout, "  pa_bits      = %d\n", cfg.pa_bits);
        std::fprintf(stdout, "  ways         = %d\n", cfg.ways);
        std::fprintf(stdout, "  set_bits     = %d\n", cfg.set_bits);
        std::fprintf(stdout, "\n");

        ResidentDir dir(cfg);
        const ResidentDirLayout &layout = dir.layout();

        std::fprintf(stdout, "=== Init (searchOptimalLayout + allocate) ===\n");
        std::fprintf(stdout, "  ways          = %d\n", layout.ways);
        std::fprintf(stdout, "  set_bits      = %d\n", layout.set_bits);
        std::fprintf(stdout, "  num_sets      = %d\n", layout.num_sets);
        std::fprintf(stdout, "  tag_bits      = %d\n", layout.tag_bits);
        std::fprintf(stdout, "  entry_bits    = %d\n", layout.entry_bits);
        std::fprintf(stdout, "  set_total_bits= %d\n", layout.set_total_bits);
        std::fprintf(stdout, "  set_bytes     = %d\n", layout.set_bytes);
        std::fprintf(stdout, "  capacity      = %zu entries\n", layout.capacity);
        std::fprintf(stdout, "  dir_bytes     = %zu bytes (%.1f KB)\n",
                     layout.dir_bytes, (double)layout.dir_bytes / 1024.0);
        std::fprintf(stdout, "  bloom_bytes   = %zu bytes (%.1f KB)\n",
                     cfg.bloom_bytes, (double)cfg.bloom_bytes / 1024.0);
        std::fprintf(stdout, "\n");

        const int k = ResidentDir::BloomHashes;
        const int groups = ResidentDir::BloomGroups;
        size_t group_bloom_bits = (cfg.bloom_bytes / groups) * 8;
        size_t total_bloom_bits = cfg.bloom_bytes * 8;

        std::fprintf(stdout, "=== Bloom Filter FPR Estimates ===\n");
        std::fprintf(stdout, "  Hashes (k)        = %d\n", k);
        std::fprintf(stdout, "  Groups            = %d\n", groups);
        std::fprintf(stdout, "  Bloom bits total  = %zu\n", total_bloom_bits);
        std::fprintf(stdout, "  Bloom bits/group  = %zu\n", group_bloom_bits);
        std::fprintf(stdout, "\n");

        struct { const char *label; size_t n; } test_points[] = {
            {"FPR@1K",  1000},
            {"FPR@10K", 10000},
            {"FPR@50K", 50000},
        };
        for (auto &tp : test_points) {
            size_t n_per_group = (tp.n + groups - 1) / groups;
            double fpr_group = estimateFPR_static(group_bloom_bits, k, n_per_group);
            std::fprintf(stdout, "  %-12s = %.6f (%.4f%%) [n/group=%zu, m/group=%zu]\n",
                         tp.label, fpr_group, fpr_group * 100.0,
                         n_per_group, group_bloom_bits);
        }
        std::fprintf(stdout, "\n");

        if (cfg.bloom_bytes > 0) {
            std::fprintf(stdout, "SUMMARY: capacity=%zu dir_bytes=%zu bloom_bytes=%zu "
                             "entry_bits=%d ways=%d sets=%d\n",
                     layout.capacity, layout.dir_bytes, cfg.bloom_bytes,
                     layout.entry_bits, layout.ways, layout.num_sets);
        }
    }

    // ── Latency Mode ────────────────────────────────────────────────
    if (std::strcmp(benchMode, "latency") == 0 || std::strcmp(benchMode, "both") == 0) {
        std::fprintf(stdout, "\n=== Latency Benchmark ===\n");
        std::fprintf(stdout, "NumOps: %zu, Seed: 42\n", numOps);
        double dramDelayNs = (double)dramDelayPs / 1000.0;  // ps -> ns

        auto pas = genRandomPAs(numOps, 42);

        // ── Case 1: Pure Dir (bloom_bytes=0) ──
        {
            ResidentDirConfig cfgPure = cfg;
            cfgPure.bloom_bytes = 0;
            ResidentDir dirPure(cfgPure);
            size_t capPure = dirPure.capacity();
            std::fprintf(stdout, "\n--- Pure Dir (bloom_bytes=0, capacity=%zu) ---\n", capPure);

            double tTotal0 = now_ns();
            auto rPure = benchLookupInsert(dirPure, pas);
            double tTotal1 = now_ns();
            double wallNs = tTotal1 - tTotal0;

            std::fprintf(stdout, "  capacity        = %zu\n", rPure.capacity);
            std::fprintf(stdout, "  operations      = %zu\n", numOps);
            std::fprintf(stdout, "  hits            = %zu\n", rPure.hits);
            std::fprintf(stdout, "  misses          = %zu\n", rPure.misses);
            std::fprintf(stdout, "  evictions       = %zu\n", rPure.evictions);
            std::fprintf(stdout, "  final_count     = %zu\n", rPure.finalCount);
            std::fprintf(stdout, "  avg_lookup_ns   = %.3f ns\n", rPure.avgLookupNs);
            std::fprintf(stdout, "  avg_insert_ns   = %.3f ns\n", rPure.avgInsertNs);
            std::fprintf(stdout, "  avg_lookup+insert_ns = %.3f ns\n", rPure.avgLookupInsertNs);
            std::fprintf(stdout, "  wall_time_ns    = %.1f ns\n", wallNs);
            std::fprintf(stdout, "METRIC1_PURE: lookup_ns=%.3f insert_ns=%.3f combined_ns=%.3f hits=%zu misses=%zu evicts=%zu\n",
                         rPure.avgLookupNs, rPure.avgInsertNs, rPure.avgLookupInsertNs,
                         rPure.hits, rPure.misses, rPure.evictions);
        }

        // ── Case 2: Bloom+Dir (bloom_bytes=61440) ──
        {
            ResidentDir dirBloom(cfg);
            size_t capBloom = dirBloom.capacity();
            std::fprintf(stdout, "\n--- Bloom+Dir (bloom_bytes=%zu, capacity=%zu) ---\n",
                         cfg.bloom_bytes, capBloom);

            double tTotal0 = now_ns();
            auto rBloom = benchLookupInsert(dirBloom, pas);
            double tTotal1 = now_ns();
            double wallNs = tTotal1 - tTotal0;

            std::fprintf(stdout, "  capacity        = %zu\n", rBloom.capacity);
            std::fprintf(stdout, "  operations      = %zu\n", numOps);
            std::fprintf(stdout, "  hits            = %zu\n", rBloom.hits);
            std::fprintf(stdout, "  misses          = %zu\n", rBloom.misses);
            std::fprintf(stdout, "  bloom_fp        = %zu\n", rBloom.bloomFp);
            std::fprintf(stdout, "  evictions       = %zu\n", rBloom.evictions);
            std::fprintf(stdout, "  final_count     = %zu\n", rBloom.finalCount);
            std::fprintf(stdout, "  avg_lookup_ns   = %.3f ns\n", rBloom.avgLookupNs);
            std::fprintf(stdout, "  avg_insert_ns   = %.3f ns\n", rBloom.avgInsertNs);
            std::fprintf(stdout, "  avg_lookup+insert_ns = %.3f ns\n", rBloom.avgLookupInsertNs);
            std::fprintf(stdout, "  wall_time_ns    = %.1f ns\n", wallNs);
            std::fprintf(stdout, "METRIC1_BLOOM: lookup_ns=%.3f insert_ns=%.3f combined_ns=%.3f hits=%zu misses=%zu bloomFp=%zu evicts=%zu\n",
                         rBloom.avgLookupNs, rBloom.avgInsertNs, rBloom.avgLookupInsertNs,
                         rBloom.hits, rBloom.misses, rBloom.bloomFp, rBloom.evictions);
        }

        // ── Case 3: DRAM Offload Model ──
        {
            ResidentDir dirDram(cfg);
            size_t capDram = dirDram.capacity();
            // Generate enough PAs to force evictions (2x capacity)
            size_t dramOps = capDram * 2;
            if (dramOps > numOps) dramOps = numOps;
            auto dramPas = genRandomPAs(dramOps, 12345);

            std::fprintf(stdout, "\n--- DRAM Offload Model (capacity=%zu, dramOps=%zu, dramDelay=%.1f ns) ---\n",
                         capDram, dramOps, dramDelayNs);

            double tTotal0 = now_ns();
            auto rDram = benchDramOffload(dirDram, dramPas, dramDelayNs);
            double tTotal1 = now_ns();
            double wallNs = tTotal1 - tTotal0;

            std::fprintf(stdout, "  capacity        = %zu\n", capDram);
            std::fprintf(stdout, "  operations      = %zu\n", dramOps);
            std::fprintf(stdout, "  final_count     = %zu\n", dirDram.count());
            std::fprintf(stdout, "  evictions       = %zu\n", rDram.evictionCount);
            std::fprintf(stdout, "  avg_evict_ns    = %.3f ns\n", rDram.avgEvictNs);
            std::fprintf(stdout, "  avg_refill_ns   = %.3f ns\n", rDram.avgRefillNs);
            std::fprintf(stdout, "  avg_dram_total  = %.3f ns (evict+refill)\n",
                         rDram.avgEvictNs + rDram.avgRefillNs);
            std::fprintf(stdout, "  dram_delay_ns   = %.1f ns\n", rDram.dramDelayNs);
            std::fprintf(stdout, "  wall_time_ns    = %.1f ns\n", wallNs);
            std::fprintf(stdout, "METRIC1_DRAM: evict_ns=%.3f refill_ns=%.3f total_ns=%.3f evictions=%zu\n",
                         rDram.avgEvictNs, rDram.avgRefillNs,
                         rDram.avgEvictNs + rDram.avgRefillNs,
                         rDram.evictionCount);
        }

        // ── Weighted Latency Model ──
        std::fprintf(stdout, "\n=== Weighted Latency Model (指标1) ===\n");
        std::fprintf(stdout, "  T_dram_delay     = %.1f ns (UBIO_DRAM_DELAY_PS=%.0f)\n",
                     dramDelayNs, (double)dramDelayPs);
        std::fprintf(stdout, "  Model: T_weighted = (1 - evict_rate) * T_bloom + evict_rate * T_dram\n");
        std::fprintf(stdout, "  Constraint: T_weighted / T_direct - 1 <= 50%%\n");
    }

    return 0;
}
