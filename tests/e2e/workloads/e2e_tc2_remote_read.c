/* E2E-TC2: Wait-nop parameter sweep */
#include "dsm_access.h"
#include "e2e_common.h"

#ifndef WAIT_NOPS
#define WAIT_NOPS 0
#endif

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC2");
    if (node_id > 1) { if (primary) emit_phase_done(node_id, "idle"); _exit_program(0); return 0; }
    if (!primary) { _exit_program(0); return 0; }
    int fail = 0;

    if (node_id == 0) {
        uint32_t val = 0x11223344;
        emit_before_wr(node_id, 1, val);
        dsm_store(1, 0, val);
        uint32_t v; int retries = 10000;
        do { v = dsm_load(1, 0); asm volatile("dmb osh" ::: "memory"); } while (v != val && --retries > 0);
        emit_after_wr(node_id, 1, val);
    }

    sync_wait(0b011, 1);

    if (node_id == 1) {
        uint32_t expected = 0x11223344;
        emit_before_rd(node_id, 1);
        uint32_t got = dsm_load(1, 0);
        int match = (got == expected);
        emit_read_val(node_id, 1, expected, got, match);
        if (!match) fail++;
#ifdef EP_CLOSE_WRAP_MICRO
        // Real distinct coherent reads, not synthetic completion notifications.
        for (unsigned i = 1; i <= 300; ++i) {
            (void)dsm_load(1, i * 64);
            asm volatile("dmb osh" ::: "memory");
        }
#endif
#ifdef EP_AUTHORITY_OVERFLOW_MICRO
        for (unsigned i = 1; i <= 17; ++i) {
#ifdef EP_AUTHORITY_DIRTY_MICRO
            dsm_store(1, i * 64, 0x24680000u + i);
#else
            (void)dsm_load(1, i * 64);
#endif
            asm volatile("dsb sy" ::: "memory");
        }
        for (unsigned i = 1; i <= 17; ++i) {
            uint32_t v = dsm_load(1, i * 64);
#ifdef EP_AUTHORITY_DIRTY_MICRO
            if (v != 0x24680000u + i) fail++;
#else
            if (v != 0) fail++;
#endif
        }
#endif
    }

    sync_wait(0b011, 1);
    emit_phase_done(node_id, fail ? "fail" : "done");
    _exit_program(fail ? 1 : 0);
    return 0;
}
