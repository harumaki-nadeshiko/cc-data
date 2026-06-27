/* E2E-TC2: Dual-node remote read (Writer→Reader).
 *
 * Phase 1: Node0 writes 0x11223344 to DSM_1 (home=Node1).
 * Phase 2: Node1 reads DSM_1 and verifies the value.
 *
 * Only primary CPU does store/load. Other CPUs still participate
 * in coherence (L1/L2 caches active) but skip the barrier.
 */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC2");

    if (node_id > 1) {
        if (primary) emit_phase_done(node_id, "idle");
        return 0;
    }

    int fail = 0;

    /* Phase 1: Node0 primary writes */
    if (node_id == 0 && primary) {
        uint32_t val = 0x11223344;
        emit_before_wr(node_id, 1, val);
        dsm_store(1, 0, val);
        uint32_t v;
        int retries = 10000;
        do {
            v = dsm_load(1, 0);
            asm volatile("dmb osh" ::: "memory");
        } while (v != val && --retries > 0);
        if (v != val) {
            char *msg = (char *)"[FATAL] TC2 store confirmation failed\n";
            _raw_write(msg, 38);
        }
        emit_after_wr(node_id, 1, val);
    }

    /* Only primary CPUs engage in barrier (2 threads total) */
    if (primary) sync_wait(0b011);

    /* Phase 2: Node1 primary reads */
    if (node_id == 1 && primary) {
        uint32_t expected = 0x11223344;
        emit_before_rd(node_id, 1);
        uint32_t got = dsm_load(1, 0);
        int match = (got == expected);
        emit_read_val(node_id, 1, expected, got, match);
        if (!match) fail++;
    }

    if (primary) sync_wait(0b011);

    if (primary) emit_phase_done(node_id, fail ? "fail" : "done");
    return fail ? 1 : 0;
}
