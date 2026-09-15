/* Store-miss acquisition test: Park may revoke the fill, never replay a store. */
#include "e2e_common.h"

static volatile uint32_t *target(void)
{
    uint64_t base = 0x1000000000000ULL -
        (NUM_NODES * NUM_SOCKETS + 1ULL) * 0x8000000ULL;
    return (volatile uint32_t *)(base + 0x7c00);
}

int main(int argc, char **argv)
{
    int node = argc > 1 ? parse_int(argv[1]) : 0;
    int cpu = argc > 2 ? parse_int(argv[2]) : 0;
    if (NUM_NODES != 3 || NUM_SOCKETS != 1) _exit_program(2);
    if (cpu % 4 != 0) _exit_program(0);
    emit_e2e_meta(node, "TC305");
    if (node == 0) *target() = 0x30500001;
    sync_wait(0x7);
    if (node == 1) *target() = 0x30500002;
    sync_wait(0x7);
    uint32_t actual = *target();
    emit_read_val(node, 0, 0x30500002, actual, actual == 0x30500002);
    sync_wait(0x7);
    _exit_program(actual == 0x30500002 ? 0 : 1);
    return 0;
}
