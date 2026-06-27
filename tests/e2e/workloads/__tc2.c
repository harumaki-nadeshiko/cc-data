/* E2E-TC2: Dual-node remote read (Writer→Reader).
 *
 * Phase 1: Node0 writes 0x11223344 to DSM_1 (home=Node1).
 * Phase 2: Node1 reads DSM_1 and verifies the value.
 *
 * Verifies: remote write → recall → remote read complete path.
 *
 * Q2: Only the first CPU (cpu_index % 4 == 0) per node emits markers
 * to avoid simout interleaving.  All CPUs participate in coherence.
 */
#include "dsm_access.h"
#include "e2e_common.h"
#include "__tc2_sweep.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC2");

    /* Nodes 0 and 1 participate; Node 2 idle */
    if (node_id > 1) {
        if (primary) emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Phase 1: Node0 writes DSM_1 ── */
    if (node_id == 0) {
        uint32_t val = 0x11223344;
        if (primary) emit_before_wr(node_id, 1, val);
        dsm_store(1, 0, val);
        /* Q2 Fix B: Confirmation read — poll until the stored value
         * is visible on a re-read, exercising the real coherence
         * read path instead of a blind delay loop. */
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
        if (primary) emit_after_wr(node_id, 1, val);
        _tc2_sweep();
    }

    /* Barrier: sync Node0 and Node1 */
    sync_wait(0b011);   /* mask = node 0 + node 1 */

    /* ── Phase 2: Node1 reads DSM_1 ── */
    if (node_id == 1) {
        uint32_t expected = 0x11223344;
        if (primary) emit_before_rd(node_id, 1);
        uint32_t got = dsm_load(1, 0);
        int match = (got == expected);
        if (primary) emit_read_val(node_id, 1, expected, got, match);
        if (!match) fail++;
    }

    /* Final barrier: Node0 waits for Node1 to finish */
    sync_wait(0b011);

    if (primary) emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
