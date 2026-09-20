/* TC139: steady-state mixed load/store batches after directory pressure. */
#include "dsm_access.h"
#include "perf_latency.h"

#define HOT_LINES 16
#define PRESSURE_LINES 192
#define BATCHES 16
#define HOT_BASE 0x00500000u
#define PRESSURE_BASE 0x05000000u
#define CONFLICT_STRIDE 0x10000u
#define VALUE_BASE 0x13900000u

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
    emit_e2e_meta(node_id, "TC139");
    emit_timer_selftest(node_id);

    if (node_id == 0) {
        for (int i = 0; i < HOT_LINES; ++i)
            perf_store_complete(0, line_offset(HOT_BASE, i),
                                VALUE_BASE | (uint32_t)i);
        emit_phase_done(0, "seed_hot");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 0; i < HOT_LINES; ++i) {
            uint32_t expected = VALUE_BASE | (uint32_t)i;
            uint32_t got = dsm_load(0, line_offset(HOT_BASE, i));
            emit_read_val(1, 0, expected, got, got == expected);
        }
        emit_phase_done(1, "share_hot");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 1; i < HOT_LINES; i += 2)
            perf_store_complete(0, line_offset(HOT_BASE, i),
                                VALUE_BASE | (uint32_t)i);
        emit_phase_done(1, "owner_hot");
    }
    sync_wait(0b111);

    if (node_id == 0) {
        for (int i = 0; i < PRESSURE_LINES; ++i)
            perf_store_complete(0, line_offset(PRESSURE_BASE, i),
                                VALUE_BASE | 0x00800000u | (uint32_t)i);
        emit_phase_done(0, "directory_pressure");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        uint64_t samples[BATCHES];
        uint64_t total_start = read_counter_serialized();
        for (int batch = 0; batch < BATCHES; ++batch) {
            uint64_t start = read_counter_serialized();
            for (int i = 0; i < HOT_LINES; ++i) {
                if ((i & 1) == 0)
                    (void)dsm_load(0, line_offset(HOT_BASE, i));
                else
                    dsm_store(0, line_offset(HOT_BASE, i),
                              VALUE_BASE | ((uint32_t)batch << 8) | (uint32_t)i);
            }
            __asm__ volatile("dsb sy" ::: "memory");
            samples[batch] = read_counter_serialized() - start;
        }
        uint64_t total_ticks = read_counter_serialized() - total_start;
        emit_guest_timer(1, "mixed_batch_throughput", BATCHES * HOT_LINES,
                         total_ticks);
        emit_latency_summary(1, "mixed_batch_16ops", samples, BATCHES);
        emit_phase_done(1, "mixed_batches");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int i = 1; i < HOT_LINES; i += 2) {
            uint32_t expected = VALUE_BASE | ((BATCHES - 1u) << 8) | (uint32_t)i;
            uint32_t got = dsm_load(0, line_offset(HOT_BASE, i));
            emit_read_val(2, 0, expected, got, got == expected);
        }
        emit_phase_done(2, "verify_final");
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
