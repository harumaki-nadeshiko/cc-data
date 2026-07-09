/* TC91: 8-node hotspot — all nodes contend for the same DSM PA.
 * Node 0 writes a sentinel, then all 8 nodes repeatedly read it.
 * Verifies: cache-line ownership transfer under 8-way contention.
 */
#include "e2e_common.h"

#define NUM_NODES 8
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xFFFFB8000000ULL  /* 0x1000000000000 - (8+1)*128MB */

static inline volatile uint32_t *dsm_addr(int home_node, uint32_t off)
{
    return (volatile uint32_t *)(DSM_VA_BASE + (uint64_t)home_node * SEG_SIZE + off);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    int hotspot_home = 0;
    uint32_t off = 0x6400;
    uint32_t val = 0x91000000u;

    if (node_id == 0)
        __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(hotspot_home, off)));

    sync_wait(0xFF);

    int fail = 0;
    for (int r = 0; r < 8; r++) {
        uint32_t got;
        __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(hotspot_home, off)));
        if (got != val) fail++;
    }
    emit_read_val(node_id, hotspot_home, val, got ? got : val, fail == 0);
    sync_wait(0xFF);
    _exit_program(fail ? 1 : 0);
    return 0;
}
