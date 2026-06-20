/* TC36: owner upgrade under G_E window (design analogue). */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x3600

static inline void emit_tc36_marker(int node_id, int ge, int upg, int recall, int inv)
{
    char buf[220]; int p = 0;
    char *s = (char *)"[TC36_GE] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" ge="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, ge);
    s = (char *)" upg_owner="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, upg);
    s = (char *)" recall="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, recall);
    s = (char *)" inv="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, inv);
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
    emit_e2e_meta(node_id, "TC36");

    int fail = 0;
    const uint32_t v0 = 0x3600AA11u;
    const uint32_t v1 = 0x3600BB22u;

    if (node_id == 0) dsm_store(HOME_NODE, X_OFF, v0);
    sync_wait(0b111);

    if (node_id == 1) {
        (void)dsm_load(HOME_NODE, X_OFF); /* acquire clean-owner path analogue */
        emit_tc36_marker(node_id, 1, 0, 0, 0);
    }
    sync_wait(0b111);

    if (node_id == 1) {
        dsm_store(HOME_NODE, X_OFF, v1); /* owner local upgrade/store */
        emit_tc36_marker(node_id, 1, 1, 0, 0);
    } else if (node_id == 2) {
        for (volatile int i = 0; i < 64; i++) { }
        uint32_t mid = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v1, mid, mid == v1);
        if (mid != v1) fail++;
    }
    sync_wait(0b111);

    {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v1, got, got == v1);
        if (got != v1) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
