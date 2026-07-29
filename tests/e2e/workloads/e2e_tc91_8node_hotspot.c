/* TC91: 8-node hotspot — all nodes contend for the same DSM PA.
 * Node 0 writes a sentinel, then each node reads it once.
 * Verifies: cache-line ownership transfer under 8-way contention.
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
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    /* Single-arg sync_wait(mask) expects exactly ONE primary thread per node.
     * Non-primary CPUs exit immediately (same pattern as TC90/TC94); otherwise
     * all 4 CPUs enter the cross-node barrier and desynchronize the per-node
     * barrier generation, hanging the home node at the final barrier. */
    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    int hotspot_home = 0;
    uint32_t off = 0x6400;
    uint32_t val = 0x91000000u;

    if (node_id == 0)
        __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(hotspot_home, off)));

    sync_wait(0xFF);

    uint32_t got;
    uint64_t t0 = read_cntvct_el0();
    __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(hotspot_home, off)));
    int ok = (got == val);
    emit_guest_timer(node_id, "hotspot_read", 1, read_cntvct_el0() - t0);
    emit_read_val(node_id, hotspot_home, val, got, ok);
    sync_wait(0xFF);
    _exit_program(ok ? 0 : 1);
    return 0;
}
