/* E2E-TC13: Cross-node invalidate + fence ordering on two DSM lines
 * (DATA and FLAG) under the same home node (Node2).
 *
 * Scenario:
 *   1. Node0 writes DATA=0x1111 (home=Node2, offset=0).
 *   2. Node1 reads DATA, holds shared copy.
 *   3. Node0: dmb sy, writes DATA=0x2222, dmb sy, writes FLAG=1 (offset=64).
 *      Phases 2 and 3 are separated by barrier to avoid concurrent-access
 *      deadlock on the same home node.
 *   4. After barrier, Node1 reads FLAG (should be 1), dmb sy, reads DATA.
 *
 * Expected: After FLAG=1 is read, DATA must be 0x2222 (not stale 0x1111).
 *
 * Primary-only filter for barrier sync.
 */
#include "dsm_access.h"
#include "e2e_common.h"

/* DATA line offset 0, FLAG line offset 64 (different cache line) */
#define DATA_OFF  0
#define FLAG_OFF  64

static inline void emit_flag_seen(int node_id, int val)
{
    char buf[160]; int p = 0;
    char *s = (char *)"[FLAG_SEEN]  node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" val="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, val);
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

    if (primary) emit_e2e_meta(node_id, "TC13");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Phase 1: Node0 writes DATA=0x1111 ── */
    if (node_id == 0) {
        emit_before_wr(node_id, 2, 0x1111);
        dsm_store(2, DATA_OFF, 0x1111);
        emit_after_wr(node_id, 2, 0x1111);
    }
    sync_wait(0b111);

    /* ── Phase 2: Node1 reads DATA (becomes sharer) ── */
    if (node_id == 1) {
        emit_before_rd(node_id, 2);
        uint32_t got = dsm_load(2, DATA_OFF);
        int ok = (got == 0x1111);
        emit_read_val(node_id, 2, 0x1111, got, ok);
        if (!ok) fail++;
    }
    sync_wait(0b111);

    /* ── Phase 3: Node0 updates DATA=0x2222 with fence, then FLAG=1 ── */
    if (node_id == 0) {
        __asm__ volatile("dmb sy" ::: "memory");

        emit_before_wr(node_id, 2, 0x2222);
        dsm_store(2, DATA_OFF, 0x2222);
        emit_after_wr(node_id, 2, 0x2222);

        __asm__ volatile("dmb sy" ::: "memory");

        emit_before_wr(node_id, 2, 0x1);
        dsm_store(2, FLAG_OFF, 0x1);
        emit_after_wr(node_id, 2, 0x1);
    }
    sync_wait(0b111);

    /* ── Phase 4: Node1 reads FLAG then DATA ── */
    if (node_id == 1) {
        uint32_t flag_val = dsm_load(2, FLAG_OFF);
        emit_flag_seen(node_id, (int)flag_val);

        if (flag_val == 1) {
            __asm__ volatile("dmb sy" ::: "memory");

            emit_before_rd(node_id, 2);
            uint32_t data_val = dsm_load(2, DATA_OFF);
            int ok = (data_val == 0x2222);
            emit_read_val(node_id, 2, 0x2222, data_val, ok);
            if (!ok) fail++;
        } else {
            /* FLAG was not set — fail */
            fail++;
        }
    }
    sync_wait(0b111);

    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
