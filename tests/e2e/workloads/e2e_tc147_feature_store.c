/* TC147: recommendation feature-store lookups with sparse embedding updates. */
#include "portable_large_workload.h"

#define BATCHES PORTABLE_BATCHES
#define OPS_PER_BATCH 64
#define HOT_LINES_PER_PLANE 136
#define DATA_BASE 0x00c00000u
#define PRESSURE_BASE 0x04000000u
#define VALUE_BASE 0x14700000u

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if (!portable_is_primary(cpu)) { _exit_program(0); return 0; }
    int plane = portable_plane(node, cpu);
    uint32_t shard = portable_shard(DATA_BASE, plane);
    uint32_t embedding = shard;
    uint32_t accumulator = shard + 0x6000u;
    portable_emit_meta(plane, "TC147");
    portable_emit_pressure_config(
        plane, PORTABLE_PLANES * HOT_LINES_PER_PLANE);
    emit_timer_selftest(plane);

    PORTABLE_SERIAL_FOR_EACH_PLANE(plane, {
        for (int line = 0; line < 128; ++line)
            dsm_store(0, portable_line(embedding, line), VALUE_BASE |
                      ((uint32_t)plane << 16) | (uint32_t)line);
        for (int line = 0; line < 8; ++line)
            dsm_store(0, portable_line(accumulator, line), 0);
        __asm__ volatile("dsb sy" ::: "memory");
    });
    emit_phase_done(plane, "feature_store_seed");

    uint32_t warm_expected = VALUE_BASE | ((uint32_t)plane << 16);
    uint32_t warm = dsm_load(0, embedding);
    emit_read_val(plane, 0, warm_expected, warm, warm == warm_expected);
    emit_phase_done(plane, "feature_store_warm");
    portable_barrier();

    uint64_t samples[BATCHES];
    uint64_t service_ticks = 0;
    uint64_t end_to_end_start = read_counter_serialized();
    for (int batch = 0; batch < BATCHES; ++batch) {
        int first = portable_pressure_begin(batch);
        int last = portable_pressure_end(batch);
        for (int line = first + plane; line < last;
             line += PORTABLE_PLANES)
            dsm_store(0, portable_global_pressure(PRESSURE_BASE, line),
                      VALUE_BASE | 0x00800000u | (uint32_t)line);
        __asm__ volatile("dsb sy" ::: "memory");
        portable_barrier();

        uint64_t start = read_counter_serialized();
        for (int lookup = 0; lookup < 56; ++lookup) {
            int key = (lookup % 8) ? ((lookup * 9 + batch) & 31)
                                   : ((lookup * 23 + batch * 5) & 127);
            (void)dsm_load(0, portable_line(embedding, key));
        }
        for (int update = 0; update < 8; ++update)
            dsm_store(0, portable_line(accumulator, update), VALUE_BASE |
                      ((uint32_t)plane << 16) |
                      ((uint32_t)batch << 8) | (uint32_t)update);
        __asm__ volatile("dsb sy" ::: "memory");
        samples[batch] = read_counter_serialized() - start;
        service_ticks += samples[batch];
        portable_barrier();
    }
    uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
    portable_emit_results(plane, "feature_service", "feature_end_to_end",
                          "feature_batch_64ops", BATCHES * OPS_PER_BATCH,
                          service_ticks, end_to_end_ticks, samples, BATCHES);
    emit_phase_done(plane, "feature_batches");

    for (int update = 0; update < 8; ++update) {
        uint32_t expected = VALUE_BASE | ((uint32_t)plane << 16) |
                            ((BATCHES - 1u) << 8) | (uint32_t)update;
        uint32_t got = dsm_load(0, portable_line(accumulator, update));
        emit_read_val(plane, 0, expected, got, got == expected);
    }
    emit_phase_done(plane, "feature_verify");
    portable_barrier();
    _exit_program(0);
    return 0;
}
