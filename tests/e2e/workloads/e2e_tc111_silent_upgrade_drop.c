/* TC111: Silent upgrade fault immunity — 3.2 P1.
 *
 * Tests fault injection on OuterUpgradeReq under EP_SILENT_UPGRADE=0 vs =1.
 *
 * Run 1 (EP_SILENT_UPGRADE=0):
 *   Fault rule drops one OuterUpgradeReq. Protocol retries and self-heals.
 *   Expected: [UBFAULT] OuterUpgradeReq drop + retry → PASS.
 *
 * Run 2 (EP_SILENT_UPGRADE=1):
 *   Silent upgrade path bypasses OuterUpgradeReq entirely (no cross-node
 *   messages). Fault rule never matches. Zero [UBFAULT] markers.
 *   Expected: PASS (zero cross-node = zero fault surface).
 *
 * Fault rule: tc111_silent_upgrade_drop:OuterUpgradeReq:1:1:0:drop::1
 *
 * Workload: node0 writes DSM_1, node1 reads (becomes sharer), then node1
 * writes again (triggers upgrade). All nodes converge.
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

    if (primary) emit_e2e_meta(node_id, "TC111");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* Phase 1: Node0 writes DSM_1 */
    if (node_id == 0) {
        dsm_store(1, 0, 0x1110AAA1u);
    }
    sync_wait(0b111);

    /* Phase 2: Node1 reads DSM_1 (becomes sharer) */
    if (node_id == 1) {
        uint32_t got = dsm_load(1, 0);
        emit_read_val(1, 1, 0x1110AAA1u, got, got == 0x1110AAA1u);
    }
    sync_wait(0b111);

    /* Phase 3: Node1 writes DSM_1 — triggers upgrade (OuterUpgradeReq)
     * Under EP_SILENT_UPGRADE=0 this sends OuterUpgradeReq which may be
     * dropped by the fault rule. Under EP_SILENT_UPGRADE=1 it's silent. */
    if (node_id == 1) {
        dsm_store(1, 0, 0x1110BBB2u);
    }
    sync_wait(0b111);

    /* Phase 4: All nodes read, must converge to 0x1110BBB2u */
    uint64_t t0 = read_cntvct_el0();
    uint32_t got = dsm_load(1, 0);
    int match = (got == 0x1110BBB2u);
    emit_guest_timer(node_id, "convergence_read", 1,
                     read_cntvct_el0() - t0);
    if (primary) emit_read_val(node_id, 1, 0x1110BBB2u, got, match);

    sync_wait(0b111);

    if (primary) emit_phase_done(node_id, "done");
    _exit_program(match ? 0 : 1);
    return 0;
}
