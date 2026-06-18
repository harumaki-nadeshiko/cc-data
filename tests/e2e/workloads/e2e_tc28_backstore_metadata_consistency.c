/* TC28: dirty 数据 + 元数据镜像在 resident eviction 后一致性验证。 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE      0
#define DATA_OFF       0x1000
#define META_OFF       0x1040
#define DATA_VAL       0x28AA55AA
#define META_VAL       0x2855AA55
#define PRESS_BASE     0x80000
#define PRESS_LINES    3072

static inline void emit_meta_rel(int node_id, int ok)
{
    char buf[120]; int p = 0;
    char *s = (char *)"[META_REL] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" ok="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, ok);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC28");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    if (node_id == 1) {
        dsm_store(HOME_NODE, DATA_OFF, DATA_VAL);
        dsm_store(HOME_NODE, META_OFF, META_VAL);
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 0; i < PRESS_LINES; i++) {
            uint32_t off = PRESS_BASE + (uint32_t)i * 64u;
            uint32_t got = dsm_load(HOME_NODE, off);
            (void)got;
        }
        __asm__ volatile("dmb sy" ::: "memory");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        uint32_t d = dsm_load(HOME_NODE, DATA_OFF);
        uint32_t m = dsm_load(HOME_NODE, META_OFF);
        emit_read_val(node_id, HOME_NODE, DATA_VAL, d, d == DATA_VAL);
        emit_read_val(node_id, HOME_NODE, META_VAL, m, m == META_VAL);
        int rel_ok = ((d ^ m) == 0x00FFFFFFu);
        emit_meta_rel(node_id, rel_ok);
        if (d != DATA_VAL || m != META_VAL || !rel_ok) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
