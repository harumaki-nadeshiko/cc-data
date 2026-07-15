/* TC113: Silent upgrade micro-benchmark — 4.5 P2.
 *
 * Node0 repeatedly writes the same exclusive cache line in a tight loop
 * (~1000 iterations), amplifying the upgrade-path proportion.
 * Designed for EP_SILENT_UPGRADE=0 vs =1 comparison experiments.
 *
 * With EP_SILENT_UPGRADE=0: each write triggers OuterUpgradeReq cross-node.
 * With EP_SILENT_UPGRADE=1: writes are silent, zero cross-node messages.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define ITERS 1000

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC113");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* Phase 1: Node0 takes exclusive ownership of DSM_1 */
    if (node_id == 0) {
        dsm_store(1, 0, 0x11300000u);
    }
    sync_wait(0b111);

    /* Phase 2: Node1 reads DSM_1 (becomes R_E holder — shares the line) */
    if (node_id == 1) {
        uint32_t got = dsm_load(1, 0);
        emit_read_val(1, 1, 0x11300000u, got, got == 0x11300000u);
    }
    sync_wait(0b111);

    /* Phase 3: Node1 tight loop — repeatedly writes same exclusive line.
     * Each write triggers a local upgrade (R_E holder → exclusive owner).
     * Under EP_SILENT_UPGRADE=0: each iteration sends OuterUpgradeReq.
     * Under EP_SILENT_UPGRADE=1: silent, no cross-node messages. */
    if (node_id == 1) {
        for (int i = 0; i < ITERS; i++) {
            uint32_t val = 0x11300000u | ((uint32_t)i & 0xFFF);
            dsm_store(1, 0, val);
            if ((i % 128) == 0) {
                char buf[128]; int p = 0;
                char *s = (char *)"[TC113_UPG] node=1 iter=";
                while (*s) buf[p++] = *s++;
                p = fmt_int(buf, p, i);
                buf[p++] = '\n';
                _raw_write(buf, p);
            }
        }
    }
    sync_wait(0b111);

    /* Phase 4: Verify final value converged */
    uint32_t final_got = dsm_load(1, 0);
    uint32_t final_exp = 0x11300000u | ((uint32_t)(ITERS - 1) & 0xFFF);
    int match = (final_got == final_exp);
    if (primary) {
        emit_read_val(node_id, 1, final_exp, final_got, match);
        char buf[128]; int p = 0;
        char *s = (char *)"[TC113_DONE] node=";
        while (*s) buf[p++] = *s++;
        p = fmt_int(buf, p, node_id);
        s = (char *)" iters=1000\n"; while (*s) buf[p++] = *s++;
        _raw_write(buf, p);
    }

    sync_wait(0b111);
    _exit_program(match ? 0 : 1);
    return 0;
}
