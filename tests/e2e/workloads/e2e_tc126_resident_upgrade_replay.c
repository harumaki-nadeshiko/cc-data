/* TC126: Resident-waiter upgrade replay correctness.
 *
 * Verifies that an UpgradeReq waiting behind a resident-directory spill/fill
 * is replayed as the ORIGINAL upgrade operation (UPGRADE_PENDING), NOT
 * downgraded to a ReadUnique. The historical bug stored only reqType/writeIntent
 * in PendingRequester, so replayResidentWaiters called processOuterRequest
 * which created a second eviction-invalidation cascade and corrupted state.
 *
 * Test flow:
 *   Phase 1: Node0 writes V0 to target DSM line on home0.
 *   Phase 2: Node1 and Node2 shared-read the target line (G_S on home0).
 *   Phase 3: Node0 fills cold lines that ALIAS to the same resident-dir set
 *            as the target, forcing target's resident metadata to spill.
 *   Phase 4: Node1 does local upgrade store (V1) on the target line.
 *            The home first encounters resident miss, waits for metadata fill,
 *            then replays the Upgrade via processOuterUpgradeReq.
 *   Phase 5: Node2 reads the target line — must see V1 (not stale V0).
 *
 * Aliasing: with ways=1, set_bits=9, the resident directory has 512 sets
 * indexed by (PA>>6) & 0x1FF. Target offset 0x1000 maps to set 64.
 * Cold lines at offsets 0x9000, 0x11000, 0x19000, ... all alias to set 64.
 * With 64 cold lines at the same set, the target's entry is guaranteed evicted.
 *
 * Log assertions (verified by TC126 verifier):
 *   - Resident waiter diagnostics with opKind=1 (Upgrade) for the target PA.
 *   - At least one [RESIDENT-SPILL-START] or [RESIDENT-FILL-ISSUED].
 *   - At least one [UBCC-UPGRADE-COMMIT].
 *   - No evidence of a ReadUnique replay for this upgrade.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOT_LINES      16
#define COLD_LINES     2     /* aliasing lines at set 64 — first evicts target */
#define HOT_BASE       0x00000u
#define COLD_BASE      0x09000u   /* aliases to set 64: (0x9000>>6)=576, 576&0x1FF=64 */
#define TARGET_OFF     0x01000u   /* set 64: (0x1000>>6)=64 */
#define TC126_V0       0x12600000u
#define TC126_V1       0x1260BEEFu

/* Offsets that alias to resident-dir set 64 with set_bits=9:
 *   (offset >> 6) & 0x1FF == 64
 *   offset = (64 + 512*k) * 64 for k=0,1,2,...
 *   k=0: 0x1000  k=1: 0x9000  k=2: 0x11000  k=3: 0x19000
 *   k=4: 0x21000  k=5: 0x29000  k=6: 0x31000  k=7: 0x39000
 */
static uint32_t cold_off(int i)
{
    /* COLD_BASE=0x9000 (k=1), then 0x11000, 0x19000, ... */
    return (uint32_t)(0x1000 + (uint32_t)(i + 1) * 0x8000u);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC126");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Node0 owns target on home0 ── */
    if (node_id == 0) {
        dsm_store(0, TARGET_OFF, TC126_V0);
        emit_phase_done(0, "init_target");
    }
    sync_wait(0b111);

    /* ── Phase 2: Node1 and Node2 shared-read target; Node0 re-reads to sync ── */
    if (node_id == 1 || node_id == 2) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(node_id, 0, TC126_V0, got, got == TC126_V0);
        emit_phase_done(node_id, "shared_read");
    }
    if (node_id == 0) {
        /* Node0 re-reads to ensure its cache transitions to shared
         * and the home directory has all 3 sharers committed before
         * Phase 3 starts evicting resident metadata. */
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(0, 0, TC126_V0, got, got == TC126_V0);
    }
    sync_wait(0b111);

    /* Phase 2.5: Extra sync to ensure all Phase 2 coherence commits
     * (GRANT_HANDSHAKE + Clear) have completed before Phase 3 starts
     * evicting resident metadata. Without this, the backstore snapshot
     * may capture an incomplete sharers mask. */
    sync_wait(0b111);

    /* ── Phase 3: Node0 streams cold aliasing lines to evict target metadata ── */
    if (node_id == 0) {
        for (int i = 0; i < COLD_LINES; i++) {
            dsm_store(0, cold_off(i), 0x126C0000u | (uint32_t)i);
        }
        emit_phase_done(0, "cold_aliasing");
    }
    sync_wait(0b111);

    /* ── Phase 4: Node1 local upgrade — must hit resident miss, wait, replay ── */
    if (node_id == 1) {
        uint64_t t0 = read_cntvct_el0();
        dsm_store(0, TARGET_OFF, TC126_V1);
        emit_guest_timer(1, "resident_upgrade_store", 1,
                         read_cntvct_el0() - t0);
        emit_phase_done(1, "upgrade_store");
    }
    sync_wait(0b111);

    /* ── Phase 5: Node2 reads target — must see V1 ── */
    if (node_id == 2) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(2, 0, TC126_V1, got, got == TC126_V1);
        emit_phase_done(2, "verify_upgrade");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
