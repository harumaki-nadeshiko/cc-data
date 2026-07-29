/* TC124 / P4: owner/home/requester split for direct-forward.
 * Home is node1. Owner is node2. Requester is node0.  Baseline routes data
 * through home; optimized direct-forward can bypass owner->home->requester.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define LINES 32
#define BASE  0x12400000u

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC124");

    if (node_id == 2) {
        for (int i = 0; i < LINES; i++)
            dsm_store(1, (uint32_t)i * 64u, BASE | (uint32_t)i);
        emit_phase_done(2, "owner_node2");
    }
    sync_wait(0b111);

    if (node_id == 0) {
        uint64_t t0 = read_cntvct_el0();
        for (int i = 0; i < LINES; i++) {
            uint32_t exp = BASE | (uint32_t)i;
            uint32_t got = dsm_load(1, (uint32_t)i * 64u);
            if ((i % 8) == 0) emit_read_val(0, 1, exp, got, got == exp);
        }
        emit_guest_timer(0, "direct_fwd_reads", LINES,
                         read_cntvct_el0() - t0);
        emit_phase_done(0, "requester_node0");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
