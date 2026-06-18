/* TC24: 三节点并发读写 + ResidentDir 压力。 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE      1
#define PRESS_LINES    4096
#define PRESS_ROUNDS   2048
#define PRESS_BASE     0x40000
#define ANCHOR_BASE    0x900000

static const uint32_t ANCHOR_VAL[3] = {0x24A00001, 0x24B00002, 0x24C00003};

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC24");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    for (int r = 0; r < PRESS_ROUNDS; r++) {
        uint32_t idx = (uint32_t)((node_id * 8191 + r * 13) % PRESS_LINES);
        uint32_t off = PRESS_BASE + idx * 64u;
        uint32_t v = 0x24000000u | ((uint32_t)node_id << 20) | (uint32_t)(r & 0xFFFFF);
        dsm_store(HOME_NODE, off, v);
        if ((r & 7) == 0) {
            uint32_t got = dsm_load(HOME_NODE, off);
            (void)got;
        }
    }
    sync_wait(0b111);

    /* 每节点写入 anchor，供全局一致性检查 */
    uint32_t my_off = ANCHOR_BASE + (uint32_t)node_id * 64u;
    dsm_store(HOME_NODE, my_off, ANCHOR_VAL[node_id]);
    sync_wait(0b111);

    for (int owner = 0; owner < 3; owner++) {
        uint32_t off = ANCHOR_BASE + (uint32_t)owner * 64u;
        uint32_t exp = ANCHOR_VAL[owner];
        uint32_t got = dsm_load(HOME_NODE, off);
        emit_read_val(node_id, HOME_NODE, exp, got, got == exp);
        if (got != exp) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
