/* TC141 Correctness: spill shared-to-writer recovery after delayed writeback. */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOT_LINES 16
#define PRESSURE_LINES 192
#define HOT_BASE 0x00700000u
#define PRESSURE_BASE 0x06000000u
#define CONFLICT_STRIDE 0x10000u
#define INITIAL_BASE 0x14100000u
#define FINAL_BASE 0x14110000u

static uint32_t line_offset(uint32_t base, int i)
{
    return base + (uint32_t)(i & 3) * 64u +
           (uint32_t)(i >> 2) * CONFLICT_STRIDE;
}

int main(int argc, char **argv)
{
    int node_id = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu_index = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC141");

    if (node_id == 0) {
        for (int i = 0; i < HOT_LINES; ++i) {
            dsm_store(0, line_offset(HOT_BASE, i), INITIAL_BASE | (uint32_t)i);
            __asm__ volatile("dsb sy" ::: "memory");
        }
        emit_phase_done(0, "seed_hot");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 0; i < HOT_LINES; ++i) {
            uint32_t expected = INITIAL_BASE | (uint32_t)i;
            uint32_t got = dsm_load(0, line_offset(HOT_BASE, i));
            emit_read_val(1, 0, expected, got, got == expected);
        }
        emit_phase_done(1, "share_hot");
    }
    sync_wait(0b111);

    if (node_id == 0) {
        for (int i = 0; i < PRESSURE_LINES; ++i) {
            dsm_store(0, line_offset(PRESSURE_BASE, i),
                      INITIAL_BASE | 0x00800000u | (uint32_t)i);
            __asm__ volatile("dsb sy" ::: "memory");
        }
        emit_phase_done(0, "directory_pressure");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 0; i < HOT_LINES; ++i) {
            dsm_store(0, line_offset(HOT_BASE, i), FINAL_BASE | (uint32_t)i);
            __asm__ volatile("dsb sy" ::: "memory");
        }
        emit_phase_done(1, "shared_to_writer");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int i = 0; i < HOT_LINES; ++i) {
            uint32_t expected = FINAL_BASE | (uint32_t)i;
            uint32_t got = dsm_load(0, line_offset(HOT_BASE, i));
            emit_read_val(2, 0, expected, got, got == expected);
        }
        emit_phase_done(2, "verify_final");
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
