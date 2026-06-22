#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE      0
#define ACC0_OFF       0x5100
#define ACC_STRIDE     0x40
#define ROUND_COUNT    30
#define INIT_BALANCE   100000u

static inline uint32_t acc_off(int idx)
{
    return ACC0_OFF + (uint32_t)idx * ACC_STRIDE;
}

static inline void do_transfer(int src, int dst, uint32_t amount)
{
    uint32_t s = dsm_load(HOME_NODE, acc_off(src));
    uint32_t d = dsm_load(HOME_NODE, acc_off(dst));
    if (s >= amount) {
        dsm_store(HOME_NODE, acc_off(src), s - amount);
        dsm_store(HOME_NODE, acc_off(dst), d + amount);
    }
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC51");
    int fail = 0;

    if (node_id == 0) {
        for (int i = 0; i < 4; i++) {
            dsm_store(HOME_NODE, acc_off(i), INIT_BALANCE);
        }
    }
    sync_wait(0b111);

    for (int r = 0; r < ROUND_COUNT; r++) {
        uint32_t amt_a = (uint32_t)((r % 7) + 1);
        uint32_t amt_b = (uint32_t)(((r * 5) % 9) + 1);
        uint32_t amt_c = (uint32_t)(((r * 7) % 5) + 1);

        /* Phase A: Node0 & Node1 并发做不相交转账 */
        if (node_id == 0) do_transfer(0, 1, amt_a);
        if (node_id == 1) do_transfer(2, 3, amt_b);
        sync_wait(0b111);

        /* Phase B: Node2 单独执行账本内转账，降低协议路径冲突 */
        if (node_id == 2) do_transfer(1, 2, amt_c);
        sync_wait(0b111);
    }

    if (node_id == 0) {
        uint32_t total = 0;
        for (int i = 0; i < 4; i++) {
            uint32_t b = dsm_load(HOME_NODE, acc_off(i));
            emit_read_val(node_id, HOME_NODE, b, b, 1);
            total += b;
        }
        uint32_t expected_total = 4u * INIT_BALANCE;
        emit_read_val(node_id, HOME_NODE, expected_total, total,
                      total == expected_total);
        if (total != expected_total) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
