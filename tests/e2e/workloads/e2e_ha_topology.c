#include "dsm_access.h"
#include "e2e_common.h"
#include "perf_latency.h"
#include "portable_large_workload.h"

#include <stdio.h>

#ifndef HA_TOPOLOGY_SCENARIO
#define HA_TOPOLOGY_SCENARIO 1
#endif

#define TOPO_BASE 0x700000u
#define ACCEPTANCE_OPS 16
#define PLANE_STRIDE 0x10000u

static inline int plane_node(int plane)
{
    return plane / NUM_SOCKETS;
}

static inline int plane_socket(int plane)
{
    return plane % NUM_SOCKETS;
}

static inline uint32_t plane_line(int plane, int operation)
{
    return TOPO_BASE + (uint32_t)plane * PLANE_STRIDE
        + (uint32_t)operation * 64u;
}

static inline uint32_t seed_value(int plane, int operation)
{
    return 0xB1000000u | ((uint32_t)plane << 8) | (uint32_t)operation;
}

static inline uint32_t handoff_value(int owner, int writer, int operation)
{
    return 0xB2000000u | ((uint32_t)owner << 12)
        | ((uint32_t)writer << 8) | (uint32_t)operation;
}

static void emit_validation(int plane, int errors)
{
    printf("{\"kind\":\"validation\",\"scenario\":\"HAT%02d\","
           "\"plane\":%d,\"node\":%d,\"socket\":%d,\"planes\":%d,"
           "\"errors\":%d}\n", HA_TOPOLOGY_SCENARIO, plane,
           plane_node(plane), plane_socket(plane), PORTABLE_PLANES, errors);
    fflush(stdout);
}

int main(int argc, char **argv)
{
    const int node = argc >= 2 ? parse_int(argv[1]) : 0;
    const int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if (!portable_is_primary(cpu)) {
        _exit_program(0);
        return 0;
    }

    const int plane = portable_plane(node, cpu);
    int errors = 0;
    portable_emit_meta(plane, "HA_TOPOLOGY");
    emit_timer_selftest(plane);

    if (HA_TOPOLOGY_SCENARIO == 1) {
        for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation)
            perf_store_complete_plane(
                node, portable_socket(cpu), plane_line(plane, operation),
                seed_value(plane, operation));
        portable_barrier();
#if L3_PRESSURE_TARGET_LINES > 0
        errors += l3_prepare_pressure(node, portable_socket(cpu), plane,
                                      L3_PRESSURE_BASE, 0);
        portable_barrier();
#endif

        const int home_node = (node + NUM_NODES - 1) % NUM_NODES;
        const int home = home_node * NUM_SOCKETS + portable_socket(cpu);
        uint64_t samples[ACCEPTANCE_OPS];
        uint64_t total_ticks = 0;
        for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation) {
            const uint64_t start = read_counter_serialized();
            const uint32_t actual = dsm_load_plane(
                home_node, portable_socket(cpu), plane_line(home, operation));
            samples[operation] = read_counter_serialized() - start;
            total_ticks += samples[operation];
            const uint32_t expected = seed_value(home, operation);
            emit_read_val(plane, home, expected, actual, actual == expected);
            errors += actual != expected;
        }
        emit_guest_timer(plane, "topology_remote_read", ACCEPTANCE_OPS,
                         total_ticks);
        emit_latency_summary(plane, "topology_remote_read", samples,
                             ACCEPTANCE_OPS);
    } else if (HA_TOPOLOGY_SCENARIO == 2) {
        for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation)
            perf_store_complete_plane(
                node, portable_socket(cpu), plane_line(plane, operation),
                seed_value(plane, operation));
        portable_barrier();
#if L3_PRESSURE_TARGET_LINES > 0
        errors += l3_prepare_pressure(node, portable_socket(cpu), plane,
                                      L3_PRESSURE_BASE, 0);
        portable_barrier();
#endif

        const int owner_node = (node + NUM_NODES - 1) % NUM_NODES;
        const int owner = owner_node * NUM_SOCKETS + portable_socket(cpu);
        uint64_t samples[ACCEPTANCE_OPS];
        uint64_t total_ticks = 0;
        for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation) {
            const uint32_t replacement = handoff_value(owner, plane, operation);
            const uint64_t start = read_counter_serialized();
            perf_store_complete_plane(
                owner_node, portable_socket(cpu), plane_line(owner, operation),
                replacement);
            samples[operation] = read_counter_serialized() - start;
            total_ticks += samples[operation];
        }
        emit_guest_timer(plane, "topology_ownership_handoff", ACCEPTANCE_OPS,
                         total_ticks);
        emit_latency_summary(plane, "topology_ownership_handoff", samples,
                             ACCEPTANCE_OPS);
        portable_barrier();

        const int writer_node = (node + 1) % NUM_NODES;
        const int writer = writer_node * NUM_SOCKETS + portable_socket(cpu);
        for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation) {
            const uint32_t expected = handoff_value(plane, writer, operation);
            const uint32_t actual = dsm_load_plane(
                node, portable_socket(cpu), plane_line(plane, operation));
            emit_read_val(plane, plane, expected, actual, actual == expected);
            errors += actual != expected;
        }
    } else if (HA_TOPOLOGY_SCENARIO == 3) {
        if (plane == 0) {
            for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation)
                perf_store_complete_plane(
                    0, 0, plane_line(0, operation),
                    0xB3000000u | (uint32_t)operation);
        }
        portable_barrier();

        for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation) {
            const uint32_t shared = dsm_load_plane(
                0, 0, plane_line(0, operation));
            errors += shared != (0xB3000000u | (uint32_t)operation);
        }
        portable_barrier();
#if L3_PRESSURE_TARGET_LINES > 0
        errors += l3_prepare_pressure(node, portable_socket(cpu), plane,
                                      L3_PRESSURE_BASE, 0);
        portable_barrier();
#endif

        const int writer = PORTABLE_PLANES - 1;
        uint64_t samples[ACCEPTANCE_OPS];
        uint64_t total_ticks = 0;
        if (plane == writer) {
            for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation) {
                const uint64_t start = read_counter_serialized();
                perf_store_complete_plane(
                    0, 0, plane_line(0, operation),
                    0xB3000000u | ((uint32_t)writer << 8)
                        | (uint32_t)operation);
                samples[operation] = read_counter_serialized() - start;
                total_ticks += samples[operation];
            }
            emit_guest_timer(plane, "topology_all_sharer_to_writer",
                             ACCEPTANCE_OPS, total_ticks);
            emit_latency_summary(plane, "topology_all_sharer_to_writer",
                                 samples, ACCEPTANCE_OPS);
        }
        portable_barrier();

        for (int operation = 0; operation < ACCEPTANCE_OPS; ++operation) {
            const uint32_t expected = 0xB3000000u
                | ((uint32_t)writer << 8) | (uint32_t)operation;
            const uint32_t actual = dsm_load_plane(
                0, 0, plane_line(0, operation));
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
