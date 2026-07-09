/* TC84: Resident directory cacheline capacity test.
 * Writes many unique DSM lines, counts how many succeed before
 * the resident directory fills up.
 * Should be run with vanilla config (BF=0, no backstore) as baseline.
 */
#include "e2e_common.h"

#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xffff38000000ULL
#define TEST_LINES 65536

static inline volatile uint32_t *dsm_addr(int home_node, uint32_t off)
{
    return (volatile uint32_t *)(DSM_VA_BASE + (uint64_t)home_node * SEG_SIZE + off);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    if (node_id != 0) { sync_wait(0b111); _exit_program(0); return 0; }

    int success = 0; int fail = 0;
    char buf[128]; int p; char *s;
    for (int i = 0; i < TEST_LINES; i++) {
        uint32_t off = 0x1000 + (uint32_t)i * 64u;
        uint32_t val = 0x84000000u ^ (uint32_t)i;
        __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(0, off)));
        __asm__ volatile("dmb ish" : : : "memory");
        uint32_t got;
        __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr(0, off)));
        if (got == val) success++; else fail++;
    }

    p = 0; s = (char *)"[CAPACITY] success="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, success);
    s = (char *)" fail="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, fail);
    s = (char *)" total="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, TEST_LINES);
    buf[p++] = '\n'; _raw_write(buf, p);

    emit_read_val(node_id, 0, 0x84000000u, (uint32_t)success, fail < TEST_LINES/2);
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
