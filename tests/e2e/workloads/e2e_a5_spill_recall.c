/* Phase A5 targeted test: Spill-mode backstore durability + G_M recall.
 *
 * Validates Phase A4's spill path end-to-end:
 *   1) node1 writes a known 64-bit pattern to DSM line homed at node0,
 *      acquiring G_M / owner=1.
 *   2) node0 fills the ResidentDir across all sets, then accesses a same-set
 *      PA that forces capacity eviction of the G_M victim.  In spill mode
 *      this must:
 *        a) persist the DirEntry metadata to backstore;
 *        b) receive the persistence ack;
 *        c) only THEN force-remove the resident entry.
 *   3) node2 reads the original line.  The ResidentDir miss triggers a
 *      backstore fill that must reconstruct exact G_M + owner=1 + epoch.
 *   4) The restored G_M + different-owner entry must go through the
 *      existing Recall path (RecallReq -> RecallUnique -> payload -> grant),
 *      never a HomeMemory bypass.
 *   5) node2 verifies the exact pattern.
 *
 * Config: --bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0
 *   -> 8 sets x 1 way = 8 entries (set_bits=3 auto)
 *
 * Set mapping: set = (offset >> 6) & 7
 *   offset 0x00040 -> set 1,  0x00080 -> set 2, ..., 0x001C0 -> set 7
 *   offset 0x00100 -> set 4  (TARGET)
 *   offset 0x01100 -> set 4  (TRIGGER: same set as TARGET -> evict)
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define TARGET_OFF   0x00100u  /* set 4 */
#define TRIGGER_OFF  0x01100u  /* set 4 (same set, forces eviction) */
#define PATTERN      0xA5BEEFCAFEDEADULL

/* Fillers for sets 0,1,2,3,5,6,7 (all except set 4) */
static const uint32_t fill_offs[7] = {
    0x00000u, /* set 0 */
    0x00040u, /* set 1 */
    0x00080u, /* set 2 */
    0x000C0u, /* set 3 */
    0x00140u, /* set 5 */
    0x00180u, /* set 6 */
    0x001C0u, /* set 7 */
};

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    emit_e2e_meta(node_id, "A5_SPILL_RECALL");
    int verify_ok = 1;

    /* Phase 1: node1 creates G_M + owner=1 for target line */
    if (node_id == 1) {
        emit_before_wr(1, 0, (uint32_t)(PATTERN & 0xFFFFFFFFu));
        dsm_store64(0, TARGET_OFF, PATTERN);
        emit_after_wr(1, 0, (uint32_t)(PATTERN & 0xFFFFFFFFu));
        emit_phase_done(1, "gmc_owner");
    }
    sync_wait(0b111);

    /* Phase 2: node0 fills other 7 sets, then evicts target via spill */
    if (node_id == 0) {
        /* Fill all sets except 4 with dummy writes */
        for (int i = 0; i < 7; i++) {
            uint64_t fv = 0x1000000000000000ULL | ((uint64_t)i << 40);
            dsm_store64(0, fill_offs[i], fv);
        }
        /* Now capacity=8 full (7 fillers + 1 target).
         * TRIGGER in set 4 forces spill-eviction of TARGET. */
        emit_before_wr(0, 0, 0xBBBBu);
        dsm_store64(0, TRIGGER_OFF, 0xBBBBBBBBBBBBBBBBULL);
        emit_after_wr(0, 0, 0xBBBBu);
        emit_phase_done(0, "spill_evict");
    }
    sync_wait(0b111);

    /* Phase 3: node2 reads target -> backstore fill -> Recall -> data */
    if (node_id == 2) {
        emit_before_rd(2, 0);
        uint64_t got = dsm_load64(0, TARGET_OFF);
        int match = (got == PATTERN);
        verify_ok = match;
        emit_read_val(2, 0, (uint32_t)(PATTERN & 0xFFFFFFFFu),
                      (uint32_t)(got & 0xFFFFFFFFu), match);
        emit_phase_done(2, "verify");
    }

    if (node_id == 0 || node_id == 1) {
        emit_phase_done(node_id, "idle");
    }

    // Node2 must not exit before nodes 0 and 1 leave the phase-2 barrier.
    // A final rendezvous makes the split-mode workload termination explicit.
    sync_wait(0b111);
    _exit_program(node_id == 2 && !verify_ok ? 1 : 0);
    return 0;
}
