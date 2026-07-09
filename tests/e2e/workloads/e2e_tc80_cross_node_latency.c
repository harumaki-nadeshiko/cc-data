/* TC80: Cross-node read latency — single PA, repeated reads.
 * Node 1 writes a sentinel to node 0's DSM. Node 1 then reads it
 * 64 times, measuring round-trip latency with cntvct_el0.
 * Output: [LATENCY] tags with deltas in ticks.
 */
#include "e2e_common.h"

#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xffff38000000ULL

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
    uint32_t off = 0x6000;
    uint32_t val = 0x80000000u | ((uint32_t)node_id << 8);

    /* Node 0 writes, then barrier */
    if (node_id == 0)
        __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(0, off)));
    sync_wait(0b111);

    int fail = 0;
    /* Node 1 does repeated reads measuring latency */
    if (node_id == 1) {
        char buf[128];
        for (int i = 0; i < 64; i++) {
            uint64_t t0 = read_cntvct();
            uint32_t got;
            __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(0, off)));
            uint64_t t1 = read_cntvct();
            if (got != val) fail++;
            int p = 0;
            char *s = (char *)"[LATENCY] node="; while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, node_id);
            s = (char *)" iter="; while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, i);
            s = (char *)" delta="; while (*s) buf[p++] = *s++;
            p = fmt_hex(buf, p, (uint32_t)(t1 - t0));
            buf[p++] = '\n'; _raw_write(buf, p);
        }
    }

    sync_wait(0b111);
    uint32_t got;
    if (node_id == 1) {
        __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(0, off)));
        emit_read_val(node_id, 0, val, got, got == val);
    }
    _exit_program(fail ? 1 : 0);
    return 0;
}
