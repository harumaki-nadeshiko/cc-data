#include "dsm_access.h"
#include "e2e_common.h"
#include "perf_latency.h"

#include <stdio.h>

#ifndef HA_SCENARIO
#define HA_SCENARIO 1
#endif

// The 2N1S CC profile uses a 512-entry ResidentDir.  This exceeds it by 25%
// so HA05/HA06 exercise an actual capacity transition in both policies.
#define HA_CAPACITY_PRESSURE_LINES 640
#define HA10_CATALOG_LINES 16
#define HA10_BATCHES 8
#define HA10_PRESSURE_PER_BATCH 80
#define HA10_OPS_PER_BATCH 16

static inline uint64_t read_cntvct(void) { return read_cntvct_el0(); }

static void json_manifest(int node)
{
    uint32_t workingSetBytes = HA_SCENARIO == 10
        ? (HA10_CATALOG_LINES + HA10_BATCHES * HA10_PRESSURE_PER_BATCH) * 64u
        : 4096u;
    uint32_t iterations = HA_SCENARIO == 10 ? HA10_BATCHES : 1u;
    printf("{\"kind\":\"manifest\",\"scenario\":\"HA%02d\",\"mode\":\"cc\",\"node\":%d,\"seed\":131,\"nodes\":2,\"sockets_per_node\":1,\"threads_per_node\":1,\"working_set_bytes\":%u,\"iterations\":%u,\"measurement_source\":\"guest_cntvct\",\"guest_visible\":true}\n",
           HA_SCENARIO, node, workingSetBytes, iterations);
    fflush(stdout);
}

static void json_sample(int node, const char *phase, uint64_t ticks,
                        uint32_t operations)
{
    printf("{\"kind\":\"sample\",\"scenario\":\"HA%02d\",\"phase\":\"%s\",\"node\":%d,\"iteration\":0,\"latency_ticks\":%lu,\"operations\":%u,\"measurement_source\":\"guest_cntvct\"}\n",
           HA_SCENARIO, phase, node, ticks, operations);
    fflush(stdout);
    /* Also emit standardized GUEST-TIMER marker for E2E harness */
    emit_guest_timer(node, phase, operations, ticks);
}

static void json_iteration_sample(int node, const char *phase, int iteration,
                                  uint64_t ticks, uint32_t operations)
{
    printf("{\"kind\":\"sample\",\"scenario\":\"HA%02d\",\"phase\":\"%s\",\"node\":%d,\"iteration\":%d,\"latency_ticks\":%lu,\"operations\":%u,\"measurement_source\":\"guest_cntvct\"}\n",
           HA_SCENARIO, phase, node, iteration, ticks, operations);
    fflush(stdout);
}

static void json_validation(int node, int errors)
{
    printf("{\"kind\":\"validation\",\"scenario\":\"HA%02d\",\"mode\":\"cc\",\"node\":%d,\"seed\":131,\"errors\":%d}\n",
           HA_SCENARIO, node, errors);
    fflush(stdout);
}

static uint32_t ha10_catalog_offset(uint32_t base, int line)
{
    return base + (uint32_t)line * 64u;
}

static uint32_t ha10_pressure_offset(uint32_t base, int line)
{
    return base + 0x100000u + (uint32_t)line * 64u;
}

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu % 4) != 0) { _exit_program(0); return 0; }
    const uint64_t mask = 0x3;
    const uint32_t off = 0x6000 + HA_SCENARIO * 0x100;
    int fail = 0;
    emit_e2e_meta(node, "HA_2N1S");
    emit_timer_selftest(node);
    json_manifest(node);
    if (HA_SCENARIO == 1) {
        if (node == 0) dsm_store(0, off, 0x101);
        sync_wait(mask);
        if (node == 0) {
            uint64_t start = read_cntvct();
            if (dsm_load(0, off) != 0x101) fail++;
            json_sample(node, "local_reuse", read_cntvct() - start, 1);
        }
    } else if (HA_SCENARIO == 2) {
        if (node == 0) dsm_store(0, off, 0x202);
        sync_wait(mask);
        if (node == 1) {
            uint64_t start = read_cntvct();
            if (dsm_load(0, off) != 0x202) fail++;
            json_sample(node, "remote_read", read_cntvct() - start, 1);
        }
    } else if (HA_SCENARIO == 3) {
        if (node == 0) dsm_store(0, off, 0x303);
        sync_wait(mask);
        if (node == 1) {
            uint64_t start = read_cntvct();
            dsm_store(0, off, 0x304);
            json_sample(node, "ownership_write", read_cntvct() - start, 1);
        }
        sync_wait(mask);
        if (node == 0) {
            uint64_t start = read_cntvct();
            if (dsm_load(0, off) != 0x304) fail++;
            json_sample(node, "ownership_readback", read_cntvct() - start, 1);
        }
    } else if (HA_SCENARIO == 4) {
        if (node == 0) dsm_store(0, off, 0x404);
        sync_wait(mask);
        {
            uint64_t start = read_cntvct();
            (void)dsm_load(0, off);
            json_sample(node, "shared_read", read_cntvct() - start, 1);
        }
        sync_wait(mask);
        if (node == 1) {
            uint64_t start = read_cntvct();
            dsm_store(0, off, 0x405);
            json_sample(node, "shared_to_writer", read_cntvct() - start, 1);
        }
        sync_wait(mask);
        if (node == 0) {
            uint64_t start = read_cntvct();
            if (dsm_load(0, off) != 0x405) fail++;
            json_sample(node, "writer_readback", read_cntvct() - start, 1);
        }
    } else if (HA_SCENARIO == 5) {
        if (node == 0) dsm_store(0, off, 0x505);
        sync_wait(mask);
        if (node == 1 && dsm_load(0, off) != 0x505) fail++;
        sync_wait(mask);
        if (node == 0) {
            for (uint32_t i = 0; i < HA_CAPACITY_PRESSURE_LINES; ++i)
                dsm_store(0, off + 0x1000 + i * 64, 0x5000 + i);
        }
        sync_wait(mask);
        if (node == 1) {
            uint64_t start = read_cntvct();
            for (uint32_t i = 0; i < 64; ++i) (void)dsm_load(0, off);
            json_sample(node, "first_revisit", read_cntvct() - start, 64);
            uint64_t value = dsm_load(0, off);
            if (value != 0x505) fail++;
        }
    } else if (HA_SCENARIO == 6) {
        if (node == 1) dsm_store(0, off, 0x606);
        sync_wait(mask);
        if (node == 0) {
            uint64_t start = read_cntvct();
            for (uint32_t i = 0; i < HA_CAPACITY_PRESSURE_LINES; ++i)
                dsm_store(0, off + 0x2000 + i * 64, 0x6000 + i);
            json_sample(node, "eviction_admission", read_cntvct() - start,
                        HA_CAPACITY_PRESSURE_LINES);
        }
        sync_wait(mask);
        if (node == 1) {
            uint64_t start = read_cntvct();
            for (uint32_t i = 0; i < 64; ++i) (void)dsm_load(0, off);
            json_sample(node, "first_revisit", read_cntvct() - start, 64);
            uint64_t value = dsm_load(0, off);
            if (value != 0x606) fail++;
        }
    } else if (HA_SCENARIO == 7) {
        if (node == 0) {
            uint64_t start = read_cntvct();
            for (uint32_t i = 0; i < 16; ++i) {
                dsm_store(0, off + i * 64, 0x7000 + i);
                sync_wait(mask);
                sync_wait(mask);
            }
            json_sample(node, "producer", read_cntvct() - start, 16);
        } else {
            uint64_t start = read_cntvct();
            for (uint32_t i = 0; i < 16; ++i) {
                sync_wait(mask);
                if (dsm_load(0, off + i * 64) != 0x7000 + i) fail++;
                sync_wait(mask);
            }
            json_sample(node, "consumer", read_cntvct() - start, 16);
        }
    } else if (HA_SCENARIO == 8) {
        uint64_t start = read_cntvct();
        for (uint32_t i = 0; i < 16; ++i) sync_wait(mask);
        json_sample(node, "barrier", read_cntvct() - start, 16);
        if (node == 0) {
            start = read_cntvct();
            for (uint32_t i = 0; i < 16; ++i) {
                dsm_store(0, off, 2 * i + 1);
                sync_wait(mask);
                sync_wait(mask);
                if (dsm_load(0, off) != 2 * i + 2) fail++;
            }
            json_sample(node, "seq_lock_handoff", read_cntvct() - start, 16);
        } else {
            start = read_cntvct();
            for (uint32_t i = 0; i < 16; ++i) {
                sync_wait(mask);
                if (dsm_load(0, off) != 2 * i + 1) fail++;
                dsm_store(0, off, 2 * i + 2);
                sync_wait(mask);
            }
            json_sample(node, "seq_lock_handoff", read_cntvct() - start, 16);
        }
    } else if (HA_SCENARIO == 9) {
        if (node == 0) {
            uint64_t start = read_cntvct();
            for (uint32_t i = 0; i < 64; ++i)
                dsm_store(0, off + (i % 16) * 64, 0x9000 + (i % 16));
            json_sample(node, "local_under_pressure", read_cntvct() - start, 64);
        } else {
            uint64_t start = read_cntvct();
            for (uint32_t i = 0; i < 16; ++i)
                dsm_store(0, off + 0x1000 + i * 64, 0x9100 + i);
            json_sample(node, "remote_pressure", read_cntvct() - start, 16);
        }
        sync_wait(mask);
        if (node == 0 && dsm_load(0, off) != 0x9000) fail++;
    } else if (HA_SCENARIO == 10) {
        const uint32_t catalogBase = off + 0x10000u;
        uint64_t batchSamples[HA10_BATCHES];

        if (node == 0) {
            for (int i = 0; i < HA10_CATALOG_LINES; ++i)
                perf_store_complete(0, ha10_catalog_offset(catalogBase, i),
                                    0xA000u + (uint32_t)i);
        }
        sync_wait(mask);

        if (node == 1) {
            for (int i = 0; i < HA10_CATALOG_LINES; ++i) {
                uint32_t expected = 0xA000u + (uint32_t)i;
                if (dsm_load(0, ha10_catalog_offset(catalogBase, i)) != expected)
                    fail++;
            }
            // Two update keys become node1-owned before capacity pressure.
            perf_store_complete(0, ha10_catalog_offset(catalogBase, 1), 0xA001u);
            perf_store_complete(0, ha10_catalog_offset(catalogBase, 3), 0xA003u);
        }
        sync_wait(mask);

        uint64_t totalTicks = 0;
        for (int batch = 0; batch < HA10_BATCHES; ++batch) {
            if (node == 0) {
                int pressureBase = batch * HA10_PRESSURE_PER_BATCH;
                for (int i = 0; i < HA10_PRESSURE_PER_BATCH; ++i) {
                    int pressureLine = pressureBase + i;
                    perf_store_complete(0,
                        ha10_pressure_offset(catalogBase, pressureLine),
                        0xA1000000u | (uint32_t)pressureLine);
                }
            }
            sync_wait(mask);

            if (node == 1) {
                uint64_t start = read_counter_serialized();
                // 14/16 operations are skewed reads over eight hot keys.
                for (int i = 0; i < 14; ++i) {
                    int key = (i * i + batch) & 7;
                    uint32_t expected = 0xA000u + (uint32_t)(key * 2);
                    if (dsm_load(0, ha10_catalog_offset(catalogBase, key * 2)) !=
                        expected)
                        fail++;
                }
                perf_store_complete(0, ha10_catalog_offset(catalogBase, 1),
                                    0xA100u | (uint32_t)batch);
                perf_store_complete(0, ha10_catalog_offset(catalogBase, 3),
                                    0xA300u | (uint32_t)batch);
                batchSamples[batch] = read_counter_serialized() - start;
                totalTicks += batchSamples[batch];
            }
            sync_wait(mask);
        }

        if (node == 1) {
            emit_latency_summary(1, "ha10_catalog_batch_16ops", batchSamples,
                                 HA10_BATCHES);
            for (int batch = 0; batch < HA10_BATCHES; ++batch)
                json_iteration_sample(1, "catalog_batch", batch,
                                      batchSamples[batch], HA10_OPS_PER_BATCH);
            json_sample(1, "catalog_useful_throughput", totalTicks,
                        HA10_BATCHES * HA10_OPS_PER_BATCH);
        }
        sync_wait(mask);

        if (node == 0) {
            uint32_t expected1 = 0xA100u | (HA10_BATCHES - 1u);
            uint32_t expected3 = 0xA300u | (HA10_BATCHES - 1u);
            uint32_t got1 = dsm_load(0, ha10_catalog_offset(catalogBase, 1));
            uint32_t got3 = dsm_load(0, ha10_catalog_offset(catalogBase, 3));
            emit_read_val(0, 0, expected1, got1, got1 == expected1);
            emit_read_val(0, 0, expected3, got3, got3 == expected3);
            if (got1 != expected1)
                fail++;
            if (got3 != expected3)
                fail++;
        }
    } else {
        if (node == 0) dsm_store(0, off, 0x701);
        sync_wait(mask);
        if (node == 1 && dsm_load(0, off) != 0x701) fail++;
        sync_wait(mask);
    }
    json_validation(node, fail);
    _exit_program(fail ? 1 : 0);
    return 0;
}
