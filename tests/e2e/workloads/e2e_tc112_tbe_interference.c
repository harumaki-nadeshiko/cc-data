/* TC112: TBE interference workload.
 *
 * Each split-guest primary CPU performs local private stress followed by
 * sparse DSM writes.  Every node therefore reaches both barriers.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define ROUNDS    256
#define CROSS_OFF 0x11200

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    if ((cpu_index % 4) != 0) {
        _exit_program(0);
        return 0;
    }

    uint32_t seed = 0x12000000u | ((uint32_t)node_id << 16) | 1u;
    uint64_t t_local = read_cntvct_el0();
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
    emit_guest_timer(node_id, "local_stress", ROUNDS,
                     read_cntvct_el0() - t_local);

    uint32_t cv = 0x1200C000u | ((uint32_t)node_id << 8) | 1u;
    uint32_t writer_off = CROSS_OFF + (uint32_t)node_id * 0x1000u;
    uint64_t t_cross = read_cntvct_el0();
    for (int r = 0; r < 32; r++) {
        uint32_t off = writer_off + (uint32_t)r * 64u;
        dsm_store(1, off, cv ^ (uint32_t)r);
        uint32_t got = dsm_load(1, off);
        if (got != (cv ^ (uint32_t)r)) {
            emit_read_val(node_id, 1, cv ^ (uint32_t)r, got, 0);
            _exit_program(1);
            return 1;
        }
    }
    emit_guest_timer(node_id, "cross_node_stress", 32,
                     read_cntvct_el0() - t_cross);
    sync_wait(0b111);
    uint32_t final_val = dsm_load(1, writer_off);
    emit_read_val(node_id, 1, cv, final_val, 1);
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
