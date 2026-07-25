/* DIAG-WB-QLM-v5: Speedy writeback eviction test.
 *
 * DSM base PA for home=0 is 0x10000000. Use offset=0x0 → PA=0x10000000.
 * Node1 writes dirty value, then uses a small targeted eviction buffer
 * (8 KB = 128 cache lines) to evict the single dirty DSM line from L2.
 * Much faster than the 1MB full dsm_flush.
 */
#include "dsm_access.h"
#include "e2e_common.h"

/* Small eviction buffer: 8 KB = 128 lines. Should be sufficient to
 * evict a single dirty DSM line from a 256KB 8-way L2. */
volatile char _small_flush[8192] __attribute__((aligned(64)));

static inline void small_evict(void)
{
    for (int i = 0; i < 8192; i += 64) {
        __asm__ volatile("str %w0, [%1]" : : "r"(0), "r"(&_small_flush[i]) : "memory");
    }
    __asm__ volatile("dmb sy" ::: "memory");
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (primary) emit_e2e_meta(node_id, "DIAG_WB_QLM");

    if (node_id != 1) {
        if (primary) emit_phase_done(node_id, "idle");
        _exit_program(0);
        return 0;
    }

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    /* Node1: write dirty value at home=0 offset=0x0 (DSM PA=0x10000000) */
    uint32_t val = 0xBEEF;
    emit_before_wr(node_id, 0, val);
    dsm_store(0, 0x0u, val);

    /* Read back to confirm */
    uint32_t v = dsm_load(0, 0x0u);
    __asm__ volatile("dmb osh" ::: "memory");
    int match = (v == val);
    emit_read_val(node_id, 0, val, v, match);

    /* Small targeted eviction to trigger WriteNoSnp → EPSNF → QLM → WriteBackReq */
    emit_phase_done(node_id, "before_flush");
    small_evict();
    emit_phase_done(node_id, "after_flush");

    emit_phase_done(node_id, "done");
    _exit_program(match ? 0 : 1);
    return 0;
}
