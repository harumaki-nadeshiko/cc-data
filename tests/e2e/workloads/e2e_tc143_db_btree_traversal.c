/* TC143: B-tree root-to-leaf traversal with periodic leaf updates. */
#include "dsm_access.h"
#include "perf_latency.h"

#define INTERNAL_NODES 8
#define LEAF_PAGES 64
#define BATCHES 32
#define TRANSACTIONS_PER_BATCH 16
#define OPS_PER_TRANSACTION 4
#define PRESSURE_PER_BATCH 24
#define TREE_BASE 0x00700000u
#define PRESSURE_BASE 0x07000000u
#define VALUE_BASE 0x14300000u

#define ROOT_OFFSET TREE_BASE
#define INTERNAL_BASE (TREE_BASE + 0x1000u)
#define LEAF_BASE (TREE_BASE + 0x4000u)
#define RECORD_BASE (TREE_BASE + 0x8000u)

static uint32_t line_offset(uint32_t base, int line)
{
    return base + (uint32_t)line * 64u;
}

int main(int argc, char **argv)
{
    int node_id = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu_index = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC143");
    emit_timer_selftest(node_id);

    if (node_id == 0) {
        perf_store_complete(0, ROOT_OFFSET, VALUE_BASE);
        for (int i = 0; i < INTERNAL_NODES; ++i)
            perf_store_complete(0, line_offset(INTERNAL_BASE, i),
                                VALUE_BASE | 0x1000u | (uint32_t)i);
        for (int i = 0; i < LEAF_PAGES; ++i) {
            perf_store_complete(0, line_offset(LEAF_BASE, i),
                                VALUE_BASE | 0x2000u | (uint32_t)i);
            perf_store_complete(0, line_offset(RECORD_BASE, i),
                                VALUE_BASE | 0x3000u | (uint32_t)i);
        }
        emit_phase_done(0, "btree_seed");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        (void)dsm_load(0, ROOT_OFFSET);
        for (int i = 0; i < INTERNAL_NODES; ++i)
            (void)dsm_load(0, line_offset(INTERNAL_BASE, i));
        for (int i = 0; i < LEAF_PAGES; ++i) {
            (void)dsm_load(0, line_offset(LEAF_BASE, i));
            uint32_t expected = VALUE_BASE | 0x3000u | (uint32_t)i;
            uint32_t got = dsm_load(0, line_offset(RECORD_BASE, i));
            if ((i & 7) == 0)
                emit_read_val(1, 0, expected, got, got == expected);
        }
        for (int i = 0; i < 4; ++i)
            perf_store_complete(0, line_offset(RECORD_BASE, i),
                                VALUE_BASE | 0x3000u | (uint32_t)i);
        emit_phase_done(1, "btree_warm");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        uint64_t samples[BATCHES];
        uint64_t service_ticks = 0;
        uint64_t end_to_end_start = read_counter_serialized();
        for (int batch = 0; batch < BATCHES; ++batch) {
            sync_wait(0b111);
            uint64_t start = read_counter_serialized();
            for (int tx = 0; tx < TRANSACTIONS_PER_BATCH; ++tx) {
                int leaf = (tx * 17 + batch * 5) & 63;
                int internal = leaf >> 3;
                (void)dsm_load(0, ROOT_OFFSET);
                (void)dsm_load(0, line_offset(INTERNAL_BASE, internal));
                (void)dsm_load(0, line_offset(LEAF_BASE, leaf));
                if ((tx & 3) == 0) {
                    int update = tx >> 2;
                    dsm_store(0, line_offset(RECORD_BASE, update),
                              VALUE_BASE | ((uint32_t)batch << 8) |
                              (uint32_t)update);
                } else {
                    (void)dsm_load(0, line_offset(RECORD_BASE, leaf));
                }
            }
            __asm__ volatile("dsb sy" ::: "memory");
            samples[batch] = read_counter_serialized() - start;
            service_ticks += samples[batch];
            sync_wait(0b111);
        }
        uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
        emit_guest_timer(1, "db_btree_service",
                         BATCHES * TRANSACTIONS_PER_BATCH * OPS_PER_TRANSACTION,
                         service_ticks);
        emit_guest_timer(1, "db_btree_end_to_end",
                         BATCHES * TRANSACTIONS_PER_BATCH * OPS_PER_TRANSACTION,
                         end_to_end_ticks);
        emit_latency_summary(1, "db_btree_batch_64ops", samples, BATCHES);
        emit_phase_done(1, "btree_transactions");
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
            emit_phase_done(0, "btree_pressure");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int update = 0; update < 4; ++update) {
            uint32_t expected = VALUE_BASE | ((BATCHES - 1u) << 8) |
                                (uint32_t)update;
            uint32_t got = dsm_load(0, line_offset(RECORD_BASE, update));
            emit_read_val(2, 0, expected, got, got == expected);
        }
        emit_phase_done(2, "btree_verify");
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
