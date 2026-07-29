/* TC136: writer-visible stores by the original dirty owner after pressure. */
#include "dsm_access.h"
#include "perf_latency.h"

#define HOT_LINES 24
#define PRESSURE_LINES 192
#define HOT_BASE 0x00200000u
#define PRESSURE_BASE 0x02000000u
#define CONFLICT_STRIDE 0x10000u
#define INITIAL_BASE 0x13600000u
#define FINAL_BASE 0x13610000u

static uint32_t line_offset(uint32_t base, int i)
{
    return base + (uint32_t)(i & 3) * 64u +
           (uint32_t)(i >> 2) * CONFLICT_STRIDE;
}

int main(int argc, char **argv)
{
    int node_id = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu_index = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC136");
    emit_timer_selftest(node_id);

    if (node_id == 1) {
        for (int i = 0; i < HOT_LINES; ++i)
            perf_store_complete(0, line_offset(HOT_BASE, i),
                                INITIAL_BASE | (uint32_t)i);
        emit_phase_done(1, "dirty_owner_seed");
    }
    sync_wait(0b111);

    if (node_id == 0) {
        for (int i = 0; i < PRESSURE_LINES; ++i)
            perf_store_complete(0, line_offset(PRESSURE_BASE, i),
                                INITIAL_BASE | 0x00800000u | (uint32_t)i);
        emit_phase_done(0, "directory_pressure");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        uint64_t samples[HOT_LINES];
        for (int i = 0; i < HOT_LINES; ++i) {
            uint64_t start = read_counter_serialized();
            perf_store_complete(0, line_offset(HOT_BASE, i),
                                FINAL_BASE | (uint32_t)i);
            samples[i] = read_counter_serialized() - start;
        }
        emit_latency_summary(1, "preserved_owner_store_complete", samples,
                             HOT_LINES);
        emit_phase_done(1, "owner_store_reuse");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int i = 0; i < HOT_LINES; ++i) {
            uint32_t expected = FINAL_BASE | (uint32_t)i;
            uint32_t got = dsm_load(0, line_offset(HOT_BASE, i));
            emit_read_val(2, 0, expected, got, got == expected);
        }
        emit_phase_done(2, "verify_final");
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
