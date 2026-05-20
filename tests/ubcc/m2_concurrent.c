/*
 * UBCC M2 Isolation Test v2: Concurrent Multi-Node Workload
 *
 * ALL nodes run effective payload concurrently. Each node's cores
 * access their own working set. Domain isolation is enforced by
 * strict downstream filtering (every RN-F -> only same-node HN-F).
 *
 * Usage: m2_concurrent <node_id> <core_id>
 *
 * Node 0 cores: process 0-1, access low addresses
 * Node 1 cores: process 2-3, access low addresses
 * (SE mode allocates separate PAs per process, CHI isolation
 *  handled by downstream filtering)
 */

#include <stdlib.h>
#include <string.h>

#define WORKING_SET_WORDS 4096
#define ITERATIONS 50

int main(int argc, char *argv[]) {
    int node_id = 0;
    int core_id = 0;
    if (argc > 1) node_id = atoi(argv[1]);
    if (argc > 2) core_id = atoi(argv[2]);

    /* Each core allocates and works on its own array.
     * All accesses go through its node's HN-F. */
    volatile int data[WORKING_SET_WORDS];

    /* Phase 1: Initialize with per-node/per-core patterns */
    int init_val = (node_id << 20) | (core_id << 16);
    for (int i = 0; i < WORKING_SET_WORDS; i++) {
        data[i] = init_val ^ (i * 0x5555);
    }

    /* Phase 2: Compute-intensive access to exercise caches */
    int sum = 0;
    for (int iter = 0; iter < ITERATIONS; iter++) {
        int stride = (iter + 1) * 13;
        for (int i = 0; i < WORKING_SET_WORDS; i += stride) {
            if (i >= WORKING_SET_WORDS) break;
            sum += data[i];
            data[i] = sum ^ (iter << 8);
        }
    }

    /* Phase 3: Streaming access to force L2/L3 refills */
    for (int i = 0; i < WORKING_SET_WORDS; i++) {
        volatile int tmp = data[i];
        data[i] = tmp + core_id + node_id;
    }

    return 0; /* deterministic exit code for verification */
}
