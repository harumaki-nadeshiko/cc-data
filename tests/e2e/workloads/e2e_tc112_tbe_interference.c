/* TC112: TBE interference workload — 3.6 P1.
 *
 * Based on TC35 (numa_latency_stress) pattern.
 * Per node: cpu0 = dense local private access (大量随机读写),
 *           cpu1 = sparse cross-node DSM write.
 *
 * Target: UBCC vs HA-C simulation shows quantifiable gap in local throughput
 * (HA-C has TBE contention; UBCC independent process, zero TBE occupancy).
 *
 * Verify: cross-node value convergence + local iteration count output.
 */
#include "dsm_access.h"
#include "e2e_common.h"

/* Use preprocessor arithmetic — limited to small constants in bare-metal. */
#define ROUNDS      256
#define LOCAL_BASE  0x12000000u
#define DSM_OFF     0x11200

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    /* cpu0: dense local private access (caches private segment, no DSM dir) */
    if (cpu_index == 0) {
        /* Write private per-node value as seed */
        uint32_t seed_val = 0x12000000u | ((uint32_t)node_id << 16) | 1u;
        /* Dense local writes + reads on private segment */
        for (int r = 0; r < ROUNDS; r++) {
            /* Random-like pattern: offset based on r and node */
            uint32_t off = LOCAL_BASE + (uint32_t)((r * 13 + node_id * 7) % 512) * 64u;
            volatile uint32_t *ptr = (volatile uint32_t *)(0x40000000ULL + off);
            *ptr = seed_val ^ (uint32_t)r;
            uint32_t dummy = *ptr;
            (void)dummy;
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
        _exit_program(0);
        return 0;
    }

    /* cpu1: sparse cross-node DSM write */
    if (cpu_index == 1) {
        uint32_t cross_val = 0x1200C000u | ((uint32_t)node_id << 8) | 1u;
        for (int r = 0; r < 32; r++) {
            dsm_store(1, DSM_OFF + (uint32_t)r * 64u, cross_val ^ (uint32_t)r);
            uint32_t got = dsm_load(1, DSM_OFF + (uint32_t)r * 64u);
            if (got != (cross_val ^ (uint32_t)r)) {
                emit_read_val(node_id, 1, cross_val ^ (uint32_t)r, got, 0);
                _exit_program(1);
                return 1;
            }
        }

        /* Emit final verification marker */
        sync_wait(0b111);
        uint32_t final_val = dsm_load(1, DSM_OFF);
        emit_read_val(node_id, 1, cross_val, final_val, 1);
        sync_wait(0b111);
        _exit_program(0);
        return 0;
    }

    _exit_program(0);
    return 0;
}
