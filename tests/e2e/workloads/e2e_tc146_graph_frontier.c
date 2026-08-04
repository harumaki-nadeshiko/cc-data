/* TC146: graph frontier expansion with adjacency/property reuse and updates. */
#include "portable_large_workload.h"

#define BATCHES PORTABLE_BATCHES
#define OPS_PER_BATCH 64
#define HOT_LINES_PER_PLANE 192
#define DATA_BASE 0x00a00000u
#define PRESSURE_BASE 0x04000000u
#define VALUE_BASE 0x14600000u

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if (!portable_is_primary(cpu)) { _exit_program(0); return 0; }
    int plane = portable_plane(node, cpu);
    uint32_t shard = portable_shard(DATA_BASE, plane);
    uint32_t frontier = shard;
    uint32_t adjacency = shard + 0x2000u;
    uint32_t property = shard + 0x6000u;
    portable_emit_meta(plane, "TC146");
    portable_emit_pressure_config(
        plane, PORTABLE_PLANES * HOT_LINES_PER_PLANE);
    emit_timer_selftest(plane);

    PORTABLE_SERIAL_FOR_EACH_PLANE(plane, {
        for (int line = 0; line < 64; ++line) {
            dsm_store(0, portable_line(frontier, line), VALUE_BASE |
                      ((uint32_t)plane << 16) | 0x1000u | (uint32_t)line);
            dsm_store(0, portable_line(adjacency, line), VALUE_BASE |
                      ((uint32_t)plane << 16) | 0x2000u | (uint32_t)line);
            dsm_store(0, portable_line(property, line), VALUE_BASE |
                      ((uint32_t)plane << 16) | 0x3000u | (uint32_t)line);
        }
        __asm__ volatile("dsb sy" ::: "memory");
    });
    emit_phase_done(plane, "graph_seed");

    uint32_t warm_expected = VALUE_BASE | ((uint32_t)plane << 16) | 0x2000u;
    uint32_t warm = dsm_load(0, adjacency);
    emit_read_val(plane, 0, warm_expected, warm, warm == warm_expected);
    emit_phase_done(plane, "graph_frontier_warm");
    portable_barrier();

    uint64_t samples[BATCHES];
    uint64_t service_ticks = 0;
    uint64_t end_to_end_start = read_counter_serialized();
    for (int batch = 0; batch < BATCHES; ++batch) {
        int first = portable_pressure_begin(batch);
        int last = portable_pressure_end(batch);
        for (int line = first + plane; line < last;
             line += PORTABLE_PLANES)
            dsm_store(0, portable_global_pressure(PRESSURE_BASE, line),
                      VALUE_BASE | 0x00800000u | (uint32_t)line);
        __asm__ volatile("dsb sy" ::: "memory");
        portable_barrier();

        uint64_t start = read_counter_serialized();
        for (int vertex = 0; vertex < 16; ++vertex) {
            int id = (vertex * 13 + batch * 7) & 63;
            (void)dsm_load(0, portable_line(frontier, id));
            (void)dsm_load(0, portable_line(adjacency, id));
            (void)dsm_load(0, portable_line(adjacency, (id + 17) & 63));
            if ((vertex & 3) == 0) {
                int update = vertex >> 2;
                dsm_store(0, portable_line(property, update), VALUE_BASE |
                          ((uint32_t)plane << 16) |
                          ((uint32_t)batch << 8) | (uint32_t)update);
            } else {
                (void)dsm_load(0, portable_line(property, id));
            }
        }
        __asm__ volatile("dsb sy" ::: "memory");
        samples[batch] = read_counter_serialized() - start;
        service_ticks += samples[batch];
        portable_barrier();
    }
    uint64_t end_to_end_ticks = read_counter_serialized() - end_to_end_start;
    portable_emit_results(plane, "graph_service", "graph_end_to_end",
                          "graph_batch_64ops", BATCHES * OPS_PER_BATCH,
                          service_ticks, end_to_end_ticks, samples, BATCHES);
    emit_phase_done(plane, "graph_iterations");

    for (int update = 0; update < 4; ++update) {
        uint32_t expected = VALUE_BASE | ((uint32_t)plane << 16) |
                            ((BATCHES - 1u) << 8) | (uint32_t)update;
        uint32_t got = dsm_load(0, portable_line(property, update));
        emit_read_val(plane, 0, expected, got, got == expected);
    }
    emit_phase_done(plane, "graph_verify");
    portable_barrier();
    _exit_program(0);
    return 0;
}
