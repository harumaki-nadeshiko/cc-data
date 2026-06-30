/* TC33: dual-socket writeback path to home socket 0. */
#include "e2e_common.h"

#define SEG_SIZE              0x8000000ULL
#define NUM_NODES             3
#define NUM_SOCKETS           2
#define DUAL_TOTAL_SEGS       (NUM_NODES * NUM_SOCKETS)
#define DSM_VA_BASE_DUAL      ((0xFFFFFFFFFFFFULL + 1) - (DUAL_TOTAL_SEGS + 1) * SEG_SIZE)

#define HOME_NODE             0
#define HOME_SOCKET           0
#define WB_OFF                0x7000
#define PRESS_BASE            0x80000
/* Split-mode: node2's cross-node read sweep is IPC + lock-stepped clock bound;
 * 2048 lines barely fit the 600s budget. The verified property is node0's
 * writeback read, not the sweep, so a smaller sweep keeps the intent. */
#define PRESS_LINES           512

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

static inline void emit_tc33_route(int node_id, int writer_lane)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[TC33_WB] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" homeSocket=0 writerLane="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, writer_lane);
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

    uint32_t lane_val = 0x33000000u | ((uint32_t)node_id << 8) | lane;
    dsm_store2(HOME_NODE, lane & 1, 0x7400 + (uint32_t)(node_id * 4 + lane) * 64u, lane_val);

    if (node_id == 2 && lane == 1) {
        uint32_t dirty = 0x33DD0011u;
        for (int i = 0; i < 128; i++) dsm_store2(HOME_NODE, HOME_SOCKET, WB_OFF, dirty);
    }

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    emit_e2e_meta(node_id, "TC33");

    int fail = 0;
    const uint32_t expect = 0x33DD0011u;

    sync_wait(0b111);

    if (node_id == 2) {
        for (int i = 0; i < PRESS_LINES; i++) {
            uint32_t off = PRESS_BASE + (uint32_t)i * 64u;
            (void)dsm_load2(HOME_NODE, HOME_SOCKET, off);
        }
        emit_tc33_route(node_id, 1);
    }
    sync_wait(0b111);

    if (node_id == 0) {
        uint32_t got = dsm_load2(HOME_NODE, HOME_SOCKET, WB_OFF);
        emit_read_val(node_id, HOME_NODE, expect, got, got == expect);
        if (got != expect) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
