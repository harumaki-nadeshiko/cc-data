/* TC142: skewed OLTP buffer-pool transactions under incremental pressure. */
#include "dsm_access.h"
#include "perf_latency.h"

#define HOT_PAGES 64
#define BATCHES 32
#define OPS_PER_BATCH 32
#define PRESSURE_PER_BATCH 24
#define HOT_BASE 0x00600000u
#define PRESSURE_BASE 0x06000000u
#define VALUE_BASE 0x14200000u

static uint32_t page_offset(uint32_t base, int page)
{
    return base + (uint32_t)page * 64u;
}

int main(int argc, char **argv)
{
    int node_id = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu_index = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC142");
    emit_timer_selftest(node_id);

    if (node_id == 0) {
        for (int page = 0; page < HOT_PAGES; ++page)
            perf_store_complete(0, page_offset(HOT_BASE, page),
                                VALUE_BASE | (uint32_t)page);
        emit_phase_done(0, "buffer_pool_seed");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int page = 0; page < HOT_PAGES; ++page) {
            uint32_t expected = VALUE_BASE | (uint32_t)page;
            uint32_t got = dsm_load(0, page_offset(HOT_BASE, page));
            if ((page & 3) == 0)
                emit_read_val(1, 0, expected, got, got == expected);
        }
        for (int page = 1; page < 8; page += 2)
            perf_store_complete(0, page_offset(HOT_BASE, page),
                                VALUE_BASE | (uint32_t)page);
        emit_phase_done(1, "buffer_pool_warm");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        uint64_t samples[BATCHES];
        uint64_t service_ticks = 0;
        uint64_t end_to_end_start = read_counter_serialized();
        for (int batch = 0; batch < BATCHES; ++batch) {
            sync_wait(0b111);
            uint64_t start = read_counter_serialized();
            for (int op = 0; op < 28; ++op) {
                int page = (op % 5) ? ((op * 7 + batch) & 15)
                                    : ((op * 13 + batch * 3) & 63);
                (void)dsm_load(0, page_offset(HOT_BASE, page));
            }
            for (int update = 0; update < 4; ++update) {
                int page = update * 2 + 1;
                dsm_store(0, page_offset(HOT_BASE, page),
                          VALUE_BASE | ((uint32_t)batch << 8) |
                          (uint32_t)page);
            }
            __asm__ volatile("dsb sy" ::: "memory");
            samples[batch] = read_counter_serialized() - start;
            service_ticks += samples[batch];
            sync_wait(0b111);
        }
        uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
        emit_guest_timer(1, "db_oltp_service", BATCHES * OPS_PER_BATCH,
                         service_ticks);
        emit_guest_timer(1, "db_oltp_end_to_end", BATCHES * OPS_PER_BATCH,
                         end_to_end_ticks);
        emit_latency_summary(1, "db_oltp_batch_32ops", samples, BATCHES);
        emit_phase_done(1, "oltp_transactions");
    } else {
        for (int batch = 0; batch < BATCHES; ++batch) {
            if (node_id == 0) {
                int first = batch * PRESSURE_PER_BATCH;
                for (int line = 0; line < PRESSURE_PER_BATCH; ++line)
                    perf_store_complete(0,
                        page_offset(PRESSURE_BASE, first + line),
                        VALUE_BASE | 0x00800000u | (uint32_t)(first + line));
            }
            sync_wait(0b111);
            sync_wait(0b111);
        }
        if (node_id == 0)
            emit_phase_done(0, "incremental_pressure");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int update = 0; update < 4; ++update) {
            int page = update * 2 + 1;
            uint32_t expected = VALUE_BASE | ((BATCHES - 1u) << 8) |
                                (uint32_t)page;
            uint32_t got = dsm_load(0, page_offset(HOT_BASE, page));
            emit_read_val(2, 0, expected, got, got == expected);
        }
        emit_phase_done(2, "oltp_verify");
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
