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
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    uint32_t off = 0x6700;
    uint32_t val = 0x94000000u | ((uint32_t)node_id << 8);

    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(node_id, off)));
    sync_wait(0xFF);

    uint32_t got;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(node_id, off)));
    emit_read_val(node_id, node_id, val, got, got == val);
    _exit_program((got != val) ? 1 : 0);
    return 0;
}
