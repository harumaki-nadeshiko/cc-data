/* TC98: 8-node dual-socket same-PA hot-spot contention.
 * All 16 socket-plane primaries (8 nodes × 2 sockets) repeatedly write to
 * a SINGLE cache line on home node 0, socket 0.  The home UBCC must serialize
 * 16 concurrent requestors through its outstanding / replayArmed pipeline.
 * After N rounds each socket writes a unique final value; node 0 reads
 * them back after a barrier to verify no data was lost or corrupted.
 *
 * WARNING: this workload causes extreme directory serialization —
 * 16 requestors × 16 rounds all targeting the same PA. With push-grant
 * speedup the contention is O(n^2) and may exceed practical timeout limits
 * (observed >1800s at 8n2s scale).  For a milder variant that still
 * exercises the directory pipeline at 8n2s scale without single-PA
 * contention, see TC99.
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_SEGS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_SEGS + 1) * SEG_SIZE)
#define ROUNDS      16

static inline volatile uint32_t *hot_addr(void)
{
    /* Same PA for everyone: home node 0, socket 0, offset 0x7800 */
    uint64_t va = DSM_VA_BASE + 0 * SEG_SIZE + 0x7800;
    return (volatile uint32_t *)va;
}

static inline volatile uint32_t *done_addr(int node, int socket)
{
    /* Separate done markers per socket-plane, home node 0 socket 0 */
    uint64_t va = DSM_VA_BASE + 0 * SEG_SIZE + 0x7800;
    va += (uint64_t)(node * NUM_SOCKETS + socket + 1) * 64ULL;
    return (volatile uint32_t *)va;
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

    emit_e2e_meta(node_id, "TC98");

    int fail = 0;
    for (int r = 0; r < ROUNDS; r++) {
        uint32_t v = 0x98000000u | ((uint32_t)node_id << 8)
                     | ((uint32_t)socket_id << 4) | (uint32_t)r;
        *hot_addr() = v;
        (void)*hot_addr();  /* force read-back for pipeline drain */
        if ((r % 4) == 0) {
            char buf[128]; int p = 0;
            char *s = (char *)"[TC98_PROGRESS] node=";
            while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, node_id);
            s = (char *)" sock="; while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, socket_id);
            s = (char *)" r="; while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, r);
            buf[p++] = '\n'; _raw_write(buf, p);
        }
    }

    /* Write unique final value to per-socket-plane done slot */
    uint32_t done_val = 0x98DD0000u | ((uint32_t)node_id << 8) | (uint32_t)socket_id;
    *done_addr(node_id, socket_id) = done_val;

    sync_wait((1u << TOTAL_SEGS) - 1, NUM_SOCKETS);

    /* Node 0 socket 0 reads all done markers */
    if (node_id == 0 && socket_id == 0) {
        for (int n = 0; n < NUM_NODES; n++) {
            for (int s = 0; s < NUM_SOCKETS; s++) {
                uint32_t expected = 0x98DD0000u | ((uint32_t)n << 8) | (uint32_t)s;
                uint32_t got = *done_addr(n, s);
                emit_read_val(node_id, n * NUM_SOCKETS + s, expected, got, got == expected);
                if (got != expected) fail++;
            }
        }
    }

    sync_wait((1u << TOTAL_SEGS) - 1, NUM_SOCKETS);
    _exit_program(fail ? 1 : 0);
    return 0;
}
