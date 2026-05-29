/* E2E-TC3: Dual-node Ping-Pong owner transfer (3 rounds).
 *
 * Round 1: Node0 writes 0xA → Node1 reads, expects 0xA
 * Round 2: Node1 writes 0xB → Node0 reads, expects 0xB
 * Round 3: Node0 writes 0xC → Node1 reads, expects 0xC
 *
 * Total expected output: 6 [READ_VAL] markers, all MATCH.
 */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    emit_e2e_meta(node_id, "TC3");

    if (node_id > 1) {
        emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Round 1: Node0 writes 0xA, Node1 reads ── */
    if (node_id == 0) {
        emit_before_wr(node_id, 1, 0xA);
        dsm_store(1, 0, 0xA);
        emit_after_wr(node_id, 1, 0xA);
    }
    sync_wait(0b011);
    if (node_id == 1) {
        uint32_t got = dsm_load(1, 0);
        emit_read_val(node_id, 1, 0xA, got, got == 0xA);
        if (got != 0xA) fail++;
    }
    sync_wait(0b011);

    /* ── Round 2: Node1 writes 0xB, Node0 reads ── */
    if (node_id == 1) {
        emit_before_wr(node_id, 1, 0xB);
        dsm_store(1, 0, 0xB);
        emit_after_wr(node_id, 1, 0xB);
    }
    sync_wait(0b011);
    if (node_id == 0) {
        uint32_t got = dsm_load(1, 0);
        emit_read_val(node_id, 1, 0xB, got, got == 0xB);
        if (got != 0xB) fail++;
    }
    sync_wait(0b011);

    /* ── Round 3: Node0 writes 0xC, Node1 reads ── */
    if (node_id == 0) {
        emit_before_wr(node_id, 1, 0xC);
        dsm_store(1, 0, 0xC);
        emit_after_wr(node_id, 1, 0xC);
    }
    sync_wait(0b011);
    if (node_id == 1) {
        uint32_t got = dsm_load(1, 0);
        emit_read_val(node_id, 1, 0xC, got, got == 0xC);
        if (got != 0xC) fail++;
    }
    sync_wait(0b011);

    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
