#ifndef PORTABLE_LARGE_WORKLOAD_H
#define PORTABLE_LARGE_WORKLOAD_H

#include "dsm_access.h"
#include "perf_latency.h"

#define PORTABLE_PLANES (NUM_NODES * NUM_SOCKETS)
#define PORTABLE_ALL_MASK ((1u << PORTABLE_PLANES) - 1u)

#ifndef L3_PRESSURE_LEVEL
#define L3_PRESSURE_LEVEL 0
#endif
#ifndef L3_PRESSURE_CACHE_LINES
#define L3_PRESSURE_CACHE_LINES 4096
#endif
#ifndef L3_PRESSURE_SETS
#define L3_PRESSURE_SETS 256
#endif
#ifndef L3_PRESSURE_SEED
#define L3_PRESSURE_SEED 0
#endif
#ifndef L3_PRESSURE_TARGET_LINES_OVERRIDE
#define L3_PRESSURE_TARGET_LINES_OVERRIDE -1
#endif
#if L3_PRESSURE_TARGET_LINES_OVERRIDE >= 0
#define L3_PRESSURE_TARGET_LINES L3_PRESSURE_TARGET_LINES_OVERRIDE
#define L3_PRESSURE_EFFECTIVE_PCT \
    ((L3_PRESSURE_TARGET_LINES * 100) / L3_PRESSURE_CACHE_LINES)
#else
#define L3_PRESSURE_TARGET_LINES \
    ((L3_PRESSURE_CACHE_LINES * L3_PRESSURE_LEVEL) / 100)
#define L3_PRESSURE_EFFECTIVE_PCT L3_PRESSURE_LEVEL
#endif
#define L3_PRESSURE_PRIVATE_CACHE_LINES 4096
#define L3_PRESSURE_LINES \
    (L3_PRESSURE_PRIVATE_CACHE_LINES + L3_PRESSURE_TARGET_LINES)
#define L3_PRESSURE_BASE 0x2000000u
#ifndef L3_DIRECTORY_PRESSURE_LINES
#define L3_DIRECTORY_PRESSURE_LINES 0
#endif
#define L3_DIRECTORY_PRESSURE_BASE 0x4000000u

static inline void l3_pressure_marker(int plane, const char *phase, int done)
{
    char b[224]; int p = 0; const char *s;
#define L3_APPEND_TEXT(text) do { s = (text); while (*s) b[p++] = *s++; } while (0)
#define L3_APPEND_INT(value) do { p = fmt_int(b, p, (int)(value)); } while (0)
    L3_APPEND_TEXT("[L3-PRESSURE] node="); L3_APPEND_INT(plane);
    L3_APPEND_TEXT(" level_pct="); L3_APPEND_INT(L3_PRESSURE_EFFECTIVE_PCT);
    L3_APPEND_TEXT(" target_lines_per_hnf=");
    L3_APPEND_INT(L3_PRESSURE_TARGET_LINES);
    L3_APPEND_TEXT(" generated_lines="); L3_APPEND_INT(L3_PRESSURE_LINES);
    L3_APPEND_TEXT(" private_cache_lines=");
    L3_APPEND_INT(L3_PRESSURE_PRIVATE_CACHE_LINES);
    L3_APPEND_TEXT(" source=local_private_writeback");
    L3_APPEND_TEXT(" cache_lines_per_hnf="); L3_APPEND_INT(L3_PRESSURE_CACHE_LINES);
    L3_APPEND_TEXT(" sets="); L3_APPEND_INT(L3_PRESSURE_SETS);
    L3_APPEND_TEXT(" seed="); L3_APPEND_INT(L3_PRESSURE_SEED);
    L3_APPEND_TEXT(" phase="); L3_APPEND_TEXT(phase);
    L3_APPEND_TEXT(" progress="); L3_APPEND_INT(done);
    b[p++] = '\n'; _raw_write(b, p);
#undef L3_APPEND_INT
#undef L3_APPEND_TEXT
}

static inline int l3_pressure_fill(int node, int socket, int plane,
                                   uint32_t base, int disjoint_tag)
{
#if L3_PRESSURE_TARGET_LINES > 0
    const uint32_t start = base + (uint32_t)disjoint_tag * 0x100000u;
    l3_pressure_marker(plane, "fill_begin", 0);
    for (int line = 0; line < L3_PRESSURE_LINES; ++line) {
        const uint32_t tag = (uint32_t)line / L3_PRESSURE_SETS;
        const uint32_t set = ((uint32_t)line + (uint32_t)L3_PRESSURE_SEED) %
            L3_PRESSURE_SETS;
        const uint32_t pressure_line = tag * L3_PRESSURE_SETS + set;
        local_dram_store(start + pressure_line * 64u,
                         0xD3000000u ^ ((uint32_t)plane << 16) ^
                         pressure_line);
        if ((line & 1023) == 1023)
            l3_pressure_marker(plane, "local_write", line + 1);
    }
    __asm__ volatile("dsb sy" ::: "memory");
    l3_pressure_marker(plane, "fill_done", L3_PRESSURE_LINES);
    return 0;
#else
    (void)node; (void)socket; (void)plane; (void)base; (void)disjoint_tag;
    return 0;
#endif
}

static inline void l3_directory_pressure_fill(int node, int socket, int plane)
{
#if L3_DIRECTORY_PRESSURE_LINES > 0
    l3_pressure_marker(plane, "directory_begin", 0);
    for (int line = 0; line < L3_DIRECTORY_PRESSURE_LINES; ++line) {
        dsm_store_plane(node, socket,
                        L3_DIRECTORY_PRESSURE_BASE + (uint32_t)line * 64u,
                        0xD4000000u ^ ((uint32_t)plane << 16) ^
                        (uint32_t)line);
        if ((line & 4095) == 4095)
            l3_pressure_marker(plane, "directory", line + 1);
    }
    __asm__ volatile("dsb sy" ::: "memory");
    l3_pressure_marker(plane, "directory_done",
                       L3_DIRECTORY_PRESSURE_LINES);
#else
    (void)node; (void)socket; (void)plane;
#endif
}

static inline int l3_prepare_pressure(int node, int socket, int plane,
                                      uint32_t base, int disjoint_tag)
{
    l3_directory_pressure_fill(node, socket, plane);
    if (L3_DIRECTORY_PRESSURE_LINES > 0)
        sync_wait(PORTABLE_ALL_MASK, NUM_SOCKETS);
    return l3_pressure_fill(node, socket, plane, base, disjoint_tag);
}

#ifndef PORTABLE_BATCHES
#define PORTABLE_BATCHES 32
#endif

#ifndef PORTABLE_PRESSURE_LINES
#define PORTABLE_PRESSURE_LINES 768
#endif

#ifndef PORTABLE_TARGET_FOOTPRINT_LINES
#define PORTABLE_TARGET_FOOTPRINT_LINES 0
#endif

#ifndef PORTABLE_NAIVE_CAPACITY_LINES
#define PORTABLE_NAIVE_CAPACITY_LINES 65536
#endif

#ifndef PORTABLE_PRESSURE_LEVEL_PCT
#define PORTABLE_PRESSURE_LEVEL_PCT 0
#endif

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

static inline int portable_pressure_begin(int batch)
{
    return (int)(((uint64_t)PORTABLE_PRESSURE_LINES * (uint64_t)batch) /
                 (uint64_t)PORTABLE_BATCHES);
}

static inline int portable_pressure_end(int batch)
{
    return (int)(((uint64_t)PORTABLE_PRESSURE_LINES *
                  (uint64_t)(batch + 1)) /
                 (uint64_t)PORTABLE_BATCHES);
}

static inline void portable_emit_pressure_config(int plane, int hot_lines)
{
    char b[384]; int p = 0; const char *s;
#define PORTABLE_APPEND_TEXT(text) \
    do { s = (text); while (*s) b[p++] = *s++; } while (0)
#define PORTABLE_APPEND_INT(value) \
    do { p = fmt_int(b, p, (int)(value)); } while (0)
    PORTABLE_APPEND_TEXT("[PORTABLE-PRESSURE] node=");
    PORTABLE_APPEND_INT(plane);
    PORTABLE_APPEND_TEXT(" planes=");
    PORTABLE_APPEND_INT(PORTABLE_PLANES);
    PORTABLE_APPEND_TEXT(" hot_lines=");
    PORTABLE_APPEND_INT(hot_lines);
    PORTABLE_APPEND_TEXT(" pressure_lines=");
    PORTABLE_APPEND_INT(PORTABLE_PRESSURE_LINES);
    PORTABLE_APPEND_TEXT(" total_unique_lines=");
    PORTABLE_APPEND_INT(hot_lines + PORTABLE_PRESSURE_LINES);
    PORTABLE_APPEND_TEXT(" naive_capacity_lines=");
    PORTABLE_APPEND_INT(PORTABLE_NAIVE_CAPACITY_LINES);
    PORTABLE_APPEND_TEXT(" target_footprint_lines=");
    PORTABLE_APPEND_INT(PORTABLE_TARGET_FOOTPRINT_LINES);
    PORTABLE_APPEND_TEXT(" pressure_level_pct=");
    PORTABLE_APPEND_INT(PORTABLE_PRESSURE_LEVEL_PCT);
    PORTABLE_APPEND_TEXT(" batches=");
    PORTABLE_APPEND_INT(PORTABLE_BATCHES);
    b[p++] = '\n';
    _raw_write(b, p);
#undef PORTABLE_APPEND_INT
#undef PORTABLE_APPEND_TEXT
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
