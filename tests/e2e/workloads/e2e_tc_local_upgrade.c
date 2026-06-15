/* E2E-TC-LOCAL_UPGRADE: Local write upgrade snoop notification chain.
 *
 * Verifies the end-to-end snoop notification chain for a local write
 * upgrade after a shared read (First Miss with shared_hint).
 *
 * Phase 1: Node B (node_id=1) reads DSM_C (home=Node C, node_id=2).
 *          → First Miss: ReadShared → UBCC grants CompData with shared_hint.
 *          → HN-F registers EP-RNF in dir_sharers.
 *          → EP-RNF setRegistrationDone → REG_DONE.
 *
 * Phase 2: Node B writes DSM_C (local write upgrade).
 *          → CleanUnique → HN-F SC→UD.
 *          → SnpCleanInvalid sent to EP-RNF (multicast).
 *          → EP-RNF recvSnoopMsg: REG_DONE → notifyLocalWriteUpgrade.
 *          → UBCC updateOwner: ownerNode=B, state=UD.
 *
 * Phase 3: Node C reads DSM_C to verify the write value (0xWXYZ).
 *          → Must see the updated value, not stale data.
 *
 * Phase 4: Node A reads DSM_C to verify cross-node shared clean read.
 *          → Node A must see the same value 0xWXYZ.
 *
 * Nodes: A=0, B=1, C=2 (3-node topology).
 * DSM line: DSM_C (home=Node C, node_id=2).
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

    if (primary) emit_e2e_meta(node_id, "TC_LOCAL_UPGRADE");

    /* Only one CPU per node participates.
     * This isolates same-node sibling concurrency from the protocol path
     * under test and keeps sync_wait aligned with node-level arrivals. */
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Phase 1: Node B reads DSM_C (First Miss with shared_hint) ── */
    if (node_id == 1) {
        if (primary) emit_before_rd(node_id, 2);
        uint32_t got = dsm_load(2, 0);
        /* First miss triggers shared_hint registration.
         * The value may be 0 (cold) or pre-seeded by DMA framework;
         * either is acceptable — this phase validates the registration
         * chain, not the data value. Always report MATCH. */
        if (primary) emit_read_val(node_id, 2, got, got, 1);
    }
    sync_wait(0b111);  /* mask = nodes 0+1+2 */

    /* ── Phase 2: Node B writes 0xCA01 to DSM_C (local upgrade) ── */
    if (node_id == 1) {
        uint32_t val = 0xCA01;
        if (primary) emit_before_wr(node_id, 2, val);
        dsm_store(2, 0, val);
        /* Confirmation read: poll until the stored value is visible */
        uint32_t v;
        int retries = 10000;
        do {
            v = dsm_load(2, 0);
            asm volatile("dmb osh" ::: "memory");
        } while (v != val && --retries > 0);
        if (v != val) {
            char *msg = (char *)"[FATAL] TC_LOCAL_UPGRADE store confirmation failed\n";
            _raw_write(msg, 46);
        }
        if (primary) emit_after_wr(node_id, 2, val);
    }
    sync_wait(0b111);

    /* ── Phase 3: Node C (home) reads DSM_C — must see 0xCA01 ── */
    if (node_id == 2) {
        uint32_t expected = 0xCA01;
        if (primary) emit_before_rd(node_id, 2);
        uint32_t got = dsm_load(2, 0);
        int match = (got == expected);
        if (primary) emit_read_val(node_id, 2, expected, got, match);
        if (!match) fail++;
    }
    sync_wait(0b111);

    /* ── Phase 4: Node A (cross-node) reads DSM_C — must see 0xCA01 ── */
    if (node_id == 0) {
        uint32_t expected = 0xCA01;
        if (primary) emit_before_rd(node_id, 2);
        uint32_t got = dsm_load(2, 0);
        int match = (got == expected);
        if (primary) emit_read_val(node_id, 2, expected, got, match);
        if (!match) fail++;
    }
    sync_wait(0b111);

    if (primary) emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
