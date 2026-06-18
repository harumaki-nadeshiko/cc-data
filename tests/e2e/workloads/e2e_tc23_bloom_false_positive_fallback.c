/* TC23: Bloom Filter 假阳性容忍路径（miss 回退）验证。 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE   2
#define SAT_LINES   2048
#define SAT_BASE    0x20000
#define MISS_OFF    0x700000
#define MAGIC       0x23ABCDEF

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC23");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* Phase1: 大量插入，提升 BF 饱和度 */
    if (node_id == 1) {
        for (int i = 0; i < SAT_LINES; i++) {
            dsm_store(HOME_NODE, SAT_BASE + (uint32_t)i * 64u,
                      0x23000000u + (uint32_t)i);
        }
    }
    sync_wait(0b111);

    /* Phase2: 读取未初始化 line，期望 0（即便 BF 假阳性也应回退正确） */
    if (node_id == 0) {
        uint32_t got0 = dsm_load(HOME_NODE, MISS_OFF);
        emit_read_val(node_id, HOME_NODE, 0x0, got0, got0 == 0);
        if (got0 != 0) fail++;
    }
    sync_wait(0b111);

    /* Phase3: Home 写入该 line */
    if (node_id == HOME_NODE) {
        dsm_store(HOME_NODE, MISS_OFF, MAGIC);
    }
    sync_wait(0b111);

    /* Phase4: 再读应命中 MAGIC */
    if (node_id == 0) {
        uint32_t got1 = dsm_load(HOME_NODE, MISS_OFF);
        emit_read_val(node_id, HOME_NODE, MAGIC, got1, got1 == MAGIC);
        if (got1 != MAGIC) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
