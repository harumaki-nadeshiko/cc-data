/* TC81: Cross-socket read latency — same node, 2 sockets.
 * Core@S0 reads DSM@S1 (cross-socket) and DSM@S0 (same-socket),
 * measuring both latencies with cntvct_el0.
 * Requires --2s topology (3 nodes x 2 sockets).
 */
#include "e2e_common.h"

#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xffff00000000ULL

static inline volatile uint32_t *dsm_addr2(int home_node, int home_socket, uint32_t off)
{
    uint64_t seg = (uint64_t)home_node * 2 + (uint64_t)home_socket;
    return (volatile uint32_t *)(DSM_VA_BASE + seg * SEG_SIZE + off);
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
    uint32_t off = 0x6100;

    if (node_id == 0) {
        __asm__ volatile("str %w0, [%1]" : : "r"(0x810000AAu), "r"(dsm_addr2(0, 0, off)));
        __asm__ volatile("str %w0, [%1]" : : "r"(0x810000BBu), "r"(dsm_addr2(0, 1, off)));
    }
    sync_wait(0b111);

    if (node_id == 0) {
        char buf[128]; int p; char *s; uint32_t got;
        for (int i = 0; i < 32; i++) {
            uint64_t t0 = read_cntvct();
            __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr2(0, 0, off)));
            uint64_t t1 = read_cntvct();
            p = 0; s = (char *)"[LATENCY] type=same iter="; while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, i); s = (char *)" delta="; while (*s) buf[p++] = *s++;
            p = fmt_hex(buf, p, (uint32_t)(t1 - t0)); buf[p++] = '\n'; _raw_write(buf, p);
        }
        for (int i = 0; i < 32; i++) {
            uint64_t t0 = read_cntvct();
            __asm__ volatile("ldr %w0, [%1]" : "=r"(got) : "r"(dsm_addr2(0, 1, off)));
            uint64_t t1 = read_cntvct();
            p = 0; s = (char *)"[LATENCY] type=cross iter="; while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, i); s = (char *)" delta="; while (*s) buf[p++] = *s++;
            p = fmt_hex(buf, p, (uint32_t)(t1 - t0)); buf[p++] = '\n'; _raw_write(buf, p);
        }
        emit_read_val(node_id, 0, 0x810000AAu, got, 1);
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
