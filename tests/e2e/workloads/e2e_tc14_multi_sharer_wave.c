/* E2E-TC14: Three-node mixed read/write wave with two full re-sharing phases.
 *
 * Each wave: one node writes, then the other two nodes read and verify.
 *
 * Wave 1: Node0 writes V0=0x1001 → Node1+Node2 read → must see V0.
 * Wave 2: Node1 writes V1=0x2002 → Node0+Node2 read → must see V1.
 * Wave 3: Node2 writes V2=0x3003 → Node0+Node1 read → must see V2.
 *
 * This stresses G_M→G_S→G_M→G_S→G_M with multiple sharers
 * re-created between writes.
 *
 * Primary-only filter for barrier sync.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 2
#define OFFSET    0

static inline void emit_phase_rd(int node_id, int step, uint32_t val)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[PHASE_RD]   node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" step="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, step);
    s = (char *)" val="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, val);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC14");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Wave 1: Node0 writes V0=0x1001 ── */
    if (node_id == 0) {
        emit_before_wr(node_id, HOME_NODE, 0x1001);
        dsm_store(HOME_NODE, OFFSET, 0x1001);
        emit_after_wr(node_id, HOME_NODE, 0x1001);
    }
    sync_wait(0b111);

    /* Node1 + Node2 read — must see V0 */
    if (node_id == 1 || node_id == 2) {
        emit_before_rd(node_id, HOME_NODE);
        uint32_t got = dsm_load(HOME_NODE, OFFSET);
        int ok = (got == 0x1001);
        emit_read_val(node_id, HOME_NODE, 0x1001, got, ok);
        emit_phase_rd(node_id, 1, got);
        if (!ok) fail++;
    }
    sync_wait(0b111);

    /* ── Wave 2: Node1 writes V1=0x2002 ── */
    if (node_id == 1) {
        emit_before_wr(node_id, HOME_NODE, 0x2002);
        dsm_store(HOME_NODE, OFFSET, 0x2002);
        emit_after_wr(node_id, HOME_NODE, 0x2002);
    }
    sync_wait(0b111);

    /* Node0 + Node2 read — must see V1 */
    if (node_id == 0 || node_id == 2) {
        emit_before_rd(node_id, HOME_NODE);
        uint32_t got = dsm_load(HOME_NODE, OFFSET);
        int ok = (got == 0x2002);
        emit_read_val(node_id, HOME_NODE, 0x2002, got, ok);
        emit_phase_rd(node_id, 2, got);
        if (!ok) fail++;
    }
    sync_wait(0b111);

    /* ── Wave 3: Node2 writes V2=0x3003 ── */
    if (node_id == 2) {
        emit_before_wr(node_id, HOME_NODE, 0x3003);
        dsm_store(HOME_NODE, OFFSET, 0x3003);
        emit_after_wr(node_id, HOME_NODE, 0x3003);
    }
    sync_wait(0b111);

    /* Node0 + Node1 read — must see V2 */
    if (node_id == 0 || node_id == 1) {
        emit_before_rd(node_id, HOME_NODE);
        uint32_t got = dsm_load(HOME_NODE, OFFSET);
        int ok = (got == 0x3003);
        emit_read_val(node_id, HOME_NODE, 0x3003, got, ok);
        emit_phase_rd(node_id, 3, got);
        if (!ok) fail++;
    }
    sync_wait(0b111);

    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
