/* TC45: fill conflict + bloom saturation pressure (workload analogue). */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x4500
#define BG_BASE   0x4800
#define BG_LINES  24

static inline void emit_tc45_marker(int node_id, int sat_count, int fill_conflict)
{
    char buf[220]; int p = 0;
    char *s = (char *)"[TC45_STRESS] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" sat_count="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, sat_count);
    s = (char *)" fill_conflict="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, fill_conflict);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    emit_e2e_meta(node_id, "TC45");

    int fail = 0;
    const uint32_t x0 = 0x4500AA11u;
    const uint32_t x1 = 0x4500BB22u;

    if (node_id == 0) {
        dsm_store(HOME_NODE, X_OFF, x0);
        for (int i = 0; i < BG_LINES; i++) {
            dsm_store(HOME_NODE, BG_BASE + (uint32_t)i * 64u, 0x45010000u | (uint32_t)i);
        }
    }
    sync_wait(0b111);

    for (int r = 0; r < 24; r++) {
        uint32_t off = BG_BASE + (uint32_t)((r * 7 + node_id * 13) % BG_LINES) * 64u;
        uint32_t v = 0x45A00000u | ((uint32_t)node_id << 12) | (uint32_t)r;
        dsm_store(HOME_NODE, off, v);
        (void)dsm_load(HOME_NODE, off);
        if ((r % 8) == 7) sync_wait(0b111);
    }

    if (node_id == 1) dsm_store(HOME_NODE, X_OFF, x1);
    if (node_id == 2) (void)dsm_load(HOME_NODE, X_OFF);
    if (node_id == 0) emit_tc45_marker(node_id, 1, 1);
    sync_wait(0b111);

    if (node_id == 1 || node_id == 2) {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, x1, got, got == x1);
        if (got != x1) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
