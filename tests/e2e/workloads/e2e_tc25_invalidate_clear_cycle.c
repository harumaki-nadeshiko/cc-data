/* TC25: 快速 INVALIDATE/Clear 循环（双写者轮转 + 观察者）。 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE  2
#define OFF        0x200
#define CYCLES     512

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC25");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    for (int c = 0; c < CYCLES; c++) {
        int writer = (c & 1); /* node0/node1 轮替 */
        uint32_t val = 0x25000000u | (uint32_t)c;

        if (node_id == writer) {
            dsm_store(HOME_NODE, OFF, val);
        }
        sync_wait(0b111);

        if (node_id != writer) {
            uint32_t got = dsm_load(HOME_NODE, OFF);
            int ok = (got == val);
            if ((c & 63) == 0 || c == CYCLES - 1) {
                emit_read_val(node_id, HOME_NODE, val, got, ok);
            }
            if (!ok) fail++;
        }
        sync_wait(0b111);
    }

    uint32_t final_exp = 0x25000000u | (uint32_t)(CYCLES - 1);
    uint32_t final_got = dsm_load(HOME_NODE, OFF);
    emit_read_val(node_id, HOME_NODE, final_exp, final_got, final_got == final_exp);
    if (final_got != final_exp) fail++;

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
