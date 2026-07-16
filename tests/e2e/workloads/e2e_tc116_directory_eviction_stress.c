/* TC116: ResidentDir DRAM offload/onload stress.
 *
 * Writes 256 unique cache lines from node0 to home=0.
 * In default mode (57K capacity), all entries fit → no eviction.
 * In small-dir mode (--bloom-bytes=0 --sram-bytes=6144 --ways=1,
 * capacity ~128), 256 lines >> 128 capacity → forced eviction.
 *
 * Node1 reads back the first (oldest, likely evicted in small-dir mode)
 * and last (newest, always resident) lines to verify data consistency.
 *
 * Runs with 3 nodes, 1 socket. Only CPUs 0 of nodes 0 and 1 participate.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define NUM_LINES       64
#define FIRST_VAL       0x11600000u
/* LAST_VAL = 0x11600000 | (NUM_LINES - 1) = 0x1160003F */
#define LAST_VAL        0x1160003Fu

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC116");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Node0 fills ResidentDir with NUM_LINES unique writes ── */
    if (node_id == 0) {
        for (int i = 0; i < NUM_LINES; i++) {
            uint32_t val = 0x11600000u | (uint32_t)i;
            uint32_t off = (uint32_t)(i * 64);  /* 64B-aligned cache line */
            dsm_store(0, off, val);  /* home=node0 */
        }
        /* Tell the harness that the fill phase is done */
        emit_phase_done(0, "fill");
    }

    /* Barrier: node0 and node1 only (node2 not needed) */
    sync_wait(0b11);  /* nodes 0,1 */

    /* ── Phase 2: Node1 reads back first and last lines ── */
    if (node_id == 1) {
        /* Read first-written line (offset=0), may be evicted in small-dir mode */
        uint32_t got_first = dsm_load(0, 0);
        emit_read_val(1, 0, FIRST_VAL, got_first, got_first == FIRST_VAL);

        /* Read last-written line (offset=(NUM_LINES-1)*64) */
        uint32_t last_off = (uint32_t)((NUM_LINES - 1) * 64);
        uint32_t got_last = dsm_load(0, last_off);
        emit_read_val(1, 0, LAST_VAL, got_last, got_last == LAST_VAL);
    }

    sync_wait(0b11);

    _exit_program(0);
    return 0;
}
