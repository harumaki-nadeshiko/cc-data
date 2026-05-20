/*
 * UBCC M1 Smoke Test: Cache Hierarchy Exercise
 *
 * Each core accesses a moderately-sized array to exercise
 * L1 -> shared L2 -> HN-F/L3 -> SN-F/DRAM path.
 * Different array sizes per core create varied access patterns.
 *
 * Usage: smoke_test <core_id>
 */

#include <stdlib.h>

#define ARRAY_SIZE 4096

volatile int data[ARRAY_SIZE];

int main(int argc, char *argv[]) {
    int core_id = 0;
    if (argc > 1) {
        core_id = atoi(argv[1]);
    }

    /* Phase 1: Streaming write - causes capacity/conflict misses
     * at L1 level, forcing L2/L3/DRAM access */
    for (int i = 0; i < ARRAY_SIZE; i++) {
        data[i] = (core_id + 1) * 100 + i;
    }

    /* Phase 2: Random-like read back - exercises tag lookups
     * at all cache levels */
    int sum = 0;
    int step = 127 + core_id * 31;  /* Stride to avoid prefetch */
    for (int i = 0; i < ARRAY_SIZE; i++) {
        int idx = (i * step) % ARRAY_SIZE;
        sum += data[idx];
    }

    /* Phase 3: Write-back - modify some entries */
    for (int i = 0; i < ARRAY_SIZE; i += 256) {
        data[i] = sum + core_id + i;
    }

    return sum & 0xFF;
}
