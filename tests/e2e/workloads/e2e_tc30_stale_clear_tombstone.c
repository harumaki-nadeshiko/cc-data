/* TC30: stale clear/tombstone replay analogue + per-node multi-CPU activity. */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE    0
#define MAIN_OFF     0x3000
#define CPU_OFF      0x3400

static inline void emit_tc30_marker(int node_id, int stale, int replay)
{
    char buf[160]; int p = 0;
    char *s = (char *)"[TC30_CLR] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" stale="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, stale);
    s = (char *)" replay="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, replay);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

static inline void secondary_ops(int node_id, int lane)
{
    uint32_t off = CPU_OFF + (uint32_t)(node_id * 4 + lane) * 64u;
    uint32_t v0 = 0x30000000u | ((uint32_t)node_id << 8) | (uint32_t)lane;
    dsm_store(HOME_NODE, off, v0);
    if (lane == 2) {
        uint32_t v1 = dsm_load(HOME_NODE, off);
        dsm_store(HOME_NODE, off + 0x800, v1 ^ 0x00330033u);
    }
    __asm__ volatile("dmb sy" ::: "memory");
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

    emit_e2e_meta(node_id, "TC30");
    int fail = 0;
    uint32_t oldv = 0x30AA0011u;
    uint32_t newv = 0x30BB0022u;

    if (node_id == 0) {
        dsm_store(HOME_NODE, MAIN_OFF, oldv);
    }
    sync_wait(0b111);

    if (node_id == 1) {
        dsm_store(HOME_NODE, MAIN_OFF, newv);
    }
    sync_wait(0b111);

    if (node_id == 0) {
        /* stale clear analogue: re-apply old value then replay new value */
        dsm_store(HOME_NODE, MAIN_OFF + 64, oldv);
        dsm_store(HOME_NODE, MAIN_OFF + 64, newv);
        emit_tc30_marker(node_id, 1, 1);
    }

    if (node_id == 2) {
        uint32_t got = dsm_load(HOME_NODE, MAIN_OFF);
        emit_read_val(node_id, HOME_NODE, newv, got, got == newv);
        if (got != newv) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
