#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE       0
#define HOT_OFF         0x5400
#define FAIR_BASE_OFF   0x5500
#define STRIDE          0x40
#define ROUNDS          96

static inline uint32_t fair_off(int node) { return FAIR_BASE_OFF + (uint32_t)node * STRIDE; }

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC53");
    int fail = 0;

    if (node_id == 0) {
        dsm_store(HOME_NODE, HOT_OFF, 0x53000000u);
        for (int n = 0; n < 3; n++) dsm_store(HOME_NODE, fair_off(n), 0u);
    }
    sync_wait(0b111);

    for (int r = 0; r < ROUNDS; r++) {
        int writer = r % 3;
        if (node_id == writer) {
            uint32_t v = 0x53000000u | ((uint32_t)node_id << 12) | (uint32_t)r;
            dsm_store(HOME_NODE, HOT_OFF, v);
        } else {
            (void)dsm_load(HOME_NODE, HOT_OFF);
        }
        dsm_store(HOME_NODE, fair_off(node_id), (uint32_t)(r + 1));
        sync_wait(0b111);
    }

    if (node_id == 0) {
        for (int n = 0; n < 3; n++) {
            uint32_t got = dsm_load(HOME_NODE, fair_off(n));
            emit_read_val(node_id, HOME_NODE, ROUNDS, got, got == ROUNDS);
            if (got != ROUNDS) fail++;
        }
        uint32_t hot = dsm_load(HOME_NODE, HOT_OFF);
        emit_read_val(node_id, HOME_NODE, hot, hot, 1);
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
