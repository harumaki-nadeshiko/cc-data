/* Dedicated cap8 HA overlap: ordinary architectural stores only. */
#include "e2e_common.h"

static volatile uint32_t *line(unsigned i)
{
    uint64_t base = 0x1000000000000ULL -
        (NUM_NODES * NUM_SOCKETS + 1ULL) * 0x8000000ULL;
    return (volatile uint32_t *)(base + 0x18000 + i * 64);
}

int main(int argc, char **argv)
{
    int node = argc > 1 ? parse_int(argv[1]) : 0;
    int cpu = argc > 2 ? parse_int(argv[2]) : 0;
    if (NUM_NODES != 3 || NUM_SOCKETS != 1) _exit_program(2);
    if (cpu % 4 != 0) _exit_program(0);
    emit_e2e_meta(node, "TC306");
    if (node == 0)
        for (unsigned i = 0; i < 24; ++i) *line(i) = 0x30610000 + i;
    sync_wait(7);
    int failed = 0;
    for (unsigned round = 0; round < 4; ++round) {
        // Both remote nodes stream the same 24 lines with only eight stable
        // slots. The writer alternates each round, overlapping reader escapes.
        const int writer = 1 + (round & 1);
        const uint32_t value = 0x30620000 + round * 256;
        if (node != 0)
            for (unsigned i = 0; i < 8; ++i) {
                volatile uint32_t warm = *line(i);
                (void)warm;
            }
        sync_wait(7);
        if (node == writer)
            for (unsigned i = 0; i < 24; ++i) *line(i) = value + i;
        else if (node != 0)
            for (unsigned i = 0; i < 24; ++i) {
                volatile uint32_t observed = *line((i + 8) % 24);
                (void)observed;
            }
        sync_wait(7);
        if (node != 0)
            for (unsigned i = 0; i < 24; ++i) {
                uint32_t actual = *line(i);
                emit_read_val(node, round * 24 + i, value + i, actual,
                              actual == value + i);
                failed |= actual != value + i;
            }
        sync_wait(7);
    }
    _exit_program(failed ? 1 : 0);
    return 0;
}
