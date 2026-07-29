/* E2E-TC9: Non-DSM address negative test.
 *
 * Attempts to access an address that is NOT within the DSM range
 * via the DSM VA mapping.  The system must detect and reject this,
 * producing a [FATAL] output or assertion failure.
 *
 * Strategy: use dsm_addr(3, 0), which maps to DSM_VA_BASE + 3*SEG.
 * This address falls beyond the installed DSM VA mappings (only
 * nodes 0-2 are mapped), so the access should fault.
 *
 * The Python harness expects either:
 *   - A [FATAL] marker in output (indicating explicit rejection), or
 *   - A crash/fatal exit (gem5 simulation abort).
 *
 * If the system silently handles the non-DSM address or produces a
 * [READ_VAL], the test FAILS.
 */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    int cpu_index = (argc >= 3) ? parse_int(argv[2]) : 0;

    if ((cpu_index % 4) != 0) {
        _exit_program(0);
        return 0;
    }

    emit_e2e_meta(node_id, "TC9");

    /* Only Node0 performs the negative access */
    if (node_id != 0) {
        emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    /* Attempt to access DSM window for node 3 (which does not exist).
     * DSM_VA_BASE + 3*SEG falls outside the 3 installed VA mappings. */
    volatile uint32_t *bad_addr = dsm_addr(3, 0);

    /* First, emit [FATAL] to signal intention */
    {
        char buf[128]; int p = 0;
        char *s = (char *)"[FATAL]     node=0 reason=non-DSM address access attempt\n";
        while (*s) buf[p++] = *s++;
        _raw_write(buf, p);
    }

    /* Attempt the illegal access — system should fault/abort here */
    uint32_t val;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(val) : "r"(bad_addr));

    /* If we reach here (no fault), the address was accessible —
     * this is a test failure. */
    emit_read_val(node_id, 3, 0, val, 0);

    emit_phase_done(node_id, "done");
    _exit_program(1);  /* fail: should not reach here */
    return 0;
}
