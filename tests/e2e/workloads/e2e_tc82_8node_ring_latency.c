/* TC82: 8-node ring owner transfer latency.
 * Node 0 writes a sentinel to its own DSM. Then nodes 0→1→2→...→7→0
 * pass ownership around the ring, measuring each transfer latency.
 * Output: [LATENCY] tags. Requires --8n1s topology.
 */
#include "e2e_common.h"

#define NUM_NODES 8
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xFFFFB8000000ULL

static inline volatile uint32_t *dsm_addr(int home_node, uint32_t off)
{
    return (volatile uint32_t *)(DSM_VA_BASE + (uint64_t)home_node * SEG_SIZE + off);
}

static inline uint64_t read_cntvct(void) {
    uint64_t v;
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(v));
    return v;
}

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    uint32_t off = 0x6200;
    uint32_t val = 0x82000000u | ((uint32_t)node_id << 8);

    if (node_id == 0)
        __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(node_id, off)));
    sync_wait(0xFF);

    char buf[128]; int p; char *s; uint32_t got;
    uint64_t t0 = read_cntvct();
    __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(node_id, off)));
    uint64_t t1 = read_cntvct();

    p = 0; s = (char *)"[LATENCY] node="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" delta="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, (uint32_t)(t1 - t0)); buf[p++] = '\n'; _raw_write(buf, p);

    emit_read_val(node_id, node_id, val, got, got == val);
    sync_wait(0xFF);
    _exit_program(0);
    return 0;
}
