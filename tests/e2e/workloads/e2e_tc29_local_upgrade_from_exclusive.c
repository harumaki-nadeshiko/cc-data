/* TC29: local upgrade from exclusive + per-node multi-CPU line activity. */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE      0
#define UPG_OFF        0x2000
#define CPU_BASE_OFF   0x2400

static inline void secondary_ops(int node_id, int lane)
{
    uint32_t off = CPU_BASE_OFF + (uint32_t)(node_id * 4 + lane) * 64u;
    uint32_t val = 0x29000000u | ((uint32_t)node_id << 8) | (uint32_t)lane;
    if (lane == 1) {
        dsm_store(HOME_NODE, off, val);
        dsm_store(HOME_NODE, off + 0x400, val ^ 0x11110000u);
    } else if (lane == 2) {
        uint32_t old = dsm_load(HOME_NODE, off);
        dsm_store(HOME_NODE, off, old ^ val);
    } else if (lane == 3) {
        dsm_store(HOME_NODE, off, val ^ 0x00FF00FFu);
        (void)dsm_load(HOME_NODE, off);
    }
    __asm__ volatile("dmb sy" ::: "memory");
}

static inline void emit_tc29_upgrade(int node_id, uint32_t oldv, uint32_t newv)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[TC29_UPG] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" old="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, oldv);
    s = (char *)" new="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, newv);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int lane = cpu_index & 3;

    secondary_ops(node_id, lane);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC29");
    int fail = 0;
    const uint32_t init_v = 0x29000E11u;
    const uint32_t upg_v = 0x2900F111u;

    if (node_id == 0) {
        dsm_store(HOME_NODE, UPG_OFF, init_v);
        uint32_t oldv = dsm_load(HOME_NODE, UPG_OFF);
        dsm_store(HOME_NODE, UPG_OFF, upg_v);
        emit_tc29_upgrade(node_id, oldv, upg_v);
        if (oldv != init_v) fail++;
    }
    sync_wait(0b111);

    if (node_id == 1) {
        uint32_t got = dsm_load(HOME_NODE, UPG_OFF);
        emit_read_val(node_id, HOME_NODE, upg_v, got, got == upg_v);
        if (got != upg_v) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
