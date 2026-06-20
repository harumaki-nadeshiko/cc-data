/* TC34: dual-socket pingpong — Node0 writes DSM(0,0), Node1 writes DSM(1,0).
 * With NUM_SOCKETS=2, DSM(0,0)=socket0 of node0, DSM(1,0)=socket0 of node1.
 * Different home_nodes exercise different UBCC directory planes.
 * Compile: aarch64-linux-gnu-gcc -DNUM_SOCKETS=2 -DNUM_NODES=3 */
#include "e2e_common.h"
#include "dsm_access.h"

#define A_HOME 0      /* DSM(node=0, socket=0) — UBCC plane 0 */
#define B_HOME 1      /* DSM(node=1, socket=0) — UBCC plane 1 */
#define OFFS   0x100
#define ROUNDS 8

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    emit_e2e_meta(node_id, "TC34");

    int fail = 0;
    for (int r = 0; r < ROUNDS; r++) {
        if (node_id == 0) dsm_store(A_HOME, OFFS, 0x340A0000u | (uint32_t)r);
        else if (node_id == 1) dsm_store(B_HOME, OFFS, 0x340B0000u | (uint32_t)r);
        else if ((r & 3) == 0) { (void)dsm_load(A_HOME, OFFS); (void)dsm_load(B_HOME, OFFS); }
        sync_wait(0b111);
    }

    if (node_id == 2) {
        uint32_t exp_a = 0x340A0000u | (uint32_t)(ROUNDS - 1);
        uint32_t exp_b = 0x340B0000u | (uint32_t)(ROUNDS - 1);
        uint32_t got_a = dsm_load(A_HOME, OFFS);
        uint32_t got_b = dsm_load(B_HOME, OFFS);
        emit_read_val(node_id, A_HOME, exp_a, got_a, got_a == exp_a);
        emit_read_val(node_id, B_HOME, exp_b, got_b, got_b == exp_b);
        if (got_a != exp_a || got_b != exp_b) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
