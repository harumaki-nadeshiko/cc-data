/* TC122 / P2: hot reuse after directory eviction.
 * Hot lines are populated and shared, cold lines pressure ResidentDir, then hot
 * lines are reused. Spill/load should preserve precise metadata; naive eviction
 * should pay future misses after eager invalidation.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOT_LINES  24
#define COLD_LINES 128
#define COLD_BASE  0x30000u
#define BASE       0x12200000u

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC122");

    if (node_id == 0) {
        for (int i = 0; i < HOT_LINES; i++)
            dsm_store(0, (uint32_t)i * 64u, BASE | (uint32_t)i);
        emit_phase_done(0, "hot_populate");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 0; i < HOT_LINES; i++) {
            uint32_t exp = BASE | (uint32_t)i;
            uint32_t got = dsm_load(0, (uint32_t)i * 64u);
            if ((i % 8) == 0) emit_read_val(1, 0, exp, got, got == exp);
        }
        emit_phase_done(1, "hot_share");
    }
    sync_wait(0b111);

    if (node_id == 0) {
        for (int i = 0; i < COLD_LINES; i++)
            dsm_store(0, COLD_BASE + (uint32_t)i * 64u,
                      BASE | 0x8000u | (uint32_t)i);
        emit_phase_done(0, "cold_overflow");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int i = 0; i < HOT_LINES; i++) {
            uint32_t exp = BASE | (uint32_t)i;
            uint32_t got = dsm_load(0, (uint32_t)i * 64u);
            if ((i % 8) == 0) emit_read_val(2, 0, exp, got, got == exp);
        }
        emit_phase_done(2, "hot_reuse");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
