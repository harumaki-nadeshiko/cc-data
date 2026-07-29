/* TC114: Silent upgrade minimal test — exclusive holder second write.
 *
 * Node1 first store gets R_M (cross-node, home Node0).
 * Then second store to same line: without cache eviction this is a
 * local hit (no CHI message); with my EPBackend fix, even if it
 * reaches handleRemoteMiss it would be short-circuited.
 *
 * This version prints diagnostic markers.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define X_OFF     0x11400

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (primary) emit_e2e_meta(node_id, "TC114");
    if (!primary) { _exit_program(0); return 0; }

    int fail = 0;
    const uint32_t v1 = 0x1140A000u;
    const uint32_t v2 = 0x1140B000u;

    /* Phase 1: Node1 first store → R_M */
    if (node_id == 1) {
        char buf[128]; int p = 0;
        char *s = (char *)"[TC114_S1] first store\n";
        while (*s) buf[p++] = *s++;
        _raw_write(buf, p);
        dsm_store(HOME_NODE, X_OFF, v1);
    }
    sync_wait(0b111);

    /* Phase 2: Node1 second store — should be silent with upgrade */
    if (node_id == 1) {
        char buf[128]; int p = 0;
        char *s = (char *)"[TC114_S2] second store\n";
        while (*s) buf[p++] = *s++;
        _raw_write(buf, p);
        dsm_store(HOME_NODE, X_OFF, v2);
    }
    sync_wait(0b111);

    /* Phase 3: Verify */
    {
        uint64_t t0 = read_cntvct_el0();
        uint32_t got = dsm_load(HOME_NODE, X_OFF);
        emit_guest_timer(node_id, "upgrade_verify", 1,
                         read_cntvct_el0() - t0);
        emit_read_val(node_id, HOME_NODE, v2, got, got == v2);
        if (got != v2) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
