/* TC94: 8-node barrier stress — 8 rounds of sync_wait(0xFF).
 * Each round each node writes a monotonic counter, then barrier.
 * Verifies: 8-node barrier correctness over multiple rounds.
 */
#include "e2e_common.h"

#define NUM_NODES 8
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xFFFFB8000000ULL

static inline volatile uint32_t *dsm_addr(int home_node, uint32_t off)
{
    return (volatile uint32_t *)(DSM_VA_BASE + (uint64_t)home_node * SEG_SIZE + off);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    uint32_t off = 0x6700;

    int fail = 0;
    for (int round = 0; round < 8; round++) {
        uint32_t val = 0x94000000u | ((uint32_t)round << 16) | ((uint32_t)node_id << 8);
        __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(node_id, off)));
        sync_wait(0xFF);

        uint32_t got;
        __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(node_id, off)));
        if (got != val) fail++;
    }

    emit_read_val(node_id, node_id, 0x94000000u | (7u << 16) | ((uint32_t)node_id << 8),
                  (uint32_t)0, fail == 0);
    sync_wait(0xFF);
    _exit_program(fail ? 1 : 0);
    return 0;
}
