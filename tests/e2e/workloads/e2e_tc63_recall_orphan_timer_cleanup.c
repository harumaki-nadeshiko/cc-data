/* TC63: RECALL orphan timer cleanup.
 * Node1 writes data, Node2 reads (triggers recall to Node1).
 * Node1 never responds → RECALL stays WAITING_TARGET_RESP.
 * Timer cleanup in wakeup() eventually reclaims it.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x6300

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);
    emit_e2e_meta(node_id, "TC63");

    const uint32_t v1 = 0x6300ABCDu;

    if (node_id == 1) {
        dsm_store(HOME_NODE, X_OFF, v1);
        /* Abandon — never respond to recall snoop */
    }
    sync_wait(0b111);

    if (node_id == 2) {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        (void)got;
        char buf[128]; int p = 0;
        char *s = (char *)"[TC63_ORPHAN] cleanup=timer node="; while (*s) buf[p++] = *s++;
        p = fmt_int(buf, p, node_id);
        buf[p++] = '\n';
        _raw_write(buf, p);
    }
    sync_wait(0b111);

    sync_wait(0b111);
    sync_wait(0b111);
    sync_wait(0b111);

    if (node_id == 0) {
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_read_val(node_id, HOME_NODE, v1, got, got == v1);
    }

    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
