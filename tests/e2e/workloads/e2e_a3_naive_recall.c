/* Phase A3 targeted test: NaiveEvict dirty Recall payload invariant.
 *
 * Validates Phase A2's G_M Recall path end-to-end:
 *   1) node1 writes a known 64-bit pattern to DSM line homed at node0,
 *      acquiring G_M.
 *   2) The true naive ResidentDir is small enough that node0's next access
 *      to a same-set PA triggers capacity eviction of the G_M victim.
 *   3) evictOneVictimNaive creates a Recall outstanding, keeps the entry
 *      pinned, and issues RecallReq to node1.
 *   4) node1's EP-RNF responds with RecallResp carrying the dirty payload.
 *   5) home persists the payload (writeDsmData + _lineDataCache), removes
 *      the victim, and replays capacity waiters.
 *   6) node2 reads the original line and verifies the exact pattern.
 *
 * ResidentDir: --sram-bytes=64 --ways=1 --set-bits=0 (layout auto-
 * searches, resulting in ~4 entries / 4 sets with 1 way each).
 *
 * Set mapping (set_bits=2 from auto-search for 64B SRAM):
 *   set = (pa >> 6) & 3
 *   offset=0x00100: (0x400 >> 6 = 4) & 3 = 0 → set 0  (target G_M)
 *   offset=0x00200: (0x800 >> 6 = 8) & 3 = 0 → set 0  (eviction trigger)
 * Both map to set 0, so accessing 0x00200 must evict 0x00100.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define TARGET_OFF   0x00100u  /* set 0 — node1 creates G_M here  */
#define TRIGGER_OFF  0x00200u  /* set 0 — node0 triggers eviction  */
#define PATTERN      0xA3DEADBEEFCAFEULL   /* 64-bit signature     */

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    int verify_ok = 1;

    emit_e2e_meta(node_id, "A3_NAIVE_RECALL");

    /* ── Phase 1: node1 writes the target line, acquiring G_M ─────── */
    if (node_id == 1) {
        emit_before_wr(1, 0, (uint32_t)(PATTERN & 0xFFFFFFFFu));
        dsm_store64(0, TARGET_OFF, PATTERN);
        emit_after_wr(1, 0, (uint32_t)(PATTERN & 0xFFFFFFFFu));
        emit_phase_done(1, "gmc_owner");
    }

    /* ── Barrier 1: ensure G_M is committed at home ───────────────── */
    sync_wait(0b111);  /* nodes 0,1,2 */

    /* ── Phase 2: node0 triggers capacity eviction ────────────────── *
     * Accessing TRIGGER_OFF (same set as TARGET_OFF) forces the
     * ResidentDir to pick the G_M entry as victim.  The store blocks
     * until Recall→payload→evictDone→waiterReplay completes, so by
     * the time this returns the dirty line has been persisted and the
     * victim entry freed.
     */
    if (node_id == 0) {
        emit_before_wr(0, 0, 0xBBBBu);
        dsm_store64(0, TRIGGER_OFF, 0xBBBBBBBBBBBBBBBBULL);
        emit_after_wr(0, 0, 0xBBBBu);
        emit_phase_done(0, "evict_recall");
    }

    /* ── Barrier 2: all recall/eviction side-effects settled ──────── */
    sync_wait(0b111);

    /* ── Phase 3: node2 verifies the original pattern survived ────── */
    if (node_id == 2) {
        emit_before_rd(2, 0);
        uint64_t got = dsm_load64(0, TARGET_OFF);
        int match = (got == PATTERN);
        verify_ok = match;
        emit_read_val(2, 0, (uint32_t)(PATTERN & 0xFFFFFFFFu),
                      (uint32_t)(got & 0xFFFFFFFFu), match);
        emit_phase_done(2, "verify");
    }

    /* node0/node1 idle after their phases */
    if (node_id == 0 || node_id == 1) {
        emit_phase_done(node_id, "idle");
    }

    // Keep every gem5/UBIO pair alive while node2's final read completes.
    sync_wait(0b111);
    _exit_program(node_id == 2 && !verify_ok ? 1 : 0);
    return 0;
}
