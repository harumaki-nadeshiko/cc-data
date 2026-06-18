/* TC26: L3 容量压力触发逐出后，目标 line 数据保持正确。 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE     1
#define TARGET_OFF    0x0
#define TARGET_VAL    0x26ABCDEF
#define THRASH_BASE   0x60000
#define THRASH_LINES  512

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC26");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    if (node_id == 0) {
        dsm_store(HOME_NODE, TARGET_OFF, TARGET_VAL);
    }
    sync_wait(0b111);

    /* 大量写入触发 L3 eviction + 下游写回链路 */
    if (node_id == 0) {
        for (int i = 0; i < THRASH_LINES; i++) {
            uint32_t off = THRASH_BASE + (uint32_t)i * 64u;
            dsm_store(HOME_NODE, off, 0x26000000u + (uint32_t)i);
        }
        __asm__ volatile("dmb sy" ::: "memory");
    }
    sync_wait(0b111);

    if (node_id == 1 || node_id == 2) {
        uint32_t got = dsm_load(HOME_NODE, TARGET_OFF);
        emit_read_val(node_id, HOME_NODE, TARGET_VAL, got, got == TARGET_VAL);
        if (got != TARGET_VAL) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
