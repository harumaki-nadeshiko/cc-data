/* TC93: 8-node pairwise pingpong — 4 pairs simultaneously.
 * 4 independent pairs (0↔1, 2↔3, 4↔5, 6↔7) do DSM pingpong.
 * Verifies: concurrent cross-node pairs don't deadlock.
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

    int pair_home = (node_id & 1) ? (node_id ^ 1) : node_id;
    uint32_t off = 0x6600 + (uint32_t)(node_id & ~1) * 16u;
    uint32_t val = 0x93000000u | ((uint32_t)node_id << 8);

    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(node_id, off)));
    sync_wait(0xFF);

    uint32_t got;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(pair_home, off)));
    uint32_t expected = 0x93000000u | ((uint32_t)pair_home << 8);
    int ok = (got == expected);
    emit_read_val(node_id, pair_home, expected, got, ok);
    sync_wait(0xFF);
    _exit_program(ok ? 0 : 1);
    return 0;
}
