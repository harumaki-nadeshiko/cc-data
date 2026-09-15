#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE    0
#define RING_ITERS   24
#define SLOT0_OFF    0x5000
#define SLOT_STRIDE  0x40
#define ITER_STRIDE  0x100

static inline uint32_t ring_val(int producer, int iter)
{
    return 0x50000000u | ((uint32_t)producer << 12) | (uint32_t)iter;
}

static inline uint32_t ring_off(int slot_node, int iter)
{
    return SLOT0_OFF + (uint32_t)slot_node * SLOT_STRIDE + (uint32_t)iter * ITER_STRIDE;
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC50");
    int fail = 0;
    int prev = (node_id + 2) % 3;
    int next = (node_id + 1) % 3;

#ifdef EP_CONTROL_CREDIT_BURST
    // Opt-in pressure prelude; retain the original TC50 workload and verifier.
    // Node0 owns 32 independent dirty lines. O3 Node1 issues independent loads
    // without an intervening barrier, exercising one Home->owner credit window.
    if (node_id == 0)
        for (unsigned i = 0; i < 32; ++i)
            dsm_store(0, 0x18000 + i * 64, 0x5c000000u + i);
    sync_wait(0b111);
    uint32_t burst[32];
    if (node_id == 1) {
        for (unsigned i = 0; i < 32; i += 8) {
            volatile uint32_t *base = dsm_addr(0, 0x18000 + i * 64);
            __asm__ volatile(
                "ldr %w0, [%8, #0]\n\tldr %w1, [%8, #64]\n\t"
                "ldr %w2, [%8, #128]\n\tldr %w3, [%8, #192]\n\t"
                "ldr %w4, [%8, #256]\n\tldr %w5, [%8, #320]\n\t"
                "ldr %w6, [%8, #384]\n\tldr %w7, [%8, #448]"
                : "=&r"(burst[i]), "=&r"(burst[i+1]), "=&r"(burst[i+2]),
                  "=&r"(burst[i+3]), "=&r"(burst[i+4]), "=&r"(burst[i+5]),
                  "=&r"(burst[i+6]), "=&r"(burst[i+7]) : "r"(base) : "memory");
        }
        for (unsigned i = 0; i < 32; ++i)
            if (burst[i] != 0x5c000000u + i) ++fail;
    }
    sync_wait(0b111);
#endif

    for (int iter = 0; iter < RING_ITERS; iter++) {
        uint32_t my_prod = ring_val(node_id, iter);
        dsm_store(HOME_NODE, ring_off(next, iter), my_prod);

        sync_wait(0b111);

        uint32_t expected = ring_val(prev, iter);
        uint32_t got = dsm_load(HOME_NODE, ring_off(node_id, iter));
        if (got != expected) fail++;

        sync_wait(0b111);
    }

    uint32_t expected_last = ring_val(prev, RING_ITERS - 1);
    uint32_t got_last = dsm_load(HOME_NODE, ring_off(node_id, RING_ITERS - 1));
    emit_read_val(node_id, HOME_NODE, expected_last, got_last, got_last == expected_last);
    if (got_last != expected_last) fail++;

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
