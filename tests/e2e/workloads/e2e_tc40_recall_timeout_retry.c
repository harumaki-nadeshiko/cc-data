/* TC40: recall timeout/retry analogue (workload-side marker). */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x4000

static inline void emit_tc40_retry(int node_id, int retry_count)
{
    char buf[180]; int p = 0;
    char *s = (char *)"[TC40_RECALL] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" retry_count="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, retry_count);
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
    emit_e2e_meta(node_id, "TC40");

    int fail = 0;
    const uint32_t v1 = 0x4000D1A1u;

    if (node_id == 1) dsm_store(HOME_NODE, X_OFF, v1);
    sync_wait(0b111);

    if (node_id == 2) {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v1, got, got == v1);
        if (got != v1) fail++;
    }
    if (node_id == 0) emit_tc40_retry(node_id, 1);
    sync_wait(0b111);

    if (node_id == 0) {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v1, got, got == v1);
        if (got != v1) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
