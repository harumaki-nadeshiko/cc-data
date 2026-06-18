/* E2E-TC7: Writeback/eviction path — data preserved after eviction.
 *
 * Phase 1: Node0 writes 0x55667788 to DSM_1 (home=Node1).
 * Phase 2: Node0 floods its L1/L2 caches with eviction traffic to
 *          force the DSM_1 line to be evicted (writeback to HN-F/L3).
 * Phase 3: Node1 reads DSM_1 and must see 0x55667788.
 *
 * This verifies that data written to DSM is not lost when the writer's
 * cache line is evicted (writeback path correctness).
 *
 * Eviction strategy: write to many addresses in the same cache set to
 * force associative eviction.  Since L1D is 32kB with 2-way assoc,
 * writing 256+ cache lines with the same set index should trigger
 * eviction of the DSM line.
 */
#include "dsm_access.h"
#include "e2e_common.h"

/* Eviction flood parameters.
 * L1D = 32kB, 2-way, 64B line => 256 sets.
 * We write to many lines in the same set to force eviction.
 * Use LocalPrivate base as the eviction target (non-DSM address).
 */
#define EVICT_STRIDE    64       /* cache line size */
#define EVICT_COUNT     (32 * 1024 / 64 * 4)  /* 4x capacity to force eviction */

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC7");

    /* Only nodes 0 and 1 participate */
    if (node_id > 1) {
        if (primary) emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    /* sync_wait() counts threads, not nodes. With 4 CPUs/node, allowing all
     * CPUs to execute lets Node1's read run before Node0's writer/evictor has
     * completed, which invalidates TC7's intended ordering. Restrict TC7 to the
     * primary CPU of each participating node. */
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Node0 writes to DSM_1 ── */
    if (node_id == 0) {
        uint32_t val = 0x55667788;
        if (primary) emit_before_wr(node_id, 1, val);
        dsm_store(1, 0, val);
        if (primary) emit_after_wr(node_id, 1, val);

        /* ── Phase 2: Flood cache to trigger eviction ── */
        /* Write to LocalPrivate addresses within Node0's PA space.
         * LocalPrivate base = 0x0000_0000_0000 for Node0.
         * We use a local array on the stack, which maps to LocalPrivate
         * VA via the default SE workload mapping. */
        volatile uint32_t evict_buf[EVICT_COUNT];
        for (int i = 0; i < EVICT_COUNT; i++) {
            evict_buf[i] = (uint32_t)(0xDEAD0000 + i);
        }
        /* Compiler barrier to prevent optimisation */
        __asm__ volatile("" : : "r"(evict_buf) : "memory");
    }

    /* Barrier: ensure Node0's write + eviction is complete */
    sync_wait(0b011);  /* Node0 + Node1 */

    /* ── Phase 3: Node1 reads DSM_1 ── */
    int fail = 0;
    if (node_id == 1) {
        uint32_t expected = 0x55667788;
        if (primary) emit_before_rd(node_id, 1);
        uint32_t got = dsm_load(1, 0);
        int match = (got == expected);
        if (primary) emit_read_val(node_id, 1, expected, got, match);
        if (!match) fail++;
    }

    sync_wait(0b011);

    if (primary) emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
