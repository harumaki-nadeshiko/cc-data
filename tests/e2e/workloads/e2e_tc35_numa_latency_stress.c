/* TC35: 3-node NUMA mixed cross-socket/cross-node stress, verify progress. */
#include "e2e_common.h"

#define SEG_SIZE              0x8000000ULL
#define NUM_NODES             3
#define NUM_SOCKETS           2
#define DUAL_TOTAL_SEGS       (NUM_NODES * NUM_SOCKETS)
#define DSM_VA_BASE_DUAL      ((0xFFFFFFFFFFFFULL + 1) - (DUAL_TOTAL_SEGS + 1) * SEG_SIZE)

#define ROUNDS                192
#define BASE_OFF              0xA000
// DONE_OFF must be OUTSIDE the round address space [BASE_OFF, BASE_OFF+128×64).
// Previously 0xAF00 overlapped with round offsets (e.g. node2 r=102 hits 0xAF00),
// so a late round write could overwrite the done marker on the same cache line.
// Moved to 0x8000 (below BASE_OFF) so the two address spaces never collide.
#define DONE_OFF              0x8000

static inline volatile uint32_t *dsm_addr2(int home_node, int home_socket, uint32_t off)
{
    uint64_t seg = (uint64_t)home_node * NUM_SOCKETS + (uint64_t)home_socket;
    return (volatile uint32_t *)(DSM_VA_BASE_DUAL + seg * SEG_SIZE + off);
}

static inline void dsm_store2(int home_node, int home_socket, uint32_t off, uint32_t val)
{
    __asm__ volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr2(home_node, home_socket, off)));
}

static inline uint32_t dsm_load2(int home_node, int home_socket, uint32_t off)
{
    uint32_t v;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"(dsm_addr2(home_node, home_socket, off)));
    return v;
}

static inline void emit_tc35_progress(int node_id, int iter)
{
    char buf[128]; int p = 0;
    char *s = (char *)"[TC35_PROGRESS] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" iter="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, iter);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int lane = cpu_index & 3;

    /* 每 CPU 不同 line/不同 socket 模式，先并发触发。 */
    int home_n = 0;
    int home_s = lane & 1;
    uint32_t off = BASE_OFF + (uint32_t)(node_id * 4 + lane) * 64u;
    uint32_t v = 0x35000000u | ((uint32_t)node_id << 8) | lane;
    for (int i = 0; i < 64; i++) {
        dsm_store2(home_n, home_s, off, v ^ (uint32_t)i);
        (void)dsm_load2(home_n, home_s, off);
    }

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    emit_e2e_meta(node_id, "TC35");

    int fail = 0;
    for (int r = 0; r < ROUNDS; r++) {
        int hn = 0;
        int hs = (r ^ node_id) & 1;
        uint32_t roff = BASE_OFF + (uint32_t)((r * 13 + node_id * 7) % 128) * 64u;
        uint32_t val = 0x350A0000u | ((uint32_t)node_id << 12) | (uint32_t)r;
        dsm_store2(hn, hs, roff, val);
        (void)dsm_load2(hn, hs, roff);
        if ((r % 64) == 0) emit_tc35_progress(node_id, r);
    }

    dsm_store2(0, 0, DONE_OFF + (uint32_t)node_id * 64u, 0x35DD0000u | (uint32_t)node_id);
    sync_wait(0x3F);

    if (node_id == 0) {
        for (int n = 0; n < 3; n++) {
            uint32_t exp = 0x35DD0000u | (uint32_t)n;
            uint32_t got = dsm_load2(0, 0, DONE_OFF + (uint32_t)n * 64u);
            emit_read_val(node_id, n, exp, got, got == exp);
            if (got != exp) fail++;
        }
    }

    sync_wait(0x3F);
    _exit_program(fail ? 1 : 0);
    return 0;
}
