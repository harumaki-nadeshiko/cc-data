/* TC94: 8-node barrier — single sync_wait(0xFF) round.
 * Node i writes sentinel to own DSM, barrier, then reads it back.
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
    uint32_t off = 0x6700;
    uint32_t val = 0x94000000u | ((uint32_t)node_id << 8);

    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(node_id, off)));
    sync_wait(0xFF);

    uint32_t got;
    uint64_t t0 = read_cntvct_el0();
    __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(node_id, off)));
    emit_guest_timer(node_id, "barrier_readback", 1, read_cntvct_el0() - t0);
    emit_read_val(node_id, node_id, val, got, got == val);
    _exit_program((got != val) ? 1 : 0);
    return 0;
}
