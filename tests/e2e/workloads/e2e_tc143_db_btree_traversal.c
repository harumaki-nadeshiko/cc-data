/* TC143: topology-portable B-tree root-to-leaf traversal and updates. */
#include "portable_large_workload.h"

#define BATCHES PORTABLE_BATCHES
#define TRANSACTIONS_PER_BATCH 16
#define OPS_PER_BATCH 64
#define HOT_LINES_PER_PLANE 137
#define DATA_BASE 0x00400000u
#define PRESSURE_BASE 0x04000000u
#define VALUE_BASE 0x14300000u

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    int primary = portable_is_primary(cpu);
#ifndef E2E_TC143_HYBRID_SWITCH
    if (!primary) { _exit_program(0); return 0; }
#endif
    portable_wait_ready();
    int plane = portable_plane(node, cpu);
    uint32_t shard = portable_shard(DATA_BASE, plane);
    uint32_t root = shard;
    uint32_t internal = shard + 0x1000u;
    uint32_t leaf = shard + 0x2000u;
    uint32_t record = shard + 0x4000u;
    if (primary) {
        portable_emit_meta(plane, "TC143");
        portable_emit_pressure_config(
            plane, PORTABLE_PLANES * HOT_LINES_PER_PLANE);
        emit_timer_selftest(plane);
    }

#ifdef E2E_TC143_HYBRID_SWITCH
    for (int turn = 0; turn < PORTABLE_PLANES; ++turn) {
        if (primary && plane == turn) {
            dsm_store(0, root, VALUE_BASE | ((uint32_t)plane << 16));
            for (int i = 0; i < 8; ++i)
                dsm_store(0, portable_line(internal, i),
                          VALUE_BASE | 0x1000u |
                          ((uint32_t)plane << 16) | (uint32_t)i);
            for (int i = 0; i < 64; ++i) {
                dsm_store(0, portable_line(leaf, i),
                          VALUE_BASE | 0x2000u |
                          ((uint32_t)plane << 16) | (uint32_t)i);
                dsm_store(0, portable_line(record, i),
                          VALUE_BASE | 0x3000u |
                          ((uint32_t)plane << 16) | (uint32_t)i);
            }
            __asm__ volatile("dsb sy" ::: "memory");
        }
        portable_full_cpu_barrier();
    }
#else
    PORTABLE_SERIAL_FOR_EACH_PLANE(plane, {
        dsm_store(0, root, VALUE_BASE | ((uint32_t)plane << 16));
        for (int i = 0; i < 8; ++i)
            dsm_store(0, portable_line(internal, i), VALUE_BASE | 0x1000u |
                      ((uint32_t)plane << 16) | (uint32_t)i);
        for (int i = 0; i < 64; ++i) {
            dsm_store(0, portable_line(leaf, i), VALUE_BASE | 0x2000u |
                      ((uint32_t)plane << 16) | (uint32_t)i);
            dsm_store(0, portable_line(record, i), VALUE_BASE | 0x3000u |
                      ((uint32_t)plane << 16) | (uint32_t)i);
        }
        __asm__ volatile("dsb sy" ::: "memory");
    });
#endif
    if (primary) emit_phase_done(plane, "btree_seed");

    if (primary) {
        uint32_t warm_expected = VALUE_BASE | 0x3000u |
            ((uint32_t)plane << 16);
        uint32_t warm = dsm_load(0, portable_line(record, 0));
        emit_read_val(plane, 0, warm_expected, warm, warm == warm_expected);
        emit_phase_done(plane, "btree_warm");
    }
#ifdef E2E_TC143_HYBRID_SWITCH
    portable_full_cpu_barrier();
#else
    portable_barrier();
#endif

    uint64_t samples[BATCHES];
    uint64_t service_ticks = 0;
    uint64_t end_to_end_start = read_counter_serialized();
    int pressure_completed = 0;
    int pressure_target = portable_pressure_plane_target(plane);
#ifdef E2E_TC143_HYBRID_SWITCH
    /* Build the exact PCT footprint with real DSM stores on TimingSimpleCPU.
     * Once setup is complete, switch once and run every measured operation on
     * O3. This avoids unreliable repeated CPU handovers without fabricating
     * ResidentDir, H64, owner, or writeback state. */
    int setup_lane = cpu & 1;
    int setup_worker = plane + setup_lane * PORTABLE_PLANES;
    int worker_first = portable_weighted_worker_begin(setup_worker);
    int worker_last = portable_weighted_worker_end(setup_worker);
    pressure_target = worker_last - worker_first;
    for (int batch = 0; batch < BATCHES; ++batch) {
        int first = worker_first + (int)(((uint64_t)pressure_target *
            (uint64_t)batch) / BATCHES);
        int last = worker_first + (int)(((uint64_t)pressure_target *
            (uint64_t)(batch + 1)) / BATCHES);
        int batch_completed = 0;
        portable_emit_workload_progress(
            setup_worker, "batch_begin", batch, pressure_completed,
            pressure_target);
        for (int line = first; line < last; ++line) {
            dsm_store(0, portable_global_pressure(PRESSURE_BASE, line),
                      VALUE_BASE | 0x00800000u | (uint32_t)line);
            ++batch_completed;
            ++pressure_completed;
            if (batch_completed == 64 || batch_completed == 128 ||
                batch_completed == 256 || (batch_completed % 512) == 0)
                portable_emit_workload_progress(
                    setup_worker, "stores_progress", batch, pressure_completed,
                    pressure_target);
        }
        portable_emit_workload_progress(
            setup_worker, "stores_done", batch, pressure_completed,
            pressure_target);
        __asm__ volatile("dsb sy" ::: "memory");
        portable_full_cpu_barrier();
    }

    if (!primary) {
        _exit_program(0);
        return 0;
    }
    pressure_completed = portable_pressure_plane_target(plane);
    pressure_target = pressure_completed;

    portable_emit_switch_progress(plane, "to_o3_pre_syscall", BATCHES);
    portable_switch_cpu(node, cpu);
    portable_emit_switch_progress(plane, "to_o3_post_syscall", BATCHES);
    portable_switch_barrier();
    portable_emit_switch_progress(plane, "to_o3_post_barrier", BATCHES);

    for (int batch = 0; batch < BATCHES; ++batch) {
        uint64_t start = read_counter_serialized();
        for (int tx = 0; tx < TRANSACTIONS_PER_BATCH; ++tx) {
            int page = (tx * 17 + batch * 5) & 63;
            (void)dsm_load(0, root);
            (void)dsm_load(0, portable_line(internal, page >> 3));
            (void)dsm_load(0, portable_line(leaf, page));
            if ((tx & 3) == 0) {
                int update = tx >> 2;
                dsm_store(0, portable_line(record, update),
                          VALUE_BASE | ((uint32_t)plane << 16) |
                          ((uint32_t)batch << 8) | (uint32_t)update);
            } else {
                (void)dsm_load(0, portable_line(record, page));
            }
        }
        __asm__ volatile("dsb sy" ::: "memory");
        samples[batch] = read_counter_serialized() - start;
        service_ticks += samples[batch];
        portable_emit_workload_progress(
            plane, "measure_batch_done", batch, batch + 1, BATCHES);
        portable_switch_barrier();
    }
#else
    for (int batch = 0; batch < BATCHES; ++batch) {
        int first = portable_pressure_begin(batch);
        int last = portable_pressure_end(batch);
        int batch_completed = 0;
        portable_emit_workload_progress(
            plane, "batch_begin", batch, pressure_completed, pressure_target);
        for (int line = first + plane; line < last;
             line += PORTABLE_PLANES) {
            dsm_store(0, portable_global_pressure(PRESSURE_BASE, line),
                      VALUE_BASE | 0x00800000u | (uint32_t)line);
            ++batch_completed;
            ++pressure_completed;
            if (batch_completed == 64 || batch_completed == 128 ||
                batch_completed == 256 || (batch_completed % 512) == 0)
                portable_emit_workload_progress(
                    plane, "stores_progress", batch, pressure_completed,
                    pressure_target);
        }
        portable_emit_workload_progress(
            plane, "stores_done", batch, pressure_completed, pressure_target);
        __asm__ volatile("dsb sy" ::: "memory");
        portable_barrier();
        portable_emit_workload_progress(
            plane, "post_pressure_barrier", batch,
            pressure_completed, pressure_target);

        uint64_t start = read_counter_serialized();
        for (int tx = 0; tx < TRANSACTIONS_PER_BATCH; ++tx) {
            int page = (tx * 17 + batch * 5) & 63;
            (void)dsm_load(0, root);
            (void)dsm_load(0, portable_line(internal, page >> 3));
            (void)dsm_load(0, portable_line(leaf, page));
            if ((tx & 3) == 0) {
                int update = tx >> 2;
                dsm_store(0, portable_line(record, update),
                          VALUE_BASE | ((uint32_t)plane << 16) |
                          ((uint32_t)batch << 8) | (uint32_t)update);
            } else {
                (void)dsm_load(0, portable_line(record, page));
            }
            __asm__ volatile("dsb sy" ::: "memory");
        }
        portable_emit_workload_progress(
            plane, "pre_transaction_dsb", batch,
            pressure_completed, pressure_target);
        __asm__ volatile("dsb sy" ::: "memory");
        portable_emit_workload_progress(
            plane, "post_transaction_dsb", batch,
            pressure_completed, pressure_target);
        samples[batch] = read_counter_serialized() - start;
        service_ticks += samples[batch];
        portable_emit_workload_progress(
            plane, "batch_done", batch, pressure_completed, pressure_target);
        portable_barrier();
    }
#endif
    uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
    emit_phase_done(plane, "btree_pressure");
    portable_emit_results(plane, "db_btree_service", "db_btree_end_to_end",
                          "db_btree_batch_64ops", BATCHES * OPS_PER_BATCH,
                          service_ticks, end_to_end_ticks, samples, BATCHES);
    emit_phase_done(plane, "btree_transactions");

    for (int update = 0; update < 4; ++update) {
        uint32_t expected = VALUE_BASE | ((uint32_t)plane << 16) |
                            ((BATCHES - 1u) << 8) | (uint32_t)update;
        uint32_t got = dsm_load(0, portable_line(record, update));
        emit_read_val(plane, 0, expected, got, got == expected);
    }
    emit_phase_done(plane, "btree_verify");
    portable_barrier();
    _exit_program(0);
    return 0;
}
