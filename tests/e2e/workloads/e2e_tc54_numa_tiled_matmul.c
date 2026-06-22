#include "dsm_access.h"
#include "e2e_common.h"

/* TC54: NUMA-aware tiled matmul (模拟版)
 * - A(行) 放在 home node0
 * - B(列) 放在 home node1
 * - C 结果放在 home node2
 */

#define A_HOME       0
#define B_HOME       1
#define C_HOME       2

#define A_BASE       0x6000
#define B_BASE       0x7000
#define C_BASE       0x8000
#define STRIDE       0x40

static inline uint32_t a_off(int r, int c) { return A_BASE + (uint32_t)(r * 2 + c) * STRIDE; }
static inline uint32_t b_off(int r, int c) { return B_BASE + (uint32_t)(r * 2 + c) * STRIDE; }
static inline uint32_t c_off(int r, int c) { return C_BASE + (uint32_t)(r * 2 + c) * STRIDE; }

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC54");
    int fail = 0;

    if (node_id == 0) {
        dsm_store(A_HOME, a_off(0, 0), 1u);
        dsm_store(A_HOME, a_off(0, 1), 2u);
        dsm_store(A_HOME, a_off(1, 0), 3u);
        dsm_store(A_HOME, a_off(1, 1), 4u);
    }
    if (node_id == 1) {
        dsm_store(B_HOME, b_off(0, 0), 5u);
        dsm_store(B_HOME, b_off(0, 1), 6u);
        dsm_store(B_HOME, b_off(1, 0), 7u);
        dsm_store(B_HOME, b_off(1, 1), 8u);
    }
    sync_wait(0b111);

    if (node_id == 0) {
        uint32_t a00 = dsm_load(A_HOME, a_off(0, 0));
        uint32_t a01 = dsm_load(A_HOME, a_off(0, 1));
        uint32_t b00 = dsm_load(B_HOME, b_off(0, 0));
        uint32_t b01 = dsm_load(B_HOME, b_off(0, 1));
        uint32_t b10 = dsm_load(B_HOME, b_off(1, 0));
        uint32_t b11 = dsm_load(B_HOME, b_off(1, 1));
        dsm_store(C_HOME, c_off(0, 0), a00 * b00 + a01 * b10);
        dsm_store(C_HOME, c_off(0, 1), a00 * b01 + a01 * b11);
    }
    if (node_id == 1) {
        uint32_t a10 = dsm_load(A_HOME, a_off(1, 0));
        uint32_t a11 = dsm_load(A_HOME, a_off(1, 1));
        uint32_t b00 = dsm_load(B_HOME, b_off(0, 0));
        uint32_t b01 = dsm_load(B_HOME, b_off(0, 1));
        uint32_t b10 = dsm_load(B_HOME, b_off(1, 0));
        uint32_t b11 = dsm_load(B_HOME, b_off(1, 1));
        dsm_store(C_HOME, c_off(1, 0), a10 * b00 + a11 * b10);
        dsm_store(C_HOME, c_off(1, 1), a10 * b01 + a11 * b11);
    }

    sync_wait(0b111);

    if (node_id == 2) {
        uint32_t c00 = dsm_load(C_HOME, c_off(0, 0));
        uint32_t c01 = dsm_load(C_HOME, c_off(0, 1));
        uint32_t c10 = dsm_load(C_HOME, c_off(1, 0));
        uint32_t c11 = dsm_load(C_HOME, c_off(1, 1));

        emit_read_val(node_id, C_HOME, 19u, c00, c00 == 19u);
        emit_read_val(node_id, C_HOME, 22u, c01, c01 == 22u);
        emit_read_val(node_id, C_HOME, 43u, c10, c10 == 43u);
        emit_read_val(node_id, C_HOME, 50u, c11, c11 == 50u);

        if (c00 != 19u || c01 != 22u || c10 != 43u || c11 != 50u) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
