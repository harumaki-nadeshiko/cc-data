/* TC128: Clean evict offload/onload — clean-shared line survives
 *        metadata spill, sharer eviction, and remote re-read.
 *
 * Establishes a clean-shared (G_S) state, forces metadata spill, then
 * evicts the line from the initiating node's cache.  A remote node reads
 * afterward to validate data integrity through the spill/fill cycle.
 *
 * NOTE on clean-EvictReq evidence:
 *   In the current CHI EP implementation, a clean (SC) eviction at the
 *   requesting node is handled locally by the HN-F (SnpCleanInvalid +
 *   Ack) and does NOT generate a ubio-level EvictReq.  The ubio only
 *   observes the resident fill on the next access.  The verifier check
 *   for an explicit EvictReq is therefore marked as optional/soft and
 *   will never cause a spurious failure.
 *
 * Test flow:
 *   Phase 1: Node0 writes V0 to target on home0 (G_M → G_S after Phase 2).
 *   Phase 2: Node1 and Node2 shared-read → G_S, all three sharers.
 *   Phase 3: Node0 writes cold aliasing lines → spill target metadata.
 *   Phase 4: Node1 evicts its target copy from L2 (line is clean-shared).
 *            Clean eviction is silent at ubio level.
 *   Phase 5: Node1 reads the target → triggers resident fill, must see V0.
 *
 * Aliasing: ways=1, set_bits=9.  Target offset 0x6000 → set 384.
 * Cold: 0xE000 (k=1), 0x16000 (k=2), ...
 */
#include "dsm_access.h"
#include "e2e_common.h"

/* ── TC128 constants ──────────────────────────────────────────────── */
#define TARGET_OFF     0x06000u   /* set 384 */
#define TC128_V0       0x1280C1E0u
#define COLD_LINES     2

static uint32_t cold_off(int i)
{
    /* k=1: 0xE000, k=2: 0x16000, ... */
    return (uint32_t)(0xE000u + (uint32_t)i * 0x8000u);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC128");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Node0 writes V0 → G_M on home0 ── */
    if (node_id == 0) {
        dsm_store(0, TARGET_OFF, TC128_V0);
        emit_phase_done(0, "init_target");
    }
    sync_wait(0b111);

    /* ── Phase 2: Node1 and Node2 shared-read → G_S, 3 sharers ── */
    if (node_id == 1 || node_id == 2) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(node_id, 0, TC128_V0, got, got == TC128_V0);
        emit_phase_done(node_id, "shared_read");
    }
    if (node_id == 0) {
        /* Node0 re-reads to transition to shared, ensuring all
         * sharer masks are committed before cold pressure. */
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(0, 0, TC128_V0, got, got == TC128_V0);
    }
    sync_wait(0b111);
    /* Extra sync to drain coherence commits before spill. */
    sync_wait(0b111);

    /* ── Phase 3: Node0 cold aliasing → spill target metadata ── */
    if (node_id == 0) {
        for (int i = 0; i < COLD_LINES; i++) {
            dsm_store(0, cold_off(i), 0x128C0000u | (uint32_t)i);
        }
        emit_phase_done(0, "cold_spill");
    }
    sync_wait(0b111);

    /* ── Phase 4: Node1 evicts its clean target from private caches. ──
     * dsm_flush writes 1MB of local lines, exceeding the L1/L2 hierarchy.
     * The target itself is clean G_S, so the sweep evicts it without turning
     * this phase into a target WritebackReq test. */
    if (node_id == 1) {
        dsm_flush(0, TARGET_OFF);
        emit_phase_done(1, "clean_evict");
    }
    sync_wait(0b111);

    /* ── Phase 5: Node1 reads target → fill + re-read, must see V0 ── */
    if (node_id == 1) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(1, 0, TC128_V0, got, got == TC128_V0);
        emit_phase_done(1, "verify_read");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
