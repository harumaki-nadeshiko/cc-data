/* TC43: rapid ownership cycling across three nodes. */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x4300
#define ROUNDS    64

static inline void emit_tc43_round(int node_id, int r)
{
    char buf[160]; int p = 0;
    char *s = (char *)"[TC43_ROUND] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" round="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, r);
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
    emit_e2e_meta(node_id, "TC43");

    int fail = 0;
    for (int r = 0; r < ROUNDS; r++) {
        int owner = r % 3;
        int reader = (owner + 1) % 3;
        uint32_t val = 0x43000000u | (uint32_t)r;

        if (node_id == owner) dsm_store(HOME_NODE, X_OFF, val);
        sync_wait(0b111);

        if (node_id == reader) {
            uint32_t got = dsm_load(HOME_NODE, X_OFF);
            emit_read_val(node_id, HOME_NODE, val, got, got == val);
            if (got != val) fail++;
        }
        sync_wait(0b111);

        if ((r & 7) == 7) {
            if (node_id != owner) (void)dsm_load(HOME_NODE, X_OFF);
            sync_wait(0b111);
        }
        if ((r & 15) == 0 && node_id == 0) emit_tc43_round(node_id, r);
    }

    uint32_t final_exp = 0x43000000u | (ROUNDS - 1);
    uint32_t final_got = dsm_load(HOME_NODE, X_OFF);
    emit_read_val(node_id, HOME_NODE, final_exp, final_got, final_got == final_exp);
    if (final_got != final_exp) fail++;

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
