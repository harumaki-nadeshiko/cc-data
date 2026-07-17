/* TC118: Combined fault — Drop Clear + Delay Clear on same HOME.
 *
 * Two writes to different PAs on the same home (node1), each with a
 * different fault.  Protocol must converge both lines despite concurrent
 * faults targeting the same UBCC process.
 *
 *   Fault 1: First ClearReq (write A) is DROPPED — V_A never committed.
 *   Fault 2: Second ClearReq (write B) is DELAYED by 100µs — V_B eventually
 *            commits after the delay.
 *
 * When V_A's Clear is dropped, the line stays in old state at home but
 * node0 holds dirty data.  Any subsequent RECALL to node0 recovers the
 * correct data.  When V_B's Clear is delayed, it eventually arrives and
 * commits correctly.
 *
 * This is stronger than TC117 (two independent PAs with simple reorder)
 * because both faults concurrently stress the SAME UBCC home process,
 * testing epoch monotonicity and tombstone replay under combined faults.
 *
 * Fault rules (matched by distinct homeLinePa):
 *   tc118_drop:  ClearReq:0:1:0x10018011800:drop::1
 *   tc118_delay: ClearReq:0:1:0x10018011900:delay:100000:1
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE    1
#define LINE_OFF1    0x11800
#define LINE_OFF2    0x11900
#define V1           0x1180AAA1u
#define V2           0x1190BBB2u

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC118");
    if (!primary) { _exit_program(0); return 0; }
    if (node_id == 2) { _exit_program(0); return 0; }

    /* ── Phase 1: V1 write — ClearReq DROPPED ── */
    if (node_id == 0) {
        dsm_store(HOME_NODE, LINE_OFF1, V1);
    }
    sync_wait(0b11);

    /* ── Phase 2: V2 write — ClearReq DELAYED 100µs ── */
    if (node_id == 0) {
        dsm_store(HOME_NODE, LINE_OFF2, V2);
    }
    sync_wait(0b11);

    /* ── Phase 3: Node1 reads both — must converge ── */
    if (node_id == 1) {
        uint32_t got1 = dsm_load(HOME_NODE, LINE_OFF1);
        emit_read_val(1, HOME_NODE, V1, got1, got1 == V1);

        uint32_t got2 = dsm_load(HOME_NODE, LINE_OFF2);
        emit_read_val(1, HOME_NODE, V2, got2, got2 == V2);
    }
    sync_wait(0b11);

    if (primary) emit_phase_done(node_id, "done");
    _exit_program(0);
    return 0;
}
