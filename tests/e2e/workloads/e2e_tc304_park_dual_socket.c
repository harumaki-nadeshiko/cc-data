/* Dedicated native Park micro workload. No changes to existing guest tests. */
#include "e2e_common.h"

static volatile uint32_t *line_at(int socket)
{
    const uint64_t seg = 0x8000000ULL;
    const uint64_t base = (0x1000000000000ULL -
                           (NUM_NODES * NUM_SOCKETS + 1ULL) * seg);
    return (volatile uint32_t *)(base + socket * seg + 0x7800);
}

int main(int argc, char **argv)
{
    int node = argc > 1 ? parse_int(argv[1]) : 0;
    int cpu = argc > 2 ? parse_int(argv[2]) : 0;
    if (NUM_NODES != 3 || NUM_SOCKETS != 2) _exit_program(2);
    if (cpu % 4 != 0) _exit_program(0);
    emit_e2e_meta(node, "TC304");
    if (node == 0) {
        *line_at(0) = 0x3040AA01;
        *line_at(1) = 0x3040BB02;
    }
    sync_wait(0x3F);
    int fail = 0;
    if (node == 1) {
        for (int socket = 0; socket < 2; ++socket) {
            uint32_t expected = socket ? 0x3040BB02 : 0x3040AA01;
            uint32_t actual = *line_at(socket);
            emit_read_val(node, 0, expected, actual, expected == actual);
            fail |= expected != actual;
        }
    }
    sync_wait(0x3F);
    _exit_program(fail ? 1 : 0);
    return 0;
}
