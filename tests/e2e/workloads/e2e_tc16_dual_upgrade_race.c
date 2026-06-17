/* E2E-TC16: Dual shared-upgrade race — two sharers race to upgrade the
 * same shared line to exclusive/modified at the same time.
 *
 * Scenario:
 *   1. Node2 (home) writes 0x55 to DSM_2.
 *   2. Node0 and Node1 both shared-read DSM_2, becoming sharers.
 *   3. Barrier release: Node0 stores 0xA0A0, Node1 stores 0xB0B0 concurrently.
 *   4. Node0, Node1, and Node2 all read DSM_2.
 *
 * Expected: final value ∈ {0xA0A0, 0xB0B0}, all 3 nodes agree.
 *
 * Primary-only filter for barrier sync.
 */
#include "dsm_access.h"
#include "e2e_common.h"

static inline void emit_read_phase(int node_id, const char *tag, uint32_t val)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[READ_PHASE] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" tag="; while (*s) buf[p++] = *s++;
    while (*tag) buf[p++] = *tag++;
    s = (char *)" val="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, val);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC16");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    /* ── Phase 1: Node2 initialises DSM_2 to 0x55 ── */
    if (node_id == 2) {
        emit_before_wr(node_id, 2, 0x55);
        dsm_store(2, 0, 0x55);
        emit_after_wr(node_id, 2, 0x55);
    }
    sync_wait(0b111);

    /* ── Phase 2: Node0 and Node1 shared-read, become sharers ── */
    if (node_id == 0 || node_id == 1) {
        emit_before_rd(node_id, 2);
        uint32_t got = dsm_load(2, 0);
        emit_read_val(node_id, 2, 0x55, got, got == 0x55);
        if (got != 0x55) fail++;
    }
    sync_wait(0b111);

    /* ── Phase 3: Concurrent upgrades ── */
    if (node_id == 0) {
        emit_before_wr(node_id, 2, 0xA0A0);
        dsm_store(2, 0, 0xA0A0);
        emit_after_wr(node_id, 2, 0xA0A0);
    }
    if (node_id == 1) {
        emit_before_wr(node_id, 2, 0xB0B0);
        dsm_store(2, 0, 0xB0B0);
        emit_after_wr(node_id, 2, 0xB0B0);
    }
    sync_wait(0b111);

    /* ── Phase 4: All 3 nodes read the final value ── */
    /* Read with retry loop in case the line is still being updated */
    uint32_t final_val;
    int retries = 10000;
    do {
        final_val = dsm_load(2, 0);
        __asm__ volatile("dmb osh" ::: "memory");
    } while (final_val != 0xA0A0 && final_val != 0xB0B0 && --retries > 0);

    int legal = (final_val == 0xA0A0 || final_val == 0xB0B0);
    emit_read_val(node_id, 2, 0xA0A0 /* expected is legal */, final_val, legal);
    emit_read_phase(node_id, "final", final_val);
    if (!legal) fail++;

    sync_wait(0b111);

    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
