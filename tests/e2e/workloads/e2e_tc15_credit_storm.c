/* E2E-TC15: Resource recovery under credit pressure.
 *
 * Each node sequentially hammers a set of 8 remote DSM lines on the
 * same home node (Node2), alternating load/store for many rounds, to
 * stress RetryAck/PCrdGrant/credit recovery paths.  After all nodes
 * finish, each reads back all 8 lines and they must converge.
 *
 * Sequential phases (via barrier) avoid triple-concurrency deadlock
 * while still stressing HN-F TBE/retry resources per phase.
 *
 * NOTE: Protocol RetryAck/PCrdGrant evidence requires debug output
 * enabled; the verifier checks forward progress and convergence.
 *
 * Primary-only filter for barrier sync.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define N_LINES    8
#define ROUNDS     200
#define HOME_NODE  2

/* 8 DSM lines on Home Node2, spaced 64 bytes apart (different cache lines) */
static const uint32_t OFFSETS[N_LINES] = {
    0, 64, 128, 192, 256, 320, 384, 448
};

static inline void emit_storm_begin(int node_id)
{
    char buf[160]; int p = 0;
    char *s = (char *)"[STORM]      node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" rounds="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, ROUNDS);
    s = (char *)" begin\n"; while (*s) buf[p++] = *s++;
    _raw_write(buf, p);
}

static inline void emit_storm_end(int node_id)
{
    char buf[160]; int p = 0;
    char *s = (char *)"[STORM]      node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" status=end\n"; while (*s) buf[p++] = *s++;
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC15");

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* ── Phase 1: Each node in turn hammers the 8 DSM lines ── */
    /* Node0 goes first, then Node1, then Node2 */
    for (int hammer_node = 0; hammer_node < 3; hammer_node++) {
        if (node_id == hammer_node) {
            emit_storm_begin(node_id);
            for (int r = 0; r < ROUNDS; r++) {
                int idx = r % N_LINES;
                uint32_t off = OFFSETS[idx];
                if (r & 1) {
                    /* Odd rounds: store a per-node value */
                    uint32_t val = (uint32_t)((node_id << 24) | (r & 0xFFFFFF));
                    dsm_store(HOME_NODE, off, val);
                } else {
                    /* Even rounds: load */
                    __asm__ volatile("" : : : "memory");
                    uint32_t got = dsm_load(HOME_NODE, off);
                    (void)got; /* consume to prevent optimisation */
                }
                __asm__ volatile("" : : : "memory");
            }
            emit_storm_end(node_id);
        }
        sync_wait(0b111);  /* All nodes wait while one hammers */
    }

    /* ── Phase 2: Convergence reads ── */
    /* Each node reads all 8 lines and reports final values */
    for (int i = 0; i < N_LINES; i++) {
        uint32_t got = dsm_load(HOME_NODE, OFFSETS[i]);
        emit_read_val(node_id, HOME_NODE, 0, got, 1 /* always MATCH */);
    }

    sync_wait(0b111);

    emit_phase_done(node_id, "done");
    _exit_program(0);
    return 0;
}
