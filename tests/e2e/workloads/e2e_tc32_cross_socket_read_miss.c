/* TC32: dual-socket cross-socket read miss + latency marker. */
#include "e2e_common.h"

#define SEG_SIZE              0x8000000ULL
#define NUM_NODES             3
#define NUM_SOCKETS           2
#define DUAL_TOTAL_SEGS       (NUM_NODES * NUM_SOCKETS)
#define DSM_VA_BASE_DUAL      ((0xFFFFFFFFFFFFULL + 1) - (DUAL_TOTAL_SEGS + 1) * SEG_SIZE)

#define HOME_LOCAL_NODE       0
#define HOME_LOCAL_SOCKET     0
#define HOME_REMOTE_NODE      0
#define HOME_REMOTE_SOCKET    1
#define OFF_LOCAL             0x6000
#define OFF_REMOTE            0x6040

static inline volatile uint32_t *dsm_addr2(int home_node, int home_socket, uint32_t off)
{
    uint64_t seg = (uint64_t)home_node * NUM_SOCKETS + (uint64_t)home_socket;
    uint64_t va = DSM_VA_BASE_DUAL + seg * SEG_SIZE + off;
    return (volatile uint32_t *)va;
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

static inline uint64_t read_cntvct(void)
{
    uint64_t v;
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(v));
    return v;
}

static inline void emit_tc32_latency(uint64_t same, uint64_t cross)
{
    char buf[180]; int p = 0;
    char *s = (char *)"[TC32_LAT] same=";
    while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, (uint32_t)same);
    s = (char *)" cross="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, (uint32_t)cross);
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

    uint32_t lane_off = 0x6400 + (uint32_t)(node_id * 4 + lane) * 64u;
    dsm_store2(0, lane & 1, lane_off, 0x32000000u | ((uint32_t)node_id << 8) | lane);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    emit_e2e_meta(node_id, "TC32");

    int fail = 0;
    uint32_t local_v = 0x3200AA01u;
    uint32_t remote_v = 0x3200BB02u;

    if (node_id == 0) dsm_store2(HOME_LOCAL_NODE, HOME_LOCAL_SOCKET, OFF_LOCAL, local_v);
    if (node_id == 1) dsm_store2(HOME_REMOTE_NODE, HOME_REMOTE_SOCKET, OFF_REMOTE, remote_v);
    sync_wait(0b111);

    if (node_id == 0) {
        volatile uint32_t sink = 0;
        uint64_t t0 = read_cntvct();
        for (int i = 0; i < 256; i++) sink ^= dsm_load2(HOME_LOCAL_NODE, HOME_LOCAL_SOCKET, OFF_LOCAL);
        uint64_t t1 = read_cntvct();
        uint64_t t2 = read_cntvct();
        for (int i = 0; i < 256; i++) sink ^= dsm_load2(HOME_REMOTE_NODE, HOME_REMOTE_SOCKET, OFF_REMOTE);
        uint64_t t3 = read_cntvct();
        (void)sink;
        uint64_t same = (t1 - t0);
        uint64_t cross = (t3 - t2);
        uint32_t got = dsm_load2(HOME_REMOTE_NODE, HOME_REMOTE_SOCKET, OFF_REMOTE);
        emit_read_val(node_id, HOME_REMOTE_NODE, remote_v, got, got == remote_v);
        emit_tc32_latency(same, cross);
        if (got != remote_v) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
