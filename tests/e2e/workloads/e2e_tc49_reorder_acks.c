/* TC49: reorder acks.
 *
 * Scenario:
 *   Node0 writes a value to DSM home=0.
 *   Node1 and Node2 read (become sharers, G_S).
 *   Node0 does exclusive upgrade → INVALIDATE to Node1/Node2.
 *   Fault rule DROPS Node1's InvalidateAck (forcing retry),
 *   then Node2's ack arrives first. Reordered acks must still converge.
 *   Final read must see the upgraded value (0x49CC0033).
 *
 * Fault config (applied in test_e2e.py):
 *   Rule: match InvalidateAck, src=1, dst=0 → drop (once)
 *   This forces Node1 to retry its ack after Node2's ack has already arrived.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define MAIN_OFF   0x4900

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC49");
    int fail = 0;
    uint32_t init_val = 0x49AA0011u;
    uint32_t final_val = 0x49CC0033u;

    /* Phase 1: Node0 writes initial value */
    if (node_id == 0) {
        dsm_store(HOME_NODE, MAIN_OFF, init_val);
    }
    sync_wait(0b111);

    /* Phase 2: Node1 and Node2 read → become sharers */
    if (node_id == 1) {
        uint32_t got = dsm_load(HOME_NODE, MAIN_OFF);
        if (got != init_val) fail++;
    }
    if (node_id == 2) {
        uint32_t got = dsm_load(HOME_NODE, MAIN_OFF);
        if (got != init_val) fail++;
    }
    sync_wait(0b111);

    /* Phase 3: Node0 does exclusive upgrade (write) → INVALIDATE fanout */
    if (node_id == 0) {
        dsm_store(HOME_NODE, MAIN_OFF, final_val);
    }
    sync_wait(0b111);

    /* Phase 4: All nodes read — must see final_val despite reordered acks */
    uint32_t got = dsm_load(HOME_NODE, MAIN_OFF);
    emit_read_val(node_id, HOME_NODE, final_val, got, got == final_val);
    if (got != final_val) fail++;

    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
