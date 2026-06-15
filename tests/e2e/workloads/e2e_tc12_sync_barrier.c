/* E2E-TC12: sync_wait barrier correctness — 10 iterations × 3 segments.
 *
 * Each iteration:
 *   Segment 1: each node emits [SYNC] marker, then sync_wait(0b111)
 *   Segment 2: each node emits [SYNC] marker, then sync_wait(0b111)
 *   Segment 3: each node emits [SYNC] marker, then sync_wait(0b111)
 *
 * If the barrier is correct, all segment-1 outputs appear before any
 * segment-2 output, all segment-2 before segment-3, and all of
 * iteration N before iteration N+1.
 *
 * Each node uses any_hash(iter, seg) % 3 as the output value so that
 * the harness can also verify per-segment correctness.
 */

#include "dsm_access.h"
#include "e2e_common.h"

#define HASH_MAGIC 0x9E3779B9U

static inline uint32_t any_hash(uint32_t a, uint32_t b)
{
    uint32_t h = a * HASH_MAGIC + b;
    h ^= h >> 16;
    h *= 0x85EBCA77U;
    h ^= h >> 13;
    return h;
}

#define ITERATIONS 10
#define SEGMENTS   3

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    emit_e2e_meta(node_id, "TC12");

    /* All nodes participate — idle nodes that are unused just ride the barrier */
    for (int iter = 0; iter < ITERATIONS; iter++) {
        for (int seg = 1; seg <= SEGMENTS; seg++) {
            uint32_t val = any_hash((uint32_t)iter, (uint32_t)seg) % 3;
            emit_sync_marker(node_id, iter, seg, val);
            sync_wait(0b111);
        }
    }

    emit_phase_done(node_id, "done");
    _exit_program(0);
    return 0;
}
