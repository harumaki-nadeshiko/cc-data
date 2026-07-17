/* TC112: TBE interference workload — 3.6 P1.
 *
 * Per node: cpu0 = dense local DSM writes (stress local HN-F TBE),
 *           cpu1 = sparse cross-node DSM write (home=1).
 *
 * Target: UBCC vs HA-C simulation shows quantifiable gap in local throughput
 * (HA-C has TBE contention; UBCC independent process, zero TBE occupancy).
 *
 * Verify: cross-node value convergence + local iteration count output.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define ROUNDS       256
#define LOCAL_OFF    0x11200   /* cpu0 local DSM offset range (home=node_id) */
#define CROSS_OFF    0x11400   /* cpu1 cross-node DSM offset (home=1) */

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    /* cpu0: dense local DSM writes — stress local HN-F TBE.
     * Each write to home=node_id goes through SNF→UBAdapter→UBCC→Clear,
     * consuming a local HN-F TBE slot. */
    if (cpu_index == 0) {
        /* Nodes 0 and 1 participate; node2 spins to avoid creating
         * dsm_load/dsm_store traffic that would trigger UBCC-side protocol
         * on a non-home node (the UBCC process for home=0 is on node0). */
        if (node_id != 2) {
            uint32_t seed = 0x12000000u | ((uint32_t)node_id << 16) | 1u;
            for (int r = 0; r < ROUNDS; r++) {
                uint32_t off = LOCAL_OFF +
                    (uint32_t)((r * 13 + node_id * 7) % 64) * 64u;
                uint32_t val = seed ^ (uint32_t)r;
                dsm_store(node_id, off, val);
                /* Read-back for confirmation — stresses TBE further */
                uint32_t got = dsm_load(node_id, off);
                (void)got;
                if ((r % 64) == 0) {
                    char buf[128]; int p = 0;
                    char *s = (char *)"[TC112_LOCAL] node=";
                    while (*s) buf[p++] = *s++;
                    p = fmt_int(buf, p, node_id);
                    s = (char *)" iter="; while (*s) buf[p++] = *s++;
                    p = fmt_int(buf, p, r);
                    buf[p++] = '\n';
                    _raw_write(buf, p);
                }
            }
        }
        _exit_program(0);
        return 0;
    }

    /* cpu1: sparse cross-node DSM write */
    if (cpu_index == 1) {
        uint32_t cross_val = 0x1200C000u | ((uint32_t)node_id << 8) | 1u;
        for (int r = 0; r < 32; r++) {
            dsm_store(1, CROSS_OFF + (uint32_t)r * 64u, cross_val ^ (uint32_t)r);
            uint32_t got = dsm_load(1, CROSS_OFF + (uint32_t)r * 64u);
            if (got != (cross_val ^ (uint32_t)r)) {
                emit_read_val(node_id, 1, cross_val ^ (uint32_t)r, got, 0);
                _exit_program(1);
                return 1;
            }
        }

        /* Emit final verification marker */
        sync_wait(0b111);
        uint32_t final_val = dsm_load(1, CROSS_OFF);
        emit_read_val(node_id, 1, cross_val, final_val, 1);
        sync_wait(0b111);
        _exit_program(0);
        return 0;
    }

    _exit_program(0);
    return 0;
}
