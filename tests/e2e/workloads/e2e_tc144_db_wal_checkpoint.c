/* TC144: WAL append plus dirty data-page updates during checkpoint pressure. */
#include "dsm_access.h"
#include "perf_latency.h"

#define DATA_PAGES 64
#define WAL_LINES 128
#define BATCHES 32
#define UPDATES_PER_BATCH 16
#define OPS_PER_BATCH 32
#define PRESSURE_PER_BATCH 24
#define DATA_BASE 0x00800000u
#define WAL_BASE 0x00900000u
#define PRESSURE_BASE 0x07200000u
#define VALUE_BASE 0x14400000u

static uint32_t line_offset(uint32_t base, int line)
{
    return base + (uint32_t)line * 64u;
}

int main(int argc, char **argv)
{
    int node_id = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu_index = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC144");
    emit_timer_selftest(node_id);

    if (node_id == 0) {
        for (int page = 0; page < DATA_PAGES; ++page)
            perf_store_complete(0, line_offset(DATA_BASE, page),
                                VALUE_BASE | (uint32_t)page);
        for (int line = 0; line < WAL_LINES; ++line)
            perf_store_complete(0, line_offset(WAL_BASE, line), 0);
        emit_phase_done(0, "database_seed");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int page = 0; page < DATA_PAGES; ++page) {
            uint32_t expected = VALUE_BASE | (uint32_t)page;
            uint32_t got = dsm_load(0, line_offset(DATA_BASE, page));
            if ((page & 3) == 0)
                emit_read_val(1, 0, expected, got, got == expected);
        }
        emit_phase_done(1, "database_warm");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        uint64_t samples[BATCHES];
        uint64_t service_ticks = 0;
        uint64_t end_to_end_start = read_counter_serialized();
        for (int batch = 0; batch < BATCHES; ++batch) {
            sync_wait(0b111);
            uint64_t start = read_counter_serialized();
            for (int update = 0; update < UPDATES_PER_BATCH; ++update) {
                int page = (batch * 11 + update * 7) & 63;
                int wal_line = (batch * UPDATES_PER_BATCH + update) & 127;
                uint32_t version = VALUE_BASE | ((uint32_t)batch << 8) |
                                   (uint32_t)page;
                dsm_store(0, line_offset(WAL_BASE, wal_line), version);
                dsm_store(0, line_offset(DATA_BASE, page), version);
            }
            __asm__ volatile("dsb sy" ::: "memory");
            samples[batch] = read_counter_serialized() - start;
            service_ticks += samples[batch];
            sync_wait(0b111);
        }
        uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
        emit_guest_timer(1, "db_wal_service", BATCHES * OPS_PER_BATCH,
                         service_ticks);
        emit_guest_timer(1, "db_wal_end_to_end", BATCHES * OPS_PER_BATCH,
                         end_to_end_ticks);
        emit_latency_summary(1, "db_wal_batch_32ops", samples, BATCHES);
        emit_phase_done(1, "wal_transactions");
    } else {
        for (int batch = 0; batch < BATCHES; ++batch) {
            if (node_id == 0) {
                int first = batch * PRESSURE_PER_BATCH;
                for (int line = 0; line < PRESSURE_PER_BATCH; ++line)
                    perf_store_complete(0,
                        line_offset(PRESSURE_BASE, first + line),
                        VALUE_BASE | 0x00800000u | (uint32_t)(first + line));
            }
            sync_wait(0b111);
            sync_wait(0b111);
        }
        if (node_id == 0)
            emit_phase_done(0, "checkpoint_pressure");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int update = 0; update < UPDATES_PER_BATCH; ++update) {
            int page = ((BATCHES - 1) * 11 + update * 7) & 63;
            int wal_line = ((BATCHES - 1) * UPDATES_PER_BATCH + update) & 127;
            uint32_t expected = VALUE_BASE | ((BATCHES - 1u) << 8) |
                                (uint32_t)page;
            uint32_t data = dsm_load(0, line_offset(DATA_BASE, page));
            uint32_t wal = dsm_load(0, line_offset(WAL_BASE, wal_line));
            emit_read_val(2, 0, expected, data, data == expected);
            emit_read_val(2, 0, expected, wal, wal == expected);
        }
        emit_phase_done(2, "recovery_verify");
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
