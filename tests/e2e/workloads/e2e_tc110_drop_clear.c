/* TC110: drop ClearReq fault injection — 3.1 P1.
 *
 * Based on TC5 (single_writer) pattern.
 * Three nodes concurrently write different values to the same DSM_1 line,
 * then all nodes read and must agree on a single final value.
 *
 * Fault rule (applied via ubio --fault-rules): drop one ClearReq.
 * Rule: tc110_drop_clear:ClearReq:1:1:0:drop::1
 *
 * Verifies: value still converges, [UBFAULT] marker appears in ubio log,
 * verifier reports PASS.
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

    if (primary) emit_e2e_meta(node_id, "TC110");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Pre-barrier: all nodes rendezvous before concurrent writes ── */
    sync_wait(0b111);

    /* ── Concurrent writes: each node writes its own value ── */
    uint32_t vals[] = {0x11000001, 0x11000002, 0x11000003};
    uint32_t my_val = vals[node_id];

    if (primary) emit_before_wr(node_id, 1, my_val);
    dsm_store(1, 0, my_val);
    if (primary) emit_after_wr(node_id, 1, my_val);

    /* ── Post-barrier: wait until all writes have completed ── */
    sync_wait(0b111);

    /* ── All nodes read the value ── */
    if (primary) emit_before_rd(node_id, 1);
    uint64_t t0 = read_cntvct_el0();
    uint32_t got = dsm_load(1, 0);
    emit_guest_timer(node_id, "concurrent_write_read", 1,
                     read_cntvct_el0() - t0);

    int match = (got == 0x11000001 || got == 0x11000002 || got == 0x11000003);
    if (primary) emit_read_val(node_id, 1, my_val, got, match);

    /* ── Cross-check barrier: ensure all nodes have printed results ── */
    sync_wait(0b111);

    if (primary) emit_phase_done(node_id, "done");
    _exit_program(match ? 0 : 1);
    return 0;
}
