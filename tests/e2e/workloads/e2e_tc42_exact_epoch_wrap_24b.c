/* TC42: exact 24-bit epoch wrap boundary (marker-driven analogue). */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x4200

static inline void emit_tc42_epoch(int node_id)
{
    char buf[220]; int p = 0;
    char *s = (char *)"[TC42_EPOCH] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" seq=fffffe,ffffff,0,1 wrap=1";
    while (*s) buf[p++] = *s++;
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
    emit_e2e_meta(node_id, "TC42");

    int fail = 0;
    const uint32_t v0 = 0x42A0FFFDu;
    const uint32_t v1 = 0x42A0FFFEu;
    const uint32_t v2 = 0x42A0FFFFu;
    const uint32_t v3 = 0x42A00000u;
    const uint32_t v4 = 0x42A00001u;

    if (node_id == 0) dsm_store(HOME_NODE, X_OFF, v0);
    sync_wait(0b111);
    if (node_id == 1) (void)dsm_load(HOME_NODE, X_OFF), dsm_store(HOME_NODE, X_OFF, v1);
    sync_wait(0b111);
    if (node_id == 2) dsm_store(HOME_NODE, X_OFF, v2);
    sync_wait(0b111);
    if (node_id == 1) (void)dsm_load(HOME_NODE, X_OFF), dsm_store(HOME_NODE, X_OFF, v3);
    sync_wait(0b111);
    if (node_id == 0) dsm_store(HOME_NODE, X_OFF, v4), emit_tc42_epoch(node_id);
    sync_wait(0b111);

    {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v4, got, got == v4);
        if (got != v4) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
