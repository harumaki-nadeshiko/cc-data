/* TC125: Read offload/onload — shared read survives metadata spill+fill.
 *
 * Verifies that a shared-read path correctly triggers resident-directory
 * onload (fill) after the target's metadata was spilled via cold-set
 * pressure, and that subsequent ownership transitions remain correct.
 *
 * Test flow:
 *   Phase 1: Node0 writes V0 to target DSM line on home0.
 *   Phase 2: Node1 and Node2 shared-read the target (G_S on home0).
 *   Phase 3: Node0 fills cold lines that ALIAS to the same resident-dir set
 *            as the target, forcing target's resident metadata to spill.
 *   Phase 4: Node1 reloads/read-validates the target — triggers metadata
 *            fill (onload) and must see V0.
 *   Phase 5: Node2 performs ReadUnique / store new value V1 on target.
 *   Phase 6: Node0 reads and verifies V1.
 *
 * Aliasing: with ways=1, set_bits=9, resident directory has 512 sets
 * indexed by (PA>>6) & 0x1FF. Target offset 0x2000 maps to set 128.
 * Cold lines at offsets 0xA000, 0x12000, ... alias to set 128.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define COLD_LINES     2
#define TARGET_OFF     0x02000u   /* set 128: (0x10002000>>6)&0x1FF = 128 */
#define TC125_V0       0x12500000u
#define TC125_V1       0x1250BEEFu

/* L2: 256KB, 8-way, 64B lines.  Evict node1's clean target copy so its
 * post-spill read must leave the cache and exercise the home onload path. */
#define L2_SETS    512
#define L2_ASSOC   8
#define LINE_SIZE  64
#define L2_STRIDE  (L2_SETS * LINE_SIZE)
static volatile char _evict_buf[L2_STRIDE * (L2_ASSOC + 1)]
    __attribute__((aligned(64)));

static inline void evict_line(uint64_t target_pa)
{
    unsigned set = ((unsigned)(target_pa >> 6)) & (L2_SETS - 1);
    unsigned base_off = set * LINE_SIZE;
    for (int i = 0; i <= L2_ASSOC; i++) {
        unsigned off = base_off + (unsigned)i * L2_STRIDE;
        __asm__ volatile("str %w0, [%1]"
            : : "r"(0xE01C0000u | (unsigned)i), "r"(&_evict_buf[off]) : "memory");
    }
    __asm__ volatile("dmb sy" ::: "memory");
}

/* Offsets that alias to resident-dir set 128 with set_bits=9:
 *   (offset >> 6) & 0x1FF == 128
 *   offset = (128 + 512*k) * 64 for k=0,1,2,...
 *   k=0: 0x2000  k=1: 0xA000  k=2: 0x12000  k=3: 0x1A000
 */
static uint32_t cold_off(int i)
{
    /* Start at k=1 (0xA000), then 0x12000, 0x1A000, ... */
    return (uint32_t)(0xA000u + (uint32_t)i * 0x8000u);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC125");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Node0 owns target on home0 ── */
    if (node_id == 0) {
        dsm_store(0, TARGET_OFF, TC125_V0);
        emit_phase_done(0, "init_target");
    }
    sync_wait(0b111);

    /* ── Phase 2: Node1 and Node2 shared-read target ── */
    if (node_id == 1 || node_id == 2) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(node_id, 0, TC125_V0, got, got == TC125_V0);
        emit_phase_done(node_id, "shared_read");
    }
    if (node_id == 0) {
        /* Node0 re-reads to transition to shared and ensure all 3
         * sharers are committed before Phase 3 starts evicting. */
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(0, 0, TC125_V0, got, got == TC125_V0);
    }
    sync_wait(0b111);
    /* Extra sync to drain pending coherence commits before spill. */
    sync_wait(0b111);

    /* ── Phase 3: Node0 streams cold aliasing lines → spill target metadata ── */
    if (node_id == 0) {
        for (int i = 0; i < COLD_LINES; i++) {
            dsm_store(0, cold_off(i), 0x125C0000u | (uint32_t)i);
        }
        emit_phase_done(0, "cold_aliasing");
    }
    sync_wait(0b111);

    /* ── Phase 4: Node1 re-reads target → triggers fill (onload), must see V0 ── */
    if (node_id == 1) {
        evict_line((uint64_t)(uintptr_t)dsm_addr(0, TARGET_OFF));
        uint64_t t0 = read_cntvct_el0();
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_guest_timer(1, "read_onload", 1,
                         read_cntvct_el0() - t0);
        emit_read_val(1, 0, TC125_V0, got, got == TC125_V0);
        emit_phase_done(1, "read_onload");
    }
    sync_wait(0b111);

    /* ── Phase 5: Node2 ReadUnique / store V1 ── */
    if (node_id == 2) {
        dsm_store(0, TARGET_OFF, TC125_V1);
        emit_phase_done(2, "write_unique");
    }
    sync_wait(0b111);

    /* ── Phase 6: Node0 reads and verifies V1 ── */
    if (node_id == 0) {
        uint32_t got = dsm_load(0, TARGET_OFF);
        emit_read_val(0, 0, TC125_V1, got, got == TC125_V1);
        emit_phase_done(0, "verify_final");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
