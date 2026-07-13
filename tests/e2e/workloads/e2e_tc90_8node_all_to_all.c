/* TC90: 8-node all-to-all basic DSM read/write smoke test.
 * Each node writes a unique sentinel to its own DSM segment,
 * then every other node reads it and verifies the value.
 * Verifies: 8-node full-mesh topology correctness, cross-node DSM routing.
 */
#include "e2e_common.h"

#define NUM_NODES 8
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xFFFFB8000000ULL  /* 0x1000000000000 - (8+1)*128MB */

static inline volatile uint32_t *dsm_addr(int home_node, uint32_t off)
{
    uint64_t va = DSM_VA_BASE + (uint64_t)home_node * SEG_SIZE + off;
    return (volatile uint32_t *)va;
}

static inline void dsm_store(int home_node, uint32_t off, uint32_t val)
{
    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(home_node, off)));
}

static inline uint32_t dsm_load(int home_node, uint32_t off)
{
    uint32_t v;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"(dsm_addr(home_node, off)));
    return v;
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    /* Only the primary CPU (cpu_index % 4 == 0) runs the workload.
     * Non-primary CPUs exit immediately, matching the pattern used by
     * other multi-CPU-per-node TCs (e.g. TC37). This prevents 4× barrier
     * traffic and a race condition where a late CPU thread arrives at the
     * barrier after release, starts a new generation, and deadlocks. */
    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    uint32_t sentinel = 0x90000000u | ((uint32_t)node_id << 8);
    uint32_t off = 0x6400;

    /* Each node writes its sentinel to its own DSM segment */
    dsm_store(node_id, off, sentinel);
    sync_wait(0xFF);  /* all 8 nodes must finish writing */

    int fail = 0;
    /* Each node reads every other node's DSM segment */
    for (int home = 0; home < NUM_NODES; home++) {
        uint32_t got = dsm_load(home, off);
        uint32_t expected = 0x90000000u | ((uint32_t)home << 8);
        emit_read_val(node_id, home, expected, got, got == expected);
        if (got != expected) fail++;
    }

    sync_wait(0xFF);
    _exit_program(fail ? 1 : 0);
    return 0;
}
