/* TC41: recall + invalidate overlap serialization analogue. */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x4100

static inline void emit_tc41_phase(int node_id, const char *name)
{
    char buf[180]; int p = 0;
    char *s = (char *)"[TC41_PHASE] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" step="; while (*s) buf[p++] = *s++;
    while (*name) buf[p++] = *name++;
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
    emit_e2e_meta(node_id, "TC41");

    int fail = 0;
    const uint32_t v1 = 0x4100A111u;
    const uint32_t v2 = 0x4100B222u;

    if (node_id == 1) dsm_store(HOME_NODE, X_OFF, v1);
    sync_wait(0b111);

    if (node_id == 2) {
        uint32_t r = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v1, r, r == v1);
        emit_tc41_phase(node_id, "recall");
        if (r != v1) fail++;
    }

    if (node_id == 0) {
        for (volatile int i = 0; i < 64; i++) { }
        dsm_store(HOME_NODE, X_OFF, v2);
        emit_tc41_phase(node_id, "invalidate");
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
