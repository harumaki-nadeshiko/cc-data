/* TC127: Writeback offload/onload — dirty writeback survives metadata spill+fill.
 *
 * Verifies that a WritebackReq arriving after the target's resident-directory
 * metadata has been spilled correctly triggers a fill, persists the dirty
 * data, and makes that data available to a subsequent remote reader.
 *
 * Test flow:
 *   Phase 1: Node0 writes nonzero payload V0 to target DSL line on home0
 *            (becomes G_M / dirty owner).
 *   Phase 2: Node0 writes cold aliasing DSM lines to force target metadata
 *            spill from the resident directory.
 *   Phase 3: Node0 calls dsm_flush(0, TARGET_OFF) which writes 16K cache lines
 *            to a local buffer, evicting the dirty target line from L1+L2.
 *            Because the line is dirty, the HN-F generates WritebackDirty →
 *            WritebackReq(+data) to the UBCC.  The UBCC must fill the spilled
 *            metadata then persist the writeback data.
 *   Phase 4: Node1 reads the target — must see V0 (from persisted data, not 0).
 *
 * Aliasing: ways=1, set_bits=9, 512 sets.  Target offset 0x4000 maps
 * to set 256.  Cold lines at 0xC000, 0x14000, ... alias to set 256.
 */
#include "dsm_access.h"
#include "e2e_common.h"

/* ── TC127 constants ──────────────────────────────────────────────── */
#define TARGET_OFF     0x04000u   /* set 256: (0x10004000>>6)&0x1FF = 256 */
#define TC127_V0       0x1270C0DEu  /* nonzero payload */
#define COLD_LINES     2

/* Cold offsets aliasing to resident-dir set 256:
 *   k=0: 0x4000 (target), k=1: 0xC000, k=2: 0x14000, k=3: 0x1C000
 */
static uint32_t cold_off(int i)
{
    return (uint32_t)(0xC000u + (uint32_t)i * 0x8000u);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC127");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Node0 writes V0 → G_M owner on home0 ── */
    if (node_id == 0) {
        dsm_store(0, TARGET_OFF, TC127_V0);
        emit_phase_done(0, "init_dirty");
    }
    sync_wait(0b111);

    /* ── Phase 2: Node0 writes cold aliasing lines → spill target metadata ── */
    if (node_id == 0) {
        for (int i = 0; i < COLD_LINES; i++) {
            dsm_store(0, cold_off(i), 0x127C0000u | (uint32_t)i);
        }
        emit_phase_done(0, "cold_spill");
    }
    sync_wait(0b111);

    /* ── Phase 3: Node0 flushes caches → dirty target evicted → WritebackReq ── */
    if (node_id == 0) {
        uint64_t t0 = read_cntvct_el0();
        dsm_flush(0, TARGET_OFF);
        emit_guest_timer(0, "writeback_flush", 1,
                         read_cntvct_el0() - t0);
        emit_phase_done(0, "flush_wb");
    }
    /* Note: Node0's eviction is local.  All nodes must wait for the
     * writeback to reach ubio and complete before Phase 4. */
    sync_wait(0b111);

    /* ── Phase 4: Node1 reads target — must get V0 from persisted data ── */
    if (node_id == 1) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(1, 0, TC127_V0, got, got == TC127_V0);
        emit_phase_done(1, "remote_read");
    }
    /* Node2 also reads for cross-validation */
    if (node_id == 2) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(2, 0, TC127_V0, got, got == TC127_V0);
        emit_phase_done(2, "remote_read");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
