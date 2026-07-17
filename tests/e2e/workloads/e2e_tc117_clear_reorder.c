/* TC117: ClearReq reorder fault — 3.3 fault coverage.
 *
 * Fault rule delays the first ClearReq from node0→home1 by 50µs,
 * causing it to arrive after a subsequent ClearReq for a different PA.
 * Verifies the protocol correctly commits both writes regardless of
 * in-wire reordering.
 *
 * Flow:
 *   Phase 1: Node0 writes v1 to DSM_1 (ClearReq to home1 → REORDERED)
 *   Phase 2: Node0 writes v2 to DSM_2 (ClearReq to home1 → NORMAL)
 *            v2's Clear commits before v1's delayed Clear arrives.
 *   Phase 3: Node1 reads both lines → both should converge to v1, v2.
 *
 * Fault rule: tc117_reorder_clear:ClearReq:0:1:0:reorder:100000:1
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define LINE1_OFF  0x11700   /* first line — gets reordered Clear */
#define LINE2_OFF  0x11740   /* second line — normal Clear */
#define V1         0x1170AAA1u
#define V2         0x1170BBB2u

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC117");
    if (!primary) { _exit_program(0); return 0; }

    /* Node2 is a bystander — skip */
    if (node_id == 2) { _exit_program(0); return 0; }

    /* Phase 1: Node0 writes V1 — ClearReq to home1 gets REORDERED */
    if (node_id == 0) {
        dsm_store(1, LINE1_OFF, V1);
    }
    sync_wait(0b11);  /* nodes 0,1 */

    /* Phase 2: Node0 writes V2 — ClearReq NOT reordered (count=1 used up) */
    if (node_id == 0) {
        dsm_store(1, LINE2_OFF, V2);
    }
    sync_wait(0b11);

    /* Phase 3: Node1 reads both lines — must converge */
    if (node_id == 1) {
        uint32_t got1 = dsm_load(1, LINE1_OFF);
        int m1 = (got1 == V1);
        emit_read_val(1, 1, V1, got1, m1);

        uint32_t got2 = dsm_load(1, LINE2_OFF);
        int m2 = (got2 == V2);
        emit_read_val(1, 1, V2, got2, m2);
    }
    sync_wait(0b11);

    if (primary) emit_phase_done(node_id, "done");
    _exit_program(0);
    return 0;
}
