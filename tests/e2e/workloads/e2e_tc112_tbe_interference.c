/* TC112: TBE interference workload — 3.6 P1.
 *
 * cpu0 (lane 0): dense local private writes to the local_private_range.
 *   test_e2e.py pre-maps VA 0x01000000 → per-node local_private PA,
 *   so raw pointer accesses route through local HN-F → local_mem.
 *   Zero DSM directory — pure local cache-to-DRAM traffic.
 *
 * cpu1 (lane 1): sparse cross-node DSM writes (home=1).
 *
 * Verify: cross-node value convergence + TC112_LOCAL progress markers.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define ROUNDS       256
#define CROSS_OFF    0x11200

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    /* cpu0: dense local private access — local HN-F TBE stress */
    if (cpu_index == 0) {
        if (node_id != 2) {
            uint32_t seed = 0x12000000u | ((uint32_t)node_id << 16) | 1u;
            for (int r = 0; r < ROUNDS; r++) {
                uint32_t off = (uint32_t)((r * 13 + node_id * 7) % 512) * 64u;
                local_dram_store(off, seed ^ (uint32_t)r);
                uint32_t dummy = local_dram_load(off);
                (void)dummy;
                if ((r % 64) == 0) {
                    char b[128]; int p = 0;
                    char *s = (char *)"[TC112_LOCAL] node=";
                    while (*s) b[p++] = *s++;
                    p = fmt_int(b, p, node_id);
                    s = (char *)" iter="; while (*s) b[p++] = *s++;
                    p = fmt_int(b, p, r);
                    b[p++] = '\n';
                    _raw_write(b, p);
                }
            }
        }
        _exit_program(0);
        return 0;
    }

    /* cpu1: sparse cross-node DSM write */
    if (cpu_index == 1) {
        uint32_t cv = 0x1200C000u | ((uint32_t)node_id << 8) | 1u;
        for (int r = 0; r < 32; r++) {
            dsm_store(1, CROSS_OFF + (uint32_t)r * 64u, cv ^ (uint32_t)r);
            uint32_t got = dsm_load(1, CROSS_OFF + (uint32_t)r * 64u);
            if (got != (cv ^ (uint32_t)r)) {
                emit_read_val(node_id, 1, cv ^ (uint32_t)r, got, 0);
                _exit_program(1);
                return 1;
            }
        }
        sync_wait(0b111);
        uint32_t final_val = dsm_load(1, CROSS_OFF);
        emit_read_val(node_id, 1, cv, final_val, 1);
        sync_wait(0b111);
        _exit_program(0);
        return 0;
    }

    _exit_program(0);
    return 0;
}
