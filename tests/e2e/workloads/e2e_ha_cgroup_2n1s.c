/* Portable 2N1S adaptations of TC123/130/132/135/138/139. */
#include "dsm_access.h"
#include "e2e_common.h"
#include "perf_latency.h"

#include <stdio.h>

#ifndef HA_CGROUP_SCENARIO
#define HA_CGROUP_SCENARIO 1
#endif

#define C_HOT_LINES 24
#define C_PRESSURE_LINES 192
#define C_CONFLICT_STRIDE 0x10000u

#ifndef C224_ACTIVE_LINES
#define C224_ACTIVE_LINES 8192
#endif
#ifndef C224_PRESSURE_LINES
#define C224_PRESSURE_LINES 65536
#endif
#ifndef C224_READ_STRIDE
#define C224_READ_STRIDE 512
#endif
#define C224_STRIDE_SAMPLE_COUNT \
    (((C224_ACTIVE_LINES - 1) / C224_READ_STRIDE) + 1)
#define C224_SAMPLE_COUNT \
    (C224_STRIDE_SAMPLE_COUNT + \
     (((C224_ACTIVE_LINES - 1) % C224_READ_STRIDE) != 0))

static uint32_t conflict_offset(uint32_t base, int line)
{
    return base + (uint32_t)(line & 3) * 64u +
           (uint32_t)(line >> 2) * C_CONFLICT_STRIDE;
}

static void json_manifest(int node, const char *scenario,
                          uint32_t working_set_bytes, uint32_t iterations)
{
    printf("{\"kind\":\"manifest\",\"scenario\":\"%s\",\"mode\":\"cc\",\"implementation_status\":\"implemented_2n1s\",\"node\":%d,\"seed\":131,\"nodes\":2,\"sockets_per_node\":1,\"threads_per_node\":1,\"working_set_bytes\":%u,\"iterations\":%u,\"measurement_source\":\"guest_cntvct\",\"guest_visible\":true}\n",
           scenario, node, working_set_bytes, iterations);
    fflush(stdout);
}

static void json_validation(int node, const char *scenario, int errors)
{
    printf("{\"kind\":\"validation\",\"scenario\":\"%s\",\"mode\":\"cc\",\"node\":%d,\"seed\":131,\"errors\":%d}\n",
           scenario, node, errors);
    fflush(stdout);
}

static void json_c132_config(int node)
{
    printf("{\"kind\":\"workload_config\",\"scenario\":\"C132-HA\",\"node\":%d,\"active_lines\":%u,\"pressure_lines\":%u,\"read_stride\":%u,\"sample_count\":%u}\n",
           node, C224_ACTIVE_LINES, C224_PRESSURE_LINES, C224_READ_STRIDE,
           C224_SAMPLE_COUNT);
    fflush(stdout);
}

static void run_c123(int node, int *fail)
{
    const uint32_t hot_base = 0x00200000u;
    const uint32_t pressure_base = 0x01000000u;
    const uint32_t initial = 0xC1230000u;
    const uint32_t final = 0xC1238000u;
    uint64_t samples[4];

    if (node == 0) {
        for (int i = 0; i < 16; ++i)
            perf_store_complete(0, hot_base + (uint32_t)i * 64u,
                                initial | (uint32_t)i);
        emit_phase_done(0, "c123_seed");
    }
    sync_wait(0x3);
    if (node == 1) {
        for (int i = 0; i < 16; ++i)
            if (dsm_load(0, hot_base + (uint32_t)i * 64u) !=
                (initial | (uint32_t)i))
                (*fail)++;
        emit_phase_done(1, "c123_share");
    }
    sync_wait(0x3);
    if (node == 0) {
        for (int i = 0; i < 96; ++i)
            perf_store_complete(0, pressure_base + (uint32_t)i * 64u,
                                initial | 0x4000u | (uint32_t)i);
        emit_phase_done(0, "c123_pressure");
    }
    sync_wait(0x3);
    if (node == 1) {
        for (int update = 0; update < 4; ++update) {
            int line = update * 4;
            uint64_t start = read_counter_serialized();
            perf_store_complete(0, hot_base + (uint32_t)line * 64u,
                                final | (uint32_t)line);
            samples[update] = read_counter_serialized() - start;
        }
        emit_latency_summary(1, "c123_shared_to_writer_store", samples, 4);
        emit_guest_timer(1, "c123_shared_to_writer_store", 4,
                         samples[0] + samples[1] + samples[2] + samples[3]);
        emit_phase_done(1, "c123_writer");
    }
    sync_wait(0x3);
    if (node == 0) {
        for (int update = 0; update < 4; ++update) {
            int line = update * 4;
            uint32_t expected = final | (uint32_t)line;
            uint32_t got = dsm_load(0, hot_base + (uint32_t)line * 64u);
            emit_read_val(0, 0, expected, got, got == expected);
            if (got != expected) (*fail)++;
        }
        emit_phase_done(0, "c123_verify");
    }
    sync_wait(0x3);
}

static void run_c130(int node, int *fail)
{
    const uint32_t hot_base = 0x00300000u;
    const uint32_t pressure_base = 0x02000000u;
    const uint32_t value = 0xC1300000u;
    uint64_t first_samples[C_HOT_LINES];
    uint32_t first_values[C_HOT_LINES];

    if (node == 0) {
        for (int i = 0; i < C_HOT_LINES; ++i)
            perf_store_complete(0, conflict_offset(hot_base, i),
                                value | (uint32_t)i);
        emit_phase_done(0, "c130_seed");
    }
    sync_wait(0x3);
    if (node == 1) {
        for (int i = 0; i < C_HOT_LINES; ++i)
            if (dsm_load(0, conflict_offset(hot_base, i)) !=
                (value | (uint32_t)i))
                (*fail)++;
        emit_phase_done(1, "c130_share");
    }
    sync_wait(0x3);
    if (node == 0) {
        for (int i = 0; i < C_PRESSURE_LINES; ++i)
            perf_store_complete(0, conflict_offset(pressure_base, i),
                                value | 0x00800000u | (uint32_t)i);
        emit_phase_done(0, "c130_pressure");
    }
    sync_wait(0x3);
    if (node == 1) {
        uint64_t total_start = read_counter_serialized();
        for (int round = 0; round < 4; ++round) {
            for (int i = 0; i < C_HOT_LINES; ++i) {
                if (round == 0) {
                    uint64_t start = read_counter_serialized();
                    first_values[i] = dsm_load(0, conflict_offset(hot_base, i));
                    first_samples[i] = read_counter_serialized() - start;
                } else {
                    (void)dsm_load(0, conflict_offset(hot_base, i));
                }
            }
        }
        uint64_t total_ticks = read_counter_serialized() - total_start;
        emit_guest_timer(1, "c130_post_pressure_hot_reuse", 96, total_ticks);
        emit_latency_summary(1, "c130_first_revisit", first_samples,
                             C_HOT_LINES);
        for (int i = 0; i < C_HOT_LINES; ++i) {
            uint32_t expected = value | (uint32_t)i;
            emit_read_val(1, 0, expected, first_values[i],
                          first_values[i] == expected);
            if (first_values[i] != expected) (*fail)++;
        }
        emit_phase_done(1, "c130_reuse");
    }
    sync_wait(0x3);
}

static void run_c132(int node, int *fail)
{
    const uint32_t active_base = 0x00400000u;
    const uint32_t pressure_base = 0x02000000u;
    const uint32_t value = 0xC1320000u;
    uint32_t sampled[C224_SAMPLE_COUNT];

    if (node == 1) {
        for (int i = 0; i < C224_ACTIVE_LINES; ++i)
            perf_store_complete(0, active_base + (uint32_t)i * 64u,
                                value | (uint32_t)i);
        emit_phase_done(1, "c132_checkpoint_seed");
    }
    sync_wait(0x3);
    if (node == 0) {
        uint64_t end_to_end_start = read_counter_serialized();
        for (int i = 0; i < C224_PRESSURE_LINES; ++i)
            perf_store_complete(0, pressure_base + (uint32_t)i * 64u,
                                0xC13A0000u | (uint32_t)i);
        emit_phase_done(0, "c132_pressure");
        uint64_t service_start = read_counter_serialized();
        for (int i = 0; i < C224_ACTIVE_LINES; ++i) {
            uint32_t got = dsm_load(0, active_base + (uint32_t)i * 64u);
            if ((i % C224_READ_STRIDE) == 0)
                sampled[i / C224_READ_STRIDE] = got;
            if (i == C224_ACTIVE_LINES - 1 &&
                (i % C224_READ_STRIDE) != 0)
                sampled[C224_SAMPLE_COUNT - 1] = got;
        }
        uint64_t service_ticks = read_counter_serialized() - service_start;
        uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
        emit_guest_timer(0, "c132_checkpoint_recover", C224_ACTIVE_LINES,
                         service_ticks);
        emit_guest_timer(0, "c132_checkpoint_end_to_end", C224_ACTIVE_LINES,
                         end_to_end_ticks);
        for (int sample = 0; sample < C224_SAMPLE_COUNT; ++sample) {
            int line = sample < C224_STRIDE_SAMPLE_COUNT
                ? sample * C224_READ_STRIDE : C224_ACTIVE_LINES - 1;
            uint32_t expected = value | (uint32_t)line;
            emit_read_val(0, 0, expected, sampled[sample],
                          sampled[sample] == expected);
            if (sampled[sample] != expected) (*fail)++;
        }
        emit_phase_done(0, "c132_recover");
    }
    sync_wait(0x3);
}

static void run_c135(int node, int *fail)
{
    const uint32_t hot_base = 0x00500000u;
    const uint32_t pressure_base = 0x03000000u;
    const uint32_t value = 0xC1350000u;
    uint64_t samples[C_HOT_LINES];
    uint32_t values[C_HOT_LINES];

    if (node == 0)
        for (int i = 0; i < C_HOT_LINES; ++i)
            perf_store_complete(0, conflict_offset(hot_base, i),
                                value | (uint32_t)i);
    sync_wait(0x3);
    if (node == 1) {
        for (int i = 0; i < C_HOT_LINES; ++i) {
            uint32_t expected = value | (uint32_t)i;
            uint32_t got = dsm_load(0, conflict_offset(hot_base, i));
            emit_read_val(1, 0, expected, got, got == expected);
            if (got != expected) (*fail)++;
        }
        emit_phase_done(1, "c135_share");
    }
    sync_wait(0x3);
    if (node == 0) {
        for (int i = 0; i < C_PRESSURE_LINES; ++i)
            perf_store_complete(0, conflict_offset(pressure_base, i),
                                value | 0x00800000u | (uint32_t)i);
        emit_phase_done(0, "c135_pressure");
    }
    sync_wait(0x3);
    if (node == 1) {
        for (int i = 0; i < C_HOT_LINES; ++i) {
            uint64_t start = read_counter_serialized();
            values[i] = dsm_load(0, conflict_offset(hot_base, i));
            samples[i] = read_counter_serialized() - start;
        }
        emit_latency_summary(1, "c135_preserved_sharer_first_load", samples,
                             C_HOT_LINES);
        for (int i = 0; i < C_HOT_LINES; ++i) {
            uint32_t expected = value | (uint32_t)i;
            emit_read_val(1, 0, expected, values[i], values[i] == expected);
            if (values[i] != expected) (*fail)++;
        }
        emit_phase_done(1, "c135_revisit");
    }
    sync_wait(0x3);
}

static void run_c138(int node, int *fail)
{
    const uint32_t hot_base = 0x00600000u;
    const uint32_t pressure_base = 0x04000000u;
    const uint32_t initial = 0xC1380000u;
    const uint32_t final = 0xC1388000u;
    uint64_t samples[C_HOT_LINES];

    if (node == 1)
        for (int i = 0; i < C_HOT_LINES; ++i)
            perf_store_complete(0, hot_base + (uint32_t)i * 64u,
                                initial | (uint32_t)i);
    sync_wait(0x3);
    if (node == 0) {
        for (int i = 0; i < C_PRESSURE_LINES; ++i)
            perf_store_complete(0, pressure_base + (uint32_t)i * 64u,
                                initial | 0x00400000u | (uint32_t)i);
        emit_phase_done(0, "c138_pressure");
        for (int i = 0; i < C_HOT_LINES; ++i) {
            uint64_t start = read_counter_serialized();
            perf_store_complete(0, hot_base + (uint32_t)i * 64u,
                                final | (uint32_t)i);
            samples[i] = read_counter_serialized() - start;
        }
        emit_latency_summary(0, "c138_dirty_owner_handoff_store", samples,
                             C_HOT_LINES);
        emit_phase_done(0, "c138_handoff");
    }
    sync_wait(0x3);
    if (node == 1) {
        for (int i = 0; i < C_HOT_LINES; ++i) {
            uint32_t expected = final | (uint32_t)i;
            uint32_t got = dsm_load(0, hot_base + (uint32_t)i * 64u);
            emit_read_val(1, 0, expected, got, got == expected);
            if (got != expected) (*fail)++;
        }
        emit_phase_done(1, "c138_verify");
    }
    sync_wait(0x3);
}

static void run_c139(int node, int *fail)
{
    const uint32_t hot_base = 0x00700000u;
    const uint32_t summary_offset = 0x00800000u;
    const uint32_t pressure_base = 0x05000000u;
    const uint32_t value = 0xC1390000u;
    uint64_t samples[16];

    if (node == 0)
        for (int i = 0; i < 16; ++i)
            perf_store_complete(0, conflict_offset(hot_base, i),
                                value | (uint32_t)i);
    sync_wait(0x3);
    if (node == 1) {
        for (int i = 0; i < 16; ++i)
            if (dsm_load(0, conflict_offset(hot_base, i)) !=
                (value | (uint32_t)i))
                (*fail)++;
        for (int i = 1; i < 16; i += 2)
            perf_store_complete(0, conflict_offset(hot_base, i),
                                value | (uint32_t)i);
        emit_phase_done(1, "c139_owner_hot");
    }
    sync_wait(0x3);
    if (node == 0) {
        for (int i = 0; i < C_PRESSURE_LINES; ++i)
            perf_store_complete(0, conflict_offset(pressure_base, i),
                                value | 0x00800000u | (uint32_t)i);
        emit_phase_done(0, "c139_pressure");
    }
    sync_wait(0x3);
    if (node == 1) {
        uint64_t total_start = read_counter_serialized();
        for (int batch = 0; batch < 16; ++batch) {
            uint64_t start = read_counter_serialized();
            for (int i = 0; i < 16; ++i) {
                if ((i & 1) == 0)
                    (void)dsm_load(0, conflict_offset(hot_base, i));
                else
                    dsm_store(0, conflict_offset(hot_base, i),
                              value | ((uint32_t)batch << 8) | (uint32_t)i);
            }
            __asm__ volatile("dsb sy" ::: "memory");
            samples[batch] = read_counter_serialized() - start;
        }
        uint64_t total_ticks = read_counter_serialized() - total_start;
        emit_guest_timer(1, "c139_mixed_batch_throughput", 256, total_ticks);
        emit_latency_summary(1, "c139_mixed_batch_16ops", samples, 16);
        emit_phase_done(1, "c139_batches");
    }
    sync_wait(0x3);
    if (node == 1) {
        uint32_t checksum = 0;
        for (int i = 1; i < 16; i += 2) {
            uint32_t expected = value | (15u << 8) | (uint32_t)i;
            uint32_t got = dsm_load(0, conflict_offset(hot_base, i));
            emit_read_val(1, 0, expected, got, got == expected);
            if (got != expected) (*fail)++;
            checksum ^= got;
        }
        perf_store_complete(0, summary_offset, checksum);
        emit_phase_done(1, "c139_verify");
    }
    sync_wait(0x3);
    if (node == 0) {
        uint32_t expected = 0;
        for (int i = 1; i < 16; i += 2)
            expected ^= value | (15u << 8) | (uint32_t)i;
        uint32_t got = dsm_load(0, summary_offset);
        emit_read_val(0, 0, expected, got, got == expected);
        if (got != expected) (*fail)++;
        emit_phase_done(0, "c139_summary_verify");
    }
    sync_wait(0x3);
}

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu % 4) != 0) { _exit_program(0); return 0; }
    int fail = 0;
    const char *scenario = "C123-HA";
    uint32_t working_set = (16u + 96u) * 64u;
    uint32_t iterations = 1;

    if (HA_CGROUP_SCENARIO == 2) {
        scenario = "C130-HA";
        working_set = (C_HOT_LINES + C_PRESSURE_LINES) * 64u;
        iterations = 4;
    } else if (HA_CGROUP_SCENARIO == 3) {
        scenario = "C132-HA";
        working_set = (C224_ACTIVE_LINES + C224_PRESSURE_LINES) * 64u;
        iterations = C224_ACTIVE_LINES;
    } else if (HA_CGROUP_SCENARIO == 4) {
        scenario = "C135-HA";
        working_set = (C_HOT_LINES + C_PRESSURE_LINES) * 64u;
        iterations = C_HOT_LINES;
    } else if (HA_CGROUP_SCENARIO == 5) {
        scenario = "C138-HA";
        working_set = (C_HOT_LINES + C_PRESSURE_LINES) * 64u;
        iterations = C_HOT_LINES;
    } else if (HA_CGROUP_SCENARIO == 6) {
        scenario = "C139-HA";
        working_set = (16u + C_PRESSURE_LINES) * 64u;
        iterations = 16;
    }

    emit_e2e_meta(node, scenario);
    emit_timer_selftest(node);
    json_manifest(node, scenario, working_set, iterations);
    if (HA_CGROUP_SCENARIO == 3)
        json_c132_config(node);
    if (HA_CGROUP_SCENARIO == 1) run_c123(node, &fail);
    else if (HA_CGROUP_SCENARIO == 2) run_c130(node, &fail);
    else if (HA_CGROUP_SCENARIO == 3) run_c132(node, &fail);
    else if (HA_CGROUP_SCENARIO == 4) run_c135(node, &fail);
    else if (HA_CGROUP_SCENARIO == 5) run_c138(node, &fail);
    else if (HA_CGROUP_SCENARIO == 6) run_c139(node, &fail);
    else fail++;
    json_validation(node, scenario, fail);
    _exit_program(fail ? 1 : 0);
    return 0;
}
