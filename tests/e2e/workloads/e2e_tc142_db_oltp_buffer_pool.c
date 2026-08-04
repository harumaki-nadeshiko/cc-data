/* TC142: topology-portable skewed OLTP buffer-pool transactions. */
#include "portable_large_workload.h"

#define HOT_PAGES 32
#define BATCHES PORTABLE_BATCHES
#define OPS_PER_BATCH 32
#define DATA_BASE 0x00200000u
#define PRESSURE_BASE 0x04000000u
#define VALUE_BASE 0x14200000u

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if (!portable_is_primary(cpu)) { _exit_program(0); return 0; }
    int plane = portable_plane(node, cpu);
    uint32_t shard = portable_shard(DATA_BASE, plane);
    portable_emit_meta(plane, "TC142");
    portable_emit_pressure_config(plane, PORTABLE_PLANES * HOT_PAGES);
    emit_timer_selftest(plane);

    PORTABLE_SERIAL_FOR_EACH_PLANE(plane, {
        for (int page = 0; page < HOT_PAGES; ++page)
            dsm_store(0, portable_line(shard, page),
                      VALUE_BASE | ((uint32_t)plane << 16) | (uint32_t)page);
        __asm__ volatile("dsb sy" ::: "memory");
    });
    emit_phase_done(plane, "buffer_pool_seed");

    uint32_t warm_expected = VALUE_BASE | ((uint32_t)plane << 16);
    uint32_t warm = dsm_load(0, portable_line(shard, 0));
    emit_read_val(plane, 0, warm_expected, warm, warm == warm_expected);
    emit_phase_done(plane, "buffer_pool_warm");
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
        for (int op = 0; op < 28; ++op) {
            int page = (op % 5) ? ((op * 7 + batch) & 7)
                                : ((op * 13 + batch * 3) & 31);
            (void)dsm_load(0, portable_line(shard, page));
        }
        for (int update = 0; update < 4; ++update) {
            int page = update * 2 + 1;
            dsm_store(0, portable_line(shard, page),
                      VALUE_BASE | ((uint32_t)plane << 16) |
                      ((uint32_t)batch << 8) | (uint32_t)page);
        }
        __asm__ volatile("dsb sy" ::: "memory");
        samples[batch] = read_counter_serialized() - start;
        service_ticks += samples[batch];
        portable_barrier();
    }
    uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
    emit_phase_done(plane, "incremental_pressure");
    portable_emit_results(plane, "db_oltp_service", "db_oltp_end_to_end",
                          "db_oltp_batch_32ops", BATCHES * OPS_PER_BATCH,
                          service_ticks, end_to_end_ticks, samples, BATCHES);
    emit_phase_done(plane, "oltp_transactions");

    for (int update = 0; update < 4; ++update) {
        int page = update * 2 + 1;
        uint32_t expected = VALUE_BASE | ((uint32_t)plane << 16) |
                            ((BATCHES - 1u) << 8) | (uint32_t)page;
        uint32_t got = dsm_load(0, portable_line(shard, page));
        emit_read_val(plane, 0, expected, got, got == expected);
    }
    emit_phase_done(plane, "oltp_verify");
    portable_barrier();
    _exit_program(0);
    return 0;
}
