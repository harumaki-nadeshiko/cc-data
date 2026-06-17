/* E2E-TC17: Writeback + home-side overwrite correctness.
 *
 * Tests dirty-owner state preservation across home-side writes.
 *
 * Scenario:
 *   1. Node0 writes V1=0x12345678 to DSM_2 (home=Node2).
 *   2. Node0 reads back — must see V1 (dirty owner reads own data).
 *   3. Node2 (home) writes V2=0x87654321 to DSM_2 (local store
 *      simulating DMA/home-memory overwrite).
 *   4. Node0 reads again — must see V2 (writeback+refill from home).
 *
 * This exercises the critical path: remote dirty owner →
 * home-side overwrite → dirty-owner cache invalidation → refill.
 *
 * Primary-only filter for barrier sync.
 */
#include "dsm_access.h"
#include "e2e_common.h"

static inline void emit_read_phase(int node_id, const char *tag, uint32_t val)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[READ_PHASE] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" tag="; while (*s) buf[p++] = *s++;
    while (*tag) buf[p++] = *tag++;
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

    if (primary) emit_e2e_meta(node_id, "TC17");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;
    /* Use home=Node2 to avoid bug: Node1 home-local-write with remote
     * sharers can deadlock in current protocol.  Node2 home write after
     * Node0 dirty owner exercises the invalidate+refill path safely. */
#define HOME 2

    /* ── Phase 1: Node0 writes V1=0x12345678 ── */
    if (node_id == 0) {
        emit_before_wr(node_id, HOME, 0x12345678);
        dsm_store(HOME, 0, 0x12345678);
        emit_after_wr(node_id, HOME, 0x12345678);
    }
    sync_wait(0b111);

    /* ── Phase 2: Node0 reads back — must see V1 (dirty owner self-read) ── */
    if (node_id == 0) {
        emit_before_rd(node_id, HOME);
        uint32_t got = dsm_load(HOME, 0);
        int ok = (got == 0x12345678);
        emit_read_val(node_id, HOME, 0x12345678, got, ok);
        emit_read_phase(node_id, "pre_dma", got);
        if (!ok) fail++;
    }
    sync_wait(0b111);

    /* ── Phase 3: Node2 (home) writes V2 (DMA) ── */
    if (node_id == HOME) {
        emit_before_wr(node_id, HOME, 0x87654321);
        dsm_store(HOME, 0, 0x87654321);
        emit_after_wr(node_id, HOME, 0x87654321);
    }
    sync_wait(0b111);

    /* ── Phase 4: Node0 reads — must see V2 ── */
    if (node_id == 0) {
        emit_before_rd(node_id, HOME);
        uint32_t got = dsm_load(HOME, 0);
        int ok = (got == 0x87654321);
        emit_read_val(node_id, HOME, 0x87654321, got, ok);
        emit_read_phase(node_id, "post_dma", got);
        if (!ok) fail++;
    }
    sync_wait(0b111);

    /* ── Phase 5: Node1 also reads — must see V2 (cross-check) ── */
    if (node_id == 1) {
        emit_before_rd(node_id, HOME);
        uint32_t got = dsm_load(HOME, 0);
        int ok = (got == 0x87654321);
        emit_read_val(node_id, HOME, 0x87654321, got, ok);
        emit_read_phase(node_id, "post_dma", got);
        if (!ok) fail++;
    }
    sync_wait(0b111);

    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
