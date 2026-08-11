#include "dsm_access.h"
#include "e2e_common.h"

#define LINES 32
#define BASE 0x30200000u

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node, "TC302");

    if (node == 0) {
        for (int i = 0; i < LINES; ++i)
            dsm_store(2, (uint32_t)i * 64u, BASE | (uint32_t)i);
        __asm__ volatile("dsb sy" ::: "memory");
    }
    arch_sync_wait(0b111);

    if (node == 1) {
        uint32_t values[LINES];
        for (int i = 0; i < LINES; ++i)
            values[i] = dsm_load(2, (uint32_t)i * 64u);
        __asm__ volatile("dmb sy" ::: "memory");
        for (int i = 0; i < LINES; ++i) {
            uint32_t expected = BASE | (uint32_t)i;
            emit_read_val(node, 2, expected, values[i], values[i] == expected);
        }
    }
    arch_sync_wait(0b111);
    _exit_program(0);
    return 0;
}
