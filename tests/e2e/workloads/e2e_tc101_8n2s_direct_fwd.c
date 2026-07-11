/* TC101: 8-node dual-socket cross-node ownership transfer (C4 Direct-Forward).
 *
 * Tests C4 direct-forward: data goes from old owner directly to new requester.
 * Only socket-0 primary CPU on each node participates in the transfer chain;
 * socket-1 primaries just run barriers silently.
 *
 * Flow (sequential, barrier-gated):
 *   1. Node 0 socket-0 writes sentinel to shared slot on home 0, barrier
 *   2. Node 1 socket-0 does ReadUnique (ownership: 0→1), barrier
 *   3. Node 2 socket-0 does ReadUnique (ownership: 1→2), barrier → C4 ACTIVE
 *   4. Node 3 socket-0 does ReadUnique (ownership: 2→3), barrier → C4 ACTIVE
 *   5. Node 0 socket-0 verifies final value
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_CPUS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_CPUS + 1) * SEG_SIZE)
#define SLOT_OFF    0x8000

static inline volatile uint32_t *shared_slot(void)
{
    uint64_t va = DSM_VA_BASE + 0 * SEG_SIZE + SLOT_OFF;
    return (volatile uint32_t *)va;
}

static inline void dmb_ish(void)
{
    __asm__ volatile("dmb ish" ::: "memory");
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int socket_id = cpu_index % NUM_SOCKETS;
    int local_cpu = cpu_index % 4;
    int primary = (local_cpu < NUM_SOCKETS);
    if (!primary) { _exit_program(0); return 0; }

    int is_actor = (node_id <= 3 && socket_id == 0);  // only these nodes emit
    if (node_id == 0 && socket_id == 0) emit_e2e_meta(node_id, "TC101");

    /* Phase 1: Node 0 socket-0 writes initial value, barrier */
    if (is_actor && node_id == 0) {
        *shared_slot() = 0xC4010000u;
        dmb_ish();
    }
    if (is_actor) emit_phase_done(node_id, "phase1");
    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Phase 2: Node 1 socket-0 ReadUnique (ownership: 0→1) */
    if (is_actor && node_id == 1) {
        uint32_t got = *shared_slot();
        uint32_t expected = 0xC4010000u;
        emit_read_val(node_id, 0, expected, got, got == expected);
        *shared_slot() = 0xC4010001u;
        dmb_ish();
    }
    if (is_actor) emit_phase_done(node_id, "phase2");
    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Phase 3: Node 2 socket-0 ReadUnique (ownership: 1→2, C4 forward!)
     * Home=0, Owner=1, Requester=2 → all three distinct */
    if (is_actor && node_id == 2) {
        uint32_t got = *shared_slot();
        uint32_t expected = 0xC4010001u;
        emit_read_val(node_id, 0, expected, got, got == expected);
        *shared_slot() = 0xC4010002u;
        dmb_ish();
    }
    if (is_actor) emit_phase_done(node_id, "phase3");
    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Phase 4: Node 3 socket-0 ReadUnique (ownership: 2→3, C4 forward!)
     * Home=0, Owner=2, Requester=3 → all three distinct */
    if (is_actor && node_id == 3) {
        uint32_t got = *shared_slot();
        uint32_t expected = 0xC4010002u;
        emit_read_val(node_id, 0, expected, got, got == expected);
        *shared_slot() = 0xC4010003u;
        dmb_ish();
    }
    if (is_actor) emit_phase_done(node_id, "phase4");
    sync_wait((1u << TOTAL_CPUS) - 1);

    /* Phase 5: Node 0 socket-0 verifies final value */
    int fail = 0;
    if (is_actor && node_id == 0) {
        uint32_t expected = 0xC4010003u;
        uint32_t got = *shared_slot();
        emit_read_val(node_id, 0, expected, got, got == expected);
        if (got != expected) fail++;
    }
    if (is_actor) emit_phase_done(node_id, "phase5");
    sync_wait((1u << TOTAL_CPUS) - 1);

    _exit_program(fail ? 1 : 0);
    return 0;
}
