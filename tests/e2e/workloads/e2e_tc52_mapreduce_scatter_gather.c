#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE      0
#define IN_BASE        0x5200
#define OUT_BASE       0x5300
#define STRIDE         0x40

static inline uint32_t in_off(int idx) { return IN_BASE + (uint32_t)idx * STRIDE; }
static inline uint32_t out_off(int idx) { return OUT_BASE + (uint32_t)idx * STRIDE; }
static inline uint32_t map_fn(uint32_t x, int worker)
{
    return (x * x) + (uint32_t)(worker * 17 + 3);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC52");
    int fail = 0;

    if (node_id == 0) {
        dsm_store(HOME_NODE, in_off(0), 11u);
        dsm_store(HOME_NODE, in_off(1), 7u);
        dsm_store(HOME_NODE, in_off(2), 19u);
    }
    sync_wait(0b111);

    uint32_t x = dsm_load(HOME_NODE, in_off(node_id));
    uint32_t y = map_fn(x, node_id);
    dsm_store(HOME_NODE, out_off(node_id), y);

    sync_wait(0b111);

    if (node_id == 2) {
        uint32_t exp0 = map_fn(11u, 0);
        uint32_t exp1 = map_fn(7u, 1);
        uint32_t exp2 = map_fn(19u, 2);
        uint32_t got0 = dsm_load(HOME_NODE, out_off(0));
        uint32_t got1 = dsm_load(HOME_NODE, out_off(1));
        uint32_t got2 = dsm_load(HOME_NODE, out_off(2));
        emit_read_val(node_id, HOME_NODE, exp0, got0, got0 == exp0);
        emit_read_val(node_id, HOME_NODE, exp1, got1, got1 == exp1);
        emit_read_val(node_id, HOME_NODE, exp2, got2, got2 == exp2);

        uint32_t exp_sum = exp0 + exp1 + exp2;
        uint32_t got_sum = got0 + got1 + got2;
        emit_read_val(node_id, HOME_NODE, exp_sum, got_sum, got_sum == exp_sum);
        if (got0 != exp0 || got1 != exp1 || got2 != exp2 || got_sum != exp_sum) {
            fail++;
        }
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
