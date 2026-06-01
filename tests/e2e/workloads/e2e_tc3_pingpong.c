/* E2E-TC3: Dual-node Ping-Pong owner transfer (3 rounds).
 *
 * Round 1: Node0 writes 0xA → Node1 reads, expects 0xA
 * Round 2: Node1 writes 0xB → Node0 reads, expects 0xB
 * Round 3: Node0 writes 0xC → Node1 reads, expects 0xC
 *
 * Total expected output: 6 [READ_VAL] markers, all MATCH.
 *
 * Q2: Primary CPU filter + confirmation read loops (TC2 pattern).
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

    if (primary) emit_e2e_meta(node_id, "TC3");

    /* Nodes 0 and 1 participate; Node 2 idle */
    if (node_id > 1) {
        if (primary) emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    /* Non-primary CPUs exit to avoid sync_wait pairing issues (4 CPUs/node × 2 nodes = 8 threads,
     * but sync_wait popcount=2 groups incorrectly). Only primary (cpu_index%4==0) proceeds. */
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Round 1: Node0 writes 0xA, Node1 reads ── */
    if (node_id == 0) {
        uint32_t val = 0xA;
        if (primary) emit_before_wr(node_id, 1, val);
        dsm_store(1, 0, val);
        uint32_t v;
        int retries = 10000;
        do {
            v = dsm_load(1, 0);
            __asm__ volatile("dmb osh" ::: "memory");
        } while (v != val && --retries > 0);
        if (primary) emit_after_wr(node_id, 1, val);
    }
    sync_wait(0b011);
    if (node_id == 1) {
        uint32_t expected = 0xA;
        if (primary) emit_before_rd(node_id, 1);
        uint32_t got = dsm_load(1, 0);
        int m = (got == expected);
        if (primary) emit_read_val(node_id, 1, expected, got, m);
        if (!m) fail++;
    }
    sync_wait(0b011);

    /* ── Round 2: Node1 writes 0xB, Node0 reads ── */
    if (node_id == 1) {
        uint32_t val = 0xB;
        if (primary) emit_before_wr(node_id, 1, val);
        dsm_store(1, 0, val);
        uint32_t v;
        int retries = 10000;
        do {
            v = dsm_load(1, 0);
            __asm__ volatile("dmb osh" ::: "memory");
        } while (v != val && --retries > 0);
        if (primary) emit_after_wr(node_id, 1, val);
    }
    sync_wait(0b011);
    if (node_id == 0) {
        uint32_t expected = 0xB;
        if (primary) emit_before_rd(node_id, 1);
        uint32_t got = dsm_load(1, 0);
        int m = (got == expected);
        if (primary) emit_read_val(node_id, 1, expected, got, m);
        if (!m) fail++;
    }
    sync_wait(0b011);

    /* ── Round 3: Node0 writes 0xC, Node1 reads ── */
    if (node_id == 0) {
        uint32_t val = 0xC;
        if (primary) emit_before_wr(node_id, 1, val);
        dsm_store(1, 0, val);
        uint32_t v;
        int retries = 10000;
        do {
            v = dsm_load(1, 0);
            __asm__ volatile("dmb osh" ::: "memory");
        } while (v != val && --retries > 0);
        if (primary) emit_after_wr(node_id, 1, val);
    }
    sync_wait(0b011);
    if (node_id == 1) {
        uint32_t expected = 0xC;
        if (primary) emit_before_rd(node_id, 1);
        uint32_t got = dsm_load(1, 0);
        int m = (got == expected);
        if (primary) emit_read_val(node_id, 1, expected, got, m);
        if (!m) fail++;
    }
    sync_wait(0b011);

    if (primary) emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
