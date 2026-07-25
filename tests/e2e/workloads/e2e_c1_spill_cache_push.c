/* Phase C1 minimal test: spill _lineDataCache push-grant race fix.
 *
 * Sequence:
 *   1) node1 writes pattern to home=0, acquires G_M.
 *   2) node0 fills capacity to spill the G_M entry to backstore.
 *   3) node2 reads target → backstore fill → G_I grant → verify.
 *
 * The C1 fix ensures non-recall routed grant data bypasses the
 * controller-global _recallCaptureData slot (cross-PA race).
 *
 * Config: --bloom-bytes=128 --sram-bytes=4352 (8-entry ResidentDir).
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define TARGET_OFF   0x00100u   /* set 4 */
#define PATTERN      0xC1F1CEDDEADBEEFULL

static const uint32_t fills[7] = {
    0x00000u, 0x00040u, 0x00080u, 0x000C0u,
    0x00140u, 0x00180u, 0x001C0u,
};
#define TRIGGER_OFF  0x01100u   /* set 4 → evicts target */

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    emit_e2e_meta(node_id, "C1_SPILL_FIX");
    int verify_ok = 1;

    /* Phase 1: node1 writes target → G_M */
    if (node_id == 1) {
        // Keep the first split-mode store behind a syscall boundary.  This
        // establishes the initial PDES handshake before the coherence miss.
        emit_before_wr(1, 0, (uint32_t)PATTERN);
        dsm_store64(0, TARGET_OFF, PATTERN);
        emit_after_wr(1, 0, (uint32_t)PATTERN);
        emit_phase_done(1, "gmc_owner");
    }
    sync_wait(0b111);

    /* Phase 2: node0 fills all sets, triggers eviction of target */
    if (node_id == 0) {
        for (int i = 0; i < 7; i++) {
            emit_before_wr(0, 0, (uint32_t)((uint64_t)(i + 1) << 48));
            dsm_store64(0, fills[i], (uint64_t)(i + 1) << 48);
            emit_after_wr(0, 0, (uint32_t)((uint64_t)(i + 1) << 48));
        }
        emit_before_wr(0, 0, 0xBBBBBBBBu);
        dsm_store64(0, TRIGGER_OFF, 0xBBBBBBBBBBBBBBBBULL);
        emit_after_wr(0, 0, 0xBBBBBBBBu);
        emit_phase_done(0, "spill_evict");
    }
    sync_wait(0b111);

    /* Phase 3: node2 reads target → backstore fill → verify */
    if (node_id == 2) {
        emit_before_rd(2, 0);
        uint64_t got = dsm_load64(0, TARGET_OFF);
        int match = (got == PATTERN);
        verify_ok = match;
        emit_read_val(2, 0, (uint32_t)(PATTERN & 0xFFFFFFFFu),
                      (uint32_t)(got & 0xFFFFFFFFu), match);
        emit_phase_done(2, "verify");
    }
    if (node_id == 0 || node_id == 1)
        emit_phase_done(node_id, "idle");
    // Keep all nodes alive through the final split-mode barrier release.
    sync_wait(0b111);
    _exit_program(node_id == 2 && !verify_ok ? 1 : 0);
    return 0;
}
