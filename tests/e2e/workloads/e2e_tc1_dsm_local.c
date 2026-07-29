/* E2E-TC1: Single-node local DSM read/write smoke test.
 *
 * Node0 stores 0xCAFE to DSM_0 offset=0, then loads it back.
 * Verifies the shortest path through CHI→EP→UBCC works.
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -O0 -g -I. -o e2e_tc1_dsm_local.elf e2e_tc1_dsm_local.c
 */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    int cpu_index = (argc >= 3) ? parse_int(argv[2]) : 0;

    if ((cpu_index % 4) != 0) {
        _exit_program(0);
        return 0;
    }

    emit_e2e_meta(node_id, "TC1");

    /* Only Node0 participates */
    if (node_id != 0) {
        emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    uint32_t val = 0xCAFE;

    emit_before_wr(node_id, 0, val);
    dsm_store(0, 0, val);
    emit_after_wr(node_id, 0, val);

    emit_before_rd(node_id, 0);
    uint32_t got = dsm_load(0, 0);

    int match = (got == val);
    emit_read_val(node_id, 0, val, got, match);

    emit_phase_done(node_id, "done");
    _exit_program(match ? 0 : 1);
    return 0;
}
