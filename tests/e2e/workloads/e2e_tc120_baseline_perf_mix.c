/* TC120: baseline-vs-optimized directory overflow performance mix.
 *
 * 3-node workload with four phases:
 *   A. populate home0 with more lines than the small ResidentDir set can hold
 *   B. remote reads from node1 create/reuse shared state after overflow
 *   C. node2 migrates ownership on a subset to create owner/home/requester split
 *   D. node1 rereads hot lines to expose naive forced-eviction misses
 *
 * Correctness is checked through sampled READ_VAL markers; performance is
 * measured from UBCC/ResidentDir stats and wall-clock/nsim logs by the runner.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define TC120_LINES 12
#define TC120_HOT   6
#define TC120_BASE  0x12000000u
#define CONFLICT_STRIDE 0x10000u

static uint32_t value_for(int i, int phase)
{
    return TC120_BASE | ((uint32_t)phase << 12) | (uint32_t)i;
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC120");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    if (node_id == 0) {
        for (int i = 0; i < TC120_LINES; i++) {
            emit_progress(0, "populate_before", i);
            dsm_store(0, (uint32_t)(i * CONFLICT_STRIDE), value_for(i, 1));
            emit_progress(0, "populate_after", i);
        }
        emit_phase_done(0, "populate");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int round = 0; round < 4; round++) {
            for (int i = 0; i < TC120_HOT; i++) {
                uint32_t got = dsm_load(0, (uint32_t)(i * CONFLICT_STRIDE));
                if (round == 0 && (i % 3) == 0) {
                    emit_read_val(1, 0, value_for(i, 1), got,
                                  got == value_for(i, 1));
                }
            }
        }
        emit_phase_done(1, "shared_hot_reads");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int i = 0; i < TC120_HOT; i += 2) {
            emit_progress(2, "migrate_before", i);
            dsm_store(0, (uint32_t)(i * CONFLICT_STRIDE), value_for(i, 2));
            emit_progress(2, "migrate_after", i);
        }
        emit_phase_done(2, "owner_migration");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 0; i < TC120_HOT; i++) {
            uint32_t expected = (i % 2) == 0 ? value_for(i, 2) : value_for(i, 1);
            uint32_t got = dsm_load(0, (uint32_t)(i * CONFLICT_STRIDE));
            if ((i % 3) == 0) {
                emit_read_val(1, 0, expected, got, got == expected);
            }
        }
        emit_phase_done(1, "tc120_done");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
