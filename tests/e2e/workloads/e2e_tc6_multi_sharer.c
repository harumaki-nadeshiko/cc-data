/* E2E-TC6: Multi-sharer read consistency.
 *
 * Phase 1: Node0 writes 0xDEADBEEF to DSM_2 (home=Node2).
 * Phase 2: Node1 and Node2 simultaneously read DSM_2.
 *
 * Both Node1 and Node2 must see 0xDEADBEEF.
 * This verifies shared-state propagation and sharer invalidation
 * on subsequent writes.
 */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    emit_e2e_meta(node_id, "TC6");

    /* ── Phase 1: Node0 writes ── */
    if (node_id == 0) {
        uint32_t val = 0xDEADBEEF;
        emit_before_wr(node_id, 2, val);
        dsm_store(2, 0, val);
        emit_after_wr(node_id, 2, val);
    }
    sync_wait(0b111);

    /* ── Phase 2: Node1 and Node2 read concurrently ── */
    int fail = 0;
    if (node_id == 1 || node_id == 2) {
        uint32_t expected = 0xDEADBEEF;
        emit_before_rd(node_id, 2);
        // Spin-wait until the write is visible (barrier is no-op in SE-mode)
        uint32_t got;
        int retries = 100000;
        do {
            got = dsm_load(2, 0);
            __asm__ volatile("dmb osh" ::: "memory");
        } while (got != expected && --retries > 0);
        int match = (got == expected);
        emit_read_val(node_id, 2, expected, got, match);
        if (!match) fail++;
    }
    sync_wait(0b111);

    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
