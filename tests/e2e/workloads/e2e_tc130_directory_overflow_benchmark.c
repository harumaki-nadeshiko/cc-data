/* TC130: high-footprint ResidentDir overflow benchmark.
 *
 * Hot and pressure lines share set-index bits but differ in tag bits. The
 * 4-set x 2-way directory used by TC130 is therefore continually overfull.
 * Naive eviction invalidates hot copies; spill preserves their metadata.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOT_LINES       24
#define PRESSURE_LINES  192
#define ROUNDS          4
#define BASE            0x13000000u
#define COLD_BASE       0x01000000u
#define CONFLICT_STRIDE 0x10000u

static uint32_t hot_value(int line)
{
    return BASE | (uint32_t)line;
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC130");

    if (node_id == 0) {
        for (int i = 0; i < HOT_LINES; ++i)
            dsm_store(0, (uint32_t)i * 64u, hot_value(i));
        emit_phase_done(0, "hot_populate");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 0; i < HOT_LINES; ++i)
            (void)dsm_load(0, (uint32_t)i * 64u);
        emit_phase_done(1, "hot_share");
    }
    sync_wait(0b111);

    if (node_id == 0) {
        for (int i = 0; i < PRESSURE_LINES; ++i) {
            uint32_t offset = COLD_BASE + (uint32_t)(i & 3) * 64u +
                              (uint32_t)(i >> 2) * CONFLICT_STRIDE;
            dsm_store(0, offset, BASE | 0x800000u | (uint32_t)i);
        }
        emit_phase_done(0, "overflow_pressure");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int round = 0; round < ROUNDS; ++round) {
            for (int i = 0; i < HOT_LINES; ++i) {
                uint32_t got = dsm_load(0, (uint32_t)i * 64u);
                if (round == 0)
                    emit_read_val(1, 0, hot_value(i), got, got == hot_value(i));
            }
        }
        emit_phase_done(1, "hot_reuse");
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
