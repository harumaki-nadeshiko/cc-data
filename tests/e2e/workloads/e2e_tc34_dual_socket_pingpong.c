/* TC34: dual-socket concurrent pingpong without cross-plane interference. */
#include "e2e_common.h"

#define SEG_SIZE              0x8000000ULL
#define NUM_NODES             3
#define NUM_SOCKETS           2
#define DUAL_TOTAL_SEGS       (NUM_NODES * NUM_SOCKETS)
#define DSM_VA_BASE_DUAL      ((0xFFFFFFFFFFFFULL + 1) - (DUAL_TOTAL_SEGS + 1) * SEG_SIZE)

#define A_HOME_NODE           0
#define A_HOME_SOCKET         0
#define B_HOME_NODE           0
#define B_HOME_SOCKET         1
#define A_OFF                 0x9000
#define B_OFF                 0x9040
#define ROUNDS                16

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

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int lane = cpu_index & 3;

    dsm_store2(0, lane & 1,
               0x9800 + (uint32_t)(node_id * 4 + lane) * 64u,
               0x34000000u | ((uint32_t)node_id << 8) | lane);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    emit_e2e_meta(node_id, "TC34");

    int fail = 0;
    for (int r = 0; r < ROUNDS; r++) {
        if (node_id == 0) {
            dsm_store2(A_HOME_NODE, A_HOME_SOCKET, A_OFF, 0x340A0000u | (uint32_t)r);
        } else if (node_id == 1) {
            dsm_store2(B_HOME_NODE, B_HOME_SOCKET, B_OFF, 0x340B0000u | (uint32_t)r);
        } else {
            if ((r & 7) == 0) {
                (void)dsm_load2(A_HOME_NODE, A_HOME_SOCKET, A_OFF);
                (void)dsm_load2(B_HOME_NODE, B_HOME_SOCKET, B_OFF);
            }
        }
        sync_wait(0b111);
    }

    if (node_id == 2) {
        uint32_t exp_a = 0x340A0000u | (uint32_t)(ROUNDS - 1);
        uint32_t exp_b = 0x340B0000u | (uint32_t)(ROUNDS - 1);
        uint32_t got_a = dsm_load2(A_HOME_NODE, A_HOME_SOCKET, A_OFF);
        uint32_t got_b = dsm_load2(B_HOME_NODE, B_HOME_SOCKET, B_OFF);
        emit_read_val(node_id, A_HOME_NODE, exp_a, got_a, got_a == exp_a);
        emit_read_val(node_id, B_HOME_NODE, exp_b, got_b, got_b == exp_b);
        if (got_a != exp_a || got_b != exp_b) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
