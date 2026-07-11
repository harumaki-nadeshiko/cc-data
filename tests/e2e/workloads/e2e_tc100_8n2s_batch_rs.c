/* TC100: 8-node dual-socket batch RS grant stress test (C3).
 *
 * KEY DESIGN: All 16 primary CPUs read the SAME cache line simultaneously
 * after a barrier. This triggers N RS requests arriving at the home UBCC
 * while a live outstanding exists → queue → batch grant on replay.
 *
 * Flow:
 *   1. Node 0 socket-0 writes seed (0xCAFE0000u) to hot line, barrier
 *   2. ALL 16 CPUs read the hot line ROUNDS times simultaneously
 *   3. Barrier and node 0 verifies final value
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_CPUS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_CPUS + 1) * SEG_SIZE)
#define ROUNDS      8

static inline volatile uint32_t *hot_addr(void)
{
    return (volatile uint32_t *)(DSM_VA_BASE + 0 * SEG_SIZE + 0x1000);
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

    if (node_id == 0 && socket_id == 0) emit_e2e_meta(node_id, "TC100");

    /* Phase 1: node0 writes seed to establish data */
    if (node_id == 0 && socket_id == 0) {
        *hot_addr() = 0xCAFE0000u;
    }

    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Phase 2: ALL 16 CPUs read same line ROUNDS times simultaneously.
     * This triggers RS contention → batch grant via replayPendingRequesters. */
    uint32_t v = 0xBAAD;
    for (int r = 0; r < ROUNDS; r++) {
        v = *hot_addr();
        __asm__ volatile("" : : "r"(v) : "memory");
    }

    /* Use the read value to ensure pipeline drain */
    __asm__ volatile("" : : "r"(v) : "memory");

    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Phase 3: node0 verifies seed value survived all concurrent reads */
    int fail = 0;
    if (node_id == 0 && socket_id == 0) {
        uint32_t got = *hot_addr();
        emit_read_val(node_id, 0, 0xCAFE0000u, got, got == 0xCAFE0000u);
        if (got != 0xCAFE0000u) fail++;
    }

    sync_wait((1u << TOTAL_CPUS) - 1);
    _exit_program(fail ? 1 : 0);
    return 0;
}
