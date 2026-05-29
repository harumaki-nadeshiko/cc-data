/* E2E-TC2: Dual-node remote read (Writer→Reader).
 *
 * Phase 1: Node0 writes 0x11223344 to DSM_1 (home=Node1).
 * Phase 2: Node1 reads DSM_1 and verifies the value.
 *
 * Verifies: remote write → recall → remote read complete path.
 */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    emit_e2e_meta(node_id, "TC2");

    /* Nodes 0 and 1 participate; Node 2 idle */
    if (node_id > 1) {
        emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Phase 1: Node0 writes DSM_1 ── */
    if (node_id == 0) {
        uint32_t val = 0x11223344;
        emit_before_wr(node_id, 1, val);
        dsm_store(1, 0, val);
        emit_after_wr(node_id, 1, val);
    }

    /* Barrier: sync Node0 and Node1 */
    sync_wait(0b011);   /* mask = node 0 + node 1 */

    /* ── Phase 2: Node1 reads DSM_1 ── */
    if (node_id == 1) {
        uint32_t expected = 0x11223344;
        emit_before_rd(node_id, 1);
        uint32_t got = dsm_load(1, 0);
        int match = (got == expected);
        emit_read_val(node_id, 1, expected, got, match);
        if (!match) fail++;
    }

    /* Final barrier: Node0 waits for Node1 to finish */
    sync_wait(0b011);

    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
