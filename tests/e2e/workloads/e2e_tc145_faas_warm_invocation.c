/* TC145: FaaS warm-container state reuse with concurrent cold-package churn. */
#include "portable_large_workload.h"

#define BATCHES 32
#define OPS_PER_BATCH 64
#define PRESSURE_PER_BATCH 24
#define DATA_BASE 0x00800000u
#define PRESSURE_BASE 0x04000000u
#define VALUE_BASE 0x14500000u

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if (!portable_is_primary(cpu)) { _exit_program(0); return 0; }
    int plane = portable_plane(node, cpu);
    uint32_t shard = portable_shard(DATA_BASE, plane);
    uint32_t runtime = shard;
    uint32_t tenant = shard + 0x2000u;
    uint32_t result = shard + 0x4000u;
    portable_emit_meta(plane, "TC145");
    emit_timer_selftest(plane);

    PORTABLE_SERIAL_FOR_EACH_PLANE(plane, {
        for (int line = 0; line < 64; ++line) {
            dsm_store(0, portable_line(runtime, line), VALUE_BASE |
                      ((uint32_t)plane << 16) | 0x1000u | (uint32_t)line);
            dsm_store(0, portable_line(tenant, line), VALUE_BASE |
                      ((uint32_t)plane << 16) | 0x2000u | (uint32_t)line);
        }
        for (int line = 0; line < 8; ++line)
            dsm_store(0, portable_line(result, line), 0);
        __asm__ volatile("dsb sy" ::: "memory");
    });
    emit_phase_done(plane, "faas_runtime_seed");

    uint32_t warm_expected = VALUE_BASE | ((uint32_t)plane << 16) | 0x1000u;
    uint32_t warm = dsm_load(0, runtime);
    emit_read_val(plane, 0, warm_expected, warm, warm == warm_expected);
    emit_phase_done(plane, "faas_runtime_warm");
    portable_barrier();

    uint64_t samples[BATCHES];
    uint64_t service_ticks = 0;
    uint64_t end_to_end_start = read_counter_serialized();
    for (int batch = 0; batch < BATCHES; ++batch) {
        int first = batch * PRESSURE_PER_BATCH;
        for (int line = plane; line < PRESSURE_PER_BATCH;
             line += PORTABLE_PLANES)
            dsm_store(0, portable_global_pressure(PRESSURE_BASE, first + line),
                      VALUE_BASE | 0x00800000u | (uint32_t)(first + line));
        __asm__ volatile("dsb sy" ::: "memory");
        portable_barrier();

        uint64_t start = read_counter_serialized();
        for (int op = 0; op < 48; ++op) {
            int line = (op % 6) ? ((op * 5 + batch) & 15)
                                : ((op * 11 + batch * 3) & 63);
            (void)dsm_load(0, portable_line(runtime, line));
        }
        for (int op = 0; op < 8; ++op)
            (void)dsm_load(0, portable_line(tenant, (op * 7 + batch) & 63));
        for (int op = 0; op < 8; ++op)
            dsm_store(0, portable_line(result, op), VALUE_BASE |
                      ((uint32_t)plane << 16) | ((uint32_t)batch << 8) |
                      (uint32_t)op);
        __asm__ volatile("dsb sy" ::: "memory");
        samples[batch] = read_counter_serialized() - start;
        service_ticks += samples[batch];
        portable_barrier();
    }
    uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
    portable_emit_results(plane, "faas_service", "faas_end_to_end",
                          "faas_batch_64ops", BATCHES * OPS_PER_BATCH,
                          service_ticks, end_to_end_ticks, samples, BATCHES);
    emit_phase_done(plane, "faas_invocations");

    for (int op = 0; op < 8; ++op) {
        uint32_t expected = VALUE_BASE | ((uint32_t)plane << 16) |
                            ((BATCHES - 1u) << 8) | (uint32_t)op;
        uint32_t got = dsm_load(0, portable_line(result, op));
        emit_read_val(plane, 0, expected, got, got == expected);
    }
    emit_phase_done(plane, "faas_verify");
    portable_barrier();
    _exit_program(0);
    return 0;
}
