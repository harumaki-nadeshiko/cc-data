/* TC148: bounded high-density ClearReq fault qualification.
 *
 * Node0 writes 32 cache lines homed at node1. Each ClearReq is matched by one
 * deterministic rule: 8 drop, 8 duplicate, 8 delay, and 8 reorder. Node1 then
 * reads every line, forcing recovery where a Clear was dropped and checking
 * convergence after delayed/reordered delivery.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 1
#define LINE_COUNT 32
#define BASE_OFF 0x14800
#define VALUE_BASE 0x14800000u

static inline uint64_t line_off(int i)
{
    return BASE_OFF + (uint64_t)i * 64;
}

static inline uint32_t line_value(int i)
{
    return VALUE_BASE | (uint32_t)i;
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC148");
    if (!primary) { _exit_program(0); return 0; }
    if (node_id == 2) { _exit_program(0); return 0; }

    if (node_id == 0) {
        for (int i = 0; i < LINE_COUNT; ++i) {
            dsm_store(HOME_NODE, line_off(i), line_value(i));
        }
        emit_phase_done(0, "fault_writes_issued");
    }
    sync_wait(0b11);

    if (node_id == 1) {
        uint64_t t0 = read_cntvct_el0();
        for (int i = 0; i < LINE_COUNT; ++i) {
            uint32_t expected = line_value(i);
            uint32_t actual = dsm_load(HOME_NODE, line_off(i));
            emit_read_val(1, HOME_NODE, expected, actual, actual == expected);
        }
        emit_guest_timer(1, "fault_qualification_recovery", LINE_COUNT,
                         read_cntvct_el0() - t0);
        emit_phase_done(1, "fault_reads_verified");
    }
    sync_wait(0b11);

    _exit_program(0);
    return 0;
}
