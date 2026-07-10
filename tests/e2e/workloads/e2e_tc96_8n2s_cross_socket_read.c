/* TC96: 8-node dual-socket cross-socket read — TC32-analogue at 8n2s scale.
 * Each of 8 nodes has 2 socket planes (16 total).  Each socket's primary CPU
 * writes a unique sentinel to its own DSM sub-segment.  Then node 0's socket-0
 * CPU reads ALL 16 sub-segments — half of which are on the peer socket of each
 * remote node, exercising cross-socket NoC routing inside gem5.
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_SEGS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_SEGS + 1) * SEG_SIZE)

static inline int seg_idx(int home_node, int home_socket)
{
    return home_node * NUM_SOCKETS + home_socket;
}

static inline volatile uint32_t *dsm_addr2(int home_node, int home_socket, uint32_t off)
{
    uint64_t va = DSM_VA_BASE + (uint64_t)seg_idx(home_node, home_socket) * SEG_SIZE + off;
    return (volatile uint32_t *)va;
}

static inline void dsm_store2(int home_node, int home_socket, uint32_t off, uint32_t val)
{
    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr2(home_node, home_socket, off)));
}

static inline uint32_t dsm_load2(int home_node, int home_socket, uint32_t off)
{
    uint32_t v;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"(dsm_addr2(home_node, home_socket, off)));
    return v;
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int socket_id = cpu_index % NUM_SOCKETS;
    // cpu_index is GLOBAL (per-node offset = node_id * CPUS_PER_NODE).
    // CPUS_PER_NODE = DEFAULT_D * DEFAULT_L = 2 * 2 = 4.
    int local_cpu = cpu_index % 4;
    int primary = (local_cpu < NUM_SOCKETS);
    if (!primary) { _exit_program(0); return 0; }

    emit_e2e_meta(node_id, "TC96");

    uint32_t off = 0x6400;
    uint32_t val = 0x96000000u | ((uint32_t)node_id << 8) | (uint32_t)socket_id;

    /* Step 1: each socket-plane primary writes to its own sub-segment */
    dsm_store2(node_id, socket_id, off, val);
    sync_wait((1u << TOTAL_SEGS) - 1);  /* 0xFFFF for 8n2s */

    int fail = 0;
    /* Step 2: node 0 socket 0 reads every sub-segment */
    if (node_id == 0 && socket_id == 0) {
        for (int n = 0; n < NUM_NODES; n++) {
            for (int s = 0; s < NUM_SOCKETS; s++) {
                uint32_t expected = 0x96000000u | ((uint32_t)n << 8) | (uint32_t)s;
                uint32_t got = dsm_load2(n, s, off);
                emit_read_val(node_id, n * NUM_SOCKETS + s, expected, got, got == expected);
                if (got != expected) fail++;
            }
        }
    }

    sync_wait((1u << TOTAL_SEGS) - 1);
    _exit_program(fail ? 1 : 0);
    return 0;
}
