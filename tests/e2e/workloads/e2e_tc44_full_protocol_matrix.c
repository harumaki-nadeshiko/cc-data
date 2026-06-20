/* TC44: dense multi-line protocol matrix regression. */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define A_OFF     0x4400
#define B_OFF     0x4440
#define C_OFF     0x4480
#define D_OFF     0x44C0

static inline void emit_tc44_path(int node_id, const char *tag)
{
    char buf[180]; int p = 0;
    char *s = (char *)"[TC44_PATH] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" tag="; while (*s) buf[p++] = *s++;
    while (*tag) buf[p++] = *tag++;
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
    emit_e2e_meta(node_id, "TC44");

    int fail = 0;
    const uint32_t A0 = 0x44A00011u, A1 = 0x44A00022u;
    const uint32_t B0 = 0x44B00011u, B1 = 0x44B00022u;
    const uint32_t C0 = 0x44C00011u, C1 = 0x44C00022u;
    const uint32_t D0 = 0x44D00011u, D1 = 0x44D00022u;

    /* Phase1 init */
    if (node_id == 0) dsm_store(HOME_NODE, A_OFF, A0);
    if (node_id == 1) dsm_store(HOME_NODE, B_OFF, B0);
    if (node_id == 2) dsm_store(HOME_NODE, C_OFF, C0);
    if (node_id == 1) dsm_store(HOME_NODE, D_OFF, D0);
    sync_wait(0b111);

    /* Phase2 shared expansion */
    (void)dsm_load(HOME_NODE, A_OFF);
    (void)dsm_load(HOME_NODE, D_OFF);
    sync_wait(0b111);

    /* Phase3 upgrade/writeback/recall analogue */
    if (node_id == 1) { dsm_store(HOME_NODE, B_OFF, B1); emit_tc44_path(node_id, "upgrade"); }
    if (node_id == 2) { dsm_store(HOME_NODE, C_OFF, C1); emit_tc44_path(node_id, "writeback_fill"); }
    if (node_id == 0) { (void)dsm_load(HOME_NODE, D_OFF); emit_tc44_path(node_id, "recall"); }
    sync_wait(0b111);

    /* Phase4 invalidate-to-unique on A + another unique on D */
    if (node_id == 0) { dsm_store(HOME_NODE, A_OFF, A1); emit_tc44_path(node_id, "invalidate_unique"); }
    if (node_id == 2) dsm_store(HOME_NODE, D_OFF, D1);
    sync_wait(0b111);

    uint32_t a = dsm_load(HOME_NODE, A_OFF);
    uint32_t b = dsm_load(HOME_NODE, B_OFF);
    uint32_t c = dsm_load(HOME_NODE, C_OFF);
    uint32_t d = dsm_load(HOME_NODE, D_OFF);
    emit_read_val(node_id, HOME_NODE, A1, a, a == A1); if (a != A1) fail++;
    emit_read_val(node_id, HOME_NODE, B1, b, b == B1); if (b != B1) fail++;
    emit_read_val(node_id, HOME_NODE, C1, c, c == C1); if (c != C1) fail++;
    emit_read_val(node_id, HOME_NODE, D1, d, d == D1); if (d != D1) fail++;

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
