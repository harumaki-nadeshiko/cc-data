/* TC102: Writeback data persistence — dirty data must survive L2 eviction.
 *
 * 1. Node 0 writes sentinel 0x10200AA to Node 1's DSM segment (gets G_M)
 * 2. Node 0 forces L2 eviction of that line by writing 9 same-set local
 *    addresses (L2 is 256KB/8-way/64B → 512 sets, stride=32768)
 *    → CHI WritebackDirty → EPSNFController → WritebackReq(+data) → ubio
 * 3. Barrier
 * 4. Node 2 reads the same line from Node 1's DSM segment
 *    → UBCC directory is G_I (writeback released ownership)
 *    → Data from authoritative DsmDataStore via ReadResp payload
 * 5. Verify: node 2 reads 0x10200AA (not 0)
 */
#include "dsm_access.h"
#include "e2e_common.h"

/* L2: 256KB, 8-way, 64B line → 512 sets, stride = 512*64 = 32768.
 * Writing 9 lines at target_set + i*stride evicts the target. */
#define L2_SETS    512
#define L2_ASSOC   8
#define LINE_SIZE  64
#define L2_STRIDE  (L2_SETS * LINE_SIZE)   /* 32768 */

/* 512KB local buffer — enough for 9 strides from any set.
 * Aligned to 64B so set-index arithmetic is clean. */
static volatile char _evict_buf[L2_STRIDE * (L2_ASSOC + 1)]
    __attribute__((aligned(64)));

/* Evict a specific PA from L1+L2 by writing same-set local addresses. */
static inline void evict_line(uint64_t target_pa)
{
    /* set index in L2 = (PA >> 6) & (L2_SETS-1) */
    unsigned set = ((unsigned)(target_pa >> 6)) & (L2_SETS - 1);
    unsigned base_off = set * LINE_SIZE;  /* byte offset of that set in buffer */
    for (int i = 0; i <= L2_ASSOC; i++) {
        unsigned off = base_off + (unsigned)i * L2_STRIDE;
        __asm__ volatile("str %w0, [%1]"
            : : "r"(0xE01C0000u | (unsigned)i), "r"(&_evict_buf[off]) : "memory");
    }
    __asm__ volatile("dmb sy" ::: "memory");
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    if (node_id == 0) emit_e2e_meta(node_id, "TC102");

    if (node_id > 2) { _exit_program(0); return 0; }

    int fail = 0;
    const uint32_t sentinel = 0x10200AAu;
    const uint32_t off = 0x4400;

    /* Step 1: Node 0 writes sentinel to Node 1's DSM segment */
    if (node_id == 0) {
        dsm_store(1, off, sentinel);
        /* read-back confirm */
        { uint32_t v; int r = 10000;
          do { v = dsm_load(1, off); asm volatile("dmb osh":::"memory"); } while (v != sentinel && --r > 0); }

        /* Step 2: Evict the target line from L1+L2 via set-conflict writes */
        uint64_t t_wb = read_cntvct_el0();
        evict_line((uint64_t)(uintptr_t)dsm_addr(1, off));
        emit_guest_timer(0, "writeback_evict", 1,
                         read_cntvct_el0() - t_wb);
    }

    /* Step 3: Barrier */
    sync_wait(0b111);

    /* Step 4-5: Node 2 reads the sentinel */
    if (node_id == 2) {
        uint32_t got = dsm_load(1, off);
        emit_read_val(node_id, 1, sentinel, got, got == sentinel);
        if (got != sentinel) fail++;
    }

    /* Node 1 also reads (home-local path) */
    if (node_id == 1) {
        uint32_t got = dsm_load(1, off);
        emit_read_val(node_id, 1, sentinel, got, got == sentinel);
        if (got != sentinel) fail++;
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
