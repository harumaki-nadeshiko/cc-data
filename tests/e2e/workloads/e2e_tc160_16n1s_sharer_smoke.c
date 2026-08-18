/* TC160: true 16-node x 1-socket sharer and node-15 PA smoke. */
#include "dsm_access.h"
#include "e2e_common.h"

#if NUM_NODES != 16 || NUM_SOCKETS != 1
#error "TC160 requires NUM_NODES=16 and NUM_SOCKETS=1"
#endif

#define ALL_NODES_MASK 0xFFFFu
#define HOME_NODE 15
#define LINE_OFFSET 0x16000u
#define INITIAL_VALUE 0x1600CAFEu
#define UPDATED_VALUE 0x1600BEEFu

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    if (cpu_index % 4 != 0) _exit_program(0);

    emit_e2e_meta(node_id, "tc160_16n1s_sharer_smoke");
    emit_topology(node_id, NUM_NODES * NUM_SOCKETS);

    if (node_id == HOME_NODE) {
        dsm_store(HOME_NODE, LINE_OFFSET, INITIAL_VALUE);
        __asm__ volatile("dmb sy" ::: "memory");
    }
    sync_wait(ALL_NODES_MASK);

    uint32_t got = dsm_load(HOME_NODE, LINE_OFFSET);
    emit_read_val(node_id, HOME_NODE, INITIAL_VALUE, got,
                  got == INITIAL_VALUE);
    int fail = got != INITIAL_VALUE;
    sync_wait(ALL_NODES_MASK);

    if (node_id == 0) {
        dsm_store(HOME_NODE, LINE_OFFSET, UPDATED_VALUE);
        __asm__ volatile("dmb sy" ::: "memory");
    }
    sync_wait(ALL_NODES_MASK);

    got = dsm_load(HOME_NODE, LINE_OFFSET);
    emit_read_val(node_id, HOME_NODE, UPDATED_VALUE, got,
                  got == UPDATED_VALUE);
    fail += got != UPDATED_VALUE;
    sync_wait(ALL_NODES_MASK);

    _exit_program(fail ? 1 : 0);
    return 0;
}
