#include "dsm_access.h"
#include "e2e_common.h"

#define FIRST 0x30100011u
#define SECOND 0x30100022u

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node, "TC301");

    if (node == 0) {
        dsm_store(2, 0, FIRST);
        __asm__ volatile("dsb sy" ::: "memory");
    }
    arch_sync_wait(0b111);

    if (node == 1) {
        dsm_store(2, 0, SECOND);
        __asm__ volatile("dsb sy" ::: "memory");
    }
    arch_sync_wait(0b111);

    if (node == 0 || node == 2) {
        uint32_t got = dsm_load(2, 0);
        emit_read_val(node, 2, SECOND, got, got == SECOND);
    }
    arch_sync_wait(0b111);
    _exit_program(0);
    return 0;
}
