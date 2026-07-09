/* TC95: 8-node dual-socket barrier stress — per-socket barrier bits.
 * Mask = 0xFFFF = 16 bits for 8 nodes x 2 sockets.
 * Each node's socket 0 writes a sentinel, sync_wait, socket 1 reads it.
 */
#include "e2e_common.h"

#define NUM_NODES 8
#define NUM_SOCKETS 2
#define SEG_SIZE 0x8000000ULL
#define TOTAL_SEGS (NUM_NODES * NUM_SOCKETS)
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_SEGS + 1) * (uint64_t)SEG_SIZE)
#define BARRIER_ALL 0xFFFF  /* bits 0-15 for 8 nodes x 2 sockets */

static inline volatile uint32_t *dsm_addr2(int home_node, int home_socket, uint32_t off)
{
    uint64_t seg = (uint64_t)home_node * NUM_SOCKETS + (uint64_t)home_socket;
    return (volatile uint32_t *)(DSM_VA_BASE + seg * SEG_SIZE + off);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    uint32_t off = 0x7000;

    /* Only socket-0 CPUs participate in barrier writes */
    int cpu_index = 0;
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int socket_id = cpu_index % NUM_SOCKETS;

    if (socket_id == 0) {
        uint32_t val = 0x95000000u | ((uint32_t)node_id << 8);
        __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr2(node_id, 0, off)));
    }

    sync_wait(BARRIER_ALL);

    if (socket_id == 1) {
        uint32_t val = 0x95000000u | ((uint32_t)node_id << 8);
        uint32_t got;
        __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr2(node_id, 0, off)));
        int ok = (got == val);
        emit_read_val(node_id, 0, val, got, ok);
        _exit_program(ok ? 0 : 1);
        return 0;
    }
    _exit_program(0);
    return 0;
}
