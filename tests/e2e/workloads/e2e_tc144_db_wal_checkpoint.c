/* TC144: topology-portable WAL append and dirty-page checkpoint traffic. */
#include "portable_large_workload.h"

#define BATCHES 32
#define UPDATES_PER_BATCH 16
#define OPS_PER_BATCH 32
#define PRESSURE_PER_BATCH 24
#define DATA_BASE 0x00600000u
#define PRESSURE_BASE 0x04000000u
#define VALUE_BASE 0x14400000u

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if (!portable_is_primary(cpu)) { _exit_program(0); return 0; }
    int plane = portable_plane(node, cpu);
    uint32_t shard = portable_shard(DATA_BASE, plane);
    uint32_t data = shard;
    uint32_t wal = shard + 0x4000u;
    portable_emit_meta(plane, "TC144");
    emit_timer_selftest(plane);

    PORTABLE_SERIAL_FOR_EACH_PLANE(plane, {
        for (int page = 0; page < 64; ++page)
            dsm_store(0, portable_line(data, page), VALUE_BASE |
                      ((uint32_t)plane << 16) | (uint32_t)page);
        for (int line = 0; line < 128; ++line)
            dsm_store(0, portable_line(wal, line), 0);
        __asm__ volatile("dsb sy" ::: "memory");
    });
    emit_phase_done(plane, "database_seed");

    uint32_t warm_expected = VALUE_BASE | ((uint32_t)plane << 16);
    uint32_t warm = dsm_load(0, portable_line(data, 0));
    emit_read_val(plane, 0, warm_expected, warm, warm == warm_expected);
    emit_phase_done(plane, "database_warm");
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
        for (int update = 0; update < UPDATES_PER_BATCH; ++update) {
            int page = (batch * 11 + update * 7) & 63;
            int wal_line = (batch * UPDATES_PER_BATCH + update) & 127;
            uint32_t version = VALUE_BASE | ((uint32_t)plane << 16) |
                               ((uint32_t)batch << 8) | (uint32_t)page;
            dsm_store(0, portable_line(wal, wal_line), version);
            dsm_store(0, portable_line(data, page), version);
        }
        __asm__ volatile("dsb sy" ::: "memory");
        samples[batch] = read_counter_serialized() - start;
        service_ticks += samples[batch];
        portable_barrier();
    }
    uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
    emit_phase_done(plane, "checkpoint_pressure");
    portable_emit_results(plane, "db_wal_service", "db_wal_end_to_end",
                          "db_wal_batch_32ops", BATCHES * OPS_PER_BATCH,
                          service_ticks, end_to_end_ticks, samples, BATCHES);
    emit_phase_done(plane, "wal_transactions");

    for (int update = 0; update < 8; ++update) {
        int page = ((BATCHES - 1) * 11 + update * 7) & 63;
        int wal_line = ((BATCHES - 1) * UPDATES_PER_BATCH + update) & 127;
        uint32_t expected = VALUE_BASE | ((uint32_t)plane << 16) |
                            ((BATCHES - 1u) << 8) | (uint32_t)page;
        uint32_t data_value = dsm_load(0, portable_line(data, page));
        uint32_t wal_value = dsm_load(0, portable_line(wal, wal_line));
        emit_read_val(plane, 0, expected, data_value, data_value == expected);
        emit_read_val(plane, 0, expected, wal_value, wal_value == expected);
    }
    emit_phase_done(plane, "recovery_verify");
    portable_barrier();
    _exit_program(0);
    return 0;
}
