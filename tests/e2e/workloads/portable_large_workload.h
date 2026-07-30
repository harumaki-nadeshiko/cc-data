#ifndef PORTABLE_LARGE_WORKLOAD_H
#define PORTABLE_LARGE_WORKLOAD_H

#include "dsm_access.h"
#include "perf_latency.h"

#define PORTABLE_PLANES (NUM_NODES * NUM_SOCKETS)
#define PORTABLE_ALL_MASK ((1u << PORTABLE_PLANES) - 1u)

static inline int portable_socket(int cpu_index)
{
    return (cpu_index % 4) / 2;
}

static inline int portable_is_primary(int cpu_index)
{
    int local_cpu = cpu_index % 4;
    return (local_cpu % 2) == 0 && portable_socket(cpu_index) < NUM_SOCKETS;
}

static inline int portable_plane(int node_id, int cpu_index)
{
    return node_id * NUM_SOCKETS + portable_socket(cpu_index);
}

static inline void portable_emit_meta(int plane, const char *test_name)
{
    emit_e2e_meta(plane, test_name);
    emit_topology(plane, PORTABLE_PLANES);
}

static inline void portable_barrier(void)
{
    sync_wait(PORTABLE_ALL_MASK, NUM_SOCKETS);
}

#define PORTABLE_SERIAL_FOR_EACH_PLANE(plane_id, body) \
    do { \
        for (int portable_turn = 0; portable_turn < PORTABLE_PLANES; \
             ++portable_turn) { \
            if ((plane_id) == portable_turn) { body; } \
            portable_barrier(); \
        } \
    } while (0)

static inline uint32_t portable_line(uint32_t base, int line)
{
    return base + (uint32_t)line * 64u;
}

static inline uint32_t portable_shard(uint32_t base, int plane)
{
    return base + (uint32_t)plane * 0x10000u;
}

static inline uint32_t portable_pressure(uint32_t base, int plane, int line)
{
    return base + (uint32_t)plane * 0x200000u + (uint32_t)line * 64u;
}

static inline uint32_t portable_global_pressure(uint32_t base, int line)
{
    return base + (uint32_t)line * 64u;
}

static inline void portable_emit_results(int plane, const char *service_phase,
                                         const char *end_to_end_phase,
                                         const char *latency_phase,
                                         uint32_t operations,
                                         uint64_t service_ticks,
                                         uint64_t end_to_end_ticks,
                                         uint64_t *samples,
                                         uint32_t sample_count)
{
    emit_guest_timer(plane, service_phase, operations, service_ticks);
    emit_guest_timer(plane, end_to_end_phase, operations, end_to_end_ticks);
    emit_latency_summary(plane, latency_phase, samples, sample_count);
}

#endif
