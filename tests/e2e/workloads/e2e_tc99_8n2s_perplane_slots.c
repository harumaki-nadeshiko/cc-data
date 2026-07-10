/* TC99: 8-node dual-socket same-home per-plane contention (milder TC98 variant).
 * Each of 16 socket-plane primaries writes to a PER-PLANE private slot on
 * home node 0, socket 0 — NOT the same cache line.  This exercises the home
 * UBCC's 16-way directory pipeline without the single-PA serialization that
 * makes TC98 (>1800s timeout at 8n2s scale).  See docs/measure/tc98_tc99_hotspot.md.
 */
#include "e2e_common.h"

#define NUM_NODES   8
#define NUM_SOCKETS 2
#define TOTAL_SEGS  (NUM_NODES * NUM_SOCKETS)
#define SEG_SIZE    0x8000000ULL
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_SEGS + 1) * SEG_SIZE)
#define ROUNDS      16

static inline volatile uint32_t *my_slot(int node, int socket)
{
    uint64_t va = DSM_VA_BASE + 0 * SEG_SIZE
                  + 0x7800 + (uint64_t)(node * NUM_SOCKETS + socket) * 64ULL;
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

    emit_e2e_meta(node_id, "TC99");

    int fail = 0;
    for (int r = 0; r < ROUNDS; r++) {
        uint32_t v = 0x99000000u | ((uint32_t)node_id << 8)
                     | ((uint32_t)socket_id << 4) | (uint32_t)r;
        *my_slot(node_id, socket_id) = v;
        (void)*my_slot(node_id, socket_id);
        if ((r % 4) == 0) {
            char buf[128]; int p = 0;
            char *s = (char *)"[TC99_PROGRESS] node=";
            while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, node_id);
            s = (char *)" sock="; while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, socket_id);
            s = (char *)" r="; while (*s) buf[p++] = *s++;
            p = fmt_int(buf, p, r);
            buf[p++] = '\n'; _raw_write(buf, p);
        }
    }

    uint32_t done_val = 0x99DD0000u | ((uint32_t)node_id << 8) | (uint32_t)socket_id;
    *my_slot(node_id, socket_id) = done_val;

    sync_wait((1u << TOTAL_SEGS) - 1);

    if (node_id == 0 && socket_id == 0) {
        for (int n = 0; n < NUM_NODES; n++) {
            for (int s = 0; s < NUM_SOCKETS; s++) {
                uint32_t expected = 0x99DD0000u | ((uint32_t)n << 8) | (uint32_t)s;
                uint32_t got = *my_slot(n, s);
                emit_read_val(node_id, n * NUM_SOCKETS + s, expected, got, got == expected);
                if (got != expected) fail++;
            }
        }
    }

    sync_wait((1u << TOTAL_SEGS) - 1);
    _exit_program(fail ? 1 : 0);
    return 0;
}
