/* E2E-TC5: Single-writer correctness with concurrent writes.
 *
 * Three nodes concurrently write different values to the same DSM_1 line,
 * then all nodes read and must agree on a single final value.
 *
 * The final value must be one of {0xAA000001, 0xBB000002, 0xCC000003}.
 * This verifies that the protocol serialises writes and prevents data loss.
 */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    emit_e2e_meta(node_id, "TC5");

    /* ── Pre-barrier: all nodes rendezvous before concurrent writes ── */
    sync_wait(0b111);  /* mask = nodes 0, 1, 2 */

    /* ── Concurrent writes: each node writes its own value ── */
    uint32_t vals[] = {0xAA000001, 0xBB000002, 0xCC000003};
    uint32_t my_val = vals[node_id];

    emit_before_wr(node_id, 1, my_val);
    dsm_store(1, 0, my_val);
    emit_after_wr(node_id, 1, my_val);

    /* ── Post-barrier: wait until all writes have completed ── */
    sync_wait(0b111);

    /* ── All nodes read the value ── */
    emit_before_rd(node_id, 1);
    uint32_t got = dsm_load(1, 0);

    int match = (got == 0xAA000001 || got == 0xBB000002 || got == 0xCC000003);
    emit_read_val(node_id, 1, my_val, got, match);

    /* ── Cross-check barrier: ensure all nodes have printed results ── */
    sync_wait(0b111);

    emit_phase_done(node_id, "done");
    _exit_program(match ? 0 : 1);
    return 0;
}
