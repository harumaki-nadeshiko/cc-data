/* Phase 5 TC203: H64 spill/onload regression.
 *
 * This is no longer a Schema A page-chain overflow test. It drives enough
 * metadata churn to exercise H64 spill/fill through the normal E2E path;
 * collision and tombstone semantics are covered by h64_host_phase3_test.
 *
 * Config: --bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0
 *   (spill mode, 8-entry ResidentDir to force rapid backstore spill).
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define PATTERN 0xD1000000DEAD0000ULL

/* Offsets mapping to group 5 (pre-computed) */
static const uint32_t g5_offs[25] = {
    0x00280u, 0x00380u, 0x00cc0u, 0x00f80u, 0x01100u,
    0x015c0u, 0x01ec0u, 0x02280u, 0x02500u, 0x02940u,
    0x02980u, 0x02bc0u, 0x02d40u, 0x03380u, 0x03700u,
    0x03f00u, 0x04240u, 0x04a00u, 0x04d80u, 0x04dc0u,
    0x055c0u, 0x057c0u, 0x05b40u, 0x05e00u, 0x062c0u,
};

int main(int argc, char **argv)
{
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    emit_e2e_meta(node_id, "D1_OVERFLOW");
    int verify_ok = 1;

    /* Phase 1: node1 writes all 25 lines */
    if (node_id == 1) {
        for (int i = 0; i < 25; i++) {
            emit_before_wr(1, 0, (uint32_t)(PATTERN | (uint64_t)i));
            dsm_store64(0, g5_offs[i], PATTERN | (uint64_t)i);
            emit_after_wr(1, 0, (uint32_t)(PATTERN | (uint64_t)i));
        }
        emit_phase_done(1, "populate");
    }
    sync_wait(0b111);

    /* Phase 2: node0 accesses many cold lines to force capacity
     * eviction of the 25 lines, spilling them to backstore. */
    if (node_id == 0) {
        for (int i = 0; i < 50; i++) {
            emit_before_wr(0, 0, (uint32_t)i);
            dsm_store64(0, 0x10000u + (uint32_t)i * 64u, (uint64_t)i);
            emit_after_wr(0, 0, (uint32_t)i);
        }
        emit_phase_done(0, "flush");
    }
    sync_wait(0b111);

    /* Phase 3: node2 reads back all 25 lines.  Those that were
     * spilled should be restored via backstore fill. */
    if (node_id == 2) {
        int mismatch = 0;
        for (int i = 0; i < 25; i++) {
            uint64_t expected = PATTERN | (uint64_t)i;
            emit_before_rd(2, 0);
            uint64_t got = dsm_load64(0, g5_offs[i]);
            if (got != expected) {
                mismatch++;
                emit_read_val(2, 0, (uint32_t)(expected & 0xFFFFFFFFu),
                              (uint32_t)(got & 0xFFFFFFFFu), 0);
            }
        }
        if (mismatch == 0) {
            /* Report one successful read for the verifier */
            emit_read_val(2, 0, (uint32_t)(PATTERN & 0xFFFFFFFFu),
                          (uint32_t)(PATTERN & 0xFFFFFFFFu), 1);
        }
        emit_phase_done(2, "verify");
        verify_ok = !mismatch;
    }

    if (node_id == 0 || node_id == 1)
        emit_phase_done(node_id, "idle");
    // Keep all transport peers alive through node2's complete readback.
    sync_wait(0b111);
    _exit_program(node_id == 2 && !verify_ok ? 1 : 0);
    return 0;
}
