/* TC92: 8-node butterfly data migration.
 * Node i writes a unique value to its own DSM, then node (i+1)%8 reads it.
 * Pattern: 0→1, 1→2, ..., 7→0. Verifies 8-node ring routing.
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
    /* sync_wait(mask) has one participant per node. */
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    uint32_t off = 0x6500;

    int dst = (node_id + 1) % NUM_NODES;
    uint32_t val = 0x92000000u | ((uint32_t)node_id << 8);
    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(node_id, off)));

    sync_wait(0xFF);

    uint32_t got;
    uint64_t t0 = read_cntvct_el0();
    __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(dst, off)));
    uint32_t expected = 0x92000000u | ((uint32_t)dst << 8);
    int ok = (got == expected);
    emit_guest_timer(node_id, "butterfly_read", 1, read_cntvct_el0() - t0);
    emit_read_val(node_id, dst, expected, got, ok);
    sync_wait(0xFF);
    _exit_program(ok ? 0 : 1);
    return 0;
}
