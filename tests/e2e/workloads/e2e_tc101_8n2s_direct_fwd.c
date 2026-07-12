/* TC101: 8n2s cross-node direct-forward chain (C4 benchmark).
 *
 * Unidirectional chain: node 7→6→5→4→3→2→1. Node 0 is only verifier
 * (never requester), so every RECALL has requester != home.
 *
 * Each iteration: node i reads node (i-1)'s slot (triggers RECALL from
 * node i-1 as owner, home=0), then writes its own slot. Rounds=64 means
 * ~448 C4-FORWARD events: 7 chain steps × 2 sockets × 32 rounds.
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_CPUS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_CPUS + 1) * SEG_SIZE)
#define ROUNDS      32

static inline volatile uint32_t *slot_addr(int node, int socket)
{
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

    /* Init: each node writes its own slot once to establish ownership */
    *slot_addr(node_id, socket_id) = 0xA1010000u | ((uint32_t)node_id << 4) | (uint32_t)socket_id;

    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Chain rounds: node i reads node (i-1)'s slot, writes own slot.
     * Node 0 only participates when i==0: reads node 7's slot but this
     * round's RECALL(owner=7,requester=0) does NOT trigger C4 (requester=home).
     * All other rounds (1→0, 2→1, ...) trigger C4: owner≠requester≠home.
     * To maximize C4 events, node 0 skips the chain; only nodes 1-7 chain. */
    for (int r = 0; r < ROUNDS; r++) {
        int prev = (node_id == 0) ? NUM_NODES - 1 : node_id - 1;
        if (node_id != 0) {
            /* Node 1-7: read prev's slot → C4 direct-forward
             * (owner=prev≠home=0, requester=node_id≠home=0) */
            uint32_t got = *slot_addr(prev, socket_id);
            *slot_addr(node_id, socket_id) = got ^ 0x80000000u;
        }
        /* Node 0: no-op in chain (keeps PDES sync balanced) */
        __asm__ volatile("" : : : "memory");
    }

    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Verify: node 0 reads all done markers */
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

    sync_wait((1u << TOTAL_CPUS) - 1);
    _exit_program(fail ? 1 : 0);
    return 0;
}
