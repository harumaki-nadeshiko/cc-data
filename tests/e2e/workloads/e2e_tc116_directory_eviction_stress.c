/* TC116: ResidentDir eviction/reload performance stress.
 *
 * This is intentionally not a one-shot streaming fill.  It creates hot shared
 * lines, evicts their directory metadata with a cold set, then reuses the hot
 * lines from another requester.  Spill mode should reload precise directory
 * metadata; naive mode eagerly invalidates/recalls cache copies at eviction
 * time and pays future data-miss/rebuild cost.
 *
 * Small-dir run_multi parameters keep a tiny ResidentDir while preserving a
 * Bloom filter, so resident misses can distinguish "was spilled" from "never
 * seen" and exercise the reload path.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOT_LINES      48
#define COLD_LINES     144
#define HOT_BASE       0x00000u
#define COLD_BASE      0x20000u
#define TC116_VAL      0x11600000u
#define TC116_NEW      0x11610000u

static uint32_t hot_off(int i)
{
    return HOT_BASE + (uint32_t)i * 64u;
}

static uint32_t cold_off(int i)
{
    return COLD_BASE + (uint32_t)i * 64u;
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC116");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* Phase A: node0 owns hot lines on home0. */
    if (node_id == 0) {
        for (int i = 0; i < HOT_LINES; i++) {
            dsm_store(0, hot_off(i), TC116_VAL | (uint32_t)i);
        }
        emit_phase_done(0, "hot_populate");
    }
    sync_wait(0b111);

    /* Phase B: node1 reads hot lines, creating useful shared/cache residency. */
    if (node_id == 1) {
        uint64_t t0 = read_cntvct_el0();
        for (int i = 0; i < HOT_LINES; i++) {
            uint32_t expected = TC116_VAL | (uint32_t)i;
            uint32_t got = dsm_load(0, hot_off(i));
            if ((i % 16) == 0) {
                emit_read_val(1, 0, expected, got, got == expected);
            }
        }
        emit_guest_timer(1, "hot_shared_read", HOT_LINES,
                         read_cntvct_el0() - t0);
        emit_phase_done(1, "hot_shared");
    }
    sync_wait(0b111);

    /* Phase C: node0 streams cold lines to evict hot directory metadata. */
    if (node_id == 0) {
        for (int i = 0; i < COLD_LINES; i++) {
            dsm_store(0, cold_off(i), TC116_VAL | 0x8000u | (uint32_t)i);
        }
        emit_phase_done(0, "cold_overflow");
    }
    sync_wait(0b111);

    /* Phase D: node2 reuses hot lines.  Spill mode should reload directory
     * metadata; naive mode has already invalidated/evicted copies. */
    if (node_id == 2) {
        uint64_t t0 = read_cntvct_el0();
        for (int round = 0; round < 3; round++) {
            for (int i = 0; i < HOT_LINES; i++) {
                uint32_t expected = TC116_VAL | (uint32_t)i;
                uint32_t got = dsm_load(0, hot_off(i));
                if (round == 0 && (i % 16) == 0) {
                    emit_read_val(2, 0, expected, got, got == expected);
                }
            }
        }
        emit_guest_timer(2, "hot_reuse_reload", HOT_LINES * 3,
                         read_cntvct_el0() - t0);
        emit_phase_done(2, "hot_reuse_reload");
    }
    sync_wait(0b111);

    /* Phase E: node1 upgrades a subset. This exposes whether its hot copies
     * survived directory pressure or were eagerly invalidated by naive mode. */
    if (node_id == 1) {
        uint64_t t0 = read_cntvct_el0();
        for (int i = 0; i < HOT_LINES; i += 4) {
            dsm_store(0, hot_off(i), TC116_NEW | (uint32_t)i);
        }
        emit_guest_timer(1, "hot_upgrade", HOT_LINES / 4,
                         read_cntvct_el0() - t0);
        emit_phase_done(1, "hot_upgrade");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int i = 0; i < HOT_LINES; i += 16) {
            uint32_t expected = (i % 4) == 0
                ? (TC116_NEW | (uint32_t)i)
                : (TC116_VAL | (uint32_t)i);
            uint32_t got = dsm_load(0, hot_off(i));
            emit_read_val(2, 0, expected, got, got == expected);
        }
        emit_phase_done(0, "tc116_done");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
