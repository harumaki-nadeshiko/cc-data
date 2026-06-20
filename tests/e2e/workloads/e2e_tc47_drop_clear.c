/* TC47: drop Clear, verify tombstone recovery.
 *
 * Scenario:
 *   Node0 writes a value to DSM home=0.
 *   Node1 reads it back (remote shared miss).
 *   Fault rule drops the ClearReq from Node1, forcing retry/tombstone replay.
 *   Node1 must eventually read the correct value (0x47AA0011).
 *
 * Fault config (applied in test_e2e.py):
 *   Rule: match ClearReq, src=1, dst=0 → drop (once)
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define MAIN_OFF   0x4700

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC47");
    int fail = 0;
    uint32_t val = 0x47AA0011u;

    /* Phase 1: Node0 writes */
    if (node_id == 0) {
        dsm_store(HOME_NODE, MAIN_OFF, val);
    }
    sync_wait(0b111);

    /* Phase 2: Node1 reads — Clear may be dropped, tombstone replay recovers */
    if (node_id == 1) {
        uint32_t got = dsm_load(HOME_NODE, MAIN_OFF);
        emit_read_val(node_id, HOME_NODE, val, got, got == val);
        if (got != val) fail++;
    }
    sync_wait(0b111);

    /* Phase 3: Node2 verifies */
    if (node_id == 2) {
        uint32_t got = dsm_load(HOME_NODE, MAIN_OFF);
        emit_read_val(node_id, HOME_NODE, val, got, got == val);
        if (got != val) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
