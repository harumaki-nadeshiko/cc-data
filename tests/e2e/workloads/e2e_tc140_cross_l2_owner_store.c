/* TC140: cross-L2 store to lines already owned by the same remote node. */
#include "dsm_access.h"
#include "perf_latency.h"

#define HOT_LINES 24
#define HOME_NODE 1
#define HOT_BASE 0x00600000u
#define SEM_OFFSET 0x00014000u
#define INITIAL_BASE 0x14000000u
#define FINAL_BASE 0x14010000u
#define WAIT_TIMEOUT_TICKS 251658240u

static uint32_t hot_offset(int i)
{
    return HOT_BASE + (uint32_t)i * 64u;
}

static int wait_for_sem(uint32_t expected)
{
    uint64_t start = read_counter_serialized();
    while (local_dram_load(SEM_OFFSET) != expected) {
        if (read_counter_serialized() - start > WAIT_TIMEOUT_TICKS)
            return 0;
        __asm__ volatile("dmb sy" ::: "memory");
    }
    return 1;
}

int main(int argc, char **argv)
{
    int node_id = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu_index = argc >= 3 ? parse_int(argv[2]) : 0;
    int lane = cpu_index % 4;
    int primary = lane == 0;

    if (node_id != 0 && !primary) { _exit_program(0); return 0; }
    if (node_id == 0 && lane != 0 && lane != 2) { _exit_program(0); return 0; }
    if (primary) {
        emit_e2e_meta(node_id, "TC140");
        emit_timer_selftest(node_id);
    }

    if (node_id == 0 && lane == 0) {
        local_dram_store(SEM_OFFSET, 0u);
        __asm__ volatile("dsb sy" ::: "memory");
        for (int i = 0; i < HOT_LINES; ++i)
            perf_store_complete(HOME_NODE, hot_offset(i),
                                INITIAL_BASE | (uint32_t)i);
        coherence_settle();
        local_dram_store(SEM_OFFSET, 1u);
        __asm__ volatile("dsb sy" ::: "memory");
        if (!wait_for_sem(2u)) {
            emit_phase_done(0, "cross_l2_timeout");
            _exit_program(1);
            return 1;
        }
        emit_phase_done(0, "cross_l2_store");
    } else if (node_id == 0 && lane == 2) {
        if (!wait_for_sem(1u)) {
            emit_phase_done(0, "cross_l2_timeout");
            _exit_program(1);
            return 1;
        }
        uint64_t samples[HOT_LINES];
        for (int i = 0; i < HOT_LINES; ++i) {
            uint64_t start = read_counter_serialized();
            perf_store_complete(HOME_NODE, hot_offset(i),
                                FINAL_BASE | (uint32_t)i);
            samples[i] = read_counter_serialized() - start;
        }
        emit_latency_summary(0, "cross_l2_owner_store", samples, HOT_LINES);
        local_dram_store(SEM_OFFSET, 2u);
        __asm__ volatile("dsb sy" ::: "memory");
    }

    if (primary) sync_wait(0b111);

    if (node_id == 2 && primary) {
        for (int i = 0; i < HOT_LINES; ++i) {
            uint32_t expected = FINAL_BASE | (uint32_t)i;
            uint32_t got = dsm_load(HOME_NODE, hot_offset(i));
            emit_read_val(2, HOME_NODE, expected, got, got == expected);
        }
        emit_phase_done(2, "verify_final");
    }
    if (primary) sync_wait(0b111);
    _exit_program(0);
    return 0;
}
