/* E2E-TC8: Shared→Upgrade invalidate other sharers.
 *
 * Phase 1: Node0 writes 0xAAA to DSM_2 (home=Node2).
 * Phase 2: Node1 and Node2 both shared-read DSM_2.
 * Phase 3: Node0 writes 0xBBB to DSM_2 (must invalidate Node1/Node2).
 * Phase 4: Node1 reads DSM_2 — must see 0xBBB (not stale 0xAAA).
 *
 * This verifies the GlobalInvalidate path: when a former writer
 * upgrades from Shared back to Exclusive, other sharers are
 * correctly invalidated.
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

    if (primary) emit_e2e_meta(node_id, "TC8");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Node0 writes 0xAAA ── */
    if (node_id == 0) {
        emit_before_wr(node_id, 2, 0xAAA);
        dsm_store(2, 0, 0xAAA);
        emit_after_wr(node_id, 2, 0xAAA);
    }
    sync_wait(0b111);

    /* ── Phase 2: Node1 and Node2 shared read ── */
    if (node_id == 1 || node_id == 2) {
        emit_before_rd(node_id, 2);
        uint32_t got = dsm_load(2, 0);
        emit_read_val(node_id, 2, 0xAAA, got, got == 0xAAA);
    }
    sync_wait(0b111);

    /* ── Phase 3: Node0 writes 0xBBB (upgrade, invalidate sharers) ── */
    if (node_id == 0) {
        emit_before_wr(node_id, 2, 0xBBB);
        dsm_store(2, 0, 0xBBB);
        emit_after_wr(node_id, 2, 0xBBB);
    }
    sync_wait(0b111);

    /* ── Phase 4: Node1 reads — must see 0xBBB ── */
    int fail = 0;
    if (node_id == 1) {
        emit_before_rd(node_id, 2);
        uint32_t got = dsm_load(2, 0);
        int match = (got == 0xBBB);
        emit_read_val(node_id, 2, 0xBBB, got, match);
        if (!match) fail++;
    }
    sync_wait(0b111);

    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
