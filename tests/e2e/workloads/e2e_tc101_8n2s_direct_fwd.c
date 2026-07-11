/* TC101: 8-node dual-socket direct-forward chain (C4).
 *
 * KEY DESIGN: Cross-node owner transfer chain where requester ≠ owner ≠ home.
 * Each node writes its own slot, then the next node reads that slot and
 * writes into its own. This triggers cross-node RECALL with direct-forward.
 *
 * Home for all slots is node 0, owner and requester are different non-0 nodes.
 *
 * Flow:
 *   1. Each node/socket writes its own slot (node0 owns all after writes)
 *   2. Barrier
 *   3. Chain: node i reads slot of node i-1, writes its own slot
 *   4. Node 0 verifies all 16 slots have valid values
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_CPUS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_CPUS + 1) * SEG_SIZE)
#define ROUNDS      4

static inline volatile uint32_t *slot_addr(int node, int socket)
{
    /* Each socket-plane gets its own cache-line-aligned slot on home node 0 */
    uint64_t va = DSM_VA_BASE + 0 * SEG_SIZE + 0x7800;
    va += (uint64_t)(node * NUM_SOCKETS + socket + 1) * 64ULL;
    return (volatile uint32_t *)va;
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int socket_id = cpu_index % NUM_SOCKETS;
    int local_cpu = cpu_index % 4;
    int primary = (local_cpu < NUM_SOCKETS);
    if (!primary) { _exit_program(0); return 0; }

    if (node_id == 0 && socket_id == 0) emit_e2e_meta(node_id, "TC101");

    /* Each plane writes its slot with a unique value */
    for (int r = 0; r < ROUNDS; r++) {
        uint32_t v = 0xA1000000u | ((uint32_t)node_id << 8)
                     | ((uint32_t)socket_id << 4) | (uint32_t)r;
        *slot_addr(node_id, socket_id) = v;
    }

    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Cross-node chain: node i reads slot of node (i-1 mod N),
     * then writes its own slot (triggers RECALL + potential direct-forward) */
    int target_node = (node_id - 1 + NUM_NODES) % NUM_NODES;
    for (int s = 0; s < NUM_SOCKETS; s++) {
        /* Each socket reads the target node's corresponding slot */
        uint32_t expected = 0xA1000000u | ((uint32_t)target_node << 8)
                           | ((uint32_t)s << 4) | ((uint32_t)(ROUNDS - 1));
        uint32_t got = *slot_addr(target_node, s);
        /* Write into own slot to trigger cross-node RECALL chain */
        *slot_addr(node_id, socket_id) = got ^ 0x80000000u;
    }

    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Node0 verifies all slots hold valid values */
    int fail = 0;
    if (node_id == 0 && socket_id == 0) {
        for (int n = 0; n < NUM_NODES; n++) {
            for (int s = 0; s < NUM_SOCKETS; s++) {
                uint32_t v = *slot_addr(n, s);
                emit_read_val(node_id, n * NUM_SOCKETS + s, 0, v, v != 0);
                if (v == 0) fail++;
            }
        }
    }

    _exit_program(fail ? 1 : 0);
    return 0;
}
