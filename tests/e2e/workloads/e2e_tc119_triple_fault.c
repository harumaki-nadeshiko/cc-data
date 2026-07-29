/* TC119: Triple fault — Drop + Duplicate + Delay on same home.
 *
 * Three writes from node0 to home1, each ClearReq subjected to a
 * different fault type.  All three faults stress the SAME UBCC
 * home process concurrently.
 *
 *   Fault 1 (Drop):    ClearReq for V_A is DROPPED → RECALL recovers.
 *   Fault 2 (Dup):     ClearReq for V_B is DUPLICATED → tombstone handles.
 *   Fault 3 (Delay):   ClearReq for V_C is DELAYED 100µs → eventual commit.
 *
 * Coverage: all three fault types (drop, duplicate, delay) in one test,
 * interacting on the same UBCC home, testing:
 *   - Drop → RECALL-based data recovery from dirty owner
 *   - Dup → idempotent tombstone rejection of duplicate Clear
 *   - Delay → epoch-based commit ordering preserved despite late arrival
 *   - Concurrent faults → no deadlock or state corruption
 *
 * Fault rules (semicolon-separated, matched by distinct homeLinePa):
 *   tc119_drop: ClearReq:0:1:0x10018011900:drop::1
 *   tc119_dup:  ClearReq:0:1:0x10018011940:dup::1
 *   tc119_delay:ClearReq:0:1:0x10018011980:delay:100000:1
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME       1
#define OFF_A      0x11900
#define OFF_B      0x11940
#define OFF_C      0x11980
#define V_A        0x1190AAA1u
#define V_B        0x1190BBB2u
#define V_C        0x1190CCC3u

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC119");
    if (!primary) { _exit_program(0); return 0; }
    if (node_id == 2) { _exit_program(0); return 0; }

    /* ── Phase 1: Node0 writes V_A → Clear DROPPED ── */
    if (node_id == 0) {
        dsm_store(HOME, OFF_A, V_A);
    }
    sync_wait(0b11);

    /* ── Phase 2: Node0 writes V_B → Clear DUPLICATED ── */
    if (node_id == 0) {
        dsm_store(HOME, OFF_B, V_B);
    }
    sync_wait(0b11);

    /* ── Phase 3: Node0 writes V_C → Clear DELAYED 100µs ── */
    if (node_id == 0) {
        dsm_store(HOME, OFF_C, V_C);
    }
    sync_wait(0b11);

    /* ── Phase 4: Node1 reads all three ── */
    if (node_id == 1) {
        uint64_t t0 = read_cntvct_el0();
        uint32_t ga = dsm_load(HOME, OFF_A);
        emit_read_val(1, HOME, V_A, ga, ga == V_A);

        uint32_t gb = dsm_load(HOME, OFF_B);
        emit_read_val(1, HOME, V_B, gb, gb == V_B);

        uint32_t gc = dsm_load(HOME, OFF_C);
        emit_read_val(1, HOME, V_C, gc, gc == V_C);
        emit_guest_timer(1, "triple_fault_convergence", 3,
                         read_cntvct_el0() - t0);
    }
    sync_wait(0b11);

    if (primary) emit_phase_done(node_id, "done");
    _exit_program(0);
    return 0;
}
