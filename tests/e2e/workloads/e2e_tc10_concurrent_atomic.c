/* E2E-TC10: Concurrent read/write atomicity (no torn reads).
 *
 * Node0 loops writing incrementing values to DSM_1, while Node1
 * concurrently reads the same line.  Every read must return a value
 * that was produced by a complete write (no partial/torn values).
 *
 * The write values are in range [0xA0000000, 0xA0000000 + ROUNDS).
 * The Python harness collects all [READ_VAL] outputs and verifies
 * that every actual value is in the legal set (and not 0).
 *
 * ROUNDS = 100 iterations.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define ROUNDS 100

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    emit_e2e_meta(node_id, "TC10");

    if (node_id > 1) {
        emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    if (node_id == 0) {
        /* ── Writer: loop writing incrementing values ── */
        for (int i = 0; i < ROUNDS; i++) {
            uint32_t val = (uint32_t)(0xA0000000 + i);
            /* Thin barrier between iterations to prevent
             * compiler from merging writes */
            __asm__ volatile("" : : : "memory");
            dsm_store(1, 0, val);
        }
    } else if (node_id == 1) {
        /* ── Reader: loop reading and printing values ── */
        for (int i = 0; i < ROUNDS; i++) {
            uint32_t got = dsm_load(1, 0);
            emit_read_val(node_id, 1, 0xA0000000, got, 1);
            /* Use MATCH=1 in output; Python harness does its own
             * validation against the legal value set. */
            __asm__ volatile("" : : : "memory");
        }
    }

    emit_phase_done(node_id, "done");
    _exit_program(0);
    return 0;
}
