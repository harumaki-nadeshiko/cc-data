/* TC97: 8-node dual-socket ownership ping-pong.
 * 16 socket-plane primaries (8 nodes × 2 sockets) pass a token value around
 * in a ring.  Each step: acquire ownership (atomic write), then read it back.
 * The ring order is [(0,0), (0,1), (1,0), (1,1), ..., (7,0), (7,1)].
 * Validates ownership transfer across socket boundaries inside the home node
 * and the home UBCC's epoch/replayArmed grant pipeline.
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_SEGS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_SEGS + 1) * SEG_SIZE)
#define ROUNDS      8

static inline volatile uint32_t *token_addr(void)
{
    /* Token PA lives on home = node 0, socket = 0 */
    uint64_t va = DSM_VA_BASE + 0 * SEG_SIZE + 0x7000;
    return (volatile uint32_t *)va;
}

static inline int ring_pos(int node, int socket)
{
    return node * NUM_SOCKETS + socket;
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int socket_id = cpu_index % NUM_SOCKETS;
    int local_cpu = cpu_index % 4;
    int primary = (local_cpu < NUM_SOCKETS);
    int my_pos = ring_pos(node_id, socket_id);

    if (!primary) { _exit_program(0); return 0; }

    emit_e2e_meta(node_id, "TC97");

    /* Slot 0 writes the initial token */
    if (my_pos == 0) {
        *token_addr() = 0x97000000u;
    }
    sync_wait((1u << TOTAL_SEGS) - 1, NUM_SOCKETS);

    int fail = 0;
    uint64_t t0 = read_cntvct_el0();
    for (int r = 0; r < ROUNDS; r++) {
        int writer = r % TOTAL_SEGS;  /* ring position of this round's writer */
        uint32_t new_val = 0x97000000u | ((uint32_t)r << 8);

        if (my_pos == writer) {
            /* I am the writer: claim the token */
            *token_addr() = new_val;
        }
        sync_wait((1u << TOTAL_SEGS) - 1, NUM_SOCKETS);

        /* Everyone reads: did the writer's value land? */
        uint32_t got = *token_addr();
        if (got != new_val) {
            fail++;
        }
        sync_wait((1u << TOTAL_SEGS) - 1, NUM_SOCKETS);
    }
    emit_guest_timer(node_id, "pingpong_ownership_ring", ROUNDS,
                     read_cntvct_el0() - t0);

    /* Final verification: node 0 reads the token after all rounds */
    if (node_id == 0 && socket_id == 0) {
        uint32_t expected = 0x97000000u | ((uint32_t)(ROUNDS - 1) << 8);
        uint32_t got = *token_addr();
        emit_read_val(node_id, 0, expected, got, got == expected);
    }

    sync_wait((1u << TOTAL_SEGS) - 1, NUM_SOCKETS);
    _exit_program(fail ? 1 : 0);
    return 0;
}
