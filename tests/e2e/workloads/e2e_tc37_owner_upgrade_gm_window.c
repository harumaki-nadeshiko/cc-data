/* TC37: owner-side second write while already dirty-owner (G_M analogue). */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x3700

static inline void emit_tc37_marker(int node_id, int gm_before_second, int legal)
{
    char buf[180]; int p = 0;
    char *s = (char *)"[TC37_GM] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" gm_before_second="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, gm_before_second);
    s = (char *)" legal="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, legal);
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
    emit_e2e_meta(node_id, "TC37");

    int fail = 0;
    const uint32_t v1 = 0x3700C111u;
    const uint32_t v2 = 0x3700D222u;

    if (node_id == 1) dsm_store(HOME_NODE, X_OFF, v1);
    sync_wait(0b111);

    if (node_id == 1) {
        emit_tc37_marker(node_id, 1, 1);
        dsm_store(HOME_NODE, X_OFF, v2);
    } else if (node_id == 2) {
        for (volatile int i = 0; i < 96; i++) { }
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v2, got, got == v2);
        if (got != v2) fail++;
    }
    sync_wait(0b111);

    {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v2, got, got == v2);
        if (got != v2) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
