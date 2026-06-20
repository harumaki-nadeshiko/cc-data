/* TC34: Dual-socket smoke test — verifies num_sockets=2 topology instantiates.
 * Real cross-socket testing requires fixing dsm_access.h hardcoded single-socket
 * DSM_VA_BASE. See docs/recovery/fv_overview.md §P7 for details. */
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    if (primary) printf("[TC34_SMOKE] node=%d\n", node_id);
    emit_e2e_meta(node_id, "TC34");
    emit_phase_done(node_id, "done");
    _exit_program(0);
    return 0;
}
