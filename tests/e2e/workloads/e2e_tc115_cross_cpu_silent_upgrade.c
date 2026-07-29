/* TC115: Cross-CPU silent upgrade test.
 *
 * Purpose: verify that EP_SILENT_UPGRADE=1 eliminates the cross-node
 * CHI transaction when CPU1 writes a line already held exclusively (R_M)
 * by CPU0 on the same node.
 *
 * Flow (node0):
 *   CPU0: dsm_store(HOME=1, X_LINE, v1) → gets R_M in EP-RNF._requesterLines
 *         sets X_SEM=1 (semaphore for CPU1)
 *   CPU1: spins on X_SEM, sees 1 → dsm_store(HOME=1, X_LINE, v2)
 *         → EP-RNF finds R_M → silent upgrade (0 cross-node CHI msgs)
 *         sets X_SEM=2 (ack back to CPU0)
 *   CPU0: sees X_SEM=2 → proceeds to barrier
 *
 *   Node1/Node2: verify X_LINE == v2.
 *
 * HOME_NODE=1 ensures homeNode != _nodeId (required for silent upgrade
 * path in EPBackend::handleRemoteMiss at line 523).
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 1
#define X_LINE    0x11500   /* target line offset */
#define X_SEM     0x11540   /* semaphore line (different cache line from X_LINE) */

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int lane = cpu_index % 4;   /* 0..3 */
    int primary = (lane == 0);

    const uint32_t v1 = 0x1150A000u;
    const uint32_t v2 = 0x1150B000u;
    int fail = 0;

    /* ── Node0: CPU0 (cluster0) & CPU2 (cluster1) cross-CPU upgrade ──
     * DEFAULT_L=2: CPUs 0,1 share L2 in cluster0; CPUs 2,3 share L2 in cluster1.
     * Using lane=0 and lane=2 ensures DIFFERENT L2 caches — CPU2's store
     * will miss L2 and reach EP-RNF → EPBackend::handleRemoteMiss. ── */
    if (node_id == 0) {
        if (lane == 0) {
            /* CPU0 (cluster 0, primary): store v1 exclusively */
            emit_e2e_meta(node_id, "TC115");

            {
                char buf[128]; int p = 0;
                char *s = (char *)"[TC115_CPU0] store v1 to X_LINE\n";
                while (*s) buf[p++] = *s++;
                _raw_write(buf, p);
            }
            dsm_store(HOME_NODE, X_LINE, v1);

            /* Settle so EP-RNF._requesterLines has stable R_M entry */
            coherence_settle();

            /* Signal CPU2 (cluster 1) that it can proceed */
            {
                char buf[128]; int p = 0;
                char *s = (char *)"[TC115_CPU0] signal CPU2 via X_SEM=1\n";
                while (*s) buf[p++] = *s++;
                _raw_write(buf, p);
            }
            dsm_store(HOME_NODE, X_SEM, 1u);

            /* Wait for CPU2 to acknowledge v2 written */
            {
                char buf[128]; int p = 0;
                char *s = (char *)"[TC115_CPU0] waiting for CPU2 done (X_SEM==2)\n";
                while (*s) buf[p++] = *s++;
                _raw_write(buf, p);
            }
            for (int tries = 0; tries < 2000; tries++) {
                uint32_t sem = dsm_load(HOME_NODE, X_SEM);
                if (sem == 2u) break;
                coherence_settle();
            }

            {
                char buf[128]; int p = 0;
                char *s = (char *)"[TC115_CPU0] CPU2 done, proceeding to barrier\n";
                while (*s) buf[p++] = *s++;
                _raw_write(buf, p);
            }

        } else if (lane == 2) {
            /* CPU2 (cluster 1): wait for CPU0's signal, then store v2
             * This store will miss L2 → EP-RNF → EPBackend::handleRemoteMiss
             * → if EP_SILENT_UPGRADE=1, SILENT-WRITE-HIT fires. */

            {
                char buf[128]; int p = 0;
                char *s = (char *)"[TC115_CPU2] waiting for CPU0 flag (X_SEM==1)\n";
                while (*s) buf[p++] = *s++;
                _raw_write(buf, p);
            }
            for (int tries = 0; tries < 2000; tries++) {
                uint32_t sem = dsm_load(HOME_NODE, X_SEM);
                if (sem == 1u) break;
                coherence_settle();
            }

            {
                char buf[128]; int p = 0;
                char *s = (char *)"[TC115_CPU2] flag seen, store v2 to X_LINE\n";
                while (*s) buf[p++] = *s++;
                _raw_write(buf, p);
            }
            dsm_store(HOME_NODE, X_LINE, v2);

            /* Acknowledge back to CPU0 */
            dsm_store(HOME_NODE, X_SEM, 2u);

            {
                char buf[128]; int p = 0;
                char *s = (char *)"[TC115_CPU2] done\n";
                while (*s) buf[p++] = *s++;
                _raw_write(buf, p);
            }

        } else {
            /* lane 1,3: idle — exit immediately */
            _exit_program(0);
            return 0;
        }
    }

    /* ── Global barrier: ensure node0 CPU0+CPU2 have finished before
     * Node1/Node2 attempt verification reads. Only primary arrives. ── */
    if (primary) sync_wait(0b111);

    /* ── Phase 3: only primary CPUs verify convergence to v2 ── */
    if (primary) {
        uint64_t t0 = read_cntvct_el0();
        uint32_t got = dsm_load(HOME_NODE, X_LINE);
        emit_guest_timer(node_id, "silent_upgrade_verify", 1,
                         read_cntvct_el0() - t0);
        emit_read_val(node_id, HOME_NODE, v2, got, got == v2);
        if (got != v2) fail++;
    }
    if (primary) sync_wait(0b111);

    if (primary) emit_phase_done(node_id, "verify");
    _exit_program(fail ? 1 : 0);
    return 0;
}
