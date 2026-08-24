#include "portable_large_workload.h"

#include <stdio.h>

#ifndef HA_EXT_SCENARIO
#define HA_EXT_SCENARIO 1
#endif

#define EXT_BASE 0x900000u
#define EXT_LINES 32
#define EXT_ROUNDS 16
#define EXT_CATALOG_LINES 64
#define EXT_KV_LINES 8
#define EXT_BATCH_OPS 64

static inline uint32_t ext_line(uint32_t base, int line)
{
    return base + (uint32_t)line * 64u;
}

static inline uint32_t ext_plane_base(uint32_t base, int plane)
{
    return base + (uint32_t)plane * 0x10000u;
}

static void emit_validation(int plane, int errors)
{
    printf("{\"kind\":\"validation\",\"scenario\":\"HAE%02d\","
           "\"plane\":%d,\"planes\":%d,\"errors\":%d}\n",
           HA_EXT_SCENARIO, plane, PORTABLE_PLANES, errors);
    fflush(stdout);
}

int main(int argc, char **argv)
{
    const int node = argc >= 2 ? parse_int(argv[1]) : 0;
    const int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if (!portable_is_primary(cpu)) { _exit_program(0); return 0; }

    const int socket = portable_socket(cpu);
    const int plane = portable_plane(node, cpu);
    int errors = 0;
    portable_emit_meta(plane, "HA_EXTENDED");
    emit_timer_selftest(plane);

    if (HA_EXT_SCENARIO == 1) {
        if (plane == 0) {
            for (int line = 0; line < EXT_LINES; ++line)
                perf_store_complete_plane(
                    0, 0, ext_line(EXT_BASE, line),
                    0xC1000000u | (uint32_t)line);
        }
        portable_barrier();
        for (int line = 0; line < EXT_LINES; ++line)
            errors += dsm_load_plane(0, 0, ext_line(EXT_BASE, line)) !=
                (0xC1000000u | (uint32_t)line);
        portable_barrier();
#if L3_PRESSURE_TARGET_LINES > 0
        errors += l3_prepare_pressure(node, socket, plane, L3_PRESSURE_BASE, 0);
        portable_barrier();
#endif

        uint64_t first_sweep[EXT_LINES];
        uint64_t total_ticks = 0;
        int first_bad_sweep = -1;
        int first_bad_line = -1;
        uint32_t first_bad_expected = 0;
        uint32_t first_bad_actual = 0;
        for (int sweep = 0; sweep < 8; ++sweep) {
            for (int line = 0; line < EXT_LINES; ++line) {
                const uint64_t start = read_counter_serialized();
                const uint32_t value = dsm_load_plane(
                    0, 0, ext_line(EXT_BASE, line));
                const uint64_t ticks = read_counter_serialized() - start;
                total_ticks += ticks;
                if (sweep == 0) first_sweep[line] = ticks;
                const uint32_t expected = 0xC1000000u | (uint32_t)line;
                if (value != expected && first_bad_sweep < 0) {
                    first_bad_sweep = sweep;
                    first_bad_line = line;
                    first_bad_expected = expected;
                    first_bad_actual = value;
                }
                errors += value != expected;
            }
        }
        if (first_bad_sweep >= 0) {
            printf("[HA-MISMATCH] plane=%d scenario=1 sweep=%d line=%d "
                   "expected=%x actual=%x\n", plane, first_bad_sweep,
                   first_bad_line, first_bad_expected, first_bad_actual);
            fflush(stdout);
        }
        emit_guest_timer(plane, "clean_shared_read_service", 256, total_ticks);
        emit_latency_summary(plane, "clean_shared_first_sweep", first_sweep,
                             EXT_LINES);
        const uint32_t value = dsm_load_plane(0, 0, EXT_BASE);
        emit_read_val(plane, 0, 0xC1000000u, value, value == 0xC1000000u);
    } else if (HA_EXT_SCENARIO == 2) {
        if (plane == 0) {
            for (int key = 0; key < 8; ++key)
                perf_store_complete_plane(0, 0, ext_line(EXT_BASE, key),
                                          0xC2000000u | (uint32_t)key);
        }
        portable_barrier();
#if L3_PRESSURE_TARGET_LINES > 0
        errors += l3_prepare_pressure(node, socket, plane, L3_PRESSURE_BASE,
                                      HA_EXT_SCENARIO);
        portable_barrier();
#endif

        uint64_t read_samples[EXT_ROUNDS];
        uint64_t write_samples[EXT_ROUNDS];
        uint32_t write_count = 0;
        uint64_t read_ticks = 0;
        uint64_t write_ticks = 0;
        /* Publish a complete batch, synchronize once, then consume a complete
         * predecessor batch. This measures producer/consumer service rather
         * than sixteen per-line barriers. */
        for (int round = 0; round < EXT_ROUNDS; ++round) {
            const int writer = round % PORTABLE_PLANES;
            const int key = round & 7;
            const uint32_t expected = 0xC2000000u |
                ((uint32_t)(round + 1) << 8) | (uint32_t)writer;
            if (plane == writer) {
                const uint64_t start = read_counter_serialized();
                perf_store_complete_plane(0, 0, ext_line(EXT_BASE, key), expected);
                write_samples[write_count] = read_counter_serialized() - start;
                write_ticks += write_samples[write_count++];
            }
            portable_barrier();
            const uint64_t start = read_counter_serialized();
            const uint32_t actual = dsm_load_plane(0, 0, ext_line(EXT_BASE, key));
            read_samples[round] = read_counter_serialized() - start;
            read_ticks += read_samples[round];
            emit_read_val(plane, 0, expected, actual, actual == expected);
            errors += actual != expected;
            portable_barrier();
        }
        emit_guest_timer(plane, "hot_key_read_service", EXT_ROUNDS, read_ticks);
        emit_latency_summary(plane, "hot_key_read", read_samples, EXT_ROUNDS);
        emit_guest_timer(plane, "hot_key_write_service", write_count, write_ticks);
        emit_latency_summary(plane, "hot_key_write", write_samples, write_count);
    } else if (HA_EXT_SCENARIO == 3) {
        uint64_t load_samples[EXT_ROUNDS];
        uint64_t service_ticks = 0;
        uint64_t start;
        const int predecessor_node = (node + NUM_NODES - 1) % NUM_NODES;
        const int predecessor = predecessor_node * NUM_SOCKETS + socket;
#if L3_PRESSURE_TARGET_LINES > 0
        errors += l3_prepare_pressure(node, socket, plane, L3_PRESSURE_BASE,
                                      HA_EXT_SCENARIO);
        portable_barrier();
        for (int round = 0; round < EXT_ROUNDS; ++round) {
            const uint32_t offset = ext_line(
                ext_plane_base(EXT_BASE, plane), round);
            const uint32_t value = 0xC3000000u |
                ((uint32_t)plane << 8) | (uint32_t)round;
            start = read_counter_serialized();
            perf_store_complete_plane(node, socket, offset, value);
            service_ticks += read_counter_serialized() - start;
        }
        portable_barrier();
        for (int round = 0; round < EXT_ROUNDS; ++round) {
            const uint32_t predecessor_offset = ext_line(
                ext_plane_base(EXT_BASE, predecessor), round);
            const uint32_t expected = 0xC3000000u |
                ((uint32_t)predecessor << 8) | (uint32_t)round;
            start = read_counter_serialized();
            const uint32_t actual = dsm_load_plane(
                predecessor_node, socket, predecessor_offset);
            load_samples[round] = read_counter_serialized() - start;
            service_ticks += load_samples[round];
            emit_read_val(plane, predecessor, expected, actual, actual == expected);
            errors += actual != expected;
        }
        portable_barrier();
#else
        for (int round = 0; round < EXT_ROUNDS; ++round) {
            const uint32_t offset = ext_line(
                ext_plane_base(EXT_BASE, plane), round);
            const uint32_t value = 0xC3000000u |
                ((uint32_t)plane << 8) | (uint32_t)round;
            start = read_counter_serialized();
            perf_store_complete_plane(node, socket, offset, value);
            service_ticks += read_counter_serialized() - start;
            portable_barrier();

            const uint32_t predecessor_offset = ext_line(
                ext_plane_base(EXT_BASE, predecessor), round);
            const uint32_t expected = 0xC3000000u |
                ((uint32_t)predecessor << 8) | (uint32_t)round;
            start = read_counter_serialized();
            const uint32_t actual = dsm_load_plane(
                predecessor_node, socket, predecessor_offset);
            load_samples[round] = read_counter_serialized() - start;
            service_ticks += load_samples[round];
            emit_read_val(plane, predecessor, expected, actual, actual == expected);
            errors += actual != expected;
            portable_barrier();
        }
#endif
        emit_guest_timer(plane, "producer_consumer_service",
                         EXT_ROUNDS * 2, service_ticks);
        emit_latency_summary(plane, "producer_consumer_load", load_samples,
                             EXT_ROUNDS);
    } else if (HA_EXT_SCENARIO == 4) {
        const uint32_t token = EXT_BASE;
        const uint32_t slots = EXT_BASE + 0x10000u;
        if (plane == 0) perf_store_complete_plane(0, 0, token, 0);
        portable_barrier();
#if L3_PRESSURE_TARGET_LINES > 0
        errors += l3_prepare_pressure(node, socket, plane, L3_PRESSURE_BASE,
                                      HA_EXT_SCENARIO);
        portable_barrier();
#endif
        uint64_t samples[8];
        uint64_t total_ticks = 0;
        const uint64_t end_to_end_start = read_counter_serialized();
        for (int cycle = 0; cycle < 8; ++cycle) {
            for (int turn = 0; turn < PORTABLE_PLANES; ++turn) {
                if (plane == turn) {
                    const uint32_t expected =
                        (uint32_t)(cycle * PORTABLE_PLANES + turn);
                    errors += dsm_load_plane(0, 0, token) != expected;
                    const uint64_t start = read_counter_serialized();
                    perf_store_complete_plane(0, 0, token, expected + 1u);
                    samples[cycle] = read_counter_serialized() - start;
                    total_ticks += samples[cycle];
                    perf_store_complete_plane(
                        0, 0, ext_line(slots, plane),
                        0xC4000000u | ((uint32_t)cycle << 8) |
                            (uint32_t)plane);
                }
                portable_barrier();
            }
        }
        const uint64_t end_to_end_ticks =
            read_counter_serialized() - end_to_end_start;
        emit_guest_timer(plane, "queued_token_store", 8, total_ticks);
        emit_latency_summary(plane, "queued_token_store", samples, 8);
        if (plane == 0)
            emit_guest_timer(plane, "queued_token_end_to_end",
                             8 * PORTABLE_PLANES, end_to_end_ticks);
        const uint32_t expected = 0xC4000000u | (7u << 8) | (uint32_t)plane;
        const uint32_t actual = dsm_load_plane(0, 0, ext_line(slots, plane));
        emit_read_val(plane, 0, expected, actual, actual == expected);
        errors += actual != expected;
    } else if (HA_EXT_SCENARIO == 5) {
        const uint32_t catalog = EXT_BASE;
        const uint32_t kv = EXT_BASE + 0x20000u;
        if (plane == 0) {
            for (int line = 0; line < EXT_CATALOG_LINES; ++line)
                perf_store_complete_plane(
                    0, 0, ext_line(catalog, line),
                    0xC5000000u | (uint32_t)line);
        }
        PORTABLE_SERIAL_FOR_EACH_PLANE(plane, {
            for (int key = 0; key < EXT_KV_LINES; ++key)
                perf_store_complete_plane(
                    0, 0, ext_line(ext_plane_base(kv, plane), key),
                    0xC5100000u | ((uint32_t)plane << 8) | (uint32_t)key);
        });
        for (int line = 0; line < EXT_CATALOG_LINES; ++line)
            errors += dsm_load_plane(0, 0, ext_line(catalog, line)) !=
                (0xC5000000u | (uint32_t)line);
        portable_barrier();
#if L3_PRESSURE_TARGET_LINES > 0
        errors += l3_prepare_pressure(node, socket, plane, L3_PRESSURE_BASE,
                                      HA_EXT_SCENARIO);
        portable_barrier();
#endif

        uint64_t samples[EXT_ROUNDS];
        uint64_t service_ticks = 0;
        const uint64_t end_to_end_start = read_counter_serialized();
        for (int batch = 0; batch < EXT_ROUNDS; ++batch) {
            const uint64_t start = read_counter_serialized();
            for (int lookup = 0; lookup < 56; ++lookup) {
                const int key = (lookup % 8)
                    ? ((lookup * 9 + batch) & 15)
                    : ((lookup * 23 + batch * 5) & 63);
                const uint32_t actual = dsm_load_plane(
                    0, 0, ext_line(catalog, key));
                errors += actual != (0xC5000000u | (uint32_t)key);
            }
            for (int key = 0; key < EXT_KV_LINES; ++key)
                dsm_store_plane(
                    0, 0, ext_line(ext_plane_base(kv, plane), key),
                    0xC5200000u | ((uint32_t)plane << 16) |
                        ((uint32_t)batch << 8) | (uint32_t)key);
            __asm__ volatile("dsb sy" ::: "memory");
            samples[batch] = read_counter_serialized() - start;
            service_ticks += samples[batch];
            portable_barrier();
        }
        const uint64_t end_to_end_ticks =
            read_counter_serialized() - end_to_end_start;
        portable_emit_results(
            plane, "catalog_kv_service", "catalog_kv_end_to_end",
            "catalog_kv_batch_64ops", EXT_ROUNDS * EXT_BATCH_OPS,
            service_ticks, end_to_end_ticks, samples, EXT_ROUNDS);
        const uint32_t warm = dsm_load_plane(0, 0, catalog);
        emit_read_val(plane, 0, 0xC5000000u, warm, warm == 0xC5000000u);
        errors += warm != 0xC5000000u;
        for (int key = 0; key < EXT_KV_LINES; ++key) {
            const uint32_t expected = 0xC5200000u |
                ((uint32_t)plane << 16) | (15u << 8) | (uint32_t)key;
            const uint32_t actual = dsm_load_plane(
                0, 0, ext_line(ext_plane_base(kv, plane), key));
            emit_read_val(plane, 0, expected, actual, actual == expected);
            errors += actual != expected;
        }
    } else {
        errors++;
    }

    portable_barrier();
    emit_validation(plane, errors);
    _exit_program(errors ? 1 : 0);
    return 0;
}
