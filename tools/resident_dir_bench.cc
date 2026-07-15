// ResidentDir layout benchmark
// Usage:
//   build/bin/resident_dir_bench --bloom-bytes=61440 --sram-bytes=524288
//                                --sharers-bits=8 --epoch-bits=24
//   build/bin/resident_dir_bench --bloom-bytes=30720 --sram-bytes=524288
//                                --sharers-bits=8 --epoch-bits=24
//   build/bin/resident_dir_bench --bloom-bytes=61440 --sram-bytes=1048576
//                                --sharers-bits=8 --epoch-bits=24
//   build/bin/resident_dir_bench --bloom-bytes=61440 --sram-bytes=524288
//                                --sharers-bits=8 --epoch-bits=24 --ways=4

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>

#include "ResidentDir.hh"

using namespace cc::glob;

static void print_usage(const char *prog)
{
    std::fprintf(stderr,
        "Usage: %s [options]\n"
        "Options:\n"
        "  --bloom-bytes=N     Bloom filter bytes (default: 61440)\n"
        "  --sram-bytes=N      Total SRAM bytes (default: 524288)\n"
        "  --index-bytes=N     Group index bytes (default: 4096)\n"
        "  --sharers-bits=N    Sharers field width (default: 8)\n"
        "  --epoch-bits=N      Epoch field width (default: 24)\n"
        "  --pa-bits=N         PA bits (default: 40)\n"
        "  --ways=N            Associativity, 0=auto (default: 0)\n"
        "  --set-bits=N        log2(sets), 0=auto (default: 0)\n",
        prog);
}

// Standard Bloom filter FPR: (1 - e^{-k*n/m})^k
static double estimateFPR_static(size_t m_bits, int k, size_t n)
{
    if (n == 0) return 0.0;
    double nm = (double)n / (double)m_bits;
    return std::pow(1.0 - std::exp(-(double)k * nm), (double)k);
}

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

    for (int i = 1; i < argc; i++) {
        const char *arg = argv[i];
        if (std::strncmp(arg, "--bloom-bytes=", 14) == 0)
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
        else if (std::strcmp(arg, "--help") == 0 || std::strcmp(arg, "-h") == 0) {
            print_usage(argv[0]);
            return 0;
        } else {
            std::fprintf(stderr, "Unknown option: %s\n", arg);
            print_usage(argv[0]);
            return 1;
        }
    }

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

    // Phase 1 + 2: init (constructs, internally calls searchOptimalLayout)
    std::fprintf(stdout, "=== Init (searchOptimalLayout + allocate) ===\n");
    ResidentDir dir(cfg);
    const ResidentDirLayout &layout = dir.layout();

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
    std::fprintf(stdout, "  plru_padded_ways = %d\n", layout.plru_padded_ways);
    std::fprintf(stdout, "  plru_bits     = %d\n", layout.plru_bits);
    std::fprintf(stdout, "  off_valid     = %d\n", layout.off_valid);
    std::fprintf(stdout, "  off_mesi      = %d\n", layout.off_mesi);
    std::fprintf(stdout, "  off_dirty     = %d\n", layout.off_dirty);
    std::fprintf(stdout, "  off_ctrl      = %d\n", layout.off_ctrl);
    std::fprintf(stdout, "  off_sharers   = %d\n", layout.off_sharers);
    std::fprintf(stdout, "  off_epoch     = %d\n", layout.off_epoch);
    std::fprintf(stdout, "  off_tag       = %d\n", layout.off_tag);
    std::fprintf(stdout, "\n");

    // Phase 3: static FPR estimates for various fill levels
    // Bloom filter params: k = ResidentDir::BloomHashes = 4
    // m = bloom_bytes * 8 (total bloom bits)
    // Since bloom is partitioned into 16 groups, each group gets 1/16 of the bits.
    // For a uniform distribution, we use per-group FPR.
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

    // FPR@N assumes uniform distribution across groups: n_per_group = ceil(N / groups)
    struct {
        const char *label;
        size_t n;
    } test_points[] = {
        {"FPR@1K",  1000},
        {"FPR@10K", 10000},
        {"FPR@50K", 50000},
    };

    for (auto &tp : test_points) {
        // per-group analysis (more realistic since bloom is partitioned)
        size_t n_per_group = (tp.n + groups - 1) / groups;
        double fpr_group = estimateFPR_static(group_bloom_bits, k, n_per_group);
        // overall FPR: probability that ALL groups have a false positive
        // (simplification: treat groups independently)
        double fpr = fpr_group;
        std::fprintf(stdout, "  %-12s = %.6f (%.4f%%) [n/group=%zu, m/group=%zu]\n",
                     tp.label, fpr, fpr * 100.0, n_per_group, group_bloom_bits);
    }
    std::fprintf(stdout, "\n");

    // Summary line for easy parsing
    std::fprintf(stdout, "SUMMARY: capacity=%zu dir_bytes=%zu bloom_bytes=%zu "
                         "entry_bits=%d ways=%d sets=%d\n",
                 layout.capacity, layout.dir_bytes, cfg.bloom_bytes,
                 layout.entry_bits, layout.ways, layout.num_sets);

    return 0;
}
