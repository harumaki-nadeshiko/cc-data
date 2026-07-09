/* TC95: 8n2s per-socket barrier stress — TC12-analogue.
 * Mask = 0xFFFF = 16 bits for 8 nodes x 2 sockets.
 * Each iteration has 3 segments. Each socket's primary CPU emits a
 * [SYNC] marker then sync_wait with dynamic active threads.
 * Active threads per socket per iter = hash(node,socket,iter) % 4 + 1.
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_SEGS  (NUM_NODES * NUM_SOCKETS)
#define BARRIER_ALL ((1u << (NUM_NODES * NUM_SOCKETS)) - 1)  /* 0xFFFF for 8n2s */

#define HASH_MAGIC  0x9E3779B9U
#define ITERATIONS  3
#define SEGMENTS    3

static inline uint32_t any_hash(uint32_t a, uint32_t b)
{
    uint32_t h = a * HASH_MAGIC + b;
    h ^= h >> 16; h *= 0x85EBCA77U; h ^= h >> 13;
    return h;
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int socket_id = cpu_index % NUM_SOCKETS;
    int primary = (cpu_index % 4 == 0);

    if (!primary) { _exit_program(0); return 0; }

    for (int iter = 0; iter < ITERATIONS; iter++) {
        for (int seg = 1; seg <= SEGMENTS; seg++) {
            uint32_t val = any_hash((uint32_t)(node_id * NUM_SOCKETS + socket_id),
                                    (uint32_t)(iter * SEGMENTS + seg)) % 8;
            emit_sync_marker(node_id * NUM_SOCKETS + socket_id, iter, seg, val);
            uint32_t active = any_hash((uint32_t)node_id,
                                       (uint32_t)(socket_id * 1000 + iter)) % 4 + 1;
            sync_wait(BARRIER_ALL, (unsigned)active);
        }
    }
    emit_phase_done(node_id * NUM_SOCKETS + socket_id, "done");
    _exit_program(0);
    return 0;
}
