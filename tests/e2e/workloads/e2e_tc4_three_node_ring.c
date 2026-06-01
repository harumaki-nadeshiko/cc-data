/* E2E-TC4: Three-node ring owner transfer.
 *
 * Node0 writes 0x1 → Node1 writes 0x2 → Node2 writes 0x3
 * → Node0 reads, expects 0x3 (the latest value).
 *
 * Total: 4 [READ_VAL] markers. Final Node0 read must be 0x3.
 *
 * Q2: Primary CPU filter for barrier sync.
 */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC4");

    /* Non-primary CPUs exit to avoid sync_wait pairing issues */
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Step 1: Node0 writes 0x1 to DSM_2 (home=Node2) ── */
    if (node_id == 0 && primary) {
        uint32_t val = 0x1;
        emit_before_wr(node_id, 2, val);
        dsm_store(2, 0, val);
        emit_after_wr(node_id, 2, val);
    }
    sync_wait(0b111);
    if (node_id == 0 && primary) {
        uint32_t got = dsm_load(2, 0);
        emit_read_val(node_id, 2, 0x1, got, got == 0x1);
        if (got != 0x1) fail++;
    }
    sync_wait(0b111);

    /* ── Step 2: Node1 writes 0x2 ── */
    if (node_id == 1 && primary) {
        uint32_t val = 0x2;
        emit_before_wr(node_id, 2, val);
        dsm_store(2, 0, val);
        emit_after_wr(node_id, 2, val);
    }
    sync_wait(0b111);
    if (node_id == 1 && primary) {
        uint32_t got = dsm_load(2, 0);
        emit_read_val(node_id, 2, 0x2, got, got == 0x2);
        if (got != 0x2) fail++;
    }
    sync_wait(0b111);

    /* ── Step 3: Node2 writes 0x3 ── */
    if (node_id == 2 && primary) {
        uint32_t val = 0x3;
        emit_before_wr(node_id, 2, val);
        dsm_store(2, 0, val);
        emit_after_wr(node_id, 2, val);
    }
    sync_wait(0b111);
    if (node_id == 2 && primary) {
        uint32_t got = dsm_load(2, 0);
        emit_read_val(node_id, 2, 0x3, got, got == 0x3);
        if (got != 0x3) fail++;
    }
    sync_wait(0b111);

    /* ── Step 4: Node0 reads, expects 0x3 ── */
    if (node_id == 0 && primary) {
        uint32_t got = dsm_load(2, 0);
        emit_read_val(node_id, 2, 0x3, got, got == 0x3);
        if (got != 0x3) fail++;
    }
    sync_wait(0b111);

    if (primary) emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
