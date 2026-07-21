/* TC129: Long mixed integration — multiple spill/fill cycles with
 *        ownership transitions and remote reads.
 *
 * Exercises the full resident-directory offload/onload cycle twice on
 * the same target line with intervening ownership changes, verifying
 * data integrity across the entire sequence.
 *
 * Test flow:
 *   Phase 1: Node0 writes V0 to target on home0 → G_M.
 *   Phase 2: Node0 writes cold aliasing lines → spill #1 of target metadata.
 *   Phase 3: Node1 shared-read → triggers fill #1, gets V0.
 *   Phase 4: Node1 local upgrade (store V1) → G_M on node1.
 *   Phase 5: Node0 writes cold aliasing lines again → spill #2.
 *   Phase 6: Node2 reads target → triggers fill #2, must see V1.
 *
 * This produces exactly at least 2 RESIDENT-SPILL-START and 2
 * RESIDENT-FILL-ISSUED events for the target PA.  The second cold set differs
 * from the first, ensuring it cannot be a local cache hit from Phase 2.
 *
 * Aliasing: ways=1, set_bits=9.  Target offset 0x8000 → set 0.
 * Cold: 0x10000 (k=2), 0x18000 (k=3), ...
 */
#include "dsm_access.h"
#include "e2e_common.h"

/* ── TC129 constants ──────────────────────────────────────────────── */
#define TARGET_OFF     0x08000u   /* set 0: (0x10008000>>6)&0x1FF = 0 */
#define TC129_V0       0x12900000u
#define TC129_V1       0x1290FADEu
#define COLD_LINES     2

/* Cold offsets aliasing to resident-dir set 0:
 *   All offsets that are multiples of 0x8000 map to set 0.
 *   k=0: 0x0000, k=1: 0x8000 (target), k=2: 0x10000, k=3: 0x18000, ...
 */
static uint32_t cold_off(int phase, int i)
{
    /* Phase 2 uses k=2,3; Phase 5 uses k=4,5. */
    return (uint32_t)(0x10000u + (uint32_t)(phase * COLD_LINES + i) * 0x8000u);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC129");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Node0 writes V0 → G_M on home0 ── */
    if (node_id == 0) {
        dsm_store(0, TARGET_OFF, TC129_V0);
        emit_phase_done(0, "init_v0");
    }
    sync_wait(0b111);

    /* ── Phase 2: Node0 cold aliasing → spill #1 ── */
    if (node_id == 0) {
        for (int i = 0; i < COLD_LINES; i++) {
            dsm_store(0, cold_off(0, i), 0x129C0000u | (uint32_t)i);
        }
        emit_phase_done(0, "spill_1");
    }
    sync_wait(0b111);

    /* ── Phase 3: Node1 shared-read → fill #1, must see V0 ── */
    if (node_id == 1) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(1, 0, TC129_V0, got, got == TC129_V0);
        emit_phase_done(1, "read_v0_onload");
    }
    sync_wait(0b111);

    /* ── Phase 4: Node1 local upgrade → store V1, becomes G_M ── */
    if (node_id == 1) {
        dsm_store(0, TARGET_OFF, TC129_V1);
        emit_phase_done(1, "upgrade_v1");
    }
    sync_wait(0b111);

    /* ── Phase 5: Node0 cold aliasing again → spill #2 ── */
    if (node_id == 0) {
        /* Re-write cold aliasing lines to evict the (now-upgraded)
         * target metadata again. */
        for (int i = 0; i < COLD_LINES; i++) {
            dsm_store(0, cold_off(1, i), 0x129D0000u | (uint32_t)i);
        }
        emit_phase_done(0, "spill_2");
    }
    sync_wait(0b111);

    /* ── Phase 6: Node2 reads target → fill #2, must see V1 ── */
    if (node_id == 2) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(2, 0, TC129_V1, got, got == TC129_V1);
        emit_phase_done(2, "read_v1_onload");
    }
    sync_wait(0b111);

    /* Node0 also verifies final value */
    if (node_id == 0) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(0, 0, TC129_V1, got, got == TC129_V1);
        emit_phase_done(0, "verify_final");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
