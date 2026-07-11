/* TC100: 8-node dual-socket batch RS performance test (C3).
 * 16 readers (2 per node × 8 nodes) repeatedly read the SAME cache line.
 * This exercises the G_S shared-read scenario where multiple RS requests
 * queue up at the home UBCC and benefit from C3 batch grant.
 *
 * Flow:
 *   1. Node 0 socket-0 writes "seed" value to establish initial data
 *   2. Barrier — all nodes arrive
 *   3. All 16 primary CPUs read the same line 2 times each
 *   4. Node 0 socket-0 reads done marker (same line re-used)
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_CPUS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_CPUS + 1) * SEG_SIZE)
#define TARGET_OFF  0x1000
#define READ_ROUNDS 2

static inline volatile uint32_t *shared_line(void)
{
    uint64_t va = DSM_VA_BASE + 0 * SEG_SIZE + TARGET_OFF;
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

    int is_main_cpu = (node_id == 0 && socket_id == 0);
    if (is_main_cpu) emit_e2e_meta(node_id, "TC100");

    /* Phase 1: Node 0 socket-0 writes seed value, then barrier */
    if (is_main_cpu) {
        *shared_line() = 0xCAFE0000u;
    }
    if (is_main_cpu) emit_phase_done(node_id, "phase1_write");
    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Phase 2: All 16 readers read the same cache line, then node 0 verifies */
    for (int r = 0; r < READ_ROUNDS; r++) {
        uint32_t v = *shared_line();
        __asm__ volatile("" : : "r"(v) : "memory");
    }

    int fail = 0;
    if (is_main_cpu) {
        uint32_t got = *shared_line();
        uint32_t expected = 0xCAFE0000u;
        emit_read_val(node_id, 0, expected, got, got == expected);
        if (got != expected) fail++;
    }

    if (is_main_cpu) emit_phase_done(node_id, "phase2_done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
