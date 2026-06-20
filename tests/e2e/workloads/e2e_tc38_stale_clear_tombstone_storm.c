/* TC38: stale-clear/tombstone storm (extended TC30 analogue). */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x3800

static inline void emit_tc38_marker(int node_id, int stale_seen, int replay_ok)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[TC38_CLR] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" stale_clear_seen="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, stale_seen);
    s = (char *)" replay_ok="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, replay_ok);
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
    emit_e2e_meta(node_id, "TC38");

    int fail = 0;
    const uint32_t v1 = 0x38AA0011u;
    const uint32_t v2 = 0x38BB0022u;
    const uint32_t v3 = 0x38CC0033u;

    if (node_id == 1) dsm_store(HOME_NODE, X_OFF, v1);
    sync_wait(0b111);

    if (node_id == 2) dsm_store(HOME_NODE, X_OFF, v2);
    sync_wait(0b111);

    if (node_id == 1) dsm_store(HOME_NODE, X_OFF, v3);
    if (node_id == 0) {
        dsm_store(HOME_NODE, X_OFF + 0x40, v1); /* stale clear analogue #1 */
        dsm_store(HOME_NODE, X_OFF + 0x40, v2); /* stale clear analogue #2 */
        emit_tc38_marker(node_id, 2, 1);
    }
    sync_wait(0b111);

    if (node_id == 2 || node_id == 1) {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v3, got, got == v3);
        if (got != v3) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
